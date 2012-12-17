# -*- coding: utf-8 -*-
# vim: set et sw=4 sts=4:

# Copyright 2012 Dave Hughes.
#
# This file is part of oxitopdump.
#
# oxitopdump is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# oxitopdump is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# oxitopdump.  If not, see <http://www.gnu.org/licenses/>.

"""
Defines the structures and interfaces for gathering data from an OC110

This module defines a couple of data structures which represent gas bottle
(`Bottle`) and the measuring head(s) of a gas bottle (`BottleHead`). These can
be constructed manually but more usually will be obtained from an instance of
`DataLogger` which provides an interface to the OC110 serial port. For testing
purposes a "fake OC110" can be found in the `DummySerial` class which takes the
same initialization parameters as the Python `Serial` class and hence can be
used in place of it.
"""

from __future__ import (
    unicode_literals,
    absolute_import,
    division,
    print_function,
    )

import re
import time
import logging
from datetime import datetime, timedelta

import serial


ENCODING = 'ascii'
TIMESTAMP_FORMAT = '%y%m%d%H%M%S'


class Error(Exception):
    """
    Base class for errors related to the data-logger
    """


class SendError(Error):
    """
    Exception raised due to a transmission error
    """


class HandshakeFailed(SendError):
    """
    Exception raised when the RTS/CTS handshake fails
    """


class PartialSend(SendError):
    """
    Exception raised when not all bytes of the message were sent
    """


class ReceiveError(Error):
    """
    Exception raise due to a reception error
    """


class UnexpectedReply(ReceiveError):
    """
    Exception raised when the data logger sends back an unexpected reply
    """


class ChecksumMismatch(ReceiveError):
    """
    Exception raised when a check-sum doesn't match the data sent
    """


