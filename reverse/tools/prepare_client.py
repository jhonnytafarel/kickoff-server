"""Prepare a private client copy for local testing; leave originals untouched."""
import argparse,hashlib,json,re,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEST=ROOT/'reverse/local-client'
SOURCE_SHA256='06b60416159ba35491e34fbcc188ef4ca5572130367de48d012e986f3cf48331'

def replace_tag(data,tag,value,optional=False):
    pattern=rb'(<'+tag.encode()+rb'\b[^>]*>)[^<]*(</'+tag.encode()+rb'>)'
    changed,count=re.subn(pattern,lambda m:m[1]+value.encode('ascii')+m[2],data)
    if count==1:return changed
    if count==0 and optional:
        node=(f'<{tag} AutoEnv="true" Type="Boolean">{value}</{tag}>\r\n').encode('ascii')
        changed,count=re.subn(rb'</Configure>',lambda m:node+m[0],data,count=1)
        if count==1:return changed
    raise ValueError(f'Expected exactly one {tag} element; found {count}')

def configure(data,windowed=False):
    for tag,value in dict(FrontSessionIP='127.0.0.1',SessionIP='127.0.0.1',
        FrontSessionMode='2',LogLevel='3',FullScreen='false' if windowed else 'true',
        EventNoticeUrl='http://127.0.0.1:18080/game_notice/').items():
        data=replace_tag(data,tag,value)
    # Keep 1024x768 logical hitboxes. This is the native research switch, not
    # an arbitrary executable patch or a replacement anti-cheat binary.
    return replace_tag(data,'DisableXTrap','true',optional=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Installed game root containing Bin and Data')
    p.add_argument('--windowed',action='store_true',help='Keep native 1024x768 window/hitboxes')
    p.add_argument('--patch-os',action='store_true',help='One known-version OS whitelist byte in the private copy')
    args=p.parse_args();source=args.source.resolve()
    if DEST.exists():raise SystemExit('Private client already exists; refusing to overwrite it.')
    for folder in ('Bin','Data'):
        origin=source/folder
        if not origin.is_dir():raise SystemExit('Missing source directory: '+str(origin))
        if DEST.resolve().is_relative_to(origin):raise SystemExit('Destination cannot be inside a copied source directory')
    config=source/'Bin/Game/StreetSoccer.cfg';data=configure(config.read_bytes(),args.windowed)
    exe=source/'Bin/Game/Game.exe';original=exe.read_bytes();digest=hashlib.sha256(original).hexdigest()
    if args.patch_os and (digest!=SOURCE_SHA256 or original[0x1d3a65:0x1d3a67]!=b'\x7d\x22'):
        # Offset is checked using the known native VA, see compatibility below.
        raise SystemExit('Unsupported executable for --patch-os; original files were not touched.')
    for folder in ('Bin','Data'):shutil.copytree(source/folder,DEST/folder)
    (DEST/'Bin/Game/StreetSoccer.cfg').write_bytes(data)
    # The PE32 section mapping for VA005D4665 is verified for SOURCE_SHA256.
    if args.patch_os:
        modified=bytearray(original);modified[0x1d3a65]=0xeb;(DEST/'Bin/Game/Game.exe').write_bytes(modified)
    report=dict(source=str(source),destination=str(DEST),source_exe_sha256=digest,
        executable_patched=args.patch_os,windowed=args.windowed,originals_preserved=True)
    (DEST/'preparation.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
