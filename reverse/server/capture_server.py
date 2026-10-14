"""Loopback protocol research server with an experimental ephemeral login."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from urllib.parse import urlsplit, parse_qsl, urlencode
import struct
import sys
import importlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol
from characters import Store,CharacterError,read_string
from catalog import ITEMS
from rooms import RoomRegistry
import missions
import netstate
import relay
import messenger
import match_economy

_protocol_mtime = Path(protocol.__file__).stat().st_mtime_ns


def refresh_protocol():
    """Reload local response builders during protocol research without disconnecting."""
    global _protocol_mtime
    current = Path(protocol.__file__).stat().st_mtime_ns
    if current != _protocol_mtime:
        importlib.reload(protocol)
        _protocol_mtime = current

DEFAULT_LOG = Path(__file__).resolve().parents[1] / 'captures'


async def start_room(room, store, recorder):
    match_economy.start(room,store)
    if room.get('bot'):
        import bots
        store.relay.bots[bots.BOT_ID] = bots.Opponent(room['character_id'])
    reply = protocol.free_start_cast() + protocol.free_loadstart_cast(room)
    for guest in room.get('guests', {}).values():
        guest['writer'].write(protocol.free_start_cast() + protocol.free_loadstart_cast(room, guest['team']))
        await guest['writer'].drain()
    room['playing'] = True
    recorder.write('free_room_started', room_id=room['id'], map_id=room['map_id'], ball_id=room['ball_id'])
    return reply


class Recorder:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)

    def write(self, kind, **data):
        event = dict(time=datetime.now(timezone.utc).isoformat(), kind=kind, **data)
        with (self.output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event)+'\n')


async def capture_tcp(reader, writer, recorder, port, emulate=False, store=None):
    if store is not None and not hasattr(store, 'rooms'):
        store.rooms = RoomRegistry(practice_bots=getattr(store, 'practice_bots', False))
    if store is not None and not hasattr(store, 'live_writers'):
        store.live_writers = set()
    if store is not None and port == 20000:
        store.live_writers.add(writer)
    connection = uuid.uuid4().hex
    transfer_info_seen = None
    intro_character=store.get() if store else None
    intro_character_id=intro_character['id'] if intro_character else None
    if store is not None:
        if not hasattr(store,'room_writers'):store.room_writers={}
        store.room_writers[connection]=writer
    decoder = protocol.FrameDecoder()
    recorder.write('tcp_connect', connection=connection, port=port, peer=writer.get_extra_info('peername'))
    try:
        with (recorder.output / f'tcp-{port}-{connection}.bin').open('wb') as stream:
            while chunk := await reader.read(65536):
                stream.write(chunk)
                stream.flush()
                recorder.write('tcp_chunk', connection=connection, port=port, size=len(chunk), preview_hex=chunk[:64].hex())
                if emulate:
                    refresh_protocol()
                    for kind, raw in decoder.feed(chunk):
                        message_id = struct.unpack_from('<I', raw, 4)[0] if kind == 2 and len(raw) >= 8 else None
                        recorder.write('tcp_frame', connection=connection, port=port, frame_kind=kind, message_id=message_id, hex=raw.hex())
                        reply = None
                        if port == messenger.PORT and message_id == messenger.REGISTER and store:
                            reply, registration = messenger.bootstrap(raw[8:-4], store.get())
                            recorder.write('buddy_registered', connection=connection,
                                           character_id=registration['id'], contact_count=0)
                        elif message_id == protocol.FRONT_REQ:
                            reply = protocol.front_ack()
                        elif message_id == protocol.VERSION_REQ:
                            reply = protocol.version_ack()
                        elif message_id in (protocol.LOGIN_NAME_REQ, protocol.LOGIN_PASSPORT_REQ):
                            if store:intro_character_id=store.get()['id'] if store.get() else None
                            reply = protocol.login_ack(store.get() if store else None,len(store.list_characters()) if store else 0,store.balances() if store else None)
                        elif message_id == 0x20000:
                            if len(raw) != 16 or struct.unpack_from('<i', raw, 8)[0] != 0x10000:
                                raise ValueError('Unexpected account-close request')
                            # 00442EBD: 0x20000 completes shutdown without a notice dialog.
                            # The finally block removes roster and virtual peer
                            # together; early roster removal would orphan the bot.
                            reply = protocol.frame(0x20000, struct.pack('<i', 0x20000))
                            recorder.write('account_closed', connection=connection)
                        elif message_id == 0x20011 and store:
                            try:
                                character = store.create(raw[8:-4])
                            except (ValueError,UnicodeError) as error:
                                reply=protocol.create_error_ack(error.code if isinstance(error,CharacterError) else 2)
                                recorder.write('character_creation_rejected',error=str(error))
                            else:
                                recorder.write('character_created', id=character['id'], nickname=character['nickname'])
                                intro_character_id=character['id']
                                reply = protocol.create_ack(character,len(store.list_characters()))
                        elif message_id == 0x2000f and store:
                            if len(raw) != 12:
                                raise ValueError('Unexpected locker-init request size')
                            reply = protocol.locker_init_ack(store.get(),store.list_characters())
                            recorder.write('locker_initialized')
                        elif message_id == 0x20013 and store:
                            try:
                                if len(raw)<19:raise ValueError('Unexpected delete-character request size')
                                _,end=read_string(raw[8:-4],4)
                                if end!=len(raw)-12:raise ValueError('Trailing delete-character fields')
                                if any(room['owner']==connection for room in store.rooms.rooms.values()):raise ValueError('Leave the room before deleting a character')
                                deleted=struct.unpack_from('<i',raw,8)[0]
                                selected=store.delete(deleted)
                            except ValueError as error:
                                reply=protocol.frame(0x20014,struct.pack('<i',(10<<16)|1))
                                recorder.write('character_delete_rejected',error=str(error))
                            else:
                                reply=protocol.delete_character_ack(deleted,selected['id'] if selected else -1,len(store.list_characters()))
                                intro_character_id=selected['id'] if selected else None
                                recorder.write('character_deleted',character_id=deleted)
                        elif message_id in (0x20022,0x20024) and store:
                            if len(raw)!=20:
                                raise ValueError('Unexpected mission action size')
                            context,mission_id=struct.unpack_from('<ii',raw,8)
                            try:
                                store.select_mission(mission_id,abandon=message_id==0x20024)
                            except ValueError as error:
                                reply=protocol.frame(message_id+1,struct.pack('<i',0))
                                recorder.write('mission_rejected',mission_id=mission_id,error=str(error))
                            else:
                                character=store.get()
                                reply=protocol.kicker_info_ack(character,0xc0000)
                                body=struct.pack('<iii',-1,context,mission_id)
                                reply+=protocol.frame(message_id+1,body)
                                recorder.write('mission_selected' if message_id==0x20022 else 'mission_abandoned',mission_id=mission_id,context=context)
                        elif message_id == 0x20028 and store:
                            # Captured: int32 category, int32 mission, byte (no extra choice).
                            if len(raw) != 21:
                                raise ValueError('Unexpected mission-compensation request size')
                            category_id,mission_id = struct.unpack_from('<ii',raw,8)
                            try:
                                actual_category,_ = missions.mission_info(mission_id)
                                if category_id!=actual_category:raise ValueError('Wrong mission category')
                                rewards=store.claim_mission_reward(mission_id)
                            except ValueError as error:
                                reply = protocol.frame(0x20029,struct.pack('<i',0))
                                recorder.write('mission_reward_rejected',mission_id=mission_id,error=str(error))
                            else:
                                reply = protocol.frame(0x20029,missions.compensation_ack(mission_id,category_id,rewards))
                                recorder.write('mission_reward_claimed',mission_id=mission_id,rewards=rewards)
                        elif message_id == 0x20015 and store:
                            if len(raw) != 16:
                                raise ValueError('Unexpected select-character request size')
                            selected = struct.unpack_from('<i',raw,8)[0]
                            try:
                                if any(room['owner']==connection for room in store.rooms.rooms.values()):
                                    raise ValueError('Leave the room before switching character')
                                character=store.select(selected)
                            except ValueError:accepted=False
                            else:accepted=True;intro_character_id=selected
                            reply = protocol.select_character_ack(selected,accepted)
                            recorder.write('character_selected',character_id=selected,accepted=accepted)
                        elif message_id == 0x2004b and store and port==20000:
                            # 0050C4C6 writes one int32; there is no native ACK.
                            try:
                                if len(raw)!=16:raise ValueError('Unexpected introduction-step request size')
                                owner,changed=store.save_intro_step(struct.unpack_from('<i',raw,8)[0],intro_character_id)
                            except ValueError as error:
                                recorder.write('intro_step_rejected',error=str(error))
                            else:
                                recorder.write('intro_step_saved',character_id=owner,step=struct.unpack_from('<i',raw,8)[0],changed=changed)
                        elif message_id == 0x2004c:
                            reply = protocol.frame(0x2004d, struct.pack('<i', -1))
                        elif message_id == 0x3002c:
                            # 004462A2 reads result + four fixed maximum levels, not a list.
                            reply = protocol.frame(0x3002d, struct.pack('<iiiii', -1, 0, 0, 0, 99))
                        elif message_id == 0x2000b:
                            if len(raw) != 16 or struct.unpack_from('<I', raw, 8)[0] != 0x10:
                                raise ValueError('Unreconstructed user-info sections')
                            # 00442409: section 0x10 is an int32 count followed by records.
                            reply = protocol.inventory_ack(store.items() if store else [])
                        elif message_id == 0x2001b and store:
                            if len(raw)!=35:
                                raise ValueError('Unexpected item-buy request size')
                            goods_id,method,option=struct.unpack_from('<iii',raw,8)
                            selection=raw[20]
                            info_id=ITEMS.get(goods_id)
                            if info_id is None or not 1<=selection<=32767:
                                reply=protocol.frame(0x2001c,struct.pack('<i',0))
                                recorder.write('unsupported_item_purchase',goods_id=goods_id)
                            else:
                                try:
                                    item=store.buy_item(goods_id,info_id,selection,raw[21:-4],method=method,option=option)
                                except ValueError as error:
                                    reply=protocol.frame(0x2001c,struct.pack('<i',0))
                                    recorder.write('item_purchase_rejected',goods_id=goods_id,error=str(error))
                                else:
                                    reply=protocol.item_buy_ack(item,store.balances())
                                    recorder.write('item_purchased',goods_id=goods_id,slot=item['slot'],option=option,method=method,balances=store.balances())
                        elif message_id == 0x20017 and store:
                            try:
                                character=store.equip(raw[8:-4])
                            except ValueError as error:
                                reply=protocol.frame(0x20018,struct.pack('<i',0))
                                recorder.write('equipment_rejected',error=str(error))
                            else:
                                # The client clears mesh slots before sending wear.
                                # ACK reads only result. Unsolicited Gear/inventory
                                # replies break this UI. Requested section4 loads
                                # DefaultFeatures before native mesh removal.
                                reply=protocol.frame(0x20018,struct.pack('<i',-1))
                                recorder.write('equipment_saved',character_id=character['id'])
                        elif message_id == 0x2001d and store:
                            if len(raw) != 20:
                                raise ValueError('Unexpected skill-buy request size')
                            goods_id,method = struct.unpack_from('<ii',raw,8)
                            try:
                                character = store.buy_skill(goods_id,method)
                            except ValueError as error:
                                reply = protocol.frame(0x2001e,struct.pack('<i',0))
                                recorder.write('skill_purchase_rejected',goods_id=goods_id,error=str(error))
                            else:
                                reply = protocol.skill_buy_ack(goods_id,character['balances'])
                                recorder.write('skill_purchased',goods_id=goods_id,character_id=character['id'],balances=character['balances'])
                        elif message_id == 0x2001f and store:
                            try:
                                character = store.save_skills(raw[8:-4])
                            except ValueError as error:
                                reply = protocol.frame(0x20020,struct.pack('<i',0))
                                recorder.write('skill_save_rejected',error=str(error))
                            else:
                                reply = protocol.frame(0x20020,struct.pack('<i',-1))
                                recorder.write('skills_saved',character_id=character['id'])
                        elif message_id == 0x20044:
                            # 00443341: result, int32 inventory record count.
                            reply = protocol.frame(0x20045, struct.pack('<ii', -1, 0))
                        elif message_id == 0x20036:
                            if len(raw) != 16:
                                raise ValueError('Unexpected tutorial compensation request size')
                            tutorial_id = struct.unpack_from('<i', raw, 8)[0]
                            try:
                                first_completion = store.complete_tutorial(tutorial_id) if store else None
                            except ValueError as error:
                                reply = protocol.frame(0x20037,struct.pack('<i',0))
                                recorder.write('tutorial_rejected',tutorial_id=tutorial_id,error=str(error))
                            else:
                                reply = protocol.tutorial_compensation_ack(tutorial_id)
                                recorder.write('tutorial_completed', tutorial_id=tutorial_id,first_completion=first_completion)
                        elif message_id in (0x2002e,0x20030) and store:
                            scope = 'user' if message_id == 0x2002e else 'kicker'
                            expected = 4 if scope == 'user' else 12
                            body = raw[8:-4]
                            if len(body) != expected:
                                raise ValueError('Unexpected environment request size')
                            try:
                                store.save_environment(scope,body)
                            except ValueError as error:
                                result = 0
                                recorder.write('environment_rejected',scope=scope,error=str(error))
                            else:
                                result = -1
                                recorder.write('environment_saved',scope=scope,hex=body.hex())
                            # 0043F37F / 00444AAE read the environment before
                            # checking result; include it even on rejection.
                            reply = protocol.frame(message_id+1,struct.pack('<i',result)+body)
                        elif message_id == 0x20032:
                            if len(raw) != 26:
                                raise ValueError('Unexpected transfer request size')
                            mode, division = struct.unpack_from('<ii', raw, 8)
                            # A fresh transfer starts a fresh acknowledgement phase.
                            transfer_info_seen = None
                            # The client reports its own local IPv4 (e.g. 192.168.56.1); its UDP
                            # socket binds there and cannot send to 127.0.0.1 (WSAEADDRNOTAVAIL).
                            netstate.CLIENT_IP = '.'.join(str(b) for b in raw[16:20])
                            reply = protocol.transfer_ack(mode, division)
                        elif message_id == 0x60000 and store:
                            if len(raw) != 24:
                                raise ValueError('Unexpected relay-join request size')
                            mode,division,character_id = struct.unpack_from('<iii',raw,8)
                            character = store.get()
                            if character and character_id == character['id'] and mode == 1:
                                reply = protocol.relay_join_ack(mode,division,character_id)
                                recorder.write('local_relay_bootstrap',character_id=character_id,division=division)
                            else:
                                reply = protocol.frame(0x60001,struct.pack('<i',0))
                        elif message_id == 0x20034 and store:
                            # 004454D8: mode/division, two int16s, four endpoints.
                            if len(raw) != 48:
                                raise ValueError('Unexpected transfer-info request size')
                            mode,division = struct.unpack_from('<ii',raw,8)
                            signature = raw[8:-4]
                            # The original client can report the same endpoints three
                            # times after its relay probes. Repeated ACKs re-enter its
                            # FreeLobby state and cancel RequestKickerInfo before send.
                            if mode == 1 and signature == transfer_info_seen:
                                recorder.write('transfer_info_duplicate_ignored',connection=connection,
                                               port=port,mode=mode,division=division)
                            else:
                                reply = protocol.frame(0x20035,struct.pack('<i',-1 if mode == 1 else 0))
                                transfer_info_seen = signature if mode == 1 else None
                                recorder.write('transfer_info_received',mode=mode,division=division)
                        elif message_id == 0x30000 and store:
                            # 00507BF5: division, room level, room number, filter.
                            if len(raw) != 28:
                                raise ValueError('Unexpected free-lobby request size')
                            division,room_level,room_number,room_filter = struct.unpack_from('<iiii',raw,8)
                            reply = protocol.free_lobby_ack(division,room_level,room_filter,store.rooms.list(division,room_level,room_number))
                            recorder.write('free_lobby_requested',division=division,room_level=room_level,room_number=room_number,room_filter=room_filter)
                        elif message_id == 0x30002 and store:
                            try:
                                room = store.rooms.create(raw[8:-4],store.get(),connection)
                            except ValueError as error:
                                reply = protocol.frame(0x30003,struct.pack('<i',0))
                                recorder.write('free_room_rejected',error=str(error))
                            else:
                                # The room header describes the roster but has no
                                # CharacterBase/Gear. Publish the host's complete
                                # player record after transitioning into the room.
                                reply = (protocol.free_create_ack(room)
                                         +protocol.free_player_join_cast(room,store.get()))
                                if room.get('bot'):
                                    import bots
                                    reply += protocol.free_player_join_cast(room, room['bot']['character'], bot=True)
                                    store.relay.bots[bots.BOT_ID] = bots.Opponent(room['character_id'])
                                recorder.write('free_room_created',room_id=room['id'],name=room['name'],map_id=room['map_id'],ball_id=room['ball_id'])
                        elif message_id == 0x3001f and store:
                            # Captured: division + two zero int32. Success is announced
                            # by START_CAST (0044022 9); the ACK id is only the error path.
                            if len(raw) != 24:
                                raise ValueError('Unexpected room-start request size')
                            division = struct.unpack_from('<i',raw,8)[0]
                            try:
                                room = store.rooms.owned(connection,division)
                                reply = await start_room(room, store, recorder)
                            except ValueError as error:
                                reply = protocol.frame(0x30020,struct.pack('<ii',0,division))
                                recorder.write('free_room_start_rejected',error=str(error))
                        elif message_id == 0x30022 and store:
                            # Captured: division, 0x78, 1, kicker ID. 0043F734 reads two int32.
                            if len(raw) < 28:
                                raise ValueError('Unexpected free kicker-info request size')
                            division, sections, count = struct.unpack_from('<iii', raw, 8)
                            room = store.rooms.owned(connection, division)
                            character = store.get()
                            roster = {character['id']: character}
                            if room.get('bot'):
                                bot_character = room['bot']['character']
                                roster[bot_character['id']] = bot_character
                            for guest in room.get('guests',{}).values():
                                roster[guest['character']['id']]=guest['character']
                            if count < 1 or count > len(roster) or len(raw) != 24 + 4*count:
                                raise ValueError('Unknown loading roster')
                            ids = struct.unpack_from(f'<{count}i', raw, 20)
                            if len(set(ids)) != count or not set(ids) <= roster.keys():
                                raise ValueError('Unknown loading player')
                            reply = (protocol.frame(0x30023, struct.pack('<ii', -1, division))
                                     + b''.join(protocol.free_kicker_info_cast(division, roster[kicker], sections) for kicker in ids))
                            recorder.write('free_kickerinfo_requested',hex=raw[8:-4].hex())
                        elif message_id == 0x30027 and store:
                            from rooms import decode_score
                            try:
                                if len(raw) < 20:
                                    raise ValueError('Truncated match results')
                                division = struct.unpack_from('<i', raw, 8)[0]
                                room = store.rooms.owned(connection, division)
                                session=room.get('match_session')
                                if not session:raise ValueError('No started match')
                                bot_ids = session['participants']-{room['character_id'],3,4}
                                division, records = decode_score(raw[8:-4], room['character_id'], bot_ids)
                                records,payout=match_economy.finish(store,room,records)
                                # Keep the room/roster so the client can return and
                                # start again. Repeated results have no payout.
                                reply = protocol.free_score_ack(division, records)
                                room.update(playing=False, ready=False)
                                if room.get('guests'):
                                    from test_peer import broadcast_guests
                                    await broadcast_guests(room,reply)
                                scores = [dict(character_id=struct.unpack_from('<i', record)[0],
                                               goals=struct.unpack_from('<h', record, 30)[0],
                                               missed_shots=struct.unpack_from('<h', record, 32)[0])
                                          for record in records]
                                recorder.write('free_match_finished', room_id=room['id'], scores=scores,payout=payout)
                            except ValueError as error:
                                reply = protocol.frame(0x30028, struct.pack('<iiBii', 0, 1, 0, 0, 0))
                                recorder.write('free_match_result_rejected', error=str(error))
                        elif message_id in (0x30009,0x3000d,0x30010,0x30016,0x30019,0x30034) and store:
                            # Quit/team-change requests contain division only;
                            # Ready additionally carries a 32-bit boolean.
                            expected = 25 if message_id == 0x30034 else (16 if message_id in (0x30009,0x3000d) else 20)
                            if len(raw) != expected:
                                raise ValueError('Unexpected room-action request size')
                            division = struct.unpack_from('<i',raw,8)[0]
                            try:
                                room = store.rooms.owned(connection,division)
                                if room.get('playing') and message_id!=0x30009:
                                    raise ValueError('Room controls are locked during a match')
                                if message_id == 0x30034:
                                    team,slot,closed = struct.unpack_from('<iiB',raw,12)
                                    store.rooms.set_slot(room,team,slot,closed)
                                    reply = protocol.free_slot_cast(room,team,slot,closed)
                                    recorder.write('free_room_slot_changed',room_id=room['id'],team=team,slot=slot,closed=bool(closed))
                                elif message_id in (0x30016,0x30019):
                                    setting = 'map_id' if message_id == 0x30016 else 'ball_id'
                                    handle = struct.unpack_from('<i',raw,12)[0]
                                    maximum = 13 if setting == 'map_id' else 6
                                    if not 1 <= handle <= maximum:
                                        raise ValueError('Unknown local map/ball handle')
                                    was_ready = room['ready']
                                    room[setting] = handle
                                    room['ready'] = False
                                    reply = protocol.free_setting_cast(room,setting)
                                    if was_ready:
                                        reply += protocol.free_ready_cast(room)
                                    recorder.write('free_room_setting_changed',room_id=room['id'],setting=setting,handle=handle)
                                elif message_id == 0x30010:
                                    ready = struct.unpack_from('<i',raw,12)[0]
                                    if ready not in (0,1):
                                        raise ValueError('Invalid ready state')
                                    room['ready'] = bool(ready)
                                    reply = protocol.free_ready_cast(room)
                                    recorder.write('free_room_ready',room_id=room['id'],ready=room['ready'])
                                elif message_id == 0x3000d:
                                    store.rooms.change_team(room)
                                    reply = protocol.free_team_cast(room)
                                    recorder.write('free_room_team_changed',room_id=room['id'],team=room['team'])
                                else:
                                    reply = protocol.free_quit_cast(room)
                                    for guest in list(room.get('guests',{}).values()):guest['writer'].close()
                                    if room.get('bot'):
                                        store.relay.bots.pop(room['bot']['character']['id'], None)
                                    store.rooms.remove_owner(connection)
                                    recorder.write('free_room_left',room_id=room['id'])
                            except ValueError as error:
                                reply = protocol.frame(message_id+1,struct.pack('<ii',0,division))
                                recorder.write('free_room_action_rejected',message_id=message_id,error=str(error))
                        elif message_id == 0x2000d and store:
                            if len(raw) != 20:
                                raise ValueError('Unexpected character-info request size')
                            flags, character_id = struct.unpack_from('<ii', raw, 8)
                            character = store.get(character_id=character_id)
                            if character:
                                reply = protocol.kicker_info_ack(character, flags)
                            else:
                                reply=protocol.frame(0x2000e,struct.pack('<i',0x00030001))
                        if reply:
                            writer.write(reply)
                            await writer.drain()
                            recorder.write('tcp_send', connection=connection, port=port, hex=reply.hex())
                            if message_id == 0x20000:
                                return
                        elif message_id is not None:
                            recorder.write('unhandled_request',connection=connection,port=port,message_id=message_id,hex=raw.hex())
    except (ConnectionError, OSError, ValueError) as error:
        recorder.write('tcp_error', connection=connection, error=str(error))
    finally:
        if store is not None and hasattr(store,'room_writers'):
            store.room_writers.pop(connection,None)
        if store is not None and hasattr(store, 'live_writers'):
            store.live_writers.discard(writer)
        if store is not None and hasattr(store, 'rooms'):
            for room in list(store.rooms.rooms.values()):
                if room['owner']==connection and room.get('guests'):
                    for guest in list(room['guests'].values()):guest['writer'].close()
                if room['owner'] == connection and room.get('bot'):
                    store.relay.bots.pop(room['bot']['character']['id'], None)
            store.rooms.remove_owner(connection)
        recorder.write('tcp_close', connection=connection, port=port)
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


class CaptureUDP(asyncio.DatagramProtocol):
    """Match transport: records datagrams and answers them as the UDP relay
    (probe, session open, heartbeat, relay join); see relay.py."""
    SAVED_FILES_PER_PORT = 200

    def __init__(self, recorder, port, relay_state=None):
        self.recorder, self.port = recorder, port
        self.relay = relay_state if relay_state is not None else relay.Relay()
        self.transport, self.saved = None, 0
        self.bot_messages_seen = set()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        name = None
        if self.saved < self.SAVED_FILES_PER_PORT:
            name = f'udp-{self.port}-{uuid.uuid4().hex}.bin'
            (self.recorder.output / name).write_bytes(data)
            self.saved += 1
        self.recorder.write('udp_datagram', port=self.port, peer=addr, file=name, size=len(data), preview_hex=data[:64].hex())
        try:
            replies, event = self.relay.handle(data, addr)
        except (ValueError, struct.error) as error:
            self.recorder.write('udp_error', port=self.port, peer=addr, error=str(error))
            return
        self.recorder.write('udp_relay', port=self.port, peer=addr, event=event, replies=len(replies))
        # Preserve one full sample of each bot P2P type even after the raw
        # datagram quota. Movement/heartbeat traffic otherwise consumes the
        # quota before Quit, possession or goal messages arrive.
        inner = data[16:] if len(data) >= 20 and struct.unpack_from('<H', data, 2)[0] == relay.KIND_ENVELOPE else data
        if len(inner) >= 60 and struct.unpack_from('<H', inner, 2)[0] == relay.KIND_DATA:
            sender, target = struct.unpack_from('<ii', inner, 4)
            bot = self.relay.bots.get(target)
            mid = bot.last_message_id if bot is not None else None
            key = (sender, target, mid)
            if mid is not None and 0x10000 <= mid <= 0x100ff and key not in self.bot_messages_seen:
                self.bot_messages_seen.add(key)
                self.recorder.write('bot_p2p_sample', port=self.port, peer=addr,
                                    sender=sender, target=target, message_id=mid, hex=data.hex())
        if self.transport is None:
            return
        for datagram, target in replies:
            self.transport.sendto(datagram, target)
            self.recorder.write('udp_send', port=self.port, peer=target, hex=datagram[:96].hex())


async def gm_mission_success(store, recorder, query):
    """Loopback-only test hook: pretend a match just completed a mission."""
    values = dict(parse_qsl(query))
    mission_id = int(values.get('id', '0'))
    store.mark_mission_success(mission_id)
    # 0044492D logs the first int32 as the category ID, the second as the mission.
    category_id, _ = missions.mission_info(mission_id)
    frame = protocol.frame(0x20026, struct.pack('<ii', category_id, mission_id))
    sent = 0
    for live in list(store.live_writers):
        live.write(frame)
        await live.drain()
        sent += 1
    recorder.write('gm_mission_success', mission_id=mission_id, sessions=sent)
    return f'mission {mission_id} marked successful; pushed to {sent} session(s)'.encode()


async def capture_http(reader, writer, recorder, auth_response, store=None):
    try:
        head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=10)
        request_line = head.split(b'\r\n', 1)[0].decode('iso-8859-1')
        method, target, _ = request_line.split(' ', 2)
        url = urlsplit(target)
        params = [(key, '<redacted>' if key.casefold() in ('pw', 'password', 'pass', 'token') else value)
                  for key, value in parse_qsl(url.query, keep_blank_values=True)]
        recorder.write('http_request', method=method, path=url.path, query=urlencode(params))
        if url.path == '/gm/mission_success' and store is not None:
            try:
                status, body = '200 OK', await gm_mission_success(store, recorder, url.query)
            except ValueError as error:
                status, body = '400 Bad Request', str(error).encode()
        elif url.path == '/login/game_auth.php':
            if auth_response is None:
                status, body = '503 Service Unavailable', b'Authentication response not reconstructed yet.'
            else:
                status, body = '200 OK', Path(auth_response).read_bytes()
        elif url.path in ('/', '/kickoff/LauncherNotice.html', '/game_notice/'):
            status, body = '200 OK', b'<html><body>KickOff local research server</body></html>'
        else:
            status, body = '404 Not Found', b'No local response available.'
        recorder.write('http_response', path=url.path, status=status, size=len(body))
        writer.write(f'HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\nContent-Type: text/html; charset=utf-8\r\n\r\n'.encode()+body)
        await writer.drain()
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, OSError) as error:
        recorder.write('http_error', error=str(error))
    finally:
        writer.close()
        await writer.wait_closed()


async def run(args):
    recorder = Recorder(args.output)
    store = Store(args.database, starter_items=True) if args.emulate_front else None
    if store is not None:
        store.practice_bots = getattr(args, 'practice_bots', False)
        store.relay = relay.Relay()
        store.rooms = RoomRegistry(practice_bots=store.practice_bots)
        store.room_writers = {}
    servers, transports = [], []
    try:
        if getattr(args,'test_client',False) and store is not None:
            import test_peer
            servers.append(await asyncio.start_server(lambda r,w:test_peer.serve(r,w,store,recorder),'127.0.0.1',test_peer.PORT))
        servers.append(await asyncio.start_server(lambda r, w: capture_http(r, w, recorder, args.auth_response, store), '127.0.0.1', args.http_port))
        loop = asyncio.get_running_loop()
        tcp_addresses = ['127.0.0.1'] + ([netstate.CLIENT_IP] if netstate.CLIENT_IP != '127.0.0.1' else [])
        for port in (19998, 19999, 20000):
            # TCP also on CLIENT_IP: the client reports the local address of
            # its game connection as its public match address (0x20034).
            for address in tcp_addresses:
                try:
                    servers.append(await asyncio.start_server(lambda r, w, p=port: capture_tcp(r, w, recorder, p, args.emulate_front, store), address, port))
                except OSError as error:
                    print(f'Cannot listen on TCP {address}:{port}: {error}', flush=True)
            # UDP 20000 on CLIENT_IP belongs to the client's match socket.
            transport, _ = await loop.create_datagram_endpoint(lambda p=port: CaptureUDP(recorder, p), local_addr=('127.0.0.1', port))
            transports.append(transport)
        # The client cannot send from that socket to 127.0.0.1 (error 10049),
        # so the UDP relay listens on CLIENT_IP with its own port.
        relay_addresses = tcp_addresses
        relay_state = store.relay if store is not None else relay.Relay()
        for address in relay_addresses:
            try:
                # TCP fallback of the relay (0x60000 when UDP probes get no answer).
                servers.append(await asyncio.start_server(lambda r, w: capture_tcp(r, w, recorder, netstate.RELAY_PORT, args.emulate_front, store), address, netstate.RELAY_PORT))
                transport, _ = await loop.create_datagram_endpoint(lambda: CaptureUDP(recorder, netstate.RELAY_PORT, relay_state), local_addr=(address, netstate.RELAY_PORT))
                transports.append(transport)
            except OSError as error:
                print(f'Cannot listen on UDP {address}:{netstate.RELAY_PORT}: {error}', flush=True)
        print(f'Listening: HTTP {args.http_port} (loopback); TCP {tcp_addresses} / UDP loopback 19998, 19999, 20000. UDP relay {relay_addresses}:{netstate.RELAY_PORT}. Logs: {args.output}', flush=True)
        print('Experimental front/version/local-account login enabled.' if args.emulate_front else 'Capture only; protocol responses disabled.', flush=True)
        await asyncio.Future()
    finally:
        for transport in transports:
            transport.close()
        for server in servers:
            server.close()
            await server.wait_closed()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http-port', type=int, default=18080)
    parser.add_argument('--output', type=Path, default=DEFAULT_LOG)
    parser.add_argument('--auth-response', type=Path, help='Explicit experimental response file; no default success response')
    parser.add_argument('--emulate-front', action='store_true', help='Respond to front/version/login using the ephemeral research account')
    parser.add_argument('--practice-bots', action='store_true', help='Experimental virtual opponent in Practice rooms; client validation required')
    parser.add_argument('--test-client', action='store_true', help='Loopback-only separate TCP test peer ingress (20002); not production authentication')
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parent / 'data/kickoff.sqlite3')
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
