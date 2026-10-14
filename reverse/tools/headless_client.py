"""Independent local TCP/UDP peer for room/join/sync/quit interoperability tests.

No renderer; optional --ai issues experimental gameplay requests. Ingress is loopback-only;
production account authentication and remote human clients are separate work.
"""
import asyncio
import argparse
import json
from pathlib import Path
import socket
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import bots
import netstate
import protocol
import relay
import test_peer
import bot_ai
from characters import read_string


class UDPClient(asyncio.DatagramProtocol):
    def __init__(self, peer_index=0):
        self.peer_id=test_peer.PEER_ID+peer_index
        self.port=test_peer.PORT+peer_index
        self.state=relay.Relay()
        self.transport=None
        self.host_id=None
        self.ai=bot_ai.Controller(self.peer_id)
        self.host_addr=None
        self.ai_enabled=False
        self.timer=None
        self.events_seen=set()
        self.requests_seen=set()
        self.udp_events_seen=set()
    def connection_made(self,transport):self.transport=transport
    def enable_ai(self):
        self.ai_enabled=True
        self.timer=asyncio.get_running_loop().call_later(.1,self.tick)
    def tick(self):
        if not self.ai_enabled:return
        try:
            peer=self.state.bots.get(self.peer_id)
            if peer and self.host_addr:
                for packet in self.ai.actions(peer):
                    self.transport.sendto(packet,self.host_addr)
                    mid,payload=bots.decode_stream(packet)
                    if mid not in self.requests_seen:
                        self.requests_seen.add(mid)
                        print('AI request',hex(mid),'payload',payload.hex(),flush=True)
        except (ValueError,struct.error) as error:print('AI error',str(error),flush=True)
        self.timer=asyncio.get_running_loop().call_later(.1,self.tick)
    def stop_ai(self):
        self.ai_enabled=False
        if self.timer:self.timer.cancel()
    def datagram_received(self,data,addr):
        try:
            kind,_=relay.decode(data)
            # This socket is a player endpoint, never a relay server. Native
            # relay discovery also reaches nearby UDP ports; replying to its
            # session/join requests produces duplicate TRANS_INFO transitions.
            if kind in (relay.KIND_MESSAGE,relay.KIND_SESSION):return
            inner=data[16:] if kind==relay.KIND_ENVELOPE else data
            if len(inner)>=60 and struct.unpack_from('<H',inner,2)[0]==relay.KIND_DATA:
                if struct.unpack_from('<I',inner,52)[0]==relay.RELAY_JOIN_REQ:return
                host,target=struct.unpack_from('<ii',inner,4)
                if target!=self.peer_id:return
                self.host_addr=addr
                if self.host_id!=host:
                    self.host_id=host
                    endpoint=socket.inet_aton(netstate.CLIENT_IP)+self.port.to_bytes(2,'big')
                    self.state.bots[self.peer_id]=bots.Opponent(host,self.peer_id,endpoint)
                if self.ai_enabled:
                    mid,body=bots.decode_stream(inner)
                    self.ai.observe(mid,body)
                    if mid==0x10002:
                        group,name,target,actor,clock,params=bot_ai.avatar_event(body)
                        key=(name,actor)
                        if key not in self.events_seen and len(self.events_seen)<128:
                            self.events_seen.add(key)
                            print('AI observed',name,'actor',actor,'clock',clock,'params',params.hex(),flush=True)
            replies,event=self.state.handle(data,addr)
            if 'message=0x10000' not in event and event not in self.udp_events_seen:
                if len(self.udp_events_seen)<128:
                    self.udp_events_seen.add(event)
                    print('UDP',event,flush=True)
            for response,target in replies:self.transport.sendto(response,target)
        except (ValueError,struct.error) as error:print('UDP error',str(error),flush=True)


async def receive(reader):
    head=await reader.readexactly(2)
    size=struct.unpack('<H',head)[0]
    if not 4<=size<=16384:raise ValueError('Invalid server frame size')
    raw=head+await reader.readexactly(size-2)
    kind,_=protocol.FrameDecoder().feed(raw)[0]
    return (struct.unpack_from('<I',raw,4)[0],raw[8:-4]) if kind==2 else (None,b'')


def first_room(body):
    result,division,filter_,level,count=struct.unpack_from('<5i',body)
    if result!=-1 or count==0:return None
    name,end=read_string(body,20)
    number=struct.unpack_from('<i',body,end)[0]
    password=struct.unpack_from('<i',body,end+20)[0]
    if password:return None
    return dict(name=name,division=division,level=level,id=number)


async def main(peer_index=0,ai=False):
    udp=UDPClient(peer_index)
    transport,_=await asyncio.get_running_loop().create_datagram_endpoint(lambda:udp,
                        local_addr=(netstate.CLIENT_IP,udp.port))
    if ai:udp.enable_ai()
    writer=None
    try:
        reader,writer=await asyncio.open_connection('127.0.0.1',test_peer.PORT)
        name=test_peer.PEER_NAMES[peer_index]
        writer.write(protocol.frame(protocol.LOGIN_NAME_REQ,protocol.wire_string(name+' '+32*'0')))
        await writer.drain()
        mid,body=await receive(reader)
        if mid!=protocol.LOGIN_ACK or struct.unpack_from('<i',body)[0]!=-1:
            raise ValueError('Test login failed')
        print(f'Connected {name}; waiting for a room without password.',flush=True)
        room=None
        while room is None:
            for level in (99,5,10,20):
                writer.write(protocol.frame(0x30000,struct.pack('<4i',1,level,0,0)))
                await writer.drain()
                mid,body=await receive(reader)
                if mid==0x30001:room=first_room(body)
                if room:break
            if room is None:await asyncio.sleep(1)
        writer.write(protocol.frame(0x30004,struct.pack('<4i',room['division'],room['level'],room['id'],0)))
        await writer.drain()
        mid,body=await receive(reader)
        if mid!=0x30005 or struct.unpack_from('<i',body)[0]!=-1:raise ValueError('Room join rejected')
        print('JOINED',json.dumps(room),flush=True)
        while True:
            mid,body=await receive(reader)
            if mid==0x30038:
                # New match: restart the virtual protocol controller, retaining
                # its independently bound UDP endpoint.
                udp.state.bots.clear()
                udp.host_id=None
                teams=udp.ai.teams
                udp.ai=bot_ai.Controller(udp.peer_id)
                udp.ai.teams=teams
                udp.events_seen.clear()
                udp.requests_seen.clear()
                udp.udp_events_seen.clear()
            if mid==0x30008 and len(body)>=70:
                udp.ai.teams[struct.unpack_from('<i',body,66)[0]]=struct.unpack_from('<i',body,4)[0]
            if mid in (0x30038,0x30028,0x3000b):print('TCP',hex(mid),flush=True)
    except asyncio.IncompleteReadError:
        print('Server closed peer session.',flush=True)
    finally:
        udp.stop_ai()
        transport.close()
        if writer:
            writer.close()
            await writer.wait_closed()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--peer-index',type=int,choices=range(3),default=0)
    parser.add_argument('--ai',action='store_true',help='Experimental observed gameplay requests')
    args=parser.parse_args()
    asyncio.run(main(args.peer_index,args.ai))
