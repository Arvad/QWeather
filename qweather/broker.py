from .constants import *
import zmq
from zmq.devices import ThreadProxy
from zmq.asyncio import Context,Poller
import asyncio
import pickle
import time
import re
import logging
#from zmq.asyncio import Context, Poller


class QWeatherStation:

    def __init__(self,IP,loop = None,verbose=False,debug = False):
        if loop is None:
            #from zmq import Context,Poller
            import asyncio
            from zmq.asyncio import Context,Poller
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = loop

        IpAndPort = re.search(IPREPATTERN,IP)
        assert IpAndPort != None, 'Ip not understood (tcp://xxx.xxx.xxx.xxx:XXXX or txp://*:XXXX)'
        self.StationIP = IpAndPort.group(1)
        self.StationSocket = IpAndPort.group(2)
        assert self.StationIP[:6] == 'tcp://', 'Ip not understood (tcp://xxx.xxx.xxx.xxx:XXXX or txp://*:XXXX)'
        assert len(self.StationSocket) == 4, 'Port not understood (tcp://xxx.xxx.xxx.xxx:XXXX or txp://*:XXXX)'
        formatting = '{:}: %(levelname)s: %(message)s'.format('QWeatherStation')
        if debug:
            logging.basicConfig(format=formatting,level=logging.DEBUG)
        if verbose:
            logging.basicConfig(format=formatting,level=logging.INFO)
        self.servers = {}
        self.clients = {}
        self.servermethods = {}
        self.serverjobs = {}
        self.pinged = []
        self.requesttimeoutdict = {}
        self.cnx = Context()
        self.socket = self.cnx.socket(zmq.ROUTER)
        self.socket.bind(self.StationIP + ':' + self.StationSocket)
        self.proxy = ThreadProxy(zmq.XSUB,zmq.XPUB)
        self.proxy.bind_in(self.StationIP + ':' + str(int(self.StationSocket) + PUBLISHSOCKET))
        self.proxy.bind_out(self.StationIP + ':' + str(int(self.StationSocket) + SUBSOCKET))
        self.proxy.start()
        self.poller = Poller()
        self.poller.register(self.socket,zmq.POLLIN)


        logging.info('Ready to run on IP: {:}'.format(self.get_own_ip()))


    def get_own_ip(self):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

    async def async_run(self):
        while True:
            try:
                items = await self.poller.poll(1000)
            except KeyboardInterrupt:
                self.close()
                break

            if items:
                msg = await self.socket.recv_multipart()
                self.handle_message(msg)

          

    def run(self):
        self.loop.run_until_complete(self.async_run())

    def close(self):
        self.poller.unregister(self.socket)
        self.socket.close()

    def handle_message(self,msg):
        sender = msg.pop(0)
        if sender in self.clients.keys():
            logging.debug('Recieved message from ID:{:}:\n{:}'.format(int.from_bytes(sender,byteorder='big'),msg,'\n\n'))    
        else:
            logging.debug('Recieved message from ID:{:}:\n{:}'.format(int.from_bytes(sender,byteorder='big'),msg,'\n\n'))
        empty = msg.pop(0)
        assert empty == b''
        SenderType = msg.pop(0)
        if SenderType == b'S': #server
            command = msg.pop(0) # 0xF? for server and 0x0? for client
            self.process_server(sender,command,msg)
        elif (SenderType == b'C'): #client
            command = msg.pop(0) # 0xF? for server and 0x0? for client
            self.process_client(sender,command,msg)

        elif SenderType == b'P': #Ping
            if sender in self.clients.keys():
                logging.debug('Recieved Ping from ID:{:}:\n{:}'.format(int.from_bytes(self.clients[sender],byteorder='big')))
            else:
                logging.debug('Recieved Ping from ID:{:}'.format(int.from_bytes(sender,byteorder='big')))

            self.socket.send_multipart([sender,b'',b'b']) #Sending an upside down P (b) to indicate a pong       



        elif SenderType ==b'b': #Pong
            print('got a pong')
            logging.debug('Recieved Pong from ID:{:}'.format(int.from_bytes(sender,byteorder='big')))
            print(sender,self.pinged,sender in self.pinged)
            if sender in self.pinged:
                print('before',self.pinged)
                self.pinged.remove(sender)
                print('after',self.pinged)

        elif SenderType == b'#': # execute broker functions
            command = msg.pop(0)
            if command == b'P': #request broker to ping all connections and remove old ones
                logging.debug('Ping of all connections requested')
                self.loop.create_task(self.ping_connections())
            elif command == b'R': #requests the broker to "restart" by removing all connections
                print('THis command is not implemented yet')
                pass #implement this in the future

            if sender in self.clients.keys():
                logging.debug('Recieved Ping from ID:{:}'.format(int.from_bytes(self.clients[sender],byteorder='big')))
            else:
                logging.debug('Recieved Ping from ID:{:}'.format(int.from_bytes(sender,byteorder='big')))
        else:
            logging.info('Invalid message')

    def process_client(self,sender,command,msg):
        if command == CREADY:
            version = msg.pop(0)
            if not version == PCLIENT:
                newmsg = [sender,b'',CREADY + CFAIL,'Mismatch in protocol between client and broker'.encode()]
            else:
                newmsg = [sender,b'',CREADY + CSUCCESS] + [pickle.dumps(self.servers)] + [pickle.dumps(self.servermethods)]

                name = msg.pop(0).decode()
                if name not in self.clients.keys():
                    self.clients[sender] = name
                logging.info('Client ready at ID:{:} name:{:}'.format(int.from_bytes(sender,byteorder='big'),self.clients[sender]))
            self.socket.send_multipart(newmsg)

        elif command == CREQUEST:
            messageid = msg.pop(0)
            servername = msg.pop(0).decode()
            try:
                serveraddr = next(key for key, value in self.servers.items() if value == servername)
                self.requesttimeoutdict[messageid+sender] = self.loop.call_later(B_SERVERRESPONSE_TIMEOUT, self.socket.send_multipart,[sender,b'',CREQUEST + CFAIL,messageid,servername.encode(),pickle.dumps((Exception('Timeout error')))])
                msg = [serveraddr,b'',CREQUEST,messageid,sender] + msg
                if len(self.serverjobs[serveraddr]) ==  0:
                    self.socket.send_multipart(msg)
                    logging.debug('Client request at {:}:\n{:}'.format(self.clients[sender],msg))
                else:
                    self.serverjobs[serveraddr].append(msg)
            except StopIteration as e:
                logging.debug('Trying to contact a server that does not exist')

        elif command == CDISCONNECT:
            logging.debug('Client with ID {:} disconnecting',self.clients[sender])
            self.clients.pop(sender)


    def process_server(self,sender,command,msg):
        if command == CREADY:
            version = msg.pop(0)
            if not version == PSERVER:
                newmsg = [sender,b'',CREADY + CFAIL,'Mismatch in protocol between server and broker'.encode()]
            else:
                servername = msg.pop(0).decode()
                servermethods = pickle.loads(msg.pop(0))
                self.servers[sender] = servername
                self.servermethods[sender] = servermethods
                self.serverjobs[sender] = []
                newmsg = [sender,b'',CREADY + CSUCCESS]
                logging.info('Server {:} ready at: {:}'.format(servername,int.from_bytes(sender,byteorder='big')))
            self.socket.send_multipart(newmsg)

        elif command == CREPLY:
            messageid = msg.pop(0)
            servername = self.servers[sender]
            client = msg.pop(0)
            answ = msg.pop(0)
            msg = [client,b'',CREQUEST + CSUCCESS,messageid,servername.encode(),answ]
            try:
                timeouttask = self.requesttimeoutdict.pop(messageid+client)
                timeouttask.cancel()
                self.socket.send_multipart(msg)
                logging.debug('Server answer to ID:{:}:\n{:}'.format(int.from_bytes(self.clients[sender],byteorder='big'),msg))
                if len(self.serverjobs[sender]) > 0:
                    self.socket.send_multipart(self.serverjobs[sender].pop(0))
            except KeyError:
                Print("Trying to send answer to client that does not exist")

        elif command == SDISCONNECT:
            server = msg.pop(0).decode()
            logging.debug('Server with ID {:} disconnecting',self.servers[sender])
            self.servers.pop(sender)
            self.serverjobs.pop(sender)
            self.servermethods.pop(sender)


    async def ping_connections(self):
        self.__ping()
        await asyncio.sleep(10)
        self.__check_ping()

    def __ping(self):
        self.pinged = []
        for addresse in self.servers.keys():
            self.socket.send_multipart([addresse,b'',CPING,b'P'])
            self.pinged.append(addresse)

        for aclient in self.clients.keys():
            self.socket.send_multipart([aclient,b'',CPING,b'P'])
            self.pinged.append(aclient)

    def __check_ping(self):
        for aping in self.pinged:
            if aping in self.clients.keys():
                del self.clients[aping]
            for aname,aserver in self.servers.items():
                if aping == aserver[0]:
                    break
            del self.servers[aname]
        print('servers:',self.servers)
#        print(self.pinged)
        self.pinged = []

    def get_servers(self):
        return self.servers

    def get_clients(self):
        return self.clients