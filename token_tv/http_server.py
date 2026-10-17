"""Bounded, loopback-only HTTP serving for the live dashboard."""
import ipaddress
import socket
import threading
from http.server import ThreadingHTTPServer


def loopback_host(host):
    # Do not resolve arbitrary DNS names: validation and binding must use the same address.
    if host.lower() == 'localhost':
        return '127.0.0.1'
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError('Dashboard host must be localhost or a loopback IP; use an SSH tunnel for remote access') from None
    if not address.is_loopback:
        raise ValueError('Dashboard host must be localhost or a loopback IP; use an SSH tunnel for remote access')
    return str(address)


class LocalHTTPServer(ThreadingHTTPServer):
    max_connections = 16
    read_timeout = 5
    request_deadline = 30

    def __init__(self, server_address, handler):
        host, port = server_address
        host = loopback_host(host)
        self.address_family = socket.AF_INET6 if ':' in host else socket.AF_INET
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__((host, port), handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.read_timeout)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        def expire():
            # A total deadline also bounds clients that drip bytes faster than the read timeout.
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        deadline = threading.Timer(self.request_deadline, expire)
        deadline.daemon = True
        try:
            deadline.start()
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self._slots.release()


def trusted_request(request):
    """Validate browser authority against the bound listener, never against DNS or forwarded headers."""
    hosts = request.headers.get_all('Host', [])
    if len(hosts) != 1:
        return False
    host = hosts[0].lower()
    address, port = request.server.server_address[:2]
    names = {'localhost', '127.0.0.1', '[::1]', f'[{address}]' if ':' in address else address}
    authorities = {f'{name}:{port}' for name in names}
    if port == 80:
        authorities.update(names)
    if host not in authorities:
        return False
    origins = request.headers.get_all('Origin', [])
    if origins and (len(origins) != 1 or origins[0].lower() != 'http://' + host):
        return False
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return False
    # Only origin-form URLs are accepted, including for non-browser clients.
    # BaseHTTPRequestHandler normalizes a leading //, so inspect the original target.
    target = request.requestline.split()[1]
    return target.startswith('/') and not target.startswith('//')
