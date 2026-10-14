"""Observed P2P gameplay requests; native host remains the match authority."""
import math
import struct
import time
from characters import read_string


def avatar_event(body):
    if len(body)<4:raise ValueError('Truncated avatar event')
    group,end=read_string(body,4)
    event,end=read_string(body,end)
    state,end=read_string(body,end)
    if len(body)<end+20:raise ValueError('Truncated event metadata')
    target,actor,clock,size=struct.unpack_from('<iidi',body,end)
    params=body[end+20:]
    if size!=len(params) or not math.isfinite(clock):raise ValueError('Invalid event payload')
    return group,event,target,actor,clock,params


def direction(start,target):
    dx,dz=target[0]-start[0],target[2]-start[2]
    length=math.hypot(dx,dz)
    return (dx/length,0.,dz/length) if length>.001 else (0.,0.,0.)


class Controller:
    def __init__(self,kicker_id):
        self.id=kicker_id
        self.teams={}
        self.positions={}
        self.owner=None
        self.ball=None
        self.clock=None
        self.observed=None
        self.active=False
        self.next_action=0.
        self.move_time=None
        self.move_direction=(0.,0.,0.)
        self.move_speed=0.
        self.target_speed=0.
        self.move_acceleration=0.
        self.pending_shot=None
        self.shot_started=False
        self.last_move=None

    def observe(self,message,body,received=None):
        received=time.monotonic() if received is None else received
        if message==0x10003 and len(body)==44:
            clock,*values=struct.unpack('<d9f',body)
            if not all(math.isfinite(v) for v in (clock,*values)):raise ValueError('Invalid ball observation')
            if self.clock is not None and clock<self.clock:return
            self.clock,self.observed=clock,received
            self.ball=tuple(values[:3])
        elif message==0x10002:
            group,event,target,actor,clock,params=avatar_event(body)
            if self.clock is None or clock>=self.clock:self.clock,self.observed=clock,received
            if event=='WorldActionDispositionProcess':
                if len(params)<8:raise ValueError('Truncated disposition')
                attacking,count=struct.unpack_from('<ii',params)
                if not 1<=count<=8:raise ValueError('Invalid disposition count')
                end=8
                entries=[]
                for _ in range(count):
                    player=struct.unpack_from('<i',params,end)[0]
                    state,end=read_string(params,end+4)
                    pose=struct.unpack_from('<6f',params,end)
                    end+=24
                    if not all(math.isfinite(v) for v in pose):raise ValueError('Invalid player pose')
                    entries.append((player,state,tuple(pose[:3])))
                if end!=len(params):raise ValueError('Trailing disposition bytes')
                self.owner=None
                for player,state,pos in entries:
                    self.positions[player]=pos
                    if state=='IDLE(Attacker)':self.owner=player
                self.move_time=None
            elif event=='WorldActionPlayReStar':
                # Captured after a goal: double followed by pose entries, no count.
                if len(params)<8:raise ValueError('Truncated restart')
                end=8
                entries=[]
                while end<len(params):
                    if len(entries)>=8:raise ValueError('Invalid restart roster')
                    player=struct.unpack_from('<i',params,end)[0]
                    state,end=read_string(params,end+4)
                    pose=struct.unpack_from('<6f',params,end)
                    end+=24
                    if not all(math.isfinite(v) for v in pose):raise ValueError('Invalid restart pose')
                    entries.append((player,state,tuple(pose[:3])))
                if not entries:raise ValueError('Empty restart roster')
                self.owner=None
                for player,state,pos in entries:
                    self.positions[player]=pos
                    if state=='IDLE(Attacker)':self.owner=player
                self.active=True
                self.move_time=clock
                self.move_direction=(0.,0.,0.)
                self.move_speed=0.
                self.target_speed=0.
                self.last_move=None
                self.next_action=clock
            elif event=='WorldActionPlayGame':self.active=True
            elif event in ('WorldActionOpeningInitial','WorldActionEffectKickOff','WorldActionPlayStop'):
                self.active=False
                self.pending_shot=None
                self.move_direction=(0.,0.,0.)
                self.move_speed=0.
                self.target_speed=0.
                self.last_move=None
            elif event in ('NormalMove','FastMove','Stop','PickUP') and len(params)>=36:
                # PickUP, like NormalMove, carries clock + speed before XYZ.
                pos=struct.unpack_from('<3f',params,12)
                if not all(math.isfinite(v) for v in pos):raise ValueError('Invalid player position')
                self.positions[actor]=pos
                if actor==self.id:
                    self.move_time=clock
                    speed=struct.unpack_from('<f',params,8)[0]
                    if not math.isfinite(speed) or not 0<=speed<=200:raise ValueError('Invalid movement speed')
                    self.move_speed=0. if event in ('Stop','PickUP') else speed
                    self.target_speed=self.move_speed
                    self.move_acceleration=0.
                    if event in ('NormalMove','FastMove') and len(params)>=44:
                        goal,acceleration=struct.unpack_from('<2f',params,36)
                        if not all(math.isfinite(v) for v in (goal,acceleration)) or not 0<=goal<=200 or not 0<=acceleration<=10000:
                            raise ValueError('Invalid movement ramp')
                        self.target_speed=goal
                        self.move_acceleration=acceleration
                    self.move_direction=tuple(struct.unpack_from('<3f',params,24)) if event in ('NormalMove','FastMove') else (0.,0.,0.)
                    if not all(math.isfinite(v) for v in self.move_direction):raise ValueError('Invalid movement direction')
                if event=='PickUP':self.owner=actor
            elif event=='ShootStartAction' and actor==self.id:
                self.shot_started=True
            elif event=='Shooting' or event.endswith(('LowPass','HighPass','ThroughPass')):
                self.owner=None

    def movement(self,peer,clock,pos,aim):
        self.next_action=clock+.4
        stopped=math.hypot(aim[0],aim[2])<.001
        previous=self.last_move
        if previous and math.dist(aim,previous[1])<.15 and clock-previous[0]<1.:
            return []
        if stopped:
            if previous is None or previous[1]==(0.,0.,0.):return []
            # STOP has actor/clock/XYZ/facing; never normalize a zero vector.
            payload=struct.pack('<id6f',self.id,clock,*pos,*previous[1])
            mid=0x1001b
        else:
            payload=struct.pack('<id6f',self.id,clock,*pos,*aim)
            mid=0x10019
        self.last_move=(clock,aim)
        self.move_direction=aim
        return [peer.wrap(mid,payload,peer.template)]

    def actions(self,peer,now=None):
        now=time.monotonic() if now is None else now
        if not self.active or peer.phase!='playing' or peer.template is None or self.clock is None:
            return []
        elapsed=now-self.observed
        if not 0<=elapsed<=1.5 or self.id not in self.positions or self.ball is None:return []
        clock=self.clock+elapsed
        if clock<self.next_action:return []
        pos=self.positions[self.id]
        if self.move_time is not None:
            dt=max(0.,min(clock-self.move_time,1.5))
            speed=self.move_speed
            delta=self.target_speed-speed
            if self.move_acceleration>0 and delta:
                ramp=min(dt,abs(delta)/self.move_acceleration)
                acceleration=math.copysign(self.move_acceleration,delta)
                distance=speed*ramp+.5*acceleration*ramp*ramp+self.target_speed*(dt-ramp)
                self.move_speed=speed+acceleration*ramp
            else:distance=speed*dt
            pos=(max(-205.,min(205.,pos[0]+self.move_direction[0]*distance)),pos[1],max(-90.,min(90.,pos[2]+self.move_direction[2]*distance)))
            self.positions[self.id]=pos
        self.move_time=clock
        team=self.teams.get(self.id)
        if team not in (1,2):return []
        if self.owner==self.id:
            allies=[p for p,t in self.teams.items() if t==team and p!=self.id and p in self.positions]
            if peer.host_id in allies:
                # Prefer the human teammate: possession must not strand the user.
                recipient=min(allies,key=lambda p:(p!=peer.host_id,math.dist(pos,self.positions[p])))
                aim=direction(pos,self.positions[recipient])
                payload=struct.pack('<id10fi',self.id,clock,0.,*pos,*aim,*aim,recipient)
                self.next_action=clock+.8
                self.move_direction=(0.,0.,0.)
                return [peer.wrap(0x10021,payload,peer.template)]
            goal=(205. if team==1 else -205.,0.,0.)
            aim=direction(pos,goal)
            if math.dist(pos,goal)>95.:
                self.move_direction=aim
                return self.movement(peer,clock,pos,aim)
            if self.pending_shot is None:
                self.pending_shot=clock
                self.shot_started=False
                self.next_action=clock+.25
                # 00512005 reads EIGHT floats and one int after actor/clock.
                # Truncating this request left uninitialized stack values in
                # ShootStartAction and produced NaN in the native host.
                distance=max(1.,math.dist(pos,goal))
                payload=struct.pack('<id8fi',self.id,clock,0.,*pos,*aim,distance,0)
                return [peer.wrap(0x10028,payload,peer.template)]
            if not self.shot_started:
                if clock-self.pending_shot>1.5:self.pending_shot=None
                self.next_action=clock+.2
                return []
            self.pending_shot=None
            self.next_action=clock+1.
            # 00511CFF: actor, clock, speed/pos/move/aim, direction, gauge, byte.
            payload=struct.pack('<id10fifB',self.id,clock,0.,*pos,*aim,*aim,5,.65,0)
            return [peer.wrap(0x10029,payload,peer.template)]
        self.pending_shot=None
        # Close down the ball; teammate support avoids chasing the human holder.
        target=self.ball
        if self.teams.get(self.owner)==team:
            holder=self.positions.get(self.owner,self.ball)
            target=(holder[0]+(22. if team==1 else -22.),0.,max(-75.,min(75.,holder[2]+18.)))
        aim=direction(pos,target)
        return self.movement(peer,clock,pos,aim)
