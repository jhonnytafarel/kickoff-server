"""Opt-in, loopback-only test client ingress; no account/inventory mutations.

Uses native SELECTJOIN 0x30004/0x30005. This separate ingress supplies a fixed
ephemeral test identity instead of pretending production authentication exists.
"""
import asyncio
import socket
import struct
import uuid
import bots
import netstate
import protocol
from characters import read_string

PEER_ID = 28674
PORT = 20002
PEER_NAMES = ('PeerTeste','PeerTeste2','PeerTeste3')


async def broadcast_guests(room, data):
    for guest in list(room.get('guests',{}).values()):
        writer = guest['writer']
        if not writer.is_closing():
            writer.write(data)
            await writer.drain()


async def serve(reader, writer, store, recorder):
    connection = uuid.uuid4().hex
    room = None
    if store.get() is None:
        writer.close()
        await writer.wait_closed()
        return
    if not hasattr(store,'test_peer_connections'):store.test_peer_connections={}
    peer_index = None
    character = None
    try:
        while True:
            head = await reader.readexactly(2)
            size = struct.unpack('<H',head)[0]
            if not 4 <= size <= 16384:raise ValueError('Invalid test peer frame size')
            raw = head+await reader.readexactly(size-2)
            frames = protocol.FrameDecoder().feed(raw)
            if not frames:continue
            kind,raw = frames[0]
            if kind != 2:continue
            mid = struct.unpack_from('<I',raw,4)[0]
            body = raw[8:-4]
            reply = None
            if mid == protocol.LOGIN_NAME_REQ:
                if character is not None:raise ValueError('Test peer already logged in')
                login,end=read_string(body,0)
                name=login.split(' ',1)[0]
                if end!=len(body) or name not in PEER_NAMES:raise ValueError('Unknown test identity')
                peer_index=PEER_NAMES.index(name)
                if peer_index in store.test_peer_connections:raise ValueError('Test identity already connected')
                store.test_peer_connections[peer_index]=connection
                character=bots.profile(store.get(),PEER_ID+peer_index,name)
                reply = protocol.login_ack(character)
            elif character is None:
                raise ValueError('Test peer login required')
            elif mid == 0x30000 and len(body)==16:
                division,level,number,filter_ = struct.unpack('<4i',body)
                reply = protocol.free_lobby_ack(division,level,filter_,store.rooms.list(division,level,number))
            elif mid == 0x30004:
                # 00507D51: division, room level, room number, password flag,
                # optional password string. 00446AE4 reads normal RoomBase/Extra.
                try:
                    if len(body)<16:raise ValueError('Truncated SELECTJOIN')
                    division,level,number,password_flag = struct.unpack_from('<4i',body)
                    password = ''
                    end=16
                    if password_flag==1:password,end=read_string(body,end)
                    if password_flag not in (0,1) or end!=len(body):raise ValueError('Invalid SELECTJOIN')
                    selected=store.rooms.rooms.get(number)
                    if room or selected is None or selected.get('playing'):
                        raise ValueError('Room missing, playing or peer already joined')
                    if selected['division']!=division or selected['level']!=level or selected['password']!=password:
                        raise ValueError('Wrong room division, level or password')
                    occupied={(selected['team'],selected['slot'])}
                    if selected.get('bot'):occupied.add((selected['bot']['team'],selected['bot']['slot']))
                    occupied.update((g['team'],g['slot']) for g in selected.get('guests',{}).values())
                    # Balance both teams. Three peers plus the human host
                    # produce 2v2, satisfying the native Common start checks.
                    teams=sorted((2,1),key=lambda t:sum(team==t for team,slot in occupied))
                    free=[(t,s) for t in teams for s in range(3)
                          if (t,s) not in occupied and (t,s) not in selected['closed_slots']]
                    if not free:raise ValueError('Room full')
                    host=store.room_writers.get(selected['owner'])
                    if host is None or host.is_closing():raise ValueError('Host disconnected')
                    room=selected
                    team,slot=free[0]
                    guest=dict(character=character,team=team,slot=slot,ready=True,
                               position=room['position'],player_level=room['player_level'],
                               endpoint=socket.inet_aton(netstate.CLIENT_IP)+(PORT+peer_index).to_bytes(2,'big'),
                               writer=writer)
                    room.setdefault('guests',{})[connection]=guest
                    base=protocol.free_create_ack(room)
                    reply=protocol.frame(0x30005,base[8:-4])
                    reply+=protocol.free_player_join_cast(room,store.get())
                    if room.get('bot'):reply+=protocol.free_player_join_cast(room,room['bot']['character'],bot=True)
                    for existing in room['guests'].values():
                        if existing is not guest:
                            reply+=protocol.free_player_join_cast(room,existing['character'],guest=existing)
                    joined=protocol.free_player_join_cast(room,character,guest=guest)
                    reply+=joined
                    host.write(joined)
                    await host.drain()
                    for existing in room['guests'].values():
                        if existing is not guest:
                            existing['writer'].write(joined)
                            await existing['writer'].drain()
                    recorder.write('test_peer_joined',connection=connection,room_id=number,character_id=character['id'],team=team,slot=slot)
                except ValueError as error:
                    reply=protocol.frame(0x30005,struct.pack('<i',0))
                    recorder.write('test_peer_join_rejected',error=str(error))
            elif mid==0x30009 and len(body)==4:
                return
            else:
                recorder.write('test_peer_unhandled',message_id=mid,hex=raw.hex())
            if reply:
                writer.write(reply)
                await writer.drain()
    except (asyncio.IncompleteReadError,ConnectionError,OSError,ValueError,struct.error) as error:
        recorder.write('test_peer_closed',connection=connection,error=str(error))
    finally:
        if room and connection in room.get('guests',{}):
            room['guests'].pop(connection)
            host=store.room_writers.get(room['owner'])
            if host is not None and not host.is_closing() and store.rooms.rooms.get(room['id']) is room:
                remaining=1+len(room['guests'])+int(bool(room.get('bot')))
                left=protocol.frame(0x3000b,struct.pack('<4i',room['division'],character['id'],remaining,-1))
                host.write(left)
                try:await host.drain()
                except (ConnectionError,OSError):pass
                try:await broadcast_guests(room,left)
                except (ConnectionError,OSError):pass
        if store.test_peer_connections.get(peer_index)==connection:
            store.test_peer_connections.pop(peer_index,None)
        writer.close()
        try:await writer.wait_closed()
        except (ConnectionError,OSError):pass
