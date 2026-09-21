"""Bounded HTTPS fetches with pinned public DNS addresses and no redirects."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit


def fetch_public_bytes(url, allowed_hosts, max_bytes, timeout=10):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    try:
        valid = (parsed.scheme == 'https' and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and any(host == h or host.endswith('.' + h) for h in allowed_hosts))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Invalid remote source URL.')
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Remote destination is not public.')
    deadline = time.monotonic() + timeout

    def connect_pinned(address, timeout=None, source_address=None):
        # Never resolve the hostname again between validation and connecting.
        last_error = None
        for family, kind, proto, _, sockaddr in addresses:
            sock = socket.socket(family, kind, proto)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                sock.settimeout(remaining)
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()
        raise OSError('Remote connection failed.') from last_error

    connection = http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
    connection._create_connection = connect_pinned
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        connection.request('GET', path, headers={'User-Agent': 'MP3MetaFix', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if response.status != 200:
            # In particular, never follow a CDN redirect to an unvalidated host.
            raise ValueError('Remote source did not return a successful response.')
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('Compressed remote responses are not supported.')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdecimal() or int(length) > max_bytes):
            raise ValueError('Remote response exceeds size limit.')
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, max_bytes + 1 - len(output)))
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > max_bytes:
                raise ValueError('Remote response exceeds size limit.')
    finally:
        connection.close()
