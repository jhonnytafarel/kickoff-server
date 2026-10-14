"""Original missions offered for local protocol testing; no invented match results."""
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

# Jeffrey's first ten challenges and all seventeen coach challenges.
MISSION_IDS = tuple(range(1,11)) + tuple(range(401,418))

# Client-side mission states: 2 accepted, 3 succeeded with reward pending
# (004449D0 sets it on MISSION_SUCCESS_CAST), 4 reward collected.
STATE_ACCEPTED, STATE_SUCCEEDED, STATE_REWARDED = 2, 3, 4

# Compensation type codes read by 0044EA12: 6 adds game points (0044ED30),
# 4 flags an item reward (0044EDEB).
REWARD_GAME_POINT, REWARD_ITEM = 6, 4
_REWARD_CODES = {'GamePoint': REWARD_GAME_POINT, 'Item': REWARD_ITEM}


# Category states read by the 0x10000 kicker-info section (0044DA42): the
# client calls 008571A3(index, state) per byte, and only states 3..5 set the
# bit that lets 00857D10 list the category. C02 stays closed (state 0).
OPEN_CATEGORIES = (1, 3)
CATEGORY_OPEN_STATE = 3


def category_record():
    # 00419503: int32 highest index, then highest+1 bytes (index 0..highest).
    highest=max(OPEN_CATEGORIES)
    states=bytearray(highest+1)
    for category in OPEN_CATEGORIES:
        states[category]=CATEGORY_OPEN_STATE
    return struct.pack('<i',highest)+bytes(states)


def mission_info(mission_id):
    from resource_catalog import RESOURCE
    entry=RESOURCE['missions'].get(str(mission_id))
    if entry is None:raise ValueError('Mission definition not found')
    return entry['category_id'],[tuple(r) for r in entry['rewards']]


def compensation_ack(mission_id, category_id, rewards):
    # 0044EA12: result, category, mission, count of (code, value) pairs, then
    # two int32 values that stay zero unless the kicker statistics follow.
    payload=struct.pack('<iiii',-1,category_id,mission_id,len(rewards))
    payload+=b''.join(struct.pack('<ii',code,value) for code,value in rewards)
    return payload+struct.pack('<ii',0,0)


def detail_record(states):
    # 0044DC52: count and signed int32 IDs, loaded through 0085734B.
    ids=sorted(states)
    return struct.pack('<i',len(ids))+struct.pack('<'+'i'*len(ids),*ids)


def progress_record(states):
    # 00419921; the client log labels the fields CategoryID, MissionID, State,
    # GameCount and m_iObjectivesCount, followed by that many objective pairs.
    # The client constructs objectives from XML when the pair count is zero.
    return struct.pack('<i',len(states))+b''.join(
        struct.pack('<iiiii',mission_info(mission_id)[0],mission_id,state,0,0)
        for mission_id,state in sorted(states.items()))
