#!/usr/bin/env python3
# temper.py -*-python-*-
# Copyright 2018 by Pham Urwen (urwen@mail.ru)
# Portions copyright Richard Kettlewell
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

# Standard python3 modules
import argparse
import binascii
import json
import os
import re
import select
import struct
import sys

# Non-standard modules
try:
    import serial
except ImportError:
    print('Cannot import "serial". Please sudo apt-get install python3-serial')
    sys.exit(1)


class USBList(object):
    '''Get a list of all of the USB devices on a system, along with their
    associated hidraw or serial (tty) devices.
    '''

    SYSPATH = '/sys/bus/usb/devices'

    def _readfile(self, path):
        '''Read data from 'path' and return it as a string. Return the empty string
        if the file does not exist, cannot be read, or has an error.
        '''
        try:
            with open(path, 'r') as fp:
                return fp.read().strip()
        except:
            return ''

    def _find_devices(self, dirname):
        '''Scan a directory hierarchy for names that start with "tty" or "hidraw".
        Return these names in a set.
        '''
        devices = set()
        for entry in os.scandir(dirname):
            if entry.is_dir() and not entry.is_symlink():
                devices |= self._find_devices(
                    os.path.join(dirname, entry.name))
            if re.search('tty.*[0-9]', entry.name):
                devices.add(entry.name)
            if re.search('hidraw[0-9]', entry.name):
                devices.add(entry.name)
        return devices

    def _get_usb_device(self, dirname):
        '''Examine the files in 'dirname', looking for files with well-known
        names expected to be in the /sys hierarchy under Linux for USB devices.
        Return a dictionary of the information gathered. If no information is found
        (i.e., because the directory is not for a USB device) return None.
        '''
        info = dict()
        vendorid = self._readfile(os.path.join(dirname, 'idVendor'))
        if vendorid == '':
            return None
        info['vendorid'] = int(vendorid, 16)
        productid = self._readfile(os.path.join(dirname, 'idProduct'))
        info['productid'] = int(productid, 16)
        info['manufacturer'] = self._readfile(os.path.join(dirname,
                                                           'manufacturer'))
        info['product'] = self._readfile(os.path.join(dirname, 'product'))
        info['busnum'] = int(self._readfile(os.path.join(dirname, 'busnum')))
        info['devnum'] = int(self._readfile(os.path.join(dirname, 'devnum')))
        info['devices'] = sorted(self._find_devices(dirname))
        return info

    def get_usb_devices(self):
        '''Scan a well-known Linux hierarchy in /sys and try to find all of the
        USB devices on a system. Return these as a dictionary indexed by the path.
        '''
        info = dict()
        for entry in os.scandir(Temper.SYSPATH):
            if entry.is_dir():
                path = os.path.join(Temper.SYSPATH, entry.name)
                device = self._get_usb_device(path)
                if device is not None:
                    info[path] = device
        return info


