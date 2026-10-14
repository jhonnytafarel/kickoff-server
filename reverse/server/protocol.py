"""KickOff framing observed on the local TCP front connection."""
import socket
import struct
import netstate

FRONT_REQ = 0x20002
FRONT_ACK = 0x20003
VERSION_REQ = 0x20004
VERSION_ACK = 0x20005
LOGIN_NAME_REQ = 0x20006
LOGIN_PASSPORT_REQ = 0x20007
LOGIN_ACK = 0x20008
SANDBOX_BALANCE = 999_999_999
# Positive Unix deadline below the old client's signed 32-bit date limit.
SANDBOX_EXPIRES_AT = 2_114_380_800  # 2037-01-01 UTC; local test items.


def checksum(data):
    """MSB-first CRC-32, polynomial 04C11DB7, initial zero, no final xor.

    Reconstructed from Game.exe 0060994A.
    """
    value = 0
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
    return value


def frame(message_id, payload=b''):
    size = 12 + len(payload)
    if size > 65535:
        raise ValueError('Frame exceeds uint16 size')
    data = struct.pack('<HHI', size, 2, message_id) + payload
    return data + struct.pack('<I', checksum(data))


def front_ack(port=20000):
    return frame(FRONT_ACK, struct.pack('<i', -1) + socket.inet_aton(netstate.TCP_IP) + struct.pack('!H', port))


def version_ack():
    return frame(VERSION_ACK, struct.pack('<i', -1))


def login_ack(character=None,character_count=None,balances=None):
    """Experimental local account with no kicker; fields from 005D49EE.

    This research account is ephemeral. No original account service is queried.
    """
    name = b'localtest\x00'
    user_flags=character.get('user_environment_flags',1) if character else 1
    onboarding=character.get('onboarding') if character else None
    if onboarding:
        # 004F44ED: account bit1 bypasses the first-mission decision. Clear
        # only that bit for an unfinished avatar; preserve unrelated user bits.
        user_flags=(user_flags|1) if onboarding['flags'] & 2 else (user_flags & ~1)
    payload = struct.pack('<iiI', -1, 1, user_flags)  # success, recommendation handled, UserEnvironment
    # UserState +0x34 = AccountInfo +0x28, the third integer (004F425B).
    # 00855D03 / 00855D2A: AccountInfo +0x20 Cash, +0x24 GamePoint.
    # 00855D51/00855D7C: count and available slots are separate fields.
    balance=balances or (character or {}).get('balances')
    if balance is None:
        from economy import Policy
        settings=Policy().data
        balance=dict(cash=settings['starting_cash'],game_points=settings['starting_game_points'])
    payload += struct.pack('<H', len(name)) + name + struct.pack('<iiii', balance['cash'], balance['game_points'], character_count if character_count is not None else int(character is not None), 3)
    payload += struct.pack('<HHii', 0, 0, 1, character['id'] if character else -1)
    payload += netstate.tcp_endpoint()
    return frame(LOGIN_ACK, payload)


def create_ack(character,character_count=1):
    return frame(0x20012, struct.pack('<i', -1) + character['payload'] + struct.pack('<ii', character['id'], character_count))


def locker_init_ack(character=None,characters=None):
    # 0044E5BE: result, selected ID, count; CharacterBase + Gear per entry.
    selected = character['id'] if character else -1
    roster=characters if characters is not None else ([character] if character else [])
    if len(roster)>3:raise ValueError('Native character roster exceeds three slots')
    return frame(0x20010,struct.pack('<iii',-1,selected,len(roster))
                 +b''.join(c['payload'] for c in roster))

def create_error_ack(code=2):
    # StringTable loader005012ED combines (KickerCreate9 <<16) | MsgID.
    return frame(0x20012,struct.pack('<i',(9<<16)|code))

def delete_character_ack(deleted_id,selected_id,count):
    # 0044C946/954/962: deleted ID, selected ID (-1 if empty), new count.
    return frame(0x20014,struct.pack('<iiii',-1,deleted_id,selected_id,count))


def select_character_ack(character_id, accepted=True):
    # 00442F6C reads only result and, on success, selected character ID.
    return frame(0x20016,struct.pack('<ii',-1,character_id) if accepted
                 else struct.pack('<i',0))


def tutorial_compensation_ack(tutorial_id):
    # 0044F1BF: result, tutorial ID, item count. Zero items takes the
    # completion branch at 0044F265 and reads no further reward fields.
    return frame(0x20037, struct.pack('<iii', -1, tutorial_id, 0))


