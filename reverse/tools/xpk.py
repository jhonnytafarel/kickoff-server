"""Read the XARC/STD archives shipped with this KickOff client. Stdlib only."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import zlib


def entries(data):
    if data[:4] != b'XARC' or data[12:16] != b'STD ':
        raise ValueError('Unsupported archive signature')
    header_size = struct.unpack_from('<I', data, 4)[0]
    count = struct.unpack_from('<I', data, 24)[0]
    table = struct.unpack_from('<Q', data, 36)[0]
    if header_size != 44 or not header_size <= table <= len(data):
        raise ValueError('Invalid archive header')
    cursor = table
    result = []
    for _ in range(count):
        if cursor + 14 > len(data):
            raise ValueError('Truncated directory')
        record_size = struct.unpack_from('<I', data, cursor + 4)[0]
        codec = data[cursor + 8:cursor + 12].decode('ascii')
        name_size = struct.unpack_from('<H', data, cursor + 12)[0]
        if record_size != 14 + name_size + 32 or cursor + record_size > len(data):
            raise ValueError('Invalid directory record')
        name_data = data[cursor + 14:cursor + 14 + name_size]
        if not name_data.endswith(b'\0'):
            raise ValueError('Unterminated archive path')
        name = name_data[:-1].decode('cp949')
        stored, size, unknown, timestamp, offset = struct.unpack_from('<QQIIQ', data, cursor + 14 + name_size)
        if offset < header_size or offset + stored > table:
            raise ValueError('Entry outside payload area: ' + name)
        result.append(dict(name=name, codec=codec, stored_size=stored, size=size,
                           metadata_u32_unknown=unknown, timestamp_raw=timestamp, offset=offset))
        cursor += record_size
    if cursor != len(data):
        raise ValueError('Unexpected data after directory')
    return result


def payload(data, entry):
    blob = data[entry['offset']:entry['offset'] + entry['stored_size']]
    if entry['codec'] == 'COMP':
        chunks = []
        cursor = 0
        total = 0
        while cursor < len(blob):
            if cursor + 4 > len(blob):
                raise ValueError('Truncated block length')
            length = struct.unpack_from('<I', blob, cursor)[0]
            cursor += 4
            if not length or cursor + length > len(blob):
                raise ValueError('Invalid compressed length: ' + entry['name'])
            decoder = zlib.decompressobj()
            chunk = decoder.decompress(blob[cursor:cursor+length], min(65536, entry['size']-total) + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(chunk) > 65536:
                raise ValueError('Invalid zlib stream: ' + entry['name'])
            chunks.append(chunk)
            total += len(chunk)
            if total > entry['size']:
                raise ValueError('Decompressed size exceeds directory size')
            cursor += length
        raw = b''.join(chunks)
    elif entry['codec'] == 'NONE':
        raw = blob
    else:
        raise ValueError('Unsupported entry codec: ' + entry['codec'])
    if len(raw) != entry['size']:
        raise ValueError('Size mismatch: ' + entry['name'])
    return raw


def extract(archive, output):
    data = archive.read_bytes()
    index = entries(data)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    seen = set()
    for entry in index:
        name = PurePosixPath(entry['name'].replace('\\', '/'))
        if name.is_absolute() or '..' in name.parts or any(':' in part for part in name.parts):
            raise ValueError('Unsafe path: ' + str(name))
        target = output.joinpath(*name.parts).resolve()
        target.relative_to(output)
        key = str(target).casefold()
        if key in seen:
            raise ValueError('Duplicate archive path: ' + str(name))
        seen.add(key)
        raw = payload(data, entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        entry['sha256'] = hashlib.sha256(raw).hexdigest()
    manifest = dict(archive=str(archive), sha256=hashlib.sha256(data).hexdigest(), entries=index)
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'{archive.name}: {len(index)} files extracted; all sizes and zlib streams verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    extract(args.archive, args.output)
