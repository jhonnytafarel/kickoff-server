"""Ephemeral local Free Game rooms; no match simulation or multiplayer relay."""
import struct
import math
from characters import read_string


def decode_score(body, character_id, bot_ids=()):
    """Read the captured FREE_SCORE_REQ roster (00416667: 138 bytes/player)."""
    if len(body) < 8:
        raise ValueError('Truncated match results')
    division, count = struct.unpack_from('<ii', body)
    if division != 1 or not 1 <= count <= 3+len(bot_ids) or len(body) != 8 + count * 138:
        raise ValueError('Invalid local match result size/division')
    records = [body[8+i*138:8+(i+1)*138] for i in range(count)]
    ids = [struct.unpack_from('<i', record)[0] for record in records]
    if character_id not in ids or len(set(ids)) != count or not set(ids) <= {character_id, 3, 4, *bot_ids}:
        raise ValueError('Unknown or duplicate match player')
    for record in records:
        result, rating = struct.unpack_from('<if', record, 4)
        if result not in (2, 3, 4) or not math.isfinite(rating):
            raise ValueError('Invalid match score')
        # Practice has no progression payout. Never accept XP/GP or bonus bytes
        # supplied by the client; preserve rating and the actual action counters.
        if any(record[14:30]):
            raise ValueError('Client supplied match rewards')
    return division, records


def decode_create(body):
    # 0050D12F: division, level, name, mode, map, ball, password flag,
    # optional password, match count, join-level byte, optional min/max.
    if len(body) < 8:
        raise ValueError('Truncated room request')
    division, level = struct.unpack_from('<ii', body)
    name, offset = read_string(body, 8)
    if not name.strip() or len(name.encode('cp949')) > 100 or len(body) < offset+16:
        raise ValueError('Invalid room name or fields')
    mode, map_id, ball_id, password_flag = struct.unpack_from('<iiii', body, offset)
    offset += 16
    if password_flag not in (0, 1):
        raise ValueError('Invalid password flag')
    password = ''
    if password_flag:
        password, offset = read_string(body, offset)
    if len(body) < offset+5:
        raise ValueError('Missing room options')
    match_count, join_level = struct.unpack_from('<iB', body, offset)
    offset += 5
    minimum = maximum = 0
    if join_level == 1:
        if len(body) < offset+2:
            raise ValueError('Missing level bounds')
        minimum, maximum = struct.unpack_from('<BB', body, offset)
        offset += 2
    if offset != len(body) or join_level not in (0, 1) or minimum > maximum:
        raise ValueError('Invalid room options')
    if division != 1 or level not in (5, 10, 20, 99) or mode not in (2, 3):
        raise ValueError('Unsupported local division, level or game mode')
    # Handles present in Map/en-us/*.inf and Ball/en-us/*.inf.
    if not (1 <= map_id <= 13 and 1 <= ball_id <= 6 and 1 <= match_count <= 9):
        raise ValueError('Invalid map, ball or match count')
    return dict(division=division, level=level, name=name, mode=mode,
                map_id=map_id, ball_id=ball_id, password=password,
                match_count=match_count, join_level=join_level,
                minimum=minimum, maximum=maximum)


class RoomRegistry:
    def __init__(self, practice_bots=False):
        self.rooms = {}
        self.next_id = 1
        self.practice_bots = practice_bots

    def create(self, body, character, connection):
        room = decode_create(body)
        if character is None:
            raise ValueError('No local character selected')
        _, offset = read_string(character['payload'], 4)
        fields = struct.unpack_from('<9i', character['payload'], offset)
        self.remove_owner(connection)
        room.update(id=self.next_id, owner=connection, character_id=character['id'],
                    nickname=character['nickname'], position=fields[1], player_level=fields[2], team=1, slot=0,
                    ready=False, closed_slots=set())
        if room['mode'] == 3 and room['match_count'] == 2:
            room['closed_slots'].update(((1,2),(2,2)))
        if self.practice_bots and room['mode'] == 2:
            import bots
            room['bot'] = dict(character=bots.profile(character), team=2, slot=0,
                               position=fields[1], player_level=fields[2])
        self.next_id += 1
        self.rooms[room['id']] = room
        return room

    def owned(self, connection, division):
        for room in self.rooms.values():
            if room['owner'] == connection and room['division'] == division:
                return room
        raise ValueError('Connection is not in a local room')

    def change_team(self, room):
        team = 2 if room['team'] == 1 else 1
        bot = room.get('bot')
        occupied = (bot['team'], bot['slot']) if bot else None
        guests = {(g['team'],g['slot']) for g in room.get('guests',{}).values()}
        available = [slot for slot in range(3) if (team,slot) not in room['closed_slots'] and (team,slot) != occupied and (team,slot) not in guests]
        if not available:
            raise ValueError('All slots on the destination team are closed')
        room.update(team=team,slot=available[0],ready=False)

    def set_slot(self, room, team, slot, closed):
        if team not in (1,2) or slot not in range(3) or closed not in (0,1):
            raise ValueError('Invalid team, slot or close flag')
        if (team,slot) == (room['team'],room['slot']):
            raise ValueError('Cannot modify an occupied slot')
        if room.get('bot') and (team,slot) == (room['bot']['team'], room['bot']['slot']):
            raise ValueError('Cannot close the bot slot')
        if any((team,slot)==(g['team'],g['slot']) for g in room.get('guests',{}).values()):
            raise ValueError('Cannot close a test client slot')
        if closed:
            room['closed_slots'].add((team,slot))
        else:
            room['closed_slots'].discard((team,slot))
        return 6-len(room['closed_slots'])

    def remove_owner(self, connection):
        for key in [key for key, room in self.rooms.items() if room['owner'] == connection]:
            del self.rooms[key]

    def list(self, division, level, room_number):
        return [room for room in self.rooms.values()
                if room['division'] == division and room['level'] == level
                and (room_number == 0 or room['id'] == room_number)][:8]
