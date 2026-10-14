import asyncio
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
sys.path.insert(0,str(ROOT/'tools'))
import bots
import protocol
import relay
import test_peer
import headless_client
from capture_server import Recorder
from characters import Store
from rooms import RoomRegistry


class HostWriter:
    def __init__(self):self.messages=[]
    def write(self,data):self.messages.append(data)
    async def drain(self):pass
    def is_closing(self):return False


class TestPeerTests(unittest.IsolatedAsyncioTestCase):
    async def test_player_endpoint_never_answers_relay_discovery_join(self):
        class Transport:
            def __init__(self):self.sent=[]
            def sendto(self,data,addr):self.sent.append((data,addr))
        for index in range(3):
            peer=headless_client.UDPClient(index)
            peer.connection_made(Transport())
            addr=('192.168.56.1',20000)
            join=protocol.frame(relay.RELAY_JOIN_REQ,struct.pack('<iii',1,1,1001))
            peer.datagram_received(join,addr)
            peer.datagram_received(relay.packet(relay.KIND_SESSION,struct.pack('<ihh',1001,-1,1)),addr)
            header=bytearray(48)
            struct.pack_into('<ii',header,0,1001,peer.peer_id)
            reliable=relay.packet(relay.KIND_DATA,header+struct.pack('<Iiii',relay.RELAY_JOIN_REQ,1,1,1001))
            peer.datagram_received(reliable,addr)
            self.assertEqual(peer.transport.sent,[])
            self.assertEqual(peer.state.sessions,{})
            self.assertEqual(peer.state.bots,{})

    async def test_three_clients_balance_common_room_and_duplicate_login_cannot_evict_peer(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'common.sqlite3')
            character=store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
            store.rooms=RoomRegistry()
            body=bytearray(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())[8:-4])
            from characters import read_string
            _,end=read_string(body,8)
            struct.pack_into('<i',body,end,3)
            room=store.rooms.create(body,character,'host')
            store.room_writers={'host':HostWriter()}
            server=await asyncio.start_server(lambda r,w:test_peer.serve(r,w,store,Recorder(temp)),'127.0.0.1',0)
            clients=[]
            try:
                for index,name in enumerate(test_peer.PEER_NAMES):
                    reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
                    clients.append((reader,writer))
                    writer.write(protocol.frame(protocol.LOGIN_NAME_REQ,protocol.wire_string(name)))
                    await writer.drain()
                    self.assertEqual((await headless_client.receive(reader))[0],protocol.LOGIN_ACK)
                    writer.write(protocol.frame(0x30004,struct.pack('<4i',1,room['level'],room['id'],0)))
                    await writer.drain()
                    mid,reply=await headless_client.receive(reader)
                    self.assertEqual((mid,struct.unpack_from('<i',reply)[0]),(0x30005,-1))
                    for _ in range(index+2):await headless_client.receive(reader)
                roster=[(room['team'],room['slot'])]+[(g['team'],g['slot']) for g in room['guests'].values()]
                self.assertEqual(len(set(roster)),4)
                self.assertEqual([sum(t==team for t,s in roster) for team in (1,2)],[2,2])
                self.assertEqual({g['character']['id'] for g in room['guests'].values()},set(range(test_peer.PEER_ID,test_peer.PEER_ID+3)))
                self.assertEqual(len({g['endpoint'] for g in room['guests'].values()}),3)
                room_record=protocol.free_room_record(room)
                _,record_start=read_string(room_record,0)
                self.assertEqual(struct.unpack_from('<ihh',room_record,record_start),(room['id'],4,6))
                for index,(reader,writer) in enumerate(clients):
                    for _ in range(2-index):
                        self.assertEqual((await headless_client.receive(reader))[0],0x30008)
                from capture_server import start_room
                room['ready']=True
                started=await start_room(room,store,Recorder(temp))
                self.assertEqual(started,protocol.free_start_cast()+protocol.free_loadstart_cast(room))
                self.assertTrue(room['playing'])
                for reader,writer in clients:
                    self.assertEqual((await headless_client.receive(reader))[0],0x30021)
                    self.assertEqual((await headless_client.receive(reader))[0],0x30038)
                duplicate_reader,duplicate_writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
                duplicate_writer.write(protocol.frame(protocol.LOGIN_NAME_REQ,protocol.wire_string('PeerTeste')))
                await duplicate_writer.drain()
                self.assertEqual(await asyncio.wait_for(duplicate_reader.read(),2),b'')
                duplicate_writer.close()
                await duplicate_writer.wait_closed()
                self.assertEqual(len(store.test_peer_connections),3)
                self.assertEqual(len(room['guests']),3)
                self.assertEqual(store.get(),character)
            finally:
                for reader,writer in clients:writer.close()
                for reader,writer in clients:await writer.wait_closed()
                for _ in range(20):
                    if not room.get('guests'):break
                    await asyncio.sleep(.01)
                self.assertEqual(room['guests'],{})
                self.assertEqual(store.test_peer_connections,{})
                server.close()
                await server.wait_closed()

    async def test_separate_tcp_join_roster_and_leave_preserve_real_account(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'account.sqlite3')
            character=store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
            store.rooms=RoomRegistry()
            room=store.rooms.create(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())[8:-4],character,'host')
            host=HostWriter()
            store.room_writers={'host':host}
            server=await asyncio.start_server(lambda r,w:test_peer.serve(r,w,store,Recorder(temp)),'127.0.0.1',0)
            try:
                reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
                writer.write(protocol.frame(protocol.LOGIN_NAME_REQ,protocol.wire_string('PeerTeste')))
                await writer.drain()
                mid,body=await headless_client.receive(reader)
                self.assertEqual(mid,protocol.LOGIN_ACK)
                writer.write(protocol.frame(0x30000,struct.pack('<4i',1,room['level'],0,0)))
                await writer.drain()
                _,body=await headless_client.receive(reader)
                self.assertEqual(headless_client.first_room(body)['id'],room['id'])
                writer.write(protocol.frame(0x30004,struct.pack('<4i',1,room['level'],room['id'],0)))
                await writer.drain()
                mid,body=await headless_client.receive(reader)
                self.assertEqual(mid,0x30005)
                self.assertEqual(struct.unpack_from('<i',body)[0],-1)
                await headless_client.receive(reader)  # host JOIN
                mid,body=await headless_client.receive(reader)  # peer JOIN
                self.assertEqual(mid,0x30008)
                self.assertIn(b'PeerTeste',body)
                self.assertEqual(len(room['guests']),1)
                self.assertIn(b'PeerTeste',host.messages[0])
                with self.assertRaises(ValueError):store.rooms.set_slot(room,2,0,1)
                store.rooms.change_team(room)
                self.assertEqual(room['slot'],1)
                writer.write(protocol.frame(0x30009,struct.pack('<i',1)))
                await writer.drain()
                await asyncio.wait_for(reader.read(),2)
                self.assertEqual(room['guests'],{})
                self.assertIn(room['id'],store.rooms.rooms)
                self.assertEqual(store.get(),character)
                self.assertEqual(Store(store.path).get(),character)
                writer.close()
                await writer.wait_closed()
            finally:
                server.close()
                await server.wait_closed()

    async def test_independent_udp_peer_has_own_id_endpoint_and_handles_quit(self):
        endpoint=b'\xc0\xa8\x38\x01'+test_peer.PORT.to_bytes(2,'big')
        peer=bots.Opponent(1001,test_peer.PEER_ID,endpoint)
        source=bytes.fromhex((ROOT/'tests/fixtures/bot-confirmation.hex').read_text())
        header=bytearray(source[4:52])
        struct.pack_into('<i',header,4,test_peer.PEER_ID)
        body=struct.pack('<Iiii',0x10001,1,1001,test_peer.PEER_ID)+endpoint
        struct.pack_into('<I',header,32,protocol.checksum(body))
        struct.pack_into('<H',header,46,len(body))
        reply=peer.handle(relay.packet(3,header+body))[0]
        self.assertEqual(struct.unpack_from('<ii',reply,4),(test_peer.PEER_ID,1001))
        self.assertEqual(bots.decode_stream(reply)[1][-6:],endpoint)
        body=struct.pack('<II',0x1000d,1001)
        struct.pack_into('<I',header,32,protocol.checksum(body))
        struct.pack_into('<H',header,46,len(body))
        reply=peer.handle(relay.packet(3,header+body))[0]
        self.assertEqual(bots.decode_stream(reply),(0x1000d,struct.pack('<I',test_peer.PEER_ID)))


if __name__=='__main__':unittest.main()