def tutorial_progress_record(completed):
    # 0044E021/0044E061 and 0085992F use tutorial ID directly as bit index.
    bits = bytearray(128)
    for tutorial_id in completed:
        if not 0 <= tutorial_id < 1024:
            raise ValueError('Tutorial ID exceeds bitfield')
        bits[tutorial_id >> 3] |= 1 << (tutorial_id & 7)
    return struct.pack('<iI', max(completed, default=-1), len(bits)) + bits


def skill_locker_record(character):
    # 0041E3B3: three shorts and count records read by 0040FFE0 (8 bytes).
    skills = character.get('skills', [])
    maximum = 5 if skills else 0
    used = sum(skill['group_id'] != -1 for skill in skills)
    return struct.pack('<hhh',used,maximum,len(skills)) + b''.join(
        struct.pack('<ihh',skill['skill_id'],skill['group_id'],skill['sort']) for skill in skills)


def skill_buy_ack(goods_id,balances):
    # 00442A0B: result, goods ID, GP, Cash. No trailing item record.
    return frame(0x2001e,struct.pack('<iiii',-1,goods_id,balances['game_points'],balances['cash']))


def item_record(item):
    # 00420274/004201F3: both flags use int32 (read +0x94/write +0x50).
    # Info(id,int16), kicker, slot, int32, uint64, int16, int32, int16,
    # then two ability(id,byte) pairs: 42 bytes per item.
    # 004CA3E9 compares CurrentTime against Item+0x2C (wire +26),
    # and treats zero Item+0x30 (wire +30) as expired. The 64-bit
    # Item+0x20 field (wire +16) is NOT the expiry date.
    return struct.pack('<ihihiQhih',item['info_id'],item['selection'],item.get('kicker_id',-1),
                       item['slot'],2,item['slot']+1,1,SANDBOX_EXPIRES_AT,1)+bytes(item['abilities'])


def item_buy_ack(item,balances=None):
    # 00442758: result, goods ID, GamePoint, CashPoint, Item record.
    if balances is None:
        from economy import Policy
        settings=Policy().data
        balances=dict(game_points=settings['starting_game_points'],cash=settings['starting_cash'])
    return frame(0x2001c,struct.pack('<iiii',-1,item['goods_id'],balances['game_points'],balances['cash'])+item_record(item))


def inventory_ack(items):
    return frame(0x2000c,struct.pack('<iii',-1,0x10,len(items))+b''.join(map(item_record,items)))


def transfer_ack(mode, division):
    # 00444C57: result, mode, division, transport byte/address twice.
    # Game server, then the relay server: the client sends UDP probes and the
    # session open there (004F8FC3) and, if those fail for 5 s, connects by
    # TCP to the same address for 0x60000. The relay listens on both.
    # The UDP probes go to the FIRST endpoint (trace_udp2.log), the TCP
    # fallback to the second; both are the relay.
    endpoint = netstate.relay_endpoint()
    return frame(0x20033, struct.pack('<iiiB', -1, mode, division, 2) + endpoint + b'\x02' + endpoint)


def relay_join_ack(mode, division, character_id):
    # 004452BD: result, mode, division, kicker, server index, transport,
    # NAT endpoint. Local bootstrap only; this is not a packet relay service.
    return frame(0x60001,struct.pack('<iiiihh',-1,mode,division,character_id,1,2)+netstate.relay_endpoint())


def wire_string(value):
    data = value.encode('cp949') + b'\x00'
    return struct.pack('<H', len(data)) + data


def free_room_record(room):
    # 00853DD5 / 008539C0. Six slots: nickname, team, position, level.
    player_count = 1 + len(room.get('guests', {})) + int(bool(room.get('bot')))
    data = wire_string(room['name']) + struct.pack('<ihhiiiiBBB',
        room['id'], player_count, 6-len(room['closed_slots']), room['mode'], 1, room['match_count'],
        int(bool(room['password'])), room['join_level'], room['minimum'], room['maximum'])
    # Three Home slots followed by three Away slots; slot index zero per team.
    for index in range(6):
        guest = next((g for g in room.get('guests',{}).values()
                      if index == g['slot']+(0 if g['team']==1 else 3)),None)
        if guest:
            data += wire_string(guest['character']['nickname']) + struct.pack('<iii',guest['team'],guest['position'],guest['player_level'])
        elif index == room['slot']+(0 if room['team'] == 1 else 3):
            data += wire_string(room['nickname']) + struct.pack('<iii', room['team'], room['position'], room['player_level'])
        elif room.get('bot') and index == room['bot']['slot'] + (0 if room['bot']['team'] == 1 else 3):
            bot = room['bot']
            data += wire_string(bot['character']['nickname']) + struct.pack('<iii', bot['team'], bot['position'], bot['player_level'])
        else:
            # Empty-slot constructor 0085386D: team 7, position 1, level 0.
            team,slot = (1 if index < 3 else 2),index%3
            empty_team = 6 if (team,slot) in room['closed_slots'] else 7
            data += wire_string('') + struct.pack('<iii', empty_team, 1, 0)
    return data


