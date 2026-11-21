#!/usr/bin/env python3
"""Decode the SWEET / SANTONI / WT86047 persistent console log (patches 0047 / 0143).

  sweet-log.py AREA.bin [OUT.txt]

AREA.bin is extracted via tools/sweet-log-pull.sh:
- Santoni: First 8193 blocks (512B) of the idle SYSTEM partition.
  Block 0 is the 512B header ("SANTONILOG1", u32 version, u32 ring size,
  u64 total bytes, u64 flush seq), followed by the 4 MiB circular ring buffer.
- wt86047: same layout and same 8193-block SYSTEM area, but 0143 stamps the
  header "WT86047LOG1" instead (12-byte magic, 36-byte struct, 512B block).
- Sweet: Last 1025 blocks (4096B) of USERDATA partition.
  Block 0 is the 4096B header ("SWEETLOG", ...), followed by the ring.

Byte n of the console stream is at ring offset n % size.
"""
import struct
import sys

data = open(sys.argv[1], 'rb').read()

if data.startswith((b'SANTONILOG1', b'WT86047LOG1')):
    magic, version, size, total, seq = struct.unpack_from('<12sIIQQ', data, 0)
    hdr_size = 512
elif data.startswith(b'SWEETLOG'):
    magic, version, size, total, seq = struct.unpack_from('<8sIIQQ', data, 0)
    hdr_size = 4096
else:
    sys.exit('no SWEETLOG / SANTONILOG1 / WT86047LOG1 header '
             '(log never flushed, or wrong area)')

ring = data[hdr_size:hdr_size + size]
if total <= size:
    text = ring[:total]
else:
    start = total % size
    text = ring[start:] + ring[:start]

out = text.decode('utf-8', 'replace').replace('\r', '')
magic_str = magic.decode('latin1', 'replace').strip('\x00')
print(f'# {magic_str}: version {version}, ring {size}, total {total} bytes, flush #{seq}'
      f'{" (wrapped: oldest bytes lost)" if total > size else ""}', file=sys.stderr)

if len(sys.argv) > 2:
    with open(sys.argv[2], 'w', encoding='utf-8') as f:
        f.write(out)
else:
    sys.stdout.write(out)
