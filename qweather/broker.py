from .constants import *
import zmq
from zmq.devices import ThreadProxy
from zmq.asyncio import Context,Poller
import asyncio
import pickle
import time
import re
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

        self.verbose = verbose
        self.debug = debug
        self.servers = {}
        self.clients = {}
        self.pinged = []
        self.requestlist = {}
        from zmq.asyncio import Context,Poller
        self.cnx = Context()
        self.socket = self.cnx.socket(zmq.ROUTER)
        self.socket.bind(self.StationIP + ':' + self.StationSocket)
        self.proxy = ThreadProxy(zmq.XSUB,zmq.XPUB)
        self.proxy.bind_in(self.StationIP + ':' + str(int(self.StationSocket) + PUBLISHSOCKET))
        self.proxy.bind_out(self.StationIP + ':' + str(int(self.StationSocket) + SUBSOCKET))
        self.proxy.start()
        self.poller = Poller()
        self.poller.register(self.socket,zmq.POLLIN)
        if self.verbose:
            print('QWeatherStation ready to run on IP: "',self.StationIP,'"')

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
        '''
        while True:
            try:
                items = self.poller.poll(1000)
            except KeyboardInterrupt:
                self.close()
                break
            if items:
                msg = self.socket.recv_multipart()
                self.handle_message(msg)
        '''

    def close(self):
        self.poller.unregister(self.socket)
        self.socket.close()

    def handle_message(self,msg):
        sender = msg.pop(0)
        if self.debug:
            if sender in self.clients.keys():
                print('DEBUG(QWeatherStation): Recieved message from "',self.clients[sender],'":\n',msg,'\n\n')    
            else:
                print('DEBUG(QWeatherStation): Recieved message from "',sender,'":\n',msg,'\n\n')
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
            if self.debug:
                if sender in self.clients.keys():
                    print('DEBUG(QWeatherStation): Recieved Ping from ',self.clients[sender],'\n\n')
                else:
                    print('DEBUG(QWeatherStation): Recieved Ping from ',sender,'\n\n')

            self.socket.send_multipart([sender,b'',b'b']) #Sending an upside down P (b) to indicate a pong       



        elif SenderType ==b'b': #Pong
            print('got a pong')
            if self.debug:
                print('DEBUG(QWeatherStation): Recieved Pong from ',sender,'\n\n')
            print(sender,self.pinged,sender in self.pinged)
            if sender in self.pinged:
                print('before',self.pinged)
                self.pinged.remove(sender)
                print('after',self.pinged)

        elif SenderType == b'#': # execute broker functions
            command = msg.pop(0)
            if command == b'P': #request broker to ping all connections and remove old ones
                if self.debug:
                    print('DEBUG(QWeatherStation): Ping of all connections requested')
                self.loop.create_task(self.ping_connections())
            elif command == b'R': #requests the broker to "restart" by removing all connections
                print('THis command is not implemented yet')
                pass #implement this in the future

            if self.debug:
                if sender in self.clients.keys():
                    print('DEBUG(QWeatherStation): Recieved Ping from ',self.clients[sender],'\n\n')
                else:
                    print('DEBUG(QWeatherStation): Recieved Ping from ',sender,'\n\n')
        else:
            if self.verbose:
                print('Invalid message')

    def process_client(self,sender,command,msg):
        if command == CREADY:
            version = msg.pop(0)
            if not version == PCLIENT:
                newmsg = [sender,b'',CREADY + CFAIL,'Mismatch in protocol between client and broker'.encode()]
            else:
                newmsg = [sender,b'',CREADY + CSUCCESS] + [pickle.dumps(self.servers)]

                name = msg.pop(0).decode()
                if name not in self.clients.keys():
                    self.clients[sender] = name
                if self.verbose:
                    print('Client ready at "',int.from_bytes(sender,byteorder='big'),self.clients[sender],'"')
            self.socket.send_multipart(newmsg)

        elif command == CREQUEST:
            messageid = msg.pop(0)
            server = msg.pop(0).decode()
            serveraddr = self.servers[server][0]
            self.requestlist[messageid+sender] = self.loop.call_later(CTIMEOUT, self.socket.send_multipart,[sender,b'',CREQUEST + CFAIL,messageid,server.encode(),pickle.dumps((Exception('Timeout error')))])
            msg = [serveraddr,b'',CREQUEST,messageid,sender] + msg
            if len(self.servers[server][2]) ==  0:
                self.socket.send_multipart(msg)
                if self.debug:
                    print('DEBUG(QWeatherStation): Client request at"',self.clients[sender],'":\n',msg,'\n\n')
            else:
                self.servers[server][2].append(msg)



    def process_server(self,sender,command,msg):
        if command == CREADY:
            version = msg.pop(0)
            if not version == PSERVER:
                newmsg = [sender,b'',CREADY + CFAIL,'Mismatch in protocol between server and broker'.encode()]
            else:
                servername = msg.pop(0).decode()
                servermethods = pickle.loads(msg.pop(0))
                self.servers[servername] = (sender,servermethods,[])
                newmsg = [sender,b'',CREADY + CSUCCESS]
                if self.verbose:
                    print('Server "',servername,'" ready at: "',int.from_bytes(sender,byteorder='big'),servername,'"')
            self.socket.send_multipart(newmsg)

        elif command == CREPLY:
            messageid = msg.pop(0)
            server = msg.pop(0).decode()
            client = msg.pop(0)
            answ = msg.pop(0)
            msg = [client,b'',CREQUEST + CSUCCESS,messageid,server.encode(),answ]
            try:
                timeouttask = self.requestlist.pop(messageid+client)
                timeouttask.cancel()
                self.socket.send_multipart(msg)
                if self.debug:
                    print('DEBUG(QWeatherStation): To "',client,'":\n',msg,'\n\n')
                if len(self.servers[server][2]) > 0:
                    self.socket.send_multipart(self.servers[server][2].pop(0))
                    if self.debug:
                        print('DEBUG(QWeatherStation): Server answer to"',self.clients[sender],'":\n',msg,'\n\n')
            except KeyError:
                pass


    async def ping_connections(self):
        self.__ping()
        await asyncio.sleep(10)
        self.__check_ping()

    def __ping(self):
        self.pinged = []
        for aserver in self.servers.values():
            addresse = aserver[0]
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
