"""Creation choices and wearable compatibility from native client catalogs."""
from pathlib import Path
import re,struct,xml.etree.ElementTree as ET


BASE_SLOTS={'Hair':0,'Face':1,'Coat':2,'Hand':3,'Trousers':4,'FootWear':19}

def _load():
    from resource_catalog import RESOURCE
    profiles={name:dict(profile, heights=tuple(profile['heights']),weights=tuple(profile['weights']),
        choices={int(k):tuple(v) for k,v in profile['choices'].items()}) for name,profile in RESOURCE['profiles'].items()}
    items={int(k):dict(v,models=tuple(v['models'])) for k,v in RESOURCE['item_info'].items()}
    return profiles,items


PROFILES,ITEM_INFO=_load()

def gear_parts(gear):
    n=struct.unpack_from('<H',gear)[0];start=n+2
    model=gear[2:start-1].decode('ascii')
    if len(gear)!=start+128:raise ValueError('Invalid gear length')
    return model,start,[struct.unpack_from('<ih',gear,start+8+6*i) for i in range(20)]

def compatible(info_id,model):
    info=ITEM_INFO.get(info_id);profile=PROFILES.get(model)
    return bool(info and profile and info['sex'] in ('NotLimite','Unisex',profile['sex'])
                and (not info['models'] or profile['body'] in info['models']))

def validate_creation_gear(gear):
    model,start,parts=gear_parts(gear);profile=PROFILES.get(model)
    if not profile:raise ValueError('Unknown character model')
    body,height,weight=struct.unpack_from('<ihh',gear,start)
    if body!=profile['body'] or height not in profile['heights'] or not min(profile['weights'])<=weight<=max(profile['weights']):
        raise ValueError('Invalid character body, height or weight')
    for slot,(info,selection) in enumerate(parts):
        if slot in BASE_SLOTS.values():
            if info not in profile['choices'][slot] or selection!=1:raise ValueError('Invalid starter appearance')
        elif (info,selection)!=(-1,-1):raise ValueError('Unexpected starter accessory')
    return model

def default_gear(gear):
    model,start,parts=gear_parts(gear);profile=PROFILES[model]
    data=bytearray(gear)
    for slot in range(20):
        part=(profile['choices'][slot][0],1) if slot in profile['choices'] else (-1,-1)
        struct.pack_into('<ih',data,start+8+6*slot,*part)
    return bytes(data)
