from setuptools import setup, find_packages

setup(
    name='QWeather',
<<<<<<< HEAD
    version='1.0.6a',
=======
    version='2.0.0',
>>>>>>> develop
    packages=['qweather',],
    author='A. Arvad Joergensen',
    author_email='Asbjorn.Arvad@nbi.ku.dk',
    license='MIT',
    url='https://github.com/Arvad/QWeather',
    description='A zeromq based distributed messaging platform',
    long_description=open('README.rst').read(),
    install_requires=[
        "pyzmq >= 17.0.0.b3",
        "pyqt5 >= 5.13.1"
    ],
    rpython_requires='~=3.5'
)