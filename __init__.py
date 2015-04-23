#  -*- coding: utf-8 -*-
'''BTCommunicator
==============

:class:`BTCommunicator(**kwargs)` handles all the Bluetooth communication with an Arduino device running the SerialCommand
library.

.. note::

    This only works on Android platforms.

Usage
-----

To initialize communication to a BT device called 'HC-06'::

    communicator = BTCommunicator()

'''
__version__ = '0.0.1'

import threading
import sys
import time
import jnius
from os.path import dirname, join
import json
from kivy.app import App
from kivy.properties import NumericProperty, ListProperty, ObjectProperty, StringProperty, BooleanProperty, DictProperty
from kivy.clock import mainthread, Clock
from kivy.uix.widget import Widget
from kivy import platform


class BTCommunicatorException(Exception):
    '''Exception for the :class:`BTCommunicator`.
    '''
    pass

class BTCommunicator(Widget):
    '''
    :Events:
        `on_connected`: ()
            Dispatched when successfully connected to the BT device.

        `on_disconnected`: ()
            Fired when the connection is lost. By default we will try to send a command 3 times before deciding that
            the connection is lost. To temporarily override this, use :class:`BTCommunicator.send(tries=<value>)`.

        `on_command_sent`: ()
            Fired when a command i successfully sent and added to the :attr:`command_buffer`

        `on_response`: ()
            Fired when a response i successfully received and added to the :attr:`response_buffer`. By default, the
            responses from the Arduino deviec is enclosed in '<' and '>'. Change this with
            :class:`BTCommunicator.set_enclosing(start=character, end=character)`

        `on_unknown`: ()
            Fired if the response == :attr:`unknown`. The command will be removed from :attr:`command_buffer`.
    '''

    language = StringProperty('se_SV')
    '''
    The language in which the exception messages will be shown. :clas:`BTCommunicator` will
    look for this file in the sub-directory /lang.

    .. versionadded:: 0.0.1
    '''

    is_connected = BooleanProperty(False)
    '''
    Is true if we are connected.

    .. versionadded:: 0.0.1
    '''

    start_enclosing = StringProperty('<')
    '''
    First character in a proper response from the Arduino device. Defaults to '<'.

    .. versionadded:: 0.0.1
    '''

    end_enclosing = StringProperty('>')
    '''
    Ending character in a proper response from the Arduino device. Defaults to '>'.

    .. versionadded:: 0.0.1
    '''

    unknown = StringProperty('UNSUPPORTED COMMAND')
    '''
    Response from the Arduino device when an unsupported command is sent. Defaults to 'UNSUPPORTED COMMAND'.

    .. versionadded:: 0.0.1
    '''

    send_reset = BooleanProperty(True)
    reset_command = StringProperty('RESET')
    '''
    By default we will send a reset command to the Arduino device when successfully connected. How you handle
    this on the board is up to you. Change this to `False` if you don't want this feature.

    :attr:`reset_command`:: Reset command recognized by the Arduino device

    .. versionadded:: 0.0.1
    '''

    command_buffer = ListProperty([])
    c_buf_length = NumericProperty(10)
    '''
    Buffer with commands successfully sent. The length of the buffer is determined by :attr:`c_buf_length`
    and is by default 10 commands.

    .. versionadded:: 0.0.1
    '''

    response_buffer = ListProperty([])
    r_buf_length = NumericProperty(10)
    '''
    Buffer with responses successfully sent. The length of the buffer is determined by :attr:`r_buf_length`
    and is by default 10 responses.

    .. versionadded:: 0.0.1
    '''

    device_name = StringProperty('HC-06')
    '''
    Name of paired BT device, default 'HC-06'.

    .. versionadded:: 0.0.1
    '''

    #===========================================================================
    # privates
    #===========================================================================
    _ping_interval = NumericProperty(10)
    _is_pingning = BooleanProperty(False)
    _num_of_resends = NumericProperty(3)
    _resend_delay = NumericProperty(0.3)
    _recv_stream = ObjectProperty()
    _send_stream = ObjectProperty()
    _stop = threading.Event()
    _lang = DictProperty({})
    BTCommunicatorException = BTCommunicatorException

    def __init__(self, **kwargs):
        super(BTCommunicator, self).__init__(**kwargs)
        self.register_event_type('on_connected')
        self.register_event_type('on_disconnected')
        self.register_event_type('on_command_sent')
        self.register_event_type('on_response')
        self.register_event_type('on_error')
        self.register_event_type('on_unknown')
        App.get_running_app().bind(on_stop=self.stop_reader_stream)
        curdir = dirname(__file__)
        try:
            with open(join(curdir, 'lang', '{}.json'.format(self.language))) as lang_file:
                self._lang = json.load(lang_file)
        except Exception as e:
            raise BTCommunicatorException("Couldn't load {}/lang/{}.json\nError: {}".format(curdir, self.language, e.message))

        if platform == 'android':
            self.BluetoothAdapter = jnius.autoclass('android.bluetooth.BluetoothAdapter')
            self.BluetoothDevice = jnius.autoclass('android.bluetooth.BluetoothDevice')
            self.BluetoothSocket = jnius.autoclass('android.bluetooth.BluetoothSocket')
            self.InputStreamReader = jnius.autoclass('java.io.InputStreamReader')
            self.BufferedReader = jnius.autoclass('java.io.BufferedReader')
            self.IOException = jnius.autoclass('java.io.IOException')
            self.UUID = jnius.autoclass('java.util.UUID')
        return

    def connect(self, *args):
        self._get_socket_stream(self.device_name)
    '''
    Connects to the device with the name :attr:`device_name`
    Set :attr:`is_connected` to True if successfully connected. By default we also send a reset
    command defined in :attr:`reset_command`.

    .. versionadded:: 0.0.1
    '''

    def disconnect(self, *args):
        try:
            self.stop_reader_stream()
            self._recv_stream.close()
            self._send_stream.close()
            self.is_connected = False
        except:
            raise BTCommunicatorException(self._lang['messages']['disconnect_error'])
    '''
    Calls :class:`BTCommunicator.stop_reader_stream()` to stop listening to incoming responses and,
    if we are pining, stop that too. Then close input and output IO streams and set
    :attr:`is_connected` to False.

    .. versionadded:: 0.0.1
    '''

    def start_reader_stream(self, *args):
        self._stop.clear()
        threading.Thread(target=self._stream_reader).start()
    '''
    Start listening for incoming responses. The :attr:`_stream_reader` runs in a separate thread and listens for
    responses enclosed in :attr:`start_enclosing` and :attr:`end_enclosing`. If a proper response is recognized it
    will be added to the :attr:`response_buffer` by a function running in the main thread.

    .. versionadded:: 0.0.1
    '''

    def stop_reader_stream(self, *args):
        if self._is_pingning:
            self.stop_ping()
            self._is_pingning = False
        self._stop.set()
    '''
    Stop listening for incoming responses. If we are pinging the Arduino device this will be unscheduled.

    .. note::

        When an instance of :class:`BTCommunicator` is created, it will be listening for the :meth:`on_stop`
        event of :class:`App.get_running_app()` with :attr:`stop_reader_stream` as callback.

    .. versionadded:: 0.0.1
    '''

    def start_ping(self, interval=0):
        Clock.schedule_interval(self._ping, interval if interval > 0 else self._ping_interval)
        self._is_pingning = True
    '''
    Start pinging the Arduino device. If no interval is passed in the arguments the default is 10 seconds.

    .. versionadded:: 0.0.1
    '''

    def stop_ping(self, *args):
        Clock.unschedule(self._ping)
        self._is_pingning = False
    '''
    Stop pinging the Arduino device

    .. versionadded:: 0.0.1
    '''

    def send(self, command='', args=[], tries=0):
        send_string = command
        error = False
        if len(args) > 0:
            send_string += ' '
            send_string += ' '.join(args)

        resends = tries if tries> 0 else self._num_of_resends
        error_message = ''
        for i in range(1, resends):
            try:
                self.send_stream.write("{}\n".format(send_string))
                self.send_stream.flush()
                error = False
                break
            except jnius.JavaException as e:
                error_message = "{} {}".format(self._lang['messages']['send_error_JavaException'], e.message)
                error = True
            except:
                error_message = "{} {}".format(self._lang['messages']['send_error_Unknown'], sys.exc_info()[0])
                error = True
            time.sleep(self._resend_delay)

        if not error:
            self._add_command(command)
            self.dispatch('on_command_sent')
        else:
            self.is_connected = False
            raise BTCommunicatorException(error_message)
        return
    '''
    Send command to the Arduino device. A list of arguments can be set with :attr:`args=[]`. By default
    we try to send the command 3 times. You can temporarily override this by setting :attr:`tries` to the
    number of tries you would like to do with this command.

    .. versionadded:: 0.0.1
    '''

    def _ping(self, *args):
        self.send(command='PING')
        return

    def _stream_reader(self, *args):
        stream = ''
        while True:
            if self._stop.is_set():
                jnius.detach()
                return
            if self.is_connected:
                try:
                    stream = self._recv_stream.readLine()
                except self.IOException as e:
                    raise BTCommunicatorException("{} {}".format(self._lang['messages']['receive_error_IOException'], e.message))
                except jnius.JavaException as e:
                    raise BTCommunicatorException("{} {}".format(self._lang['messages']['receive_error_JavaException'], e.message))
                except:
                    raise BTCommunicatorException("{} {}".format(self._lang['messages']['receive_error_Unknown'], sys.exc_info()[0]))
                try:
                    start = stream.rindex("<") + 1
                    end = stream.rindex(">", start)
                    self._add_response(stream[start:end])
                except ValueError:
                    pass

    @mainthread
    def _add_response(self, response):
        if response == self.unknown:
            self.command_buffer.pop(0)
            self.dispatch('on_unknown')
        else:
            if len(self.response_buffer) == self.r_buf_length:
                self.response_buffer.pop()
            self.response_buffer.insert(0, str(response))

    def _add_command(self, command):
        if len(self.command_buffer) == self.c_buf_length:
            self.command_buffer.pop()
        self.command_buffer.insert(0, str(command))
        return

    def _get_socket_stream(self, name):
        if hasattr(self, 'BluetoothAdapter'):
            paired_devices = self.BluetoothAdapter.getDefaultAdapter().getBondedDevices().toArray()
            self.socket = None
            for device in paired_devices:
                if device.getName() == name:
                    self.socket = device.createRfcommSocketToServiceRecord(self.UUID.fromString("00001101-0000-1000-8000-00805F9B34FB"))
                    reader = self.InputStreamReader(self.socket.getInputStream(), 'US-ASCII')
                    recv_stream = self.BufferedReader(reader)
                    send_stream = self.socket.getOutputStream()
                    self.socket.connect()
                    self._recv_stream = recv_stream
                    self._send_stream = send_stream
                    break
            if self.socket:
                return
            else:
                raise BTCommunicatorException("{} {}".format(self._lang['messages']['device_name_error'], name))
        else:
            raise BTCommunicatorException(self._lang['messages']['not_android'])

    def on_error_message(self, *args):
        pass

    def on_is_connected(self, *args):
        if not self.is_connected:
            self.dispatch('on_disconnected')

    def on_connected(self):
        pass

    def on_disconnected(self):
        pass

    def on_command_sent(self):
        pass

    def on_response(self):
        pass

    def on_unknown(self):
        pass

    def on_error(self):
        pass