class Bottle(object):
    """
    Represents a bottle as collected from an OxiTop OC110 Data Logger.

    `serial` : the bottle serial number
    `id` : additional user-assigned ID number (1-999), non-unique
    `start` : the timestamp at the start of the run
    `finish` : the timestamp at the end of the run
    `interval` : the interval between readings (expressed as a timedelta)
    `measurements` : the expected number of measurements
    `mode` : one of 'pressure' or 'bod'
    `bottle_volume` : the nominal volume (in ml) of the bottle
    `sample_volume` : the volume of the sample (in ml) within the bottle
    `logger` : a DataLogger instance that can be used to update the bottle
    """

    def __init__(
            self, serial, id, start, finish, interval, measurements, mode,
            bottle_volume, sample_volume, dilution, logger=None):
        self.logger = logger
        try:
            date, num = serial.split('-', 1)
            datetime.strptime(date, '%y%m%d')
            assert 1 <= int(num) <= 99
        except (ValueError, AssertionError) as exc:
            raise ValueError('invalid serial number %s' % serial)
        if not (1 <= id <= 999):
            raise ValueError('id must be an integer between 1 and 999')
        if not mode in ('pressure', 'bod'):
            raise ValueError('mode must be one of "pressure" or "bod"')
        self.serial = serial
        self.id = id
        self.start = start
        self.finish = finish
        self.interval = interval
        self.measurements = measurements
        self.mode = mode
        self.bottle_volume = float(bottle_volume)
        self.sample_volume = float(sample_volume)
        self.dilution = dilution
        self.heads = []

    @property
    def mode_string(self):
        return (
            'Pressure %dd' % (self.finish - self.start).days
                if self.mode == 'pressure' else
            'BOD'
                if self.mode == 'bod' else
            'Unknown'
            )

    @classmethod
    def from_string(cls, data, logger=None):
        data = data.decode(ENCODING).split('\r')
        # Discard the empty line at the end
        assert not data[-1]
        data = data[:-1]
        # Parse the first line for bottle information
        (   _,             # ???
            _,             # ???
            _,             # ???
            id,            # I.D. No.
            serial,        # bottle serial number
            start,         # start timestamp (YYMMDDhhmmss)
            finish,        # finish timestamp (YYMMDDhhmmss)
            _,             # ???
            _,             # ???
            _,             # ???
            _,             # ???
            measurements,  # number of measurements
            pressure,      # pressure type?
            bottle_volume, # bottle volume (ml)
            sample_volume, # sample volume (ml)
            _,             # ???
            _,             # ???
            _,             # ???
            _,             # number of heads, perhaps???
            interval,      # interval of readings (perhaps, see 308<=>112 note below)
        ) = data[0].split(',')
        bottle = cls(
            '%s-%s' % (serial[:-2], serial[-2:]),
            int(id),
            datetime.strptime(start, TIMESTAMP_FORMAT),
            datetime.strptime(finish, TIMESTAMP_FORMAT),
            # XXX For some reason, intervals of 112 minutes are reported as 308?!
            timedelta(seconds=60 * int(112 if int(interval) == 308 else interval)),
            int(measurements),
            'pressure',
            float(bottle_volume),
            float(sample_volume),
            0,
            # Parse all subsequent lines as BottleHead objects
            logger
            )
        bottle.heads = [
            BottleHead.from_string(bottle, line)
            for line in data[1:]
            ]
        return bottle

    def __str__(self):
        return (','.join((
            '0',
            '0',
            '3',
            str(self.id),
            ''.join(self.serial.split('-', 1)),
            self.start.strftime(TIMESTAMP_FORMAT),
            self.finish.strftime(TIMESTAMP_FORMAT),
            '2',
            '5',
            '240',
            '40',
            str(self.measurements),
            str((self.finish - self.start).days * 24 * 60),
            '%.0f' % self.bottle_volume,
            '%.1f' % self.sample_volume,
            '0',
            str({
                28: 10,
                14: 10,
                3:  6,
                2:  4,
                1:  2,
                }[(self.finish - self.start).days]),
            '2',
            '1',
            # XXX See above note about 308<=>112
            str(308 if (self.interval.seconds // 60) == 112 else (self.interval.seconds // 60)),
            )) + '\r' +
            ''.join(str(head) for head in self.heads)).encode(ENCODING)

    def __unicode__(self):
        return str(self).decode(ENCODING)

    def refresh(self):
        if self.logger:
            data = self.logger._GPRB(self.serial)
            new = Bottle.from_string(data)
            self.serial = new.serial
            self.id = new.id
            self.start = new.start
            self.finish = new.finish
            self.interval = new.interval
            self.measurements = new.measurements
            self.mode = new.mode
            self.bottle_volume = new.bottle_volume
            self.sample_volume = new.sample_volume
            self.dilution = new.dilution
            self.heads = new.heads
            # Fix up the bottle references in the new heads list (they'll all
            # point to new when they should point to self as new is about to
            # get thrown away when we return)
            for head in self.heads:
                head.bottle = self
        else:
            raise RuntimeError(
                'Cannot refresh a bottle with no associated data logger')


class BottleHead(object):
    """
    Represents a single head on a gas bottle.

    `bottle` : the bottle this head belongs to
    `serial` : the serial number of the head
    `readings` : optional sequence of integers for the head's readings
    """

    def __init__(self, bottle, serial, readings=None):
        self.bottle = bottle
        self.serial = serial
        if readings is None or isinstance(readings, BottleReadings):
            self._readings = readings
        else:
            self._readings = BottleReadings(self, readings)

    @classmethod
    def from_string(cls, bottle, data):
        data = data.decode(ENCODING)
        # Strip trailing CR
        data = data.rstrip('\r')
        (   _,      # blank value (due to extraneous leading comma)
            serial, # serial number of head
            _,      # ???
            _,      # blank value (due to extraneous trailing comma)
        ) = data.split(',')
        return cls(bottle, serial)

    def __str__(self):
        return (','.join((
            '',
            self.serial,
            '150',
            '',
            )) + '\r').encode(ENCODING)

    def __unicode__(self):
        return str(self).decode(ENCODING)

    @property
    def readings(self):
        if self._readings is None and self.bottle.logger is not None:
            data = self.bottle.logger._GMSK(self.bottle.serial, self.serial)
            # XXX Check the first line includes the correct bottle and head
            # identifiers as specified, and that the reading count matches
            self._readings = BottleReadings.from_string(self, data)
        return self._readings

    def refresh(self):
        if self.bottle is not None and self.bottle.logger is not None:
            self._readings = None
        else:
            raise RuntimeError(
                'Cannot refresh a bottle head with no associated data logger')


class BottleReadings(object):
    """
    Represents the readings of a bottle head as a sequence-like object.

    `head` : the bottle head that the readings apply to
    `readings` : the readings for the head
    """

    def __init__(self, head, readings):
        self.head = head
        self._items = list(readings)

    @classmethod
    def from_string(cls, head, data):
        data = data.decode(ENCODING).split('\r')
        # Discard the empty line at the end
        assert not data[-1]
        data = data[:-1]
        (   head_serial,      # blank value (due to extraneous leading comma)
            bottle_serial, # serial number of head
            _,      # ??? (always 1)
            _,      # ??? (always 1)
            _,      # ??? (0-247?)
            bottle_start,
            readings_len,
        ) = data[0].split(',')
        head_serial = str(int(head_serial))
        readings_len = int(readings_len)
        readings = cls(head, (
            int(value)
            for line in data[1:]
            for value in line.split(',')
            if value
            ))
        assert head_serial == head.serial
        assert '%s-%s' % (bottle_serial[:-2], bottle_serial[-2:]) == head.bottle.serial
        assert len(readings) == readings_len
        return readings

    def __str__(self):
        return (','.join((
            '%09d' % int(self.head.serial),
            ''.join(self.head.bottle.serial.split('-', 1)),
            '1',
            '1',
            '247', # can be zero, but we've no idea what this means...
            self.head.bottle.start.strftime(TIMESTAMP_FORMAT),
            str(len(self)),
            )) + '\r' +
            ''.join(
                ''.join(',%d' % reading for reading in chunk) + '\r'
                for chunk in [self[i:i + 10] for i in range(0, len(self), 10)]
                )
            ).encode(ENCODING)

    def __unicode__(self):
        return str(self).decode(ENCODING)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]


class DataLogger(object):
    """
    Interfaces with the serial port of an OxiTop OC110 Data Logger and
    communicates with it when certain properties are queried for bottle or
    head information.

    `port` : a serial.Serial object (or compatible)
    """

    def __init__(self, port):
        if not port.timeout:
            raise ValueError(
                'The port is not set for blocking I/O with a non-zero timeout')
        self.port = port
        self._bottles = None
        self._seen_prompt = False
        # Ensure the port is connected to an OC110 by requesting the
        # manufacturer's ID
        self.id = self._MAID().rstrip('\r')
        if self.id != 'OC110':
            raise UnexpectedReply(
                'The connected unit is not an OxiTop OC110')

    def _tx(self, command, *args):
        """
        Sends a command (and optionally arguments) to the OC110. The command
        should not include the line terminator and the arguments should not
        include comma separators; this method takes care of command formatting.

        `command` : The command to send
        """
        # If we're not Clear To Send, set Ready To Send and wait for CTS to be
        # raised in response
        check_response = False
        response = ''
        if not self.port.getCTS():
            cts_wait = time.time() + self.port.timeout
            self.port.setRTS()
            while not self.port.getCTS():
                time.sleep(0.1)
                if time.time() > cts_wait:
                    raise HandshakeFailed(
                        'Failed to detect readiness with RTS/CTS handshake')
            # Read anything the unit sends through; if it's just booted up
            # there's probably some BIOS crap to ignore
            check_response = True
            response += self._rx(checksum=False)
        # If we've still not yet seen the ">" prompt, hit enter and see if we
        # get one back
        if not self._seen_prompt:
            self.port.write('\r')
            check_response = True
            response += self._rx(checksum=False)
        # Because of the aforementioned BIOS crap, ignore everything but the
        # last line when checking for a response
        if check_response and not (response.endswith('LOGON\r') or
                response.endswith('INVALID COMMAND\r')):
            raise UnexpectedReply(
                'Expected LOGON or INVALID COMMAND, but got %s' % repr(response))
        logging.debug('TX: %s' % command)
        data = ','.join([command] + [str(arg) for arg in args]) + '\r'
        written = self.port.write(data)
        if written != len(data):
            raise PartialSend(
                'Only wrote first %d bytes of %d' % (written, len(data)))

    def _rx(self, checksum=True):
        """
        Receives a response from the OC110. If checksum is True, also checks
        that the transmitted checksum matches the transmitted data.

        `checksum` : If true, treat the last line of the repsonse as a checksum
        """
        response = ''
        while '>\r' not in response:
            data = self.port.read().decode(ENCODING)
            if not data:
                raise ReceiveError('Failed to read any data before timeout')
            elif data == '\n':
                # Chuck away any LFs; these only appear in the BIOS output on
                # unit startup and mess up line splits later on
                continue
            elif data == '\r':
                logging.debug('RX: %s' % response.split('\r')[-1])
            response += data
        self._seen_prompt = True
        # Split the response on the CRs and strip off the prompt at the end
        response = response.split('\r')[:-2]
        # If we're expecting a check-sum, check the last line for one and
        # ensure it matches the transmitted data
        if checksum:
            response, checksum_received = response[:-1], response[-1]
            if not checksum_received.startswith(','):
                raise UnexpectedReply('Checksum is missing leading comma')
            checksum_received = int(checksum_received.lstrip(','))
            checksum_calculated = sum(
                ord(c) for c in
                ''.join(line + '\r' for line in response)
                )
            if checksum_received != checksum_calculated:
                raise ChecksumMismatch('Checksum does not match data')
        # Return the reconstructed response (without prompt or checksum)
        return ''.join(line + '\r' for line in response)

    def _MAID(self):
        """
        Sends a MAID (MAnufacturer ID) command to the OC110 and returns the
        response.
        """
        self._tx('MAID')
        return self._rx(checksum=False)

    def _CLOC(self):
        """
        Sends a CLOC (CLOse Connection) command to the OC110 and sets RTS to
        low (indicating we're going to stop talking to it).
        """
        self._tx('CLOC')
        self._rx()
        self.port.setRTS(level=False)
        self._seen_prompt = False

    def _GAPB(self):
        """
        Sends a GAPB (Get All Pressure Bottles) command to the OC110 and
        returns the data received.
        """
        self._tx('GAPB')
        return self._rx()

    def _GPRB(self, bottle):
        """
        Sends a GPRB (Get PRessure Bottle) command to the OC110 and returns
        the data received.
        """
        self._tx('GPRB', bottle)
        return self._rx()

    def _GSNS(self, bottle):
        """
        Sends a GSNS (???) command to the OC110. No idea what this command
        does but the original software always used it between GPRB and GMSK.
        """
        self._tx('GSNS', bottle)
        self._rx(checksum=False)

    def _GMSK(self, bottle, head):
        """
        Sends a GMSK (Get ... erm ... bottle head readings - no idea how they
        get MSK out of that) command to the OC110. Returns the data received.
        """
        self._tx('GMSK', bottle, head)
        return self._rx()

    @property
    def bottles(self):
        if self._bottles is None:
            data = self._GAPB()
            self._bottles = []
            bottle = ''
            # Split the response into individual bottles and their head line(s)
            for line in data.split('\r')[:-1]:
                if not line.startswith(','):
                    if bottle:
                        self._bottles.append(
                            Bottle.from_string(bottle, logger=self))
                    bottle = line + '\r'
                else:
                    bottle += line + '\r'
            if bottle:
                self._bottles.append(
                    Bottle.from_string(bottle, logger=self))
        return self._bottles

    def refresh(self):
        self._bottles = None


class DummySerial(object):
    """
    Emulates the serial port of an OxiTop OC110 Data Logger for testing.

    `baudrate` : the baud-rate to emulate, defaults to 9600
    """

    def __init__(self, port=None, baudrate=9600, bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
            timeout=None, xonxoff=False, rtscts=False, writeTimeout=None,
            dsrdtr=False, interCharTimeout=None):
        self.port = port
        self.name = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.xonxoff = xonxoff
        self.rtscts = rtscts
        self.dsrdtr = dsrdtr
        self.writeTimeout = writeTimeout
        self.interCharTimeout = interCharTimeout
        self._cts_high = None
        self._read_buf = []
        self._write_buf = ''
        self._opened = bool(self.port)
        #assert self.baudrate == 9600
        assert self.bytesize == serial.EIGHTBITS
        assert self.parity == serial.PARITY_NONE
        assert self.stopbits == serial.STOPBITS_ONE
        # On start-up, device sends some BIOS stuff, but as the port isn't
        # usually open at this point it typically gets lost/ignored. Is there
        # some decent way to emulate this?
        self._send('\0\r\n')
        self._send('BIOS OC Version 1.0\r\n')
        # Set up the list of gas bottles and pressure readings
        self._bottles = []
        self._bottles.append(Bottle(
            serial='110222-06',
            id=999,
            start=datetime(2011, 2, 22, 16, 54, 55),
            finish=datetime(2011, 3, 8, 16, 54, 55),
            interval=timedelta(seconds=56 * 60),
            measurements=360,
            mode='pressure',
            bottle_volume=510,
            sample_volume=432,
            dilution=0
            ))
        self._bottles[-1].heads.append(BottleHead(
            self._bottles[-1],
            '60108',
            [
                970, 965, 965, 965, 965, 965, 964, 965, 965, 965, 965, 964,
                965, 965, 965, 965, 965, 965, 964, 965, 965, 965, 965, 965,
                964, 965, 965, 964, 964, 964, 965, 965, 965, 965, 965, 965,
                965, 965, 964, 964, 965, 965, 965, 964, 965, 965, 965, 965,
                965, 965, 965, 965, 965, 965, 965, 964, 964, 964, 965, 965,
                965, 965, 965, 964, 964, 964, 964, 965, 965, 965, 965, 965,
                964, 964, 964, 964, 964, 964, 965, 965, 965, 965, 965, 964,
                964, 964, 964, 964, 965, 965, 964, 964, 964, 965, 965, 964,
                965, 965, 964, 964, 965, 964, 964, 964, 965, 965, 964, 964,
                964, 965, 965, 964, 964, 964, 965, 964, 964, 964, 964, 965,
                964, 965, 965, 964, 964, 965, 965, 964, 964, 964, 964, 964,
                965, 964, 965, 965, 964, 965, 965, 964, 965, 964, 965, 965,
                965, 964, 965, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 965, 965,
                964, 964, 964, 964, 964, 965, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 965, 965, 964, 965, 964, 964, 965, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 963, 963, 964,
                963, 963, 964, 964, 964, 964, 964, 964, 963, 964, 964, 964,
                964, 964, 964, 964, 964, 963, 963, 963, 963, 964, 964, 964,
                964, 963, 963, 964, 964, 964, 963, 963, 963, 964, 963, 964,
                964, 964, 964, 964, 964, 963, 963, 963, 963, 963, 963, 963,
                963, 963, 963, 963, 963, 963, 963, 963, 964, 964, 963, 963,
                963, 963, 963, 963, 963, 964, 963, 963, 963, 963, 963, 962,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962,
                961, 962, 962, 962, 963, 962, 962, 962, 962, 962, 962, 962,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 961,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 961,
                962,
                ]))
        self._bottles.append(Bottle(
            serial='121119-03',
            id=3,
            start=datetime(2012, 11, 19, 13, 53, 4),
            finish=datetime(2012, 11, 22, 13, 53, 4),
            interval=timedelta(seconds=12 * 60),
            measurements=360,
            mode='pressure',
            bottle_volume=510,
            sample_volume=432,
            dilution=0
            ))
        self._bottles[-1].heads.append(BottleHead(
            self._bottles[-1],
            '60108',
            []))
        self._bottles.append(Bottle(
            serial='120323-01',
            id=1,
            start=datetime(2012, 3, 23, 17, 32, 23),
            finish=datetime(2012, 4, 20, 17, 32, 23),
            interval=timedelta(seconds=112 * 60),
            measurements=360,
            mode='pressure',
            bottle_volume=510,
            sample_volume=432,
            dilution=0
            ))
        self._bottles[-1].heads.append(BottleHead(
            self._bottles[-1],
            '60145',
            [
                976, 964, 963, 963, 963, 963, 963, 963, 963, 963, 963, 963,
                963, 963, 964, 963, 963, 963, 963, 963, 963, 963, 963, 963,
                962, 963, 963, 962, 962, 963, 963, 963, 963, 962, 963, 963,
                963, 962, 962, 963, 962, 963, 963, 962, 962, 963, 963, 963,
                963, 962, 962, 963, 963, 963, 962, 963, 963, 963, 963, 962,
                962, 962, 963, 962, 963, 962, 962, 963, 963, 962, 962, 962,
                962, 963, 962, 962, 962, 962, 963, 963, 962, 963, 963, 963,
                962, 962, 962, 962, 963, 962, 962, 962, 962, 962, 962, 962,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 963, 962,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962,
                962, 961, 962, 961, 962, 962, 962, 962, 962, 962, 962, 961,
                962, 961, 961, 962, 961, 962, 962, 962, 962, 961, 962, 962,
                961, 962, 962, 961, 962, 961, 962, 961, 962, 961, 962, 961,
                962, 961, 962, 961, 962, 961, 962, 961, 961, 961, 962, 961,
                962, 962, 961, 962, 962, 961, 961, 961, 962, 961, 961, 962,
                962, 961, 962, 961, 961, 961, 961, 961, 962, 961, 961, 961,
                961, 962, 961, 962, 961, 961, 962, 961, 961, 962, 961, 961,
                961, 961, 961, 961, 961, 961, 961, 961, 961, 961, 961, 961,
                962, 961, 960, 961, 961, 961, 961, 960, 961, 961, 960, 961,
                961, 961, 961, 961, 961, 961, 961, 961, 961, 961, 961, 961,
                961, 961, 960, 961, 960, 961, 961, 960, 961, 960, 961, 960,
                960, 960, 961, 961, 960, 961, 960, 961, 961, 960, 961, 960,
                961, 960, 961, 960, 960, 960, 961, 960, 960, 961, 961, 961,
                960, 961, 960, 960, 961, 960, 960, 961, 960, 960, 960, 960,
                961, 960, 960, 960, 960, 960, 960, 961, 960, 960, 960, 960,
                960, 960, 960, 959, 960, 959, 960, 960, 959, 960, 960, 960,
                960, 960, 960, 960, 960, 960, 960, 960, 960, 960, 960, 960,
                960, 959, 960, 959, 960, 960, 959, 960, 960, 959, 960, 960,
                959, 960, 959, 959, 960, 959, 959, 959, 960, 960, 960, 959,
                959, 960, 959, 960, 960, 959, 960, 959, 959, 960, 959, 959,
                960,
                ]))
        self._bottles[-1].heads.append(BottleHead(
            self._bottles[-1],
            '60143',
            [
                970, 965, 965, 965, 965, 965, 964, 965, 965, 965, 965, 964,
                965, 965, 965, 965, 965, 965, 964, 965, 965, 965, 965, 965,
                964, 965, 965, 964, 964, 964, 965, 965, 965, 965, 965, 965,
                965, 965, 964, 964, 965, 965, 965, 964, 965, 965, 965, 965,
                965, 965, 965, 965, 965, 965, 965, 964, 964, 964, 965, 965,
                965, 965, 965, 964, 964, 964, 964, 965, 965, 965, 965, 965,
                964, 964, 964, 964, 964, 964, 965, 965, 965, 965, 965, 964,
                964, 964, 964, 964, 965, 965, 964, 964, 964, 965, 965, 964,
                965, 965, 964, 964, 965, 964, 964, 964, 965, 965, 964, 964,
                964, 965, 965, 964, 964, 964, 965, 964, 964, 964, 964, 965,
                964, 965, 965, 964, 964, 965, 965, 964, 964, 964, 964, 964,
                965, 964, 965, 965, 964, 965, 965, 964, 965, 964, 965, 965,
                965, 964, 965, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 965, 965,
                964, 964, 964, 964, 964, 965, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 965, 965, 964, 965, 964, 964, 965, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964, 964,
                964, 964, 964, 964, 964, 964, 964, 964, 964, 963, 963, 964,
                963, 963, 964, 964, 964, 964, 964, 964, 963, 964, 964, 964,
                964, 964, 964, 964, 964, 963, 963, 963, 963, 964, 964, 964,
                964, 963, 963, 964, 964, 964, 963, 963, 963, 964, 963, 964,
                964, 964, 964, 964, 964, 963, 963, 963, 963, 963, 963, 963,
                963, 963, 963, 963, 963, 963, 963, 963, 964, 964, 963, 963,
                963, 963, 963, 963, 963, 964, 963, 963, 963, 963, 963, 962,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962,
                961, 962, 962, 962, 963, 962, 962, 962, 962, 962, 962, 962,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 961,
                962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 962, 961,
                962,
                ]))

    def readable(self):
        return True

    def writeable(self):
        return True

    def seekable(self):
        return False

    def open(self):
        assert not self._opened
        self._opened = True

    def close(self):
        assert self._opened
        self._opened = False

    def flush(self):
        pass

    def flushInput(self):
        self._read_buf = []

    def flushOutput(self):
        self._write_buf = ''

    def nonblocking(self):
        raise NotImplementedError

    def fileno(self):
        raise NotImplementedError

    def setXON(self, level=True):
        raise NotImplementedError

    def sendBreak(self, duration=0.25):
        raise NotImplementedError

    def setBreak(self, level=True):
        raise NotImplementedError

    def setRTS(self, level=True):
        if level and self._cts_high is None:
            # Emulate the unit taking half a second to wake up and send the
            # LOGON message and prompt
            self._cts_high = time.time() + 0.5
            self._send('LOGON\r')
            self._send('>\r')
        else:
            self._cts_high = None

    def getCTS(self):
        return (self._cts_high is not None) and (time.time() > self._cts_high)

    def setDTR(self, level=True):
        raise NotImplementedError

    def getDSR(self):
        raise NotImplementedError

    def getRI(self):
        raise NotImplementedError

    def getCD(self):
        raise NotImplementedError

    def readinto(self, b):
        # XXX Should implement this from read()
        raise NotImplementedError

    def inWaiting(self):
        return sum(1 for (c, t) in self._read_buf if t < time.time())

    def read(self, size=1):
        if not self._opened:
            raise ValueError('port is closed')
        start = time.time()
        now = start
        result = ''
        while len(result) < size:
            if self._read_buf and self._read_buf[0][1] < now:
                result += self._read_buf[0][0]
                del self._read_buf[0]
            else:
                time.sleep(0.1)
            now = time.time()
            if self.timeout is not None and now > start + self.timeout:
                break
        assert len(result) <= size
        return result.encode('ASCII')

    def write(self, data):
        # Pause for the amount of time it would take to send data
        time.sleep(len(data) * 10 / self.baudrate)
        if not self._opened:
            raise ValueError('port is closed')
        self._write_buf += data.decode('ASCII')
        while '\r' in self._write_buf:
            command, self._write_buf = self._write_buf.split('\r', 1)
            if ',' in command:
                command = command.split(',')
                command, args = command[0], command[1:]
            else:
                args = []
            self._process(command, *args)
        return len(data)

    def _bottle_by_serial(self, serial):
        if '-' not in serial:
            serial = '%s-%s' % (serial[:-2], serial[-2:])
        for bottle in self._bottles:
            if bottle.serial == serial:
                return bottle
        raise ValueError('%s is not a valid bottle serial number')


    def _process(self, command, *args):
        if command == 'MAID':
            # MAnufacturer IDentifier; OC110 sends 'OC110'
            self._send('OC110\r')
        elif command == 'CLOC':
            # CLOse Connection; OC110 sends a return, a prompt, pauses, then
            # sends a NUL char, and finally the 'LOGON' prompt
            self._send('\r')
            self._send('>\r')
            self._cts_high = time.time() + 0.5
            self._send('\0')
            self._send('LOGON\r')
        elif command == 'GAPB':
            # Get All Pressure Bottles command returns the header details of
            # all bottles and their heads
            data = ''.join(str(bottle) for bottle in self._bottles)
            self._send(data, checksum=True)
        elif command == 'GPRB':
            # Get PRessure Bottle command returns the details of the specified
            # bottle and its heads
            if len(args) != 1:
                self._send('INVALID ARGS\r')
            else:
                try:
                    bottle = self._bottle_by_serial(args[0])
                except ValueError:
                    self._send('INVALID BOTTLE\r')
                else:
                    self._send(str(bottle), checksum=True)
        elif command == 'GSNS':
            # No idea what GSNS does. Accepts a bottle serial and returns
            # nothing...
            if len(args) != 1:
                self._send('INVALID ARGS\r')
            else:
                try:
                    bottle = self._bottle_by_serial(args[0])
                except ValueError:
                    self._send('INVALID BOTTLE\r')
        elif command.startswith('GMSK'):
            # GMSK returns all readings from a specified bottle head
            if len(args) != 2:
                self._send('INVALID ARGS\r')
            else:
                try:
                    bottle = self._bottle_by_serial(args[0])
                except ValueError:
                    self._send('INVALID BOTTLE\r')
                for head in bottle.heads:
                    if head.serial == args[1]:
                        break
                    head = None
                if not head:
                    self._send('INVALID HEAD\r')
                self._send(str(head.readings), checksum=True)
        else:
            self._send('INVALID COMMAND\r')
        self._send('>\r')

    def _send(self, response, checksum=False):
        # If the port isn't open, just chuck away anything that gets sent
        if self._opened:
            # Transmission settings are 8-N-1, so cps of transmission is
            # self.baudrate / 10. Delay between characters is the reciprocal of
            # this
            delay = 10 / self.baudrate
            if self._read_buf:
                when = self._read_buf[-1][1]
            else:
                when = time.time()
            for char in response:
                when += delay
                self._read_buf.append((char, when))
            if checksum:
                value = sum(ord(c) for c in response)
                self._send(',%d\r' % value, checksum=False)

