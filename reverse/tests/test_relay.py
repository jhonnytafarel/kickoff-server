import sys
import struct
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import protocol
import relay

ADDR=('192.168.56.1',20000)


class RelayTests(unittest.TestCase):
    def test_end_to_end_ack_routes_back_without_ack_loop_or_packet_rewrite(self):
        state=relay.Relay()
        peer_addr=('192.168.56.2',20000)
        for kicker,addr in ((1001,ADDR),(1002,peer_addr)):
            state.handle(relay.packet(relay.KIND_SESSION,struct.pack('<ihh',kicker,-1,1)),addr)
        # Native 00609E8C uses original sender/recipient and terminal flag 0.
        body=struct.pack('<iidI',1001,1002,12.5,7)+bytes(6)+b'\x00\x00'+bytes(4)
        ack=relay.packet(relay.KIND_ACK,body)
        self.assertEqual(state.handle(ack,peer_addr),([(ack,ADDR)],'peer_ack 1002->1001'))
        self.assertEqual(state.handle(ack,ADDR)[0],[])
        self.assertEqual(state.handle(ack,('192.168.56.3',20000))[0],[])
        envelope=struct.pack('<HHiii',56,relay.KIND_ENVELOPE,1002,1001,0)+ack
        self.assertEqual(state.handle(envelope,peer_addr)[0],[(ack,ADDR)])
        relay_ack=relay.packet(relay.KIND_ACK,body[:26]+b'\x01'+body[27:])
        self.assertEqual(state.handle(relay_ack,peer_addr)[0],[])

    def test_probe_and_session_open_match_client_packets(self):
        state=relay.Relay()
        # Datagrams captured from the client (trace_udp.log): probe 0, probe 1, open.
        probe0=bytes.fromhex('0900080000b3042106')
        probe1=bytes.fromhex('09000800010419e002')
        open_request=bytes.fromhex('10000900e9030000ffff0100ae876ec4')
        self.assertEqual(relay.packet(relay.KIND_PROBE,b'\x00'),probe0)
        self.assertEqual(relay.packet(relay.KIND_PROBE,b'\x01'),probe1)
        self.assertEqual(relay.packet(relay.KIND_SESSION,struct.pack('<ihh',1001,-1,1)),open_request)
        replies,event=state.handle(probe0,ADDR)
        self.assertEqual(replies,[(probe1,ADDR)])
        self.assertEqual(state.handle(probe1,ADDR),([],'probe_reply'))
        replies,event=state.handle(open_request,ADDR)
        # 004F9A68: kicker must match and session must not be -1.
        self.assertEqual(replies,[(relay.packet(relay.KIND_SESSION,struct.pack('<ihh',1001,1,1)),ADDR)])
        self.assertEqual(state.sessions[1001]['session'],1)
        # Reopening keeps the same session; closing forgets it.
        self.assertEqual(state.handle(open_request,ADDR)[0][0][0][4:10],struct.pack('<ih',1001,1))
        state.handle(relay.packet(relay.KIND_SESSION,struct.pack('<ihh',1001,1,2)),ADDR)
        self.assertEqual(state.sessions,{})

    def test_heartbeat_join_and_data_forwarding(self):
        state=relay.Relay()
        self.assertEqual(state.handle(bytes.fromhex('04000700'),ADDR)[0],[(bytes.fromhex('04000700'),ADDR)])
        join=protocol.frame(0x60000,struct.pack('<iii',1,1,1001))
        replies,event=state.handle(join,ADDR)
        self.assertEqual(replies,[(protocol.relay_join_ack(1,1,1001),ADDR)])
        state.handle(relay.packet(relay.KIND_SESSION,struct.pack('<ihh',1001,-1,1)),ADDR)
        data=relay.packet(relay.KIND_DATA,struct.pack('<ii',1001,1001)+bytes(0x34-12))
        self.assertEqual(state.handle(data,ADDR),([(data,ADDR)],'data 1001->1001'))
        with self.assertRaises(ValueError): state.handle(data[:-1]+b'\x00',ADDR)
        with self.assertRaises(ValueError): state.handle(b'\x01\x02',ADDR)

    def test_captured_reliable_join_and_retransmit_envelope(self):
        # Actual client request, 17:11:34; encapsulated retransmission has no
        # outer checksum. Both must acknowledge sequence 0 and answer JOIN.
        request = bytes.fromhex('48000300e9030000e9030000020002010000000015b9f138569f26409a9999999999a93f70985472000000002900000000001000000006000100000001000000e9030000b1891501')
        envelope = bytes.fromhex('58000600e9030000e903000002000200') + request
        state = relay.Relay()
        direct, event = state.handle(request, ADDR)
        nested, event = state.handle(envelope, ADDR)
        self.assertEqual(nested, direct)
        self.assertEqual(len(direct), 2)
        ack, target = direct[0]
        self.assertEqual(target, ADDR)
        self.assertEqual(relay.decode(ack)[0], relay.KIND_ACK)
        self.assertEqual(len(ack), 40)
        self.assertEqual(ack[12:20], request[20:28])
        self.assertEqual(ack[20:24], request[40:44])
        self.assertEqual(ack[30], 1)
        self.assertEqual(direct[1], (protocol.relay_join_ack(1, 1, 1001), ADDR))
        self.assertEqual(state.handle(ack, ADDR), ([], 'reliability_ack'))
        with self.assertRaises(ValueError):
            state.handle(envelope[:-1] + b'\x00', ADDR)
        with self.assertRaises(ValueError):
            state.handle(envelope[:16], ADDR)
