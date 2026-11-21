#!/usr/bin/env python3
"""Decode the WT86047 / SWEET / SANTONI persistent console log.

  sweet-log.py AREA.bin [OUT.txt] [--list] [--boot N]
  sweet-log.py --ring-size HEADER.bin     # ring bytes implied by a 512B header

AREA.bin is extracted with tools/sweet-log-pull.sh.

Layouts
-------
WT86047 v2 (xnu patch 0253):
  SYSTEM sector 0 is a 60-byte header, magic "WT86047LOG2": 12s magic,
  u32 version, u32 ring size, u64 total, u64 flush seq, u32 boots, u32 flags,
  u64 last uptime ms, u32 header CRC32, u32 pad. Sectors 1.. hold the ring.
  `total` is cumulative across boots, so the ring is one continuous stream and
  a single pull returns every retained boot. Stream byte n lives at n % size.
WT86047 v1 (patch 0143) and Santoni:
  Same 512-byte header block, magic "WT86047LOG1" / "SANTONILOG1", 36-byte
  struct, 4 MiB ring, and `total` restarts at every boot.
Sweet (patch 0047):
  Last 1025 4 KiB blocks of USERDATA; 4 KiB header, magic "SWEETLOG".

Boot boundaries are lines the kernel injects at every start:
  === WT86047LOG boot 7 continued, resume 0x1a2b3c, carried 40960 B, ... ===
"""
import struct
import sys
import zlib

V2_HEADER = struct.Struct('<12sIIQQIIQII')   # 60 bytes
V2_CRC_OFFSET = 52
V1_HEADER = struct.Struct('<12sIIQQ')        # 36 bytes
SWEET_HEADER = struct.Struct('<8sIIQQ')

DEFAULT_RING = 8 << 20       # v1 / Santoni ring, when the header is unreadable
V2_RING_HINT = 16 << 20      # only used to size a read of a blank partition
BOOT_PREFIX = '=== WT86047LOG boot '


def magic_string(magic):
    return magic.decode('latin1', 'replace').strip('\x00')


def ring_size(path):
    """Ring bytes promised by the 512-byte header at the start of `path`."""
    data = open(path, 'rb').read(512)
    if data.startswith(b'WT86047LOG2'):
        return V2_HEADER.unpack_from(data, 0)[2]
    if data.startswith(b'WT86047LOG1'):
        return DEFAULT_RING
    if data.startswith(b'SANTONILOG1'):
        return DEFAULT_RING
    print('no WT86047 log header in %s (log never flushed, or blank area)'
          % path, file=sys.stderr)
    return V2_RING_HINT


def parse(data):
    """-> (ring size, total, magic string, header size, info string)."""
    if data.startswith(b'WT86047LOG2'):
        (magic, version, size, total, seq, boots, flags, uptime_ms, crc,
         _pad) = V2_HEADER.unpack_from(data, 0)
        raw = bytearray(data[:V2_HEADER.size])
        stored = crc
        struct.pack_into('<I', raw, V2_CRC_OFFSET, 0)
        ok = (zlib.crc32(bytes(raw)) & 0xffffffff) == stored
        info = ('version %u, ring %u KiB, total %u bytes, flush #%u, boot %u'
                '%s, last flush at %u.%03u s, header CRC %s'
                % (version, size >> 10, total, seq, boots,
                   ' (cold: history restarts here)' if flags & 1 else '',
                   uptime_ms // 1000, uptime_ms % 1000,
                   'ok' if ok else 'MISMATCH (half-written header?)'))
        return size, total, magic_string(magic), 512, info

    if data.startswith(b'SWEETLOG'):
        magic, version, size, total, seq = SWEET_HEADER.unpack_from(data, 0)
        hdr = 4096
    else:
        magic, version, size, total, seq = V1_HEADER.unpack_from(data, 0)
        hdr = 512
    info = ('version %u, ring %u KiB, total %u bytes, flush #%u'
            % (version, size >> 10, total, seq))
    return size, total, magic_string(magic), hdr, info


def split_boots(text):
    """[(label, [lines])]; text before the first marker becomes its own part."""
    segs, cur = [], None
    for line in text.split('\n'):
        if line.startswith(BOOT_PREFIX):
            cur = []
            segs.append((line.strip(), cur))
            continue
        if cur is None:
            cur = []
            segs.append(('(before the first boot marker)', cur))
        cur.append(line)
    return segs


def parse_args(argv):
    show_list = False
    want_boot = None
    positional = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--list':
            show_list = True
        elif arg == '--boot':
            i += 1
            if i >= len(argv):
                sys.exit('--boot needs a number')
            try:
                want_boot = int(argv[i])
            except ValueError:
                sys.exit('--boot needs a number, not %r' % argv[i])
        elif arg.startswith('--'):
            sys.exit('unknown option %s' % arg)
        else:
            positional.append(arg)
        i += 1
    return show_list, want_boot, positional


def main(argv):
    if argv and argv[0] == '--ring-size':
        if len(argv) != 2:
            sys.exit('usage: sweet-log.py --ring-size HEADER.bin')
        print(ring_size(argv[1]))
        return 0

    show_list, want_boot, positional = parse_args(argv)
    if not positional:
        sys.exit(__doc__)
    area = positional[0]
    out_path = positional[1] if len(positional) > 1 else None

    data = open(area, 'rb').read()
    if not data.startswith((b'WT86047LOG2', b'WT86047LOG1', b'SANTONILOG1',
                            b'SWEETLOG')):
        sys.exit('no console log header (WT86047LOG2 / WT86047LOG1 / '
                 'SANTONILOG1 / SWEETLOG): log never flushed, or wrong area')

    size, total, magic, hdr_size, info = parse(data)
    ring = data[hdr_size:hdr_size + size]
    if len(ring) < size:
        print('warning: area holds %u of %u ring bytes'
              % (len(ring), size), file=sys.stderr)

    if total <= size:
        text, wrapped = ring[:total], False
    else:
        start = total % size
        text, wrapped = ring[start:] + ring[:start], True

    print('# %s: %s%s' % (magic, info,
                          ' (wrapped: older bytes overwritten)' if wrapped
                          else ''), file=sys.stderr)

    decoded = text.decode('utf-8', 'replace').replace('\r', '')
    segs = split_boots(decoded)

    if show_list:
        for label, lines in segs:
            filled = [ln for ln in lines if ln.strip()]
            print('%-72s [%u lines]' % (label, len(filled)), file=sys.stderr)
        return 0

    if want_boot is not None:
        key = '%s%u ' % (BOOT_PREFIX, want_boot)
        picked = [lines for label, lines in segs if label.startswith(key)]
        if not picked:
            sys.exit('boot %u not found in this dump' % want_boot)
        out = '\n'.join(picked[0])
    else:
        out = decoded

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as handle:
            handle.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
