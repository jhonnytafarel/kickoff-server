"""Addresses handed to the client.

The front server stays on loopback; game TCP follows the client. The match UDP socket is different: the client
binds it to the first address of its own host name lookup (a LAN or virtual
adapter, e.g. 192.168.56.1) on port 20000 and Windows refuses to send from
that socket to 127.0.0.1 (error 10049). The relay endpoint therefore uses the
same address with another port, so the local server can own it.
"""
import socket

GAME_PORT = 20000
RELAY_PORT = 20001


def _default_ip():
    # The client binds the first address returned by the host name lookup.
    try:
        return socket.gethostbyname_ex(socket.gethostname())[2][0]
    except OSError:
        return '127.0.0.1'


CLIENT_IP = _default_ip()
# The client reports the local address of its TCP connection as its public
# match address (0x20034) and sends UDP there. Game TCP therefore also runs
# on CLIENT_IP so that address equals the UDP socket it bound.
TCP_IP = CLIENT_IP


def tcp_endpoint():
    return socket.inet_aton(TCP_IP) + GAME_PORT.to_bytes(2, 'big')


def relay_endpoint():
    return socket.inet_aton(CLIENT_IP) + RELAY_PORT.to_bytes(2, 'big')


def client_endpoint():
    """The client's own match socket, as peers would see it."""
    return socket.inet_aton(CLIENT_IP) + GAME_PORT.to_bytes(2, 'big')