class USBRead(object):
    '''Read temperature and/or humidity information from a specified USB device.
    '''

    def __init__(self, device, verbose=False):
        self.device = device
        self.verbose = verbose

    def _parse_bytes(self, name, offset, divisor, bytes, info):
        '''Data is returned from several devices in a similar format. In the first
        8 bytes, the internal sensors are returned in bytes 2 and 3 (temperature)
        and in bytes 4 and 5 (humidity). In the second 8 bytes, external sensor
        information is returned. If there are only external sensors, then only 8
        bytes are returned, and the caller is expected to use the correct 'name'.
        The caller is also expected to detect the firmware version and provide the
        appropriate divisor, which is usually 100 or 256.

        There is no return value. Instead 'info[name]' is update directly, if a
        value is found.
        '''
        try:
            if bytes[offset] == 0x4e and bytes[offset+1] == 0x20:
                return
        except:
            return
        try:
            info[name] = struct.unpack_from('>h', bytes, offset)[0] / divisor
        except:
            return

    def _read_hidraw_firmware(self, fd, verbose=False):
        ''' Get firmware identifier'''
        query = struct.pack('8B', 0x01, 0x86, 0xff, 0x01, 0, 0, 0, 0)
        if verbose:
            print('Firmware query: %s' % binascii.b2a_hex(query))

        # Sometimes we don't get all of the expected information from the
        # device.  We'll retry a few times and hope for the best.
        # See: https://github.com/urwen/temper/issues/9
        for i in range(0, 10):
            os.write(fd, query)

            firmware = b''
            while True:
                r, _, _ = select.select([fd], [], [], 0.2)
                if fd not in r:
                    break
                data = os.read(fd, 8)
                firmware += data

            if not len(firmware):
                os.close(fd)
                raise RuntimeError('Cannot read device firmware identifier')

            if len(firmware) > 8:
                break

        if verbose:
            print('Firmware value: %s %s' %
                  (binascii.b2a_hex(firmware), firmware.decode()))

        return firmware

    def _read_hidraw(self, device):
        '''Using the Linux hidraw device, send the special commands and receive the
        raw data. Then call '_parse_bytes' based on the firmware version to provide
        temperature and humidity information.

        A dictionary of temperature and humidity info is returned.
        '''
        path = os.path.join('/dev', device)
        fd = os.open(path, os.O_RDWR)

        firmware = self._read_hidraw_firmware(fd, self.verbose)

        # Get temperature/humidity
        os.write(fd, struct.pack('8B', 0x01, 0x80, 0x33, 0x01, 0, 0, 0, 0))
        bytes = b''
        while True:
            r, _, _ = select.select([fd], [], [], 0.1)
            if fd not in r:
                break
            data = os.read(fd, 8)
            bytes += data

        os.close(fd)
        if self.verbose:
            print('Data value: %s' % binascii.hexlify(bytes))

        info = dict()
        info['firmware'] = str(firmware, 'latin-1').strip()
        info['hex_firmware'] = str(binascii.b2a_hex(firmware), 'latin-1')
        info['hex_data'] = str(binascii.b2a_hex(bytes), 'latin-1')

        if info['firmware'][:10] == 'TEMPerF1.4':
            info['firmware'] = info['firmware'][:10]
            self._parse_bytes('internal temperature', 2, 256.0, bytes, info)
            return info

        if info['firmware'][:14] == 'TEMPerGold_V3.':
            info['firmware'] = info['firmware'][:15]
            self._parse_bytes('internal temperature', 2, 100.0, bytes, info)
            return info

        if info['firmware'][:12] in ['TEMPerX_V3.1', 'TEMPerX_V3.3']:
            info['firmware'] = info['firmware'][:12]
            self._parse_bytes('internal temperature', 2, 100.0, bytes, info)
            self._parse_bytes('internal humidity', 4, 100.0, bytes, info)
            self._parse_bytes('external temperature', 10, 100.0, bytes, info)
            self._parse_bytes('external humidity', 12, 100.0, bytes, info)
            return info

        info['error'] = 'Unknown firmware %s: %s' % (info['firmware'],
                                                     binascii.hexlify(bytes))
        return info

    def _read_serial(self, device):
        '''Using the Linux serial device, send the special commands and receive the
        text data, which is parsed directly in this method.

        A dictionary of device info (like that returned by USBList) combined with
        temperature and humidity info is returned.
        '''

        path = os.path.join('/dev', device)
        s = serial.Serial(path, 9600)
        s.bytesize = serial.EIGHTBITS
        s.parity = serial.PARITY_NONE
        s.stopbits = serial.STOPBITS_ONE
        s.timeout = 1
        s.xonoff = False
        s.rtscts = False
        s.dsrdtr = False
        s.writeTimeout = 0

        # Send the "Version" command and save the reply.
        s.write(b'Version')
        firmware = str(s.readline(), 'latin-1').strip()

        # Send the "ReadTemp" command and save the reply.
        s.write(b'ReadTemp')
        reply = str(s.readline(), 'latin-1').strip()
        reply += str(s.readline(), 'latin-1').strip()
        s.close()

        info = dict()
        info['firmware'] = firmware
        m = re.search(r'Temp-Inner:([0-9.]*).*, ?([0-9.]*)', reply)
        if m is not None:
            info['internal temperature'] = float(m.group(1))
            info['internal humidity'] = float(m.group(2))
        m = re.search(r'Temp-Outer:([0-9.]*)', reply)
        if m is not None:
            try:
                info['external temperature'] = float(m.group(1))
            except:
                pass
        return info

    def read(self):
        '''Read the firmware version, temperature, and humidity from the device and
        return a dictionary containing these data.
        '''
        # Use the last device found
        if self.device.startswith('hidraw'):
            return self._read_hidraw(self.device)
        if self.device.startswith('tty'):
            return self._read_serial(self.device)
        return {'error': 'No usable hid/tty devices available'}


