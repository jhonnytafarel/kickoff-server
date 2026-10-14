"""Local UDP relay for the match transport (XReliabilitySocket / UdpRelaySession).

Datagrams use the TCP frame layout: uint16 size, uint16 kind, body, CRC32 over
everything before the CRC. Kinds dispatched by 00609B49 (kind-2 index into the
table at 00609CB8):

  2  message frame (id + payload), same as TCP          -> 0060A31C
  3  relay data between kickers (0x34..0x4000 bytes)    -> 004F958D
  5  reliability acknowledgement, 40 bytes              -> 00609E8C
  6  routing header (16 bytes) + checksummed inner packet; no outer CRC
  7  heartbeat, 4 bytes                                 -> 00609DD5
  8  probe: 1 byte, 0 = request, 1 = reply (004F8FC3)    -> 004F8FC3
  9  session: int32 kicker, int16 session, int16 subtype -> 00609DF2
     subtype 1 open (004F9A68 accepts when kicker matches and session != -1),
     2 close (004F8EB9), 3 (004F958D path).
"""
import struct
import socket
import protocol

KIND_MESSAGE, KIND_DATA, KIND_ACK, KIND_ENVELOPE, KIND_HEARTBEAT, KIND_PROBE, KIND_SESSION = 2, 3, 5, 6, 7, 8, 9
SUBTYPE_OPEN, SUBTYPE_CLOSE = 1, 2
RELAY_JOIN_REQ, RELAY_JOIN_ACK = 0x60000, 0x60001


HEARTBEAT = struct.pack('<HH', 4, KIND_HEARTBEAT)   # 00609DD5 only checks size 4


def packet(kind, body):
    if kind == KIND_HEARTBEAT:
        return HEARTBEAT
    data = struct.pack('<HH', 4+len(body)+4, kind) + body
    return data + struct.pack('<I', protocol.checksum(data))


def decode(data):
    """Return (kind, body) or raise ValueError for malformed datagrams."""
    if len(data) < 4:
        raise ValueError('Datagram too short')
    size, kind = struct.unpack_from('<HH', data)
    if size != len(data):
        raise ValueError('Size mismatch')
    if kind == KIND_HEARTBEAT:
        if size != 4:
            raise ValueError('Bad heartbeat size')
        return kind, b''
    if kind == KIND_ENVELOPE:
        # 0060A23D..0060A2B1: 16-byte routing header followed by a complete
        # packet. There is NO outer CRC; the nested packet owns its checksum.
        if size < 20:
            raise ValueError('Truncated relay envelope')
        inner_kind = struct.unpack_from('<H', data, 18)[0]
        if inner_kind not in (KIND_DATA, KIND_ACK):
            raise ValueError('Unsupported nested relay packet')
        decode(data[16:])
        return kind, data[4:]
    if size < 8 or struct.unpack_from('<I', data, size-4)[0] != protocol.checksum(data[:size-4]):
        raise ValueError('Bad checksum')
    return kind, data[4:size-4]


class Relay:
    """One session per kicker; keeps the peer address for forwarding."""

    def __init__(self):
        self.sessions = {}      # kicker_id -> dict(session=int, addr=tuple)
        self.next_session = 1
        self.bots = {}

    def handle(self, data, addr):
        """Return a list of (datagram, addr) to send and an event description."""
        kind, body = decode(data)
        if kind == KIND_ENVELOPE:
            replies, event = self.handle(data[16:], addr)
            return replies, 'envelope ' + event
        if kind == KIND_ACK:
            # 00609E8C: byte 30=1 confirms relay reception; byte 30=0
            # confirms peer reception and must reach the ORIGINAL sender.
            if len(data) != 40:
                raise ValueError('Bad reliability ACK size')
            if data[30] == 0:
                sender, recipient = struct.unpack_from('<ii', data, 4)
                origin = self.sessions.get(sender)
                peer = self.sessions.get(recipient)
                if origin is not None and peer is not None and peer['addr'] == addr and origin['addr'] != addr:
                    return [(data, origin['addr'])], f'peer_ack {recipient}->{sender}'
            return [], 'reliability_ack'
        if kind == KIND_PROBE:
            if len(body) != 1:
                raise ValueError('Bad probe')
            if body[0] == 0:
                return [(packet(KIND_PROBE, b'\x01'), addr)], 'probe'
            return [], 'probe_reply'
        if kind == KIND_SESSION:
            kicker_id, session, subtype = struct.unpack('<ihh', body)
            if subtype == SUBTYPE_OPEN:
                entry = self.sessions.get(kicker_id)
                if entry is None:
                    entry = self.sessions[kicker_id] = dict(session=self.next_session, addr=addr)
                    self.next_session += 1
                entry['addr'] = addr
                reply = packet(KIND_SESSION, struct.pack('<ihh', kicker_id, entry['session'], SUBTYPE_OPEN))
                return [(reply, addr)], f'session_open kicker={kicker_id} session={entry["session"]}'
            if subtype == SUBTYPE_CLOSE:
                self.sessions.pop(kicker_id, None)
                return [], f'session_close kicker={kicker_id}'
            return [], f'session_subtype={subtype} kicker={kicker_id}'
        if kind == KIND_HEARTBEAT:
            return [(HEARTBEAT, addr)], 'heartbeat'
        if kind == KIND_MESSAGE:
            message_id = struct.unpack_from('<I', body)[0]
            payload = body[4:]
            if message_id == RELAY_JOIN_REQ and len(payload) >= 12:
                mode, division, kicker_id = struct.unpack_from('<iii', payload)
                reply = protocol.relay_join_ack(mode, division, kicker_id)
                return [(reply, addr)], f'relay_join kicker={kicker_id}'
            return [], f'message id=0x{message_id:x}'
        if kind == KIND_DATA:
            if not 0x34 <= len(data) <= 0x4000:
                raise ValueError('Bad relay data size')
            # 004F958D: +0 sender kicker, +4 target kicker (int32 each).
            sender, target = struct.unpack_from('<ii', body)
            replies = []
            if data[15]:
                # 0060A0FC..0060A194 / 00609E8C: sender, target, send time,
                # sequence, observed endpoint, acknowledgement byte, padding.
                ack_body = (data[4:12] + data[20:28] + data[40:44]
                            + struct.pack('!4sH', socket.inet_aton(addr[0]), addr[1])
                            + b'\x01\x00' + bytes(4))
                replies.append((packet(KIND_ACK, ack_body), addr))
            # Reliable data strips its 52-byte header before dispatching the
            # message ID (0060AC01). Relay join is a server request, not P2P.
            if len(data) >= 68 and struct.unpack_from('<I', data, 52)[0] == RELAY_JOIN_REQ:
                mode, division, kicker_id = struct.unpack_from('<iii', data, 56)
                replies.append((protocol.relay_join_ack(mode, division, kicker_id), addr))
                return replies, f'reliable_relay_join kicker={kicker_id}'
            peer = self.sessions.get(target)
            bot = self.bots.get(target)
            if bot is not None:
                replies.extend((response, addr) for response in bot.handle(data))
                stream = f'0x{bot.last_message_id:x}' if bot.last_message_id is not None else 'ignored'
                return replies, f'bot_peer {sender}->{target} message={stream} phase={bot.phase}'
            if peer is not None:
                replies.append((data, peer['addr']))
                return replies, f'data {sender}->{target}'
            return replies, f'data {sender}->{target} (no session)'
        return [], f'kind={kind}'
