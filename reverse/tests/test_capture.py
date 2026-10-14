import asyncio
import importlib.util
import json
import struct
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture_server', ROOT / 'server' / 'capture_server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_five_skill_shared_groups_save_ack_then_reload(self):
        from economy_helpers import set_level,fund
        store=module.Store(Path(self.temp.name)/'shared-skills.sqlite3')
        c=store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
        set_level(store,36);fund(store)
        packet=bytes.fromhex((ROOT/'tests/fixtures/equip-five-skills-shared-group.hex').read_text())
        for i in range(7):store.buy_skill(struct.unpack_from('<i',packet,14+8*i)[0],1)
        before=store.get()
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        async def receive():
            head=await asyncio.wait_for(reader.readexactly(2),3)
            return head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),3)
        try:
            for repeat in range(2):
                writer.write(packet);await writer.drain()
                self.assertEqual(await receive(),module.protocol.frame(0x20020,struct.pack('<i',-1)))
                writer.write(module.protocol.frame(0x2000d,struct.pack('<ii',0x10,c['id'])));await writer.drain()
                self.assertEqual(await receive(),module.protocol.kicker_info_ack(store.get(),0x10))
            self.assertEqual(store.balances(),before['balances'])
            self.assertEqual(struct.unpack_from('<hhh',module.protocol.skill_locker_record(module.Store(store.path).get())),(5,5,7))
        finally:writer.close();await writer.wait_closed()

    async def test_common_rewards_wire_replay_and_requested_xp_are_consistent(self):
        from characters import read_string
        from test_test_peer import HostWriter
        import bots,match_economy
        store=module.Store(Path(self.temp.name)/'rewards-tcp.sqlite3')
        c=store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        async def receive():
            head=await asyncio.wait_for(reader.readexactly(2),3)
            return head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),3)
        room=None
        try:
            writer.write(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text()));await writer.drain()
            await receive();await receive();room=next(iter(store.rooms.rooms.values()));room['ready']=True
            room['guests']={i:dict(team=t,ready=True,writer=HostWriter(),character=bots.profile(c,28674+i,'PeerTeste')) for i,t in enumerate((2,1,2))}
            writer.write(module.protocol.frame(0x3001f,struct.pack('<iii',1,0,0)));await writer.drain()
            await receive();await receive();room['match_session']['started']-=121
            score=bytes.fromhex((ROOT/'tests/fixtures/free-score-quit.hex').read_text())[8:-4]
            records=[score[8+i*138:8+(i+1)*138] for i in range(3)]
            for i in range(3):
                record=bytearray(records[0]);struct.pack_into('<i',record,0,28674+i);records.append(bytes(record))
            request=module.protocol.frame(0x30027,struct.pack('<ii',1,6)+b''.join(records))
            for repeat in range(2):
                writer.write(request);await writer.drain();ack=await receive()
                self.assertEqual(struct.unpack_from('<iiBii',ack,8),(-1,1,0,0,6))
                self.assertEqual(struct.unpack_from('<HH',ack,25+26),(300,200) if repeat==0 else (0,0))
                self.assertEqual(store.balances()['game_points'],10200)
            writer.write(module.protocol.frame(0x2000d,struct.pack('<ii',1,c['id'])));await writer.drain()
            self.assertEqual(await receive(),module.protocol.kicker_info_ack(store.get(),1))
        finally:
            if room:room.get('guests',{}).clear()
            writer.close();await writer.wait_closed()

    async def test_fresh_account_first_avatar_intro_disconnect_resume_and_native_completion(self):
        from test_character_slots import creation
        store=module.Store(Path(self.temp.name)/'first-intro-tcp.sqlite3',starter_items=True)
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        async def exchange(mid,body=b''):
            writer.write(module.protocol.frame(mid,body));await writer.drain()
            head=await asyncio.wait_for(reader.readexactly(2),3)
            return head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),3)
        try:
            login=await exchange(module.protocol.LOGIN_NAME_REQ)
            self.assertEqual(login,module.protocol.login_ack(None,0))
            self.assertEqual(await exchange(0x2000f),module.protocol.locker_init_ack(None,[]))
            created=await exchange(0x20011,creation('Novato','Woman_Normal'))
            avatar=store.get();self.assertEqual(created,module.protocol.create_ack(avatar,1))
            info=await exchange(0x2000d,struct.pack('<ii',0x100000,avatar['id']))
            self.assertEqual(struct.unpack_from('<II',info,20),(0,0))
            writer.write(module.protocol.frame(0x2004b,struct.pack('<i',3)))
            writer.write(module.protocol.frame(0x2004b,struct.pack('<i',-1)))
            writer.write(module.protocol.frame(0x2004b,b''))
            await writer.drain()
            # Step packets are one-way, including rejection: the next reply
            # must still be the requested KickerInfo, with no invented ACKs.
            info=await exchange(0x2000d,struct.pack('<ii',0x100000,avatar['id']))
            self.assertEqual(info,module.protocol.kicker_info_ack(store.get(),0x100000))
            self.assertEqual(store.get()['onboarding']['step'],3)
        finally:writer.close();await writer.wait_closed()
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        try:
            login=await exchange(module.protocol.LOGIN_NAME_REQ)
            self.assertEqual(struct.unpack_from('<I',login,16)[0]&1,0)
            self.assertEqual(store.get()['onboarding']['step'],3)
            body=struct.pack('<iii',avatar['id'],2,0)
            self.assertEqual(await exchange(0x20030,body),module.protocol.frame(0x20031,struct.pack('<i',-1)+body))
            self.assertEqual(await exchange(0x2002e,struct.pack('<I',3)),module.protocol.frame(0x2002f,struct.pack('<iI',-1,3)))
            login=await exchange(module.protocol.LOGIN_NAME_REQ)
            self.assertEqual(struct.unpack_from('<I',login,16)[0]&1,1)
            info=await exchange(0x2000d,struct.pack('<ii',0x100000,avatar['id']))
            self.assertEqual(struct.unpack_from('<II',info,20),(2,0))
            inventory=await exchange(0x2000b,struct.pack('<I',0x10))
            self.assertEqual(inventory,module.protocol.inventory_ack(store.items()))
            self.assertEqual(store.get()['tutorials'],[])
        finally:writer.close();await writer.wait_closed()

    async def test_three_character_creation_rejection_roster_details_switch_and_delete(self):
        from test_character_slots import creation
        store=module.Store(Path(self.temp.name)/'three-slots-tcp.sqlite3',starter_items=True)
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        async def exchange(mid,body):
            writer.write(module.protocol.frame(mid,body));await writer.drain()
            head=await asyncio.wait_for(reader.readexactly(2),3)
            return head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),3)
        for index,(name,model) in enumerate((('Primeiro','Man_Normal'),('Segunda','Woman_Normal'),('Terceiro','Man_Big'))):
            reply=await exchange(0x20011,creation(name,model));saved=store.get()
            self.assertEqual(reply,module.protocol.create_ack(saved,index+1))
        first=store.list_characters()[0];active=store.get()
        self.assertEqual(await exchange(0x20011,creation('Quarto')),module.protocol.create_error_ack(0))
        self.assertEqual(await exchange(0x20011,creation('primeiro')),module.protocol.create_error_ack(1))
        self.assertEqual(await exchange(0x2000f,b''),module.protocol.locker_init_ack(active,store.list_characters()))
        reply=await exchange(0x2000d,struct.pack('<ii',3,first['id']))
        self.assertEqual(struct.unpack_from('<i',reply,12)[0],first['id']);self.assertEqual(store.get()['id'],active['id'])
        self.assertEqual(await exchange(0x20015,struct.pack('<i',first['id'])),module.protocol.select_character_ack(first['id']))
        self.assertEqual(await exchange(0x20013,struct.pack('<i',active['id'])+module.protocol.wire_string('Terceiro')),module.protocol.delete_character_ack(active['id'],first['id'],2))
        self.assertEqual(len(store.list_characters()),2)
        writer.close();await writer.wait_closed()

    async def test_duplicate_transfer_info_does_not_restart_lobby_and_new_transfer_resets(self):
        store=module.Store(Path(self.temp.name)/'duplicate-transfer.sqlite3')
        character=store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        endpoint=bytes.fromhex('c0a838014e20')
        info=module.protocol.frame(0x20034,struct.pack('<iihh',1,1,1,2)+endpoint*4)
        lobby=module.protocol.frame(0x30000,struct.pack('<iiii',1,99,0,0))
        expected_ack=module.protocol.frame(0x20035,struct.pack('<i',-1))
        expected_lobby=module.protocol.free_lobby_ack(1,99,0,[])
        # Reproduce the actual three identical frames in one TCP delivery.
        writer.write(info*3+lobby);await writer.drain()
        response=await asyncio.wait_for(reader.readexactly(len(expected_ack+expected_lobby)),2)
        self.assertEqual(response,expected_ack+expected_lobby)
        with self.assertRaises(TimeoutError):await asyncio.wait_for(reader.read(1),.1)
        # A later transfer on the same connection must acknowledge again.
        transfer=module.protocol.frame(0x20032,struct.pack('<ii',1,1)+bytes.fromhex('c0a838010000'))
        # The transfer request changes CLIENT_IP. Build the expected endpoint
        # from the request, not the runner's hostname before it is processed.
        relay_endpoint=bytes.fromhex('c0a83801')+(20001).to_bytes(2,'big')
        expected_transfer=module.protocol.frame(0x20033,
            struct.pack('<iiiB',-1,1,1,2)+relay_endpoint+b'\x02'+relay_endpoint)
        writer.write(transfer+info);await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(expected_transfer+expected_ack)),2),expected_transfer+expected_ack)
        self.assertEqual(store.get(),character)
        writer.close();await writer.wait_closed()

    async def test_buddy_bootstrap_is_scoped_to_messenger_port_and_keeps_profile(self):
        import messenger
        store = module.Store(Path(self.temp.name)/'buddy.sqlite3')
        character = store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
        nickname = character['nickname'].encode('cp949') + b'\0'
        body = struct.pack('<iH', character['id'], len(nickname)) + nickname + struct.pack('<ii', 37, 3)
        reader, writer = await self.connect(lambda r,w: module.capture_tcp(r,w,self.recorder,19998,True,store))
        wire = module.protocol.frame(messenger.REGISTER, body)
        writer.write(wire[:7]); await writer.drain()
        writer.write(wire[7:]); await writer.drain()
        expected, _ = messenger.bootstrap(body, character)
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(expected)),2), expected)
        writer.close(); await writer.wait_closed()
        reader, writer = await self.connect(lambda r,w: module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(wire); await writer.drain()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(reader.read(1),.1)
        writer.close(); await writer.wait_closed()
        self.assertEqual(store.get(), character)

    async def test_common_two_vs_two_preserves_capacity_and_broadcasts_explicit_start(self):
        from characters import read_string
        from test_test_peer import HostWriter
        store=module.Store(Path(self.temp.name)/'common-start.sqlite3')
        character=store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
        body=bytearray(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())[8:-4])
        _,end=read_string(body,8)
        struct.pack_into('<i',body,end,3)
        struct.pack_into('<i',body,end+16,2)
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        async def receive():
            head=await asyncio.wait_for(reader.readexactly(2),2)
            return head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),2)
        writer.write(module.protocol.frame(0x30002,body))
        await writer.drain()
        await receive()
        await receive()
        room=next(iter(store.rooms.rooms.values()))
        self.assertEqual(room['match_count'],2)
        self.assertEqual(room['closed_slots'],{(1,2),(2,2)})
        record=module.protocol.free_room_record(room)
        _,record_start=read_string(record,0)
        self.assertEqual(struct.unpack_from('<ihh',record,record_start),(room['id'],1,4))
        import bots
        room['guests']={index:dict(team=team,ready=True,writer=HostWriter(),character=bots.profile(character,28674+index,'PeerTeste')) for index,team in enumerate((2,1,2))}
        writer.write(module.protocol.frame(0x30010,struct.pack('<ii',1,1)))
        await writer.drain()
        self.assertEqual(await receive(),module.protocol.free_ready_cast(room))
        self.assertFalse(room.get('playing',False))
        writer.write(module.protocol.frame(0x3001f,struct.pack('<iii',1,0,0)))
        await writer.drain()
        self.assertEqual(await receive(),module.protocol.free_start_cast())
        self.assertEqual(await receive(),module.protocol.free_loadstart_cast(room))
        self.assertTrue(room['playing'])
        for guest in room['guests'].values():
            self.assertEqual(guest['writer'].messages,[module.protocol.free_start_cast()+module.protocol.free_loadstart_cast(room,guest['team'])])
        self.assertEqual(store.get(),character)
        room['guests'].clear()
        writer.close()
        await writer.wait_closed()

    async def test_bot_packet_samples_survive_raw_capture_quota_without_duplicates(self):
        import bots
        state = module.relay.Relay()
        state.bots[bots.BOT_ID] = bots.Opponent(1001)
        capture = module.CaptureUDP(self.recorder,20001,state)
        capture.saved = capture.SAVED_FILES_PER_PORT
        raw = bytes.fromhex((ROOT/'tests/fixtures/bot-sync-1000c.hex').read_text())
        for _ in range(2):capture.datagram_received(raw,('192.168.56.1',20000))
        events = [json.loads(l) for l in (Path(self.temp.name)/'events.jsonl').read_text().splitlines()]
        samples = [e for e in events if e['kind']=='bot_p2p_sample']
        self.assertEqual(len(samples),1)
        self.assertEqual(samples[0]['message_id'],0x1000c)
        self.assertEqual(bytes.fromhex(samples[0]['hex']),raw)
        self.assertEqual(capture.saved,capture.SAVED_FILES_PER_PORT)

    async def test_practice_bot_loading_sections_and_disconnect_cleanup(self):
        import bots
        from characters import read_string
        store = module.Store(Path(self.temp.name)/'bot-loading.sqlite3')
        store.practice_bots = True
        store.relay = module.relay.Relay()
        char = store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
        body = bytearray(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())[8:-4])
        _, end = read_string(body,8)
        struct.pack_into('<i', body, end, 2)
        reader, writer = await self.connect(lambda r,w: module.capture_tcp(r,w,self.recorder,20000,True,store))

        async def receive():
            head = await asyncio.wait_for(reader.readexactly(2),2)
            return head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),2)

        writer.write(module.protocol.frame(0x30002,body))
        await writer.drain()
        self.assertEqual(struct.unpack_from('<I', await receive(),4)[0],0x30003)
        await receive()  # host
        bot_join = await receive()
        self.assertEqual(struct.unpack_from('<Iiiii',bot_join,4),(0x30008,1,2,0,1))
        self.assertIn(bots.BOT_ID,store.relay.bots)
        writer.write(module.protocol.frame(0x30022,struct.pack('<iiiii',1,0x78,2,char['id'],bots.BOT_ID)))
        await writer.drain()
        self.assertEqual(await receive(),module.protocol.frame(0x30023,struct.pack('<ii',-1,1)))
        for kicker in (char['id'],bots.BOT_ID):
            cast = await receive()
            self.assertEqual(struct.unpack_from('<Iiii',cast,4),(0x30024,1,kicker,0x78))
        writer.write(module.protocol.frame(0x20000,struct.pack('<i',0x10000)))
        await writer.drain()
        await receive()
        self.assertEqual(await asyncio.wait_for(reader.read(),2),b'')
        self.assertEqual(store.relay.bots,{})
        self.assertEqual(store.get(),char)
        writer.close()
        await writer.wait_closed()

    async def test_match_finish_duplicate_restart_and_leave_preserve_profile(self):
        from characters import read_string
        store = module.Store(Path(self.temp.name)/'match-cycle.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader, writer = await self.connect(lambda r,w: module.capture_tcp(r,w,self.recorder,20000,True,store))

        async def receive():
            head = await asyncio.wait_for(reader.readexactly(2), 2)
            return head + await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2), 2)

        room_body=bytearray(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())[8:-4])
        _,offset=read_string(room_body,8);struct.pack_into('<i',room_body,offset,2)
        writer.write(module.protocol.frame(0x30002,room_body))
        await writer.drain()
        await receive()  # create ACK
        await receive()  # host CAST
        room = next(iter(store.rooms.rooms.values()))
        score = bytes.fromhex((ROOT/'tests/fixtures/free-score-quit.hex').read_text())
        for cycle in range(2):
            writer.write(module.protocol.frame(0x3001f, struct.pack('<iii', 1, 0, 0)))
            await writer.drain()
            self.assertEqual(await receive(), module.protocol.free_start_cast())
            self.assertEqual(await receive(), module.protocol.free_loadstart_cast(room))
            self.assertTrue(room['playing'])
            for repeat in range(2):
                writer.write(score)
                await writer.drain()
                response = await receive()
                self.assertEqual(struct.unpack_from('<iiBii', response, 8), (-1,1,0,0,3))
                self.assertEqual(response[25:-4], score[16:-4])
                self.assertFalse(room['playing'])
                self.assertFalse(room['ready'])
        writer.write(module.protocol.frame(0x30009, struct.pack('<i',1)))
        await writer.drain()
        self.assertEqual(await receive(), module.protocol.free_quit_cast(room))
        self.assertEqual(store.rooms.rooms, {})
        self.assertEqual(store.get(), character)
        writer.close()
        await writer.wait_closed()

    async def test_mission_actions_refresh_saved_selection_before_ack(self):
        store=module.Store(Path(self.temp.name)/'missions.sqlite3')
        raw=bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character=store.create(raw[8:-4])
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        for message_id in (0x20022,0x20024):
            writer.write(module.protocol.frame(message_id,struct.pack('<ii',1,401)))
            await writer.drain()
            expected=module.protocol.kicker_info_ack(dict(character,missions={401:2} if message_id==0x20022 else {}),0xc0000)
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(expected)),2),expected)
            self.assertEqual(await asyncio.wait_for(reader.readexactly(24),2),module.protocol.frame(message_id+1,struct.pack('<iii',-1,1,401)))
        writer.write(module.protocol.frame(0x20022,struct.pack('<ii',1,99999)))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(16),2),module.protocol.frame(0x20023,struct.pack('<i',0)))
        self.assertEqual(store.get()['missions'],{})
        self.assertEqual(store.get()['payload'],character['payload'])
        writer.close()
        await writer.wait_closed()

    async def test_local_channel_and_captured_shutdown(self):
        store = module.Store(Path(self.temp.name)/'shutdown.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(module.protocol.frame(0x3002c,b''))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(32),1),
                         module.protocol.frame(0x3002d,struct.pack('<iiiii',-1,0,0,0,99)))
        writer.write(bytes.fromhex('10000200000002000000010035b6c79e'))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(16),1),
                         module.protocol.frame(0x20000,struct.pack('<i',0x20000)))
        self.assertEqual(await asyncio.wait_for(reader.read(),1),b'')
        writer.close()
        await writer.wait_closed()
        self.assertEqual(store.get()['payload'],character['payload'])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.recorder = module.Recorder(self.temp.name)
        self.servers = []
        self.client_writers = []
        self.original_client_ip = module.netstate.CLIENT_IP

    async def asyncTearDown(self):
        # Recent asyncio versions wait for active connections when closing a
        # server. Release test clients even when an assertion failed first.
        for writer in self.client_writers:
            writer.close()
        for writer in self.client_writers:
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except (ConnectionError, OSError, TimeoutError):
                pass
        for server in self.servers:
            server.close()
            await server.wait_closed()
        module.netstate.CLIENT_IP = self.original_client_ip
        self.temp.cleanup()

    async def connect(self, callback):
        server = await asyncio.start_server(callback, '127.0.0.1', 0)
        self.servers.append(server)
        reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1]), 5)
        self.client_writers.append(writer)
        return reader, writer

    async def test_unknown_auth_is_rejected_and_password_is_redacted(self):
        reader, writer = await self.connect(lambda r, w: module.capture_http(r, w, self.recorder, None))
        writer.write(b'GET /login/game_auth.php?ID=test&PW=secret HTTP/1.1\r\nHost: localhost\r\n\r\n')
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        await writer.wait_closed()
        self.assertTrue(response.startswith(b'HTTP/1.1 503'))
        logged = (Path(self.temp.name) / 'events.jsonl').read_text()
        self.assertNotIn('secret', logged)
        self.assertIn('redacted', logged)

    async def test_explicit_auth_response_is_sent_byte_for_byte(self):
        response_file = Path(self.temp.name) / 'response.bin'
        response_file.write_bytes(b'\x00experimental\xff')
        reader, writer = await self.connect(lambda r, w: module.capture_http(r, w, self.recorder, response_file))
        writer.write(b'GET /login/game_auth.php HTTP/1.1\r\nHost: localhost\r\n\r\n')
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        await writer.wait_closed()
        self.assertTrue(response.startswith(b'HTTP/1.1 200'))
        self.assertEqual(response.split(b'\r\n\r\n', 1)[1], response_file.read_bytes())

    async def test_tcp_stream_is_preserved(self):
        reader, writer = await self.connect(lambda r, w: module.capture_tcp(r, w, self.recorder, 20000))
        payload = bytes(range(256)) * 400
        writer.write(payload)
        await writer.drain()
        writer.write_eof()
        await asyncio.wait_for(reader.read(), 5)
        writer.close()
        await writer.wait_closed()
        captures = list(Path(self.temp.name).glob('tcp-*.bin'))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), payload)

    async def test_udp_datagrams_are_preserved(self):
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: module.CaptureUDP(self.recorder, 19999), local_addr=('127.0.0.1', 0))
        client, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=transport.get_extra_info('sockname'))
        try:
            client.sendto(b'\x00\xfftest')
            for _ in range(50):
                if list(Path(self.temp.name).glob('udp-*.bin')):
                    break
                await asyncio.sleep(0.01)
            files = list(Path(self.temp.name).glob('udp-*.bin'))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b'\x00\xfftest')
        finally:
            client.close()
            transport.close()

    async def test_real_kicker_request_returns_persisted_character(self):
        store = module.Store(Path(self.temp.name) / 'characters.sqlite3')
        creation = bytes.fromhex((ROOT / 'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader, writer = await self.connect(lambda r, w: module.capture_tcp(r, w, self.recorder, 20000, True, store))
        # Captured client request: flags precede the character ID.
        writer.write(module.protocol.frame(0x2000d, struct.pack('<ii',0x100003,character['id'])))
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(2), 5)
        size = int.from_bytes(header, 'little')
        response = header + await asyncio.wait_for(reader.readexactly(size - 2), 5)
        self.assertEqual(response, module.protocol.kicker_info_ack(character, 0x100003))
        writer.close()
        await writer.wait_closed()

    async def test_shop_default_features_query_returns_the_original_six_parts(self):
        store = module.Store(Path(self.temp.name) / 'characters.sqlite3')
        creation = bytes.fromhex((ROOT / 'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader, writer = await self.connect(lambda r, w: module.capture_tcp(r, w, self.recorder, 20000, True, store))
        writer.write(module.protocol.frame(0x2000d, struct.pack('<ii',4,character['id'])))
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(2), 5)
        response = header + await asyncio.wait_for(reader.readexactly(int.from_bytes(header, 'little')-2), 5)
        self.assertEqual(int.from_bytes(response[16:20], 'little'), 4)
        # Section4 is the native fallback outfit, not six empty sentinels.
        # This TCP check also proves an ordinary requested ACK is sufficient.
        from character_profiles import gear_parts
        _,_,starter=gear_parts(character['initial_gear'])
        self.assertEqual(response[20:-4], b''.join(struct.pack('<ih',*starter[i]) for i in (0,1,2,3,4,19)))
        writer.close()
        await writer.wait_closed()

    async def test_local_free_bootstrap_then_empty_room_list_keeps_player_data(self):
        store = module.Store(Path(self.temp.name)/'free.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(bytes.fromhex('18000200000006000100000001000000e90300009391e49d'))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(38),5)
        self.assertEqual(struct.unpack_from('<Iiiiihh',response,4),(0x60001,-1,1,1,character['id'],1,2))
        endpoint = bytes.fromhex('7f0000014e20')
        writer.write(module.protocol.frame(0x20034,struct.pack('<iihh',1,1,1,2)+endpoint*4))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(16),5),module.protocol.frame(0x20035,struct.pack('<i',-1)))
        writer.write(module.protocol.frame(0x30000,struct.pack('<iiii',1,5,0,1)))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(32),5)
        self.assertEqual(struct.unpack_from('<Iiiiii',response,4),(0x30001,-1,1,1,5,0))
        self.assertEqual(store.get()['payload'],character['payload'])
        writer.close()
        await writer.wait_closed()

    async def test_room_create_change_ready_leave_then_lobby_preserves_profile(self):
        store = module.Store(Path(self.temp.name)/'room-cycle.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))

        async def exchange(request):
            writer.write(request)
            await writer.drain()
            head = await asyncio.wait_for(reader.readexactly(2),5)
            response = head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),5)
            self.assertEqual(module.protocol.FrameDecoder().feed(response),[(2,response)])
            return response

        response = await exchange(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text()))
        self.assertEqual(struct.unpack_from('<Ii',response,4),(0x30003,-1))
        room = store.rooms.owned(next(iter(store.rooms.rooms.values()))['owner'],1)
        head = await asyncio.wait_for(reader.readexactly(2),5)
        joined = head+await asyncio.wait_for(reader.readexactly(int.from_bytes(head,'little')-2),5)
        self.assertEqual(joined,module.protocol.free_player_join_cast(room,character))
        self.assertEqual(struct.unpack_from('<Iiiii',joined,4),(0x30008,1,1,0,0))
        # Real Close click: Away, slot zero, closed byte one.
        response = await exchange(bytes.fromhex('190002003400030001000000020000000000000001bd547832'))
        self.assertEqual(struct.unpack_from('<IiiiBi',response,4),(0x30036,1,2,0,1,5))
        self.assertEqual(room['closed_slots'],{(2,0)})
        response = await exchange(module.protocol.frame(0x30034,struct.pack('<iiiB',1,2,0,0)))
        self.assertEqual(struct.unpack_from('<IiiiBi',response,4),(0x30036,1,2,0,0,6))
        response = await exchange(module.protocol.frame(0x30034,struct.pack('<iiiB',1,1,0,1)))
        self.assertEqual(struct.unpack_from('<Iii',response,4),(0x30035,0,1))
        self.assertEqual(room['closed_slots'],set())
        # Captured requests: choose map 13 and ball 2.
        response = await exchange(bytes.fromhex('1400020016000300010000000d000000bcbc332b'))
        self.assertEqual(struct.unpack_from('<Iii',response,4),(0x30018,1,13))
        response = await exchange(bytes.fromhex('14000200190003000100000002000000ff6b2ce6'))
        self.assertEqual(struct.unpack_from('<Iii',response,4),(0x3001b,1,2))
        self.assertEqual((room['map_id'],room['ball_id']),(13,2))
        response = await exchange(module.protocol.frame(0x30019,struct.pack('<ii',1,99)))
        self.assertEqual(struct.unpack_from('<Iii',response,4),(0x3001a,0,1))
        self.assertEqual(room['ball_id'],2)
        response = await exchange(module.protocol.frame(0x3000d,struct.pack('<i',1)))
        self.assertEqual(struct.unpack_from('<Iiiii',response,4),(0x3000f,1,character['id'],2,0))
        response = await exchange(module.protocol.frame(0x30010,struct.pack('<ii',1,1)))
        self.assertEqual(struct.unpack_from('<Iiii',response,4),(0x30012,1,character['id'],1))
        self.assertTrue(room['ready'])
        response = await exchange(module.protocol.frame(0x30010,struct.pack('<ii',1,8)))
        self.assertEqual(struct.unpack_from('<Iii',response,4),(0x30011,0,1))
        self.assertTrue(room['ready'])
        response = await exchange(module.protocol.frame(0x30009,struct.pack('<i',1)))
        self.assertEqual(struct.unpack_from('<Iiiii',response,4),(0x3000b,1,character['id'],0,-1))
        response = await exchange(module.protocol.frame(0x30000,struct.pack('<iiii',1,99,0,0)))
        self.assertEqual(struct.unpack_from('<Iiiiii',response,4),(0x30001,-1,1,0,99,0))
        self.assertEqual(store.get()['payload'],character['payload'])
        writer.close()
        await writer.wait_closed()

    async def test_locker_statistics_query_returns_all_four_requested_sections(self):
        store = module.Store(Path(self.temp.name)/'locker-stats.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(bytes.fromhex('140002000d00020060030000e90300002ed1296b'))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(24+2*116+2*66),5)
        self.assertEqual(struct.unpack_from('<iii',response,8),(-1,character['id'],0x360))
        self.assertEqual(response[20:-4],bytes(364))
        self.assertEqual(module.protocol.FrameDecoder().feed(response),[(2,response)])
        writer.close()
        await writer.wait_closed()

    async def test_captured_tutorial_completion_receives_zero_item_ack(self):
        reader, writer = await self.connect(lambda r,w: module.capture_tcp(r,w,self.recorder,20000,True))
        writer.write(bytes.fromhex('1000020036000200650000001e5511f8'))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(24),5)
        self.assertEqual(struct.unpack_from('<Iiii',response,4),(0x20037,-1,101,0))
        self.assertEqual(module.protocol.FrameDecoder().feed(response),[(2,response)])
        writer.close()
        await writer.wait_closed()

    async def test_real_item_purchase_is_saved_and_inventory_reloads_it(self):
        store=module.Store(Path(self.temp.name)/'shop.sqlite3')
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(bytes.fromhex('230002001b0002008b8a0100010000000100000001ffffffff00ffffffff00c85739fd'))
        await writer.drain()
        response=await asyncio.wait_for(reader.readexactly(70),5)
        self.assertEqual(response,module.protocol.item_buy_ack(store.items()[0],store.balances()))
        writer.write(module.protocol.frame(0x2000b,struct.pack('<i',0x10)))
        await writer.drain()
        inventory=await asyncio.wait_for(reader.readexactly(66),5)
        self.assertEqual(inventory,module.protocol.inventory_ack(store.items()))
        writer.close()
        await writer.wait_closed()

    async def test_captured_skill_buy_then_reload_and_save(self):
        from economy_helpers import set_level
        store = module.Store(Path(self.temp.name)/'skills.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        character = set_level(store,2)
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        request = bytes.fromhex((ROOT/'tests/fixtures/buy-trap-shot.hex').read_text())
        writer.write(request)
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(28),5),module.protocol.skill_buy_ack(1006012,store.balances()))
        writer.write(module.protocol.frame(0x2000d,struct.pack('<ii',0x10,character['id'])))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(38),5)
        self.assertEqual(response,module.protocol.kicker_info_ack(store.get(),0x10))
        selected = struct.pack('<hhhihh',1,5,1,1006012,1,1)
        writer.write(module.protocol.frame(0x2001f,selected))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(16),5),module.protocol.frame(0x20020,struct.pack('<i',-1)))
        self.assertEqual(module.protocol.skill_locker_record(module.Store(store.path).get()),selected)
        writer.close()
        await writer.wait_closed()

    async def test_tutorial_retry_and_reconnect_keep_progress(self):
        store = module.Store(Path(self.temp.name)/'training.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        request = bytes.fromhex('1000020036000200650000001e5511f8')
        for _ in range(2):
            writer.write(request)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(24),5),module.protocol.tutorial_compensation_ack(101))
        writer.close()
        await writer.wait_closed()
        reopened = module.Store(store.path)
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,reopened))
        writer.write(module.protocol.frame(0x2000d,struct.pack('<ii',0x200000,character['id'])))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(160),5)
        self.assertEqual(response[28+101//8],1 << (101%8))
        self.assertEqual(reopened.get()['tutorials'],[101])
        writer.close()
        await writer.wait_closed()

    async def test_locker_reloads_owned_character_then_validates_selection(self):
        store = module.Store(Path(self.temp.name)/'locker.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(bytes.fromhex('0c0002000f0002003f5100cd'))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(24+len(character['payload'])),5)
        self.assertEqual(response[8:20],struct.pack('<iii',-1,character['id'],1))
        self.assertEqual(response[20:-4],character['payload'])
        for selected,accepted in [(character['id']+1,False),(character['id'],True)]:
            writer.write(module.protocol.frame(0x20015,struct.pack('<i',selected)))
            await writer.drain()
            expected = module.protocol.select_character_ack(selected,accepted)
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(expected)),5),expected)
        self.assertEqual(store.get()['payload'],character['payload'])
        writer.close()
        await writer.wait_closed()

    async def test_captured_training_exit_updates_are_acknowledged_and_saved(self):
        store = module.Store(Path(self.temp.name)/'exit.sqlite3')
        creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character = store.create(creation[8:-4])
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        requests = [(0x20030,struct.pack('<iIi',character['id'],0x8003,0),'kicker'),
                    (0x2002e,struct.pack('<I',3),'user')]
        for message_id,body,scope in requests:
            writer.write(module.protocol.frame(message_id,body))
            await writer.drain()
            response = await asyncio.wait_for(reader.readexactly(16+len(body)),5)
            self.assertEqual(response,module.protocol.frame(message_id+1,struct.pack('<i',-1)+body))
        writer.close()
        await writer.wait_closed()
        reopened = module.Store(store.path)
        with reopened.connect() as db:
            saved = {row['scope']:bytes(row['payload']) for row in db.execute('SELECT * FROM environment_updates')}
        self.assertEqual(saved,{scope:body for _,body,scope in requests})
        with self.assertRaises(ValueError):
            reopened.save_environment('kicker',struct.pack('<iii',character['id']+1,2,0))
        self.assertEqual(reopened.get()['tutorials'],[])

    async def test_full_inventory_returns_failure_and_keeps_session_usable(self):
        store = module.Store(Path(self.temp.name)/'full.sqlite3')
        abilities = struct.pack('<ib',-1,0)*2
        for index in range(200):
            with store.connect() as db:
                db.execute('INSERT INTO items VALUES(1,?,?,?,1,?)',(index,900000+index,900000+index,abilities))
        reader,writer = await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(bytes.fromhex('230002001b0002008b8a0100010000000100000001ffffffff00ffffffff00c85739fd'))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(16),5),module.protocol.frame(0x2001c,struct.pack('<i',0)))
        writer.write(module.protocol.frame(0x2000b,struct.pack('<i',0x10)))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(24+200*42),5)
        self.assertEqual(struct.unpack_from('<i',response,16)[0],200)
        self.assertEqual(len(store.items()),200)
        writer.close()
        await writer.wait_closed()

    async def test_remove_item_restores_default_parts_and_replies_with_wear_ack_only(self):
        store=module.Store(Path(self.temp.name)/'appearance.sqlite3')
        creation=bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        character=store.create(creation[8:-4])
        store.buy_item(103008,103008,1,struct.pack('<ib',-1,0)*2)
        gear=bytearray(character['payload'][character['base_end']:])
        from characters import read_string, BASE_APPEARANCE_SLOTS
        _,start=read_string(gear,0)
        for slot in BASE_APPEARANCE_SLOTS:
            struct.pack_into('<ih',gear,start+8+6*slot,-1,-1)
        request=module.protocol.frame(0x20017,bytes(gear)+bytes(44)+struct.pack('<iH',1,0))
        reader,writer=await self.connect(lambda r,w:module.capture_tcp(r,w,self.recorder,20000,True,store))
        writer.write(request)
        await writer.drain()
        # Extra 0x2000e/0x2000c acks make the client's MyItem state show
        # "There is no user data", so only the wear ACK is sent.
        header=await asyncio.wait_for(reader.readexactly(2),5)
        response=header+await asyncio.wait_for(reader.readexactly(int.from_bytes(header,'little')-2),5)
        self.assertEqual(response,module.protocol.frame(0x20018,struct.pack('<i',-1)))
        self.assertEqual(store.get()['payload'][character['base_end']:],character['payload'][character['base_end']:])
        self.assertEqual(len(store.items()),1)
        writer.close()
        await writer.wait_closed()


if __name__ == '__main__':
    unittest.main()