class Temper(object):
    SYSPATH = '/sys/bus/usb/devices'

    def __init__(self, verbose=False):
        self.forced_vendor_id = None
        self.forced_product_id = None
        self.verbose = verbose
        self.reset()

    def reset(self):
        usblist = USBList()
        self.usb_devices = usblist.get_usb_devices()
        self.last_reset = os.times().elapsed

    def _is_known_id(self, vendorid, productid):
        '''Returns True if the vendorid and product id are valid.
        '''
        if self.forced_vendor_id is not None and \
           self.forced_product_id is not None:
            if self.forced_vendor_id == vendorid and \
               self.forced_product_id == productid:
                return True
            return False

        if vendorid == 0x0c45 and productid == 0x7401:
            return True
        if vendorid == 0x413d and productid == 0x2107:
            return True
        if vendorid == 0x1a86 and productid == 0x5523:
            return True
        if vendorid == 0x1a86 and productid == 0xe025:
            return True

        # The id is not known to this program.
        return False

    def list(self, use_json=False):
        '''Print out a list all of the USB devices on the system. If 'use_json' is
        True, then JSON formatting will be used.
        '''
        if use_json:
            print(json.dumps(self.usb_devices, indent=4))
            return

        for _, info in sorted(self.usb_devices.items(),
                              key=lambda x: x[1]['busnum'] * 1000 +
                              x[1]['devnum']):
            print('Bus %03d Dev %03d %04x:%04x %s %s %s' % (
                info['busnum'],
                info['devnum'],
                info['vendorid'],
                info['productid'],
                '*' if self._is_known_id(info['vendorid'],
                                         info['productid']) else ' ',
                info.get('product', '???'),
                list(info['devices']) if len(info['devices']) > 0 else ''))

    def read(self, verbose=False):
        '''Read all of the known devices on the system and return a list of
        dictionaries which contain the device information, firmware information,
        and environmental information obtained. If there is an error, then the
        'error' field in the dictionary will contain a string explaining the
        error.
        '''
        results = []
        for _, info in sorted(self.usb_devices.items(),
                              key=lambda x: x[1]['busnum'] * 1000 +
                              x[1]['devnum']):
            if not self._is_known_id(info['vendorid'], info['productid']):
                continue
            if len(info['devices']) == 0:
                info['error'] = 'no hid/tty devices available'
                results.append(info)
                continue
            usbread = USBRead(info['devices'][-1], verbose)
            results.append({**info, **usbread.read()})
        return results

    def _add_temperature(self, name, info):
        '''Helper method to add the temperature to a string in both Celsius and
        Fahrenheit. If no sensor data is available, then '- -' will be returned.
        '''
        if name not in info:
            return '- -'
        degC = info[name]
        degF = degC * 1.8 + 32.0
        return '%.2fC %.2fF' % (degC, degF)

    def _add_humidity(self, name, info):
        '''Helper method to add the humidity to a string. If no sensor data is
        available, then '-' will be returned.
        '''

        if name not in info:
            return '-'
        return '%d%%' % int(info[name])

    def print(self, results, use_json=False):
        '''Print out a list of all of the known USB sensor devices on the system.
        If 'use_json' is True, then JSON formatting will be used.
        '''

        if use_json:
            print(json.dumps(results, indent=4))
            return

        for info in results:
            s = 'Bus %03d Dev %03d %04x:%04x %s' % (info['busnum'],
                                                    info['devnum'],
                                                    info['vendorid'],
                                                    info['productid'],
                                                    info.get('firmware'))
            if 'error' in info:
                s += ' Error: %s' % info['error']
            else:
                s += ' ' + self._add_temperature('internal temperature', info)
                s += ' ' + self._add_humidity('internal humidity', info)
                s += ' ' + self._add_temperature('external temperature', info)
                s += ' ' + self._add_humidity('external humidity', info)
            print(s)

    def maybe_reset(self):
        # The web server will reset its device list once a minute,
        # or when an error occurs.
        if os.times().elapsed - self.last_reset > 60:
            self.reset()

    def webserver(self, server_config_path, logging=True):
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        from threading import Lock
        from traceback import print_exc
        import stat
        import socket

        with open(server_config_path) as f:
            server_config = json.load(f)

        if 'certfile' in server_config:
            protocol = 'https'
            default_port = 443
        else:
            protocol = 'http'
            default_port = 80

        if 'port' not in server_config:
            server_config['port'] = default_port

        # Look for an inherited socket
        inherited_socket = None
        fd = 3
        if stat.S_ISSOCK(os.fstat(fd).st_mode):
            inherited_socket = socket.fromfd(
                fd, ThreadingHTTPServer.socket_type, ThreadingHTTPServer.address_family)
            address = inherited_socket.getsockname()

        path_pattern = re.compile('^/([0-9]+)/([0-9]+)$')
        self.lock = Lock()
        self.last_reset = os.times().elapsed

        class RequestHandler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(rqh):
                # handle a GET request
                code, results = rqh.get_path('GET')
                body = bytes(json.dumps(results, indent=1), 'UTF-8')
                rqh.send_response(code)
                rqh.send_header("Content-Type", "application/json")
                rqh.send_header("Content-Length", "{}".format(len(body)))
                rqh.end_headers()
                rqh.wfile.write(body)

            def do_HEAD(rqh):
                # hadnle a HEAD request
                code, results = rqh.get_path('HEAD')
                rqh.send_response(code)
                rqh.send_header("Content-Type", "application/json")
                rqh.end_headers()

            def get_path(rqh, mode):
                # handle any request, returning STATUS,BODY
                with self.lock:
                    match = path_pattern.match(rqh.path)
                    if match:
                        return rqh.get_device(mode, match)
                    if rqh.path == "/devices":
                        return rqh.get_devices(mode)
                    if rqh.path == "/":
                        return rqh.get_all(mode)
                return 404, {'error': 'unrecognized path'}

            def get_devices(rqh, mode):
                # Handle a /devices request
                self.maybe_reset()
                results = []
                for _, info in self.usb_devices.items():
                    # Skip irrelevant devices
                    if not self._is_known_id(info['vendorid'], info['productid']):
                        continue
                    # Return device identification information but skip bus information
                    results.append(rqh.filter(info))
                return 200, results

            def get_all(rqh, mode):
                # Handle a / request
                self.maybe_reset()
                if mode == 'HEAD':  # don't query devices just for HEAD
                    return 200, []
                results = None
                try:
                    results = [rqh.filter(data) for data in self.read(False)]
                except FileNotFoundError:
                    print_exc()
                if results is None:
                    self.force_reset()
                    results = [rqh.filter(data) for data in self.read(False)]
                return 200, results

            def get_device(rqh, mode, match):
                # Handle a request to a single device
                vendorid = int(match.group(1))
                productid = int(match.group(2))
                try:
                    for _, info in self.usb_devices.items():
                        if not self._is_known_id(info['vendorid'], info['productid']):
                            continue
                        if vendorid == info['vendorid'] and productid == info['productid']:
                            if mode == 'HEAD':  # don't query device just for HEAD
                                return 200, {}
                            usbread = USBRead(info['devices'][-1], False)
                            return 200, rqh.filter({**info, **usbread.read()})
                except FileNotFoundError:
                    print_exc()
                self.maybe_reset()
                return 404, {'error': 'no such device'}

            def filter(rqh, info):
                filtered = {}
                for k in ['vendorid', 'productid', 'manufacturer', 'product', 'internal temperature', 'internal humidity', 'external temperature', 'external humidity']:
                    if k in info:
                        filtered[k] = info[k]
                    # Provide a stable URL for this device
                    filtered['url'] = f'{protocol}://{server_config["hostname"]}:{server_config["port"]}/{info["vendorid"]}/{info["productid"]}'
                return filtered

            def log_request(self, code='-', size='-'):
                if logging:
                    return super().log_request(code, size)

        class InheritingHTTPServer(ThreadingHTTPServer):
            def server_bind(self):
                # If stdin is a socket then we'll use that as the listening socket
                if inherited_socket is not None:
                    self.socket = inherited_socket
                    self.server_address = self.socket.getsockname()
                else:
                    return super().server_bind()

            def server_activate(self):
                # If we're inheriting the socket then listen() was already called
                if inherited_socket is None:
                    return super().server_activate()

        server = InheritingHTTPServer(
            (server_config['hostname'], server_config['port']), RequestHandler)
        if 'certfile' in server_config:
            import ssl
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            keyfile = os.path.expandvars(server_config['keyfile'])
            certfile = os.path.expandvars(server_config['certfile'])
            context.load_cert_chain(
                certfile=certfile, keyfile=keyfile)
            server.socket = context.wrap_socket(
                server.socket, server_side=True)
        server.serve_forever()

    def main(self):
        '''An example 'main' entry point that can be used to make temper.py a
        standalone program.
        '''

        parser = argparse.ArgumentParser(description='temper')
        parser.add_argument('-l', '--list', action='store_true',
                            help='List all USB devices')
        parser.add_argument('--json', action='store_true',
                            help='Provide output as JSON')
        parser.add_argument('--force', type=str,
                            help='Force the use of the hex id; ignore other ids',
                            metavar=('VENDOR_ID:PRODUCT_ID'))
        parser.add_argument('--server', type=str,
                            help='HTTP server configuration', metavar='PATH')
        parser.add_argument('--verbose', action='store_true',
                            help='Output binary data from thermometer')
        parser.add_argument('--logging', action='store_true',
                            help='Log HTTP requests')
        args = parser.parse_args()
        self.verbose = args.verbose

        if args.server:
            self.webserver(args.server, logging=args.logging)
            return

        if args.list:
            self.list(args.json)
            return 0

        if args.force:
            ids = args.force.split(':')
            if len(ids) != 2:
                print('Cannot parse hexadecimal id: %s' % args.force)
                return 1
            try:
                vendor_id = int(ids[0], 16)
                product_id = int(ids[1], 16)
            except:
                print('Cannot parse hexadecimal id: %s' % args.force)
                return 1
            self.forced_vendor_id = vendor_id
            self.forced_product_id = product_id

        # By default, output the temperature and humidity for all known sensors.
        results = self.read(args.verbose)
        self.print(results, args.json)
        return 0


if __name__ == "__main__":
    temper = Temper()
    sys.exit(temper.main())
