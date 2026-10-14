"""Initial native Buddy handshake, reconstructed from Game.exe 005D3058.

This is a contact-list bootstrap, not a replacement account/friendship service.
Do not seed test peers as friends or acknowledge invitations without storing them.
"""
import struct
from characters import read_string
import protocol

REGISTER = 0x650000
LOGIN_FAILURE = 0x650001  # ID_MESSENGER_LOGIN_REQ_FACK, not a success ACK.
CONTACT_LIST = 0x650002
PORT = 19998


def decode_registration(body):
    if len(body) < 4:
        raise ValueError('Missing Buddy character ID')
    character_id = struct.unpack_from('<i', body)[0]
    nickname, end = read_string(body, 4)
    if len(body) != end + 8:
        raise ValueError('Unexpected Buddy registration length')
    if not nickname or '\x00' in nickname or len(nickname) > 15:
        raise ValueError('Invalid Buddy nickname')
    level, position = struct.unpack_from('<ii', body, end)
    return dict(id=character_id, nickname=nickname, level=level, position=position)


def contact_list():
    # 005D2DCF: group count, (group ID, terminated string), contact count.
    # Empty contacts are intentional: the local account has no recorded friends.
    name = b'Amigos\x00'
    body = struct.pack('<iiH', 1, 1, len(name)) + name + struct.pack('<i', 0)
    return protocol.frame(CONTACT_LIST, body)


def bootstrap(body, character):
    registration = decode_registration(body)
    if not character or any(registration[k] != character[k] for k in ('id', 'nickname')):
        raise ValueError('Buddy registration does not match the local character')
    # Success is the contact list. 005D1A6B handles LOGIN_REQ_FACK and notifies
    # the native window with 0x65; sending it also displays a login error.
    return contact_list(), registration
