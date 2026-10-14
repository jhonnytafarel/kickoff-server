"""Experimental Practice opponent. Profiles are ephemeral, never account rows.

The virtual peer handshake is separate from the decision controller so missing
wire events cannot silently turn a placeholder roster into a playable opponent.
"""
import math
import struct
from characters import read_string

BOT_ID = 28673
BOT_NAME = 'BotTreino'


def decode_stream(data):
    """Decode the P2P stream, then verify its independent length and CRC.

    0060A5CB/004055DC: codec 2 is AES-128 CBC. Its wire wrapper stores
    padded length and original length before ciphertext. 0060A625 builds
    key=(sender,0,sender,target), IV=(target,nonce,nonce,nonce).
    Captured packets confirm this against the independent stream CRC.
    """
    import protocol
    compression, encryption = data[48:50]
    expected = struct.unpack_from('<H', data, 50)[0]
    stream = data[52:-4]
    if compression:
        raise ValueError('Unsupported bot compression codec')
    if encryption == 2:
        from Crypto.Cipher import AES
        if len(stream) < 24:
            raise ValueError('Truncated encrypted bot stream')
        padded, original = struct.unpack_from('<II', stream)
        if padded != len(stream)-8 or padded % 16 or not 0 < original <= padded:
            raise ValueError('Invalid encrypted bot stream sizes')
        sender, target = struct.unpack_from('<II', data, 4)
        nonce = struct.unpack_from('<I', data, 44)[0]
        key = struct.pack('<4I', sender, 0, sender, target)
        iv = struct.pack('<4I', target, nonce, nonce, nonce)
        stream = AES.new(key, AES.MODE_CBC, iv).decrypt(stream[8:])[:original]
    elif encryption:
        raise ValueError('Unsupported bot encryption codec')
    if len(stream) != expected or protocol.checksum(stream) != struct.unpack_from('<I', data, 36)[0]:
        raise ValueError('Invalid bot stream length/checksum')
    if len(stream) < 4:
        raise ValueError('Missing bot message ID')
    return struct.unpack_from('<I', stream)[0], stream[4:]


def profile(character, kicker_id=BOT_ID, nickname=BOT_NAME):
    _, end = read_string(character['payload'], 4)
    name = nickname.encode('ascii') + b'\0'
    prefix = struct.pack('<iH', kicker_id, len(name)) + name
    data = prefix + character['payload'][end:]
    return dict(character, id=kicker_id, nickname=nickname, payload=data,
                base_end=character['base_end'] + len(prefix) - end,
                skills=[], missions={}, tutorials=[])


class Opponent:
    """A virtual peer with bounded movement decisions, driven by host time."""
    def __init__(self, host_id, kicker_id=BOT_ID, endpoint=None):
        self.kicker_id = kicker_id
        self.endpoint = endpoint
        self.host_id = host_id
        self.template = None
        self.last_time = None
        self.position = (80.0, 0.0, 0.0)
        self.target = (0.0, 0.0, 0.0)
        self.phase = 'waiting'
        self.sequence = 0
        self.last_message_id = None

    def wrap(self, message_id, payload, template):
        import relay
        import protocol
        header = bytearray(template[4:52])
        if len(header) != 48:
            raise ValueError('Missing virtual peer routing header')
        struct.pack_into('<ii', header, 0, self.kicker_id, self.host_id)
        header[11] = 0  # non-reliable; sequence is still distinct
        self.sequence += 1
        struct.pack_into('<I', header, 36, self.sequence)
        message = struct.pack('<I', message_id) + payload
        # 0060A5CB verifies the decoded stream's length AND separate CRC.
        # These are distinct from the datagram's outer length/CRC.
        struct.pack_into('<I', header, 32, protocol.checksum(message))
        header[44:46] = b'\0\0'  # uncompressed, unencrypted stream
        struct.pack_into('<H', header, 46, len(message))
        return relay.packet(relay.KIND_DATA, header + message)

    def handle(self, data):
        if len(data) < 60:
            raise ValueError('Truncated bot message')
        sender, target = struct.unpack_from('<ii', data, 4)
        if sender != self.host_id or target not in (self.kicker_id, self.host_id):
            return []
        message_id, payload = decode_stream(data)
        self.last_message_id = message_id
        self.template = data
        replies = []
        # 0050E9BE reads stage, initiator ID, responder ID AND a 6-byte
        # endpoint. Stage 2 keeps both IDs in the same order as the request.
        if message_id == 0x10001 and len(payload) == 18:
            stage, initiator, responder = struct.unpack_from('<iii', payload)
            if stage == 1 and (initiator, responder) == (self.host_id, self.kicker_id):
                import netstate
                replies.append(self.wrap(0x10001, struct.pack('<iii', 2, initiator, responder)
                                         + (self.endpoint or netstate.relay_endpoint()), data))
                self.phase = 'connected'
        elif message_id == 0x10004 and len(payload) == 8:
            # 0050EB88 serializes echoed request double + local clock double.
            replies.append(self.wrap(0x10005, payload + payload, data))
        elif message_id == 0x10006 and len(payload) == 12:
            # 0050F576: requested turn, replying kicker, echoed time.
            replies.append(self.wrap(0x10007, payload[:4] + struct.pack('<i', self.kicker_id) + payload[4:], data))
        elif message_id == 0x10008 and len(payload) == 4:
            # 0050E5FA consumes only the completed kicker ID.
            replies.append(self.wrap(message_id, struct.pack('<i', self.kicker_id), data))
            if self.phase in ('waiting', 'connected'):
                self.phase = 'loading'
        elif message_id == 0x10009 and len(payload) == 4:
            # 0050E4B9: completed kicker ID.
            replies.append(self.wrap(message_id, struct.pack('<i', self.kicker_id), data))
        elif message_id == 0x1000a and len(payload) == 20:
            # 0050F888: kicker ID and two clock doubles.
            replies.append(self.wrap(message_id, struct.pack('<i', self.kicker_id) + payload[4:], data))
            if self.phase != 'playing':
                self.phase = 'synchronized'
        elif message_id == 0x1000c and len(payload) == 4:
            # 0050E6F1 sets the roster's completed-sync bit (0x800). The
            # host waits for every peer, even after turn sync has completed.
            if struct.unpack('<i', payload)[0] == self.host_id:
                replies.append(self.wrap(message_id, struct.pack('<i', self.kicker_id), data))
                self.phase = 'playing'
        elif message_id == 0x1000d and len(payload) == 4:
            # 0050DB7D reads the completed kicker ID. WorldActionEndingSync
            # waits for all peers before the client sends FREE_SCORE_REQ.
            if struct.unpack('<I', payload)[0] == self.host_id:
                replies.append(self.wrap(message_id, struct.pack('<I', self.kicker_id), data))
                self.phase = 'finished'
        return replies

    def decide(self, now, position, ball):
        """Pure MVP policy: approach the ball, bounded to the observed field.

        Wire emission is enabled only after synchronization has been verified.
        This avoids sending guessed movement into a loading/ending scene.
        """
        if not math.isfinite(now) or any(not math.isfinite(v) for v in (*position, *ball)):
            raise ValueError('Non-finite bot observation')
        if self.phase != 'playing' or (self.last_time is not None and now <= self.last_time):
            return None
        self.last_time = now
        self.position = tuple(position)
        dx, dz = ball[0]-position[0], ball[2]-position[2]
        distance = math.hypot(dx, dz)
        if distance <= 4:
            return ('stop', self.position, (0.0, 0.0, 0.0))
        direction = (dx/distance, 0.0, dz/distance)
        return ('move', self.position, direction)