def free_create_ack(room):
    # 004467E9: result, division, RoomBase, RoomExtra, NAT endpoint.
    extra = struct.pack('<iii', room['id'], room['level'], room['character_id'])
    extra += wire_string(room['password']) + struct.pack('<ii', room['map_id'], room['ball_id'])
    endpoint = netstate.relay_endpoint()
    return frame(0x30003, struct.pack('<ii', -1, room['division']) + free_room_record(room) + extra + endpoint)


def free_player_join_cast(room, character, bot=False, guest=None):
    # 0043F3F7: division, team, slot, ready; NetInfo (00854D80),
    # ObjectInfo (004131DF), CharacterBase, Gear, mix base and mix abilities.
    endpoint = guest['endpoint'] if guest else netstate.relay_endpoint() if bot else netstate.client_endpoint()
    network = endpoint*5 + struct.pack('<hhii', 1, 2, 0, 0)
    # ObjectInfo constructor 0041311F: class/type 0x1044, flags zero.
    object_info = struct.pack('<ii', 0x1044, 0)
    mix = bytes(20) + struct.pack('<hfiiiii', 0, 0.0, 0, 0, 0, 0, 0)
    entry = guest if guest else room['bot'] if bot else room
    payload = struct.pack('<iiii', room['division'], entry['team'], entry['slot'], 1 if bot else int(entry['ready']))
    return frame(0x30008, payload + network + object_info + character['payload'] + mix)


def free_lobby_ack(division, room_level, room_filter, rooms=()):
    # 004464C8: result, division, filter, room level, count, room records.
    return frame(0x30001,struct.pack('<iiiii',-1,division,room_filter,room_level,len(rooms))
                 + b''.join(free_room_record(room) for room in rooms))


def free_team_cast(room):
    # 004400AF consumes division, kicker ID, team, slot. 00520989 uses
    # zero-based slots and adds three for Away's index in the six-slot array.
    return frame(0x3000f,struct.pack('<iiii',room['division'],room['character_id'],room['team'],room['slot']))


def free_ready_cast(room):
    # 0043FF4D: division, kicker ID, ready int32. Success uses CAST; ACK
    # 004477F0 is the error path and always opens an announcement.
    return frame(0x30012,struct.pack('<iii',room['division'],room['character_id'],int(room['ready'])))


def free_quit_cast(room):
    # 004471F4: division, kicker ID, remaining count, reason. -1 follows
    # normal leave at 00447492; 0x140026 is a different transfer path.
    return frame(0x3000b,struct.pack('<iiii',room['division'],room['character_id'],0,-1))


def free_setting_cast(room, setting):
    # 00447A9A / 00447CFE: division and selected handle, no result prefix.
    message_id = 0x30018 if setting == 'map_id' else 0x3001b
    return frame(message_id,struct.pack('<ii',room['division'],room[setting]))


def free_start_cast():
    # 00440229 reads no fields; it only starts the countdown/transition effects.
    return frame(0x30021)


def free_kicker_info_cast(division, character, requested):
    # 0043F760 strips division and forwards UpdateKicker to 0051FD66.
    # That reader consumes kicker ID, section mask, then the same sections
    # as KickerInfo (without the result prefix). Loading requests 0x78:
    # Stats, skill locker, home and away match statistics.
    info = kicker_info_ack(character, requested)
    return frame(0x30024, struct.pack('<i', division) + info[12:-4])


MATCH_SECONDS = 300
EXTRA_SECONDS = 60  # Local Common test setting; original service value unknown.
DEFAULT_GOALKEEPER = 1  # Npc/en-us/GoalKeeper/GoalKeeper.xml, Basic ID.


def free_loadstart_cast(room, own_team=None):
    # 00448363 / 0085BD01: result then eleven int32: match type, mode, map,
    # ball, master kicker, attacking team, match time, extra time, home and
    # away goalkeeper, own team (log string 0091F820).
    return frame(0x30038, struct.pack('<12i', -1, 1, room['mode'], room['map_id'], room['ball_id'],
                                      room['character_id'], 1, MATCH_SECONDS, EXTRA_SECONDS if room['mode']==3 else 0,
                                      DEFAULT_GOALKEEPER, DEFAULT_GOALKEEPER, room['team'] if own_team is None else own_team))


