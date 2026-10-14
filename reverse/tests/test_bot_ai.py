import struct
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import bot_ai
import bots
import protocol


class Peer:
    host_id=1001
    phase='playing'
    template=b'template'
    def wrap(self,mid,body,template):return mid,body


def event(name,actor,clock,params=b''):
    return (struct.pack('<i',0)+protocol.wire_string('DynamicWorld')+
            protocol.wire_string(name)+protocol.wire_string('')+
            struct.pack('<iidi',actor,actor,clock,len(params))+params)


class AITests(unittest.TestCase):
    def test_real_disposition_and_pickup_assign_position_and_owner(self):
        ai=bot_ai.Controller(28673)
        for fixture in ('ai-disposition.bin','ai-pickup.bin'):
            mid,body=bots.decode_stream((ROOT/'tests/fixtures'/fixture).read_bytes())
            ai.observe(mid,body,100.)
        self.assertEqual(ai.owner,28673)
        self.assertAlmostEqual(ai.positions[28673][0],59.49,places=1)
        self.assertAlmostEqual(ai.positions[28673][1],0.)
        self.assertEqual(ai.positions[1001],(-10.,0.,0.))

    def test_human_teammate_receives_pass_with_cooldown_and_no_actions_after_quit(self):
        ai=bot_ai.Controller(28676)
        ai.teams={1001:1,28676:1,28674:2}
        ai.positions={1001:(-10.,0.,0.),28676:(0.,0.,0.)}
        ai.observe(0x10003,struct.pack('<d9f',10.,*([0.]*9)),100.)
        ai.observe(0x10002,event('WorldActionPlayGame',2,10.),100.)
        ai.owner=28676
        mid,payload=ai.actions(Peer(),100.)[0]
        self.assertEqual(mid,0x10021)
        actor,clock,*values=struct.unpack('<id10fi',payload)
        self.assertEqual((actor,clock,values[-1]),(28676,10.,1001))
        self.assertEqual(ai.actions(Peer(),100.1),[])
        peer=Peer()
        peer.phase='finished'
        self.assertEqual(ai.actions(peer,101.),[])
        self.assertEqual(ai.actions(Peer(),103.),[])

    def test_opponent_approaches_ball_then_charges_and_shoots_at_observed_position(self):
        ai=bot_ai.Controller(28674)
        ai.teams={1001:1,28674:2,28675:2}
        ai.positions={28674:(-150.,0.,0.),28675:(-140.,0.,30.)}
        ai.observe(0x10003,struct.pack('<d9f',10.,-160.,0.,0.,0.,0.,0.,0.,0.,0.),100.)
        ai.observe(0x10002,event('WorldActionPlayGame',2,10.),100.)
        self.assertEqual(ai.actions(Peer(),100.)[0][0],0x10019)
        ai.owner=28674
        charge=ai.actions(Peer(),100.5)[0]
        self.assertEqual(charge[0],0x10028)
        self.assertEqual(len(charge[1]),48)
        fields=struct.unpack('<id8fi',charge[1])
        self.assertGreater(fields[-2],0.)
        self.assertEqual(ai.actions(Peer(),100.8),[])
        ai.observe(0x10002,event('ShootStartAction',28674,10.8),100.8)
        self.assertEqual(ai.actions(Peer(),101.1)[0][0],0x10029)
        ai.observe(0x10002,event('WorldActionEffectKickOff',2,11.),101.)
        self.assertEqual(ai.actions(Peer(),101.),[])

    def test_malformed_or_nonfinite_observations_do_not_enable_gameplay(self):
        ai=bot_ai.Controller(28674)
        with self.assertRaises(ValueError):ai.observe(0x10003,struct.pack('<d9f',float('nan'),*([0.]*9)))
        with self.assertRaises(ValueError):ai.observe(0x10002,event('NormalMove',28674,10.,b'bad')[:-1])
        self.assertEqual(ai.actions(Peer()),[])

    def test_movement_suppresses_repeated_direction_and_stops_without_zero_normalization(self):
        ai=bot_ai.Controller(28674)
        peer=Peer()
        self.assertEqual(ai.movement(peer,1.,(0.,0.,0.),(1.,0.,0.))[0][0],0x10019)
        self.assertEqual(ai.movement(peer,1.5,(10.,0.,0.),(1.,0.,0.)),[])
        self.assertEqual(ai.movement(peer,2.1,(20.,0.,0.),(1.,0.,0.))[0][0],0x10019)
        mid,payload=ai.movement(peer,2.6,(21.,0.,0.),(0.,0.,0.))[0]
        self.assertEqual(mid,0x1001b)
        self.assertEqual(struct.unpack('<id6f',payload)[-3:],(1.,0.,0.))
        self.assertEqual(ai.movement(peer,3.2,(21.,0.,0.),(0.,0.,0.)),[])

    def test_goal_pause_and_captured_restart_reset_positions_and_movement(self):
        ai=bot_ai.Controller(28674)
        ai.active=True
        ai.move_speed=40.5
        ai.move_direction=(1.,0.,0.)
        ai.pending_shot=31.
        ai.observe(0x10002,event('WorldActionPlayStop',2,32.),100.)
        self.assertFalse(ai.active)
        self.assertIsNone(ai.pending_shot)
        self.assertEqual(ai.move_speed,0.)
        self.assertEqual(ai.actions(Peer(),100.),[])
        params=(ROOT/'tests/fixtures/ai-restart-params.bin').read_bytes()
        ai.observe(0x10002,event('WorldActionPlayReStar',2,39.,params),107.)
        self.assertTrue(ai.active)
        self.assertEqual(ai.owner,28675)
        self.assertEqual(ai.positions[28674],(120.,0.,-60.))
        self.assertEqual(ai.positions[1001],(-60.,0.,-60.))
        self.assertEqual(ai.next_action,39.)

    def test_prediction_uses_host_speed_and_full_elapsed_interval(self):
        ai=bot_ai.Controller(28674)
        ai.teams={28674:2}
        ai.observe(0x10003,struct.pack('<d9f',10.,100.,0.,0.,0.,0.,0.,0.,0.,0.),100.)
        ai.observe(0x10002,event('WorldActionPlayGame',2,10.),100.)
        params=struct.pack('<d7f',10.,28.,0.,0.,0.,1.,0.,0.)
        ai.observe(0x10002,event('NormalMove',28674,10.,params),100.)
        ai.move_direction=(1.,0.,0.)
        request=ai.actions(Peer(),100.5)[0]
        fields=struct.unpack('<id6f',request[1])
        self.assertAlmostEqual(fields[2],14.)

    def test_captured_movement_accelerates_from_zero_instead_of_reusing_old_position(self):
        ai=bot_ai.Controller(28674)
        ai.teams={28674:2}
        params=(ROOT/'tests/fixtures/ai-normal-move-params.bin').read_bytes()
        ai.observe(0x10003,struct.pack('<d9f',32.29600143432617,*([0.]*9)),100.)
        ai.observe(0x10002,event('WorldActionPlayGame',2,32.29600143432617),100.)
        ai.observe(0x10002,event('NormalMove',28674,32.29600143432617,params),100.)
        self.assertEqual(ai.move_speed,0.)
        self.assertEqual(ai.target_speed,40.5)
        self.assertEqual(ai.move_acceleration,800.)
        start=ai.positions[28674]
        facing=ai.move_direction
        request=ai.actions(Peer(),100.5)[0]
        fields=struct.unpack('<id6f',request[1])
        distance=40.5*.5-.5*40.5**2/800.
        self.assertAlmostEqual(fields[2],start[0]+facing[0]*distance,places=4)
        self.assertAlmostEqual(fields[4],start[2]+facing[2]*distance,places=4)
        self.assertEqual(ai.move_speed,40.5)


if __name__=='__main__':unittest.main()