def free_slot_cast(room, team, slot, closed):
    # 00449359: division, team, slot, close byte, resulting player limit.
    return frame(0x30036,struct.pack('<iiiBi',room['division'],team,slot,closed,6-len(room['closed_slots'])))


def free_score_ack(division, records):
    # 004406FB..00440741: result, division, bonus-enabled byte, bonus value,
    # count. 00416667 then consumes each packed 138-byte score record.
    # Local Practice returns the submitted action counters without XP/GP.
    return frame(0x30028, struct.pack('<iiBii', -1, division, 0, 0, len(records))
                 + b''.join(records))


def kicker_info_ack(character, requested):
    # Only sections reconstructed from 0044CD39; no invented trailing fields.
    flags = requested & 0x3f0007
    # Shop query 0x5818: stats, locker header, records, rank and performance.
    flags |= requested & 0x5b78
    data = struct.pack('<iii', -1, character['id'], flags)
    if flags & 0x100000:
        state=character.get('onboarding',{'flags':2,'observer':0})
        data += struct.pack('<II', state['flags'], state['observer'])
    if flags & 1:
        data += character['payload'][:character['base_end']]
    if flags & 2:
        data += character['payload'][character['base_end']:]
    if flags & 4:
        # DefaultFeatures: 0041555F reads Hair/Face/Coat/Hand/Trousers/FootWear;
        # 0044D0D4 stores them at Character+0x50. Native unmount restores these
        # via Avatar+0x54..0x90 (00434AC5). Empty sentinels make the avatar vanish.
        from character_profiles import gear_parts, BASE_SLOTS
        starter=character.get('initial_gear') or character['payload'][character['base_end']:]
        _,_,parts=gear_parts(starter)
        data += b''.join(struct.pack('<ih',*parts[slot]) for slot in BASE_SLOTS.values())
    if flags & 8:
        data += character.get('stats') or bytes(44)  # 004185F6 reads a fixed 0x2c byte block.
    if flags & 0x10:
        data += skill_locker_record(character)
    # Locker Room asks 0x360. These empty match-statistic sections clear
    # the corresponding dirty bits; omitting them causes a request loop.
    # 00417321: 28 int32/float fields and two int16 fields (116 bytes).
    if flags & 0x20:
        data += bytes(116)
    if flags & 0x40:
        data += bytes(116)
    if flags & 0x4000:
        data += bytes(88)  # 004179A2: twenty-two 32-bit numeric fields.
    # 00418206: 33 int16 fields, also zero in the client constructor.
    if flags & 0x100:
        data += bytes(66)
    if flags & 0x200:
        data += bytes(66)
    if flags & 0x800:
        h=character.get('match_history',{})
        data += struct.pack('<5i',h.get('played',0),h.get('wins',0),h.get('draws',0),0,0)
    if flags & 0x1000:
        data += struct.pack('<hfiiiii', 0, 0.0, 0, 0, 0, 0, 0)  # 00419D9E.
    if flags & 0x10000:
        from missions import category_record
        data += category_record()
    if flags & 0x20000:
        # 00419628 / 005FAD7C: highest populated index, byte length,
        # fixed 1024-bit buffer allocated by 0041A4B7 / 005FAC81.
        data += struct.pack('<iI', -1, 128) + bytes(128)
    if flags & 0x40000:
        from missions import detail_record
        # Only missions still in play are "assigned"; collected ones are not.
        data += detail_record({i:s for i,s in character.get('missions',{}).items() if s in (2,3)})
    if flags & 0x80000:
        from missions import progress_record
        data += progress_record(character.get('missions',{}))
    if flags & 0x200000:
        data += tutorial_progress_record(character.get('tutorials', []))
    return frame(0x2000e, data)


class FrameDecoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while len(self.buffer) >= 4:
            size, kind = struct.unpack_from('<HH', self.buffer)
            if size < 4:
                raise ValueError(f'Invalid frame length {size}')
            if len(self.buffer) < size:
                break
            raw = bytes(self.buffer[:size])
            if kind == 2 and (size < 12 or checksum(raw[:-4]) != struct.unpack_from('<I', raw, size - 4)[0]):
                raise ValueError('Invalid data-frame checksum')
            del self.buffer[:size]
            frames.append((kind, raw))
        return frames
