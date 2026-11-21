#!/usr/bin/env python3
"""Host model of the WT86047 persistent console ring (xnu patch 0253).

Transcribes the kernel's behaviour from bsd/dev/wt86047_sdhci.c and checks the
properties the decoder relies on:

  append():   before start, stage into a 256 KiB buffer; after start,
              ring[total++ % size] = byte
  start():    resume = header.total (0 when cold); total := resume;
              ready := true; replay the staged bytes; append a boot marker
  flush():    header.total := total, ring committed

The staging buffer is what stops a new boot from overwriting the oldest
retained bytes, so every boot in a dump must stay complete until the ring
itself wraps.  Host-only: no device, no XNU checkout.
"""
import atexit
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'sweet-log.py')
PY = sys.executable
TMP = tempfile.mkdtemp(prefix='wt86047-ring-')
atexit.register(shutil.rmtree, TMP, True)

V2 = struct.Struct('<12sIIQQIIQII')
fail = []


class Ring:
    """Line-by-line transcription of the kernel's behaviour."""

    def __init__(self, size, early_cap=256 << 10):
        self.size = size
        self.early_cap = early_cap
        self.buf = bytearray(size)
        self.total = 0
        self.header_total = 0
        self.boots = 0
        self.ready = False
        self.staged = bytearray()
        self.lost = 0

    def append(self, data):
        if not self.ready:
            room = self.early_cap - len(self.staged)
            take = min(len(data), room)
            self.staged += data[:take]
            self.lost += len(data) - take
            return
        for byte in data:
            self.buf[self.total % self.size] = byte
            self.total += 1

    def boot(self, early, boundary):
        self.total = 0
        self.staged = bytearray()
        self.lost = 0
        self.ready = False
        self.append(early)                 # output before the driver attaches
        self.total = self.header_total
        self.ready = True
        self.append(bytes(self.staged))    # replay at the resume point
        self.append(boundary)
        self.header_total = self.total
        self.boots = self.boots + 1 if self.boots else 1


def dump(ring, path):
    hdr = bytearray(V2.size)
    struct.pack_into('<12sIIQQIIQII', hdr, 0, b'WT86047LOG2', 2, ring.size,
                     ring.header_total, 1, ring.boots, 0, 0, 0, 0)
    struct.pack_into('<I', hdr, 52, zlib.crc32(bytes(hdr)) & 0xffffffff)
    with open(path, 'wb') as f:
        f.write(bytes(hdr).ljust(512, b'\x00'))
        f.write(bytes(ring.buf))


def decode(path):
    result = subprocess.run([PY, TOOL, path], capture_output=True)
    return (result.returncode, result.stdout.replace(b'\r\n', b'\n'),
            result.stderr.decode('utf-8', 'replace'))


def check(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name +
          (('  | ' + detail) if not cond else ''))
    if not cond:
        fail.append(name)


def boundary(n):
    return ('\n=== WT86047LOG boot %d continued, resume 0x0, carried 0 B '
            '===\n' % n).encode()


def main():
    # 1. three boots, no wrap: nothing may be lost
    stream = b''
    ring = Ring(4096)
    for n in (1, 2, 3):
        early = b'EARLY-%d ' % n
        mark = boundary(n)
        stream += early + mark
        ring.boot(early, mark)
    path = os.path.join(TMP, 'm1.bin')
    dump(ring, path)
    rc, out, err = decode(path)
    check('3 boots fit: contiguous stream', out == stream,
          '%d vs %d, heads %r vs %r'
          % (len(out), len(stream), out[:16], stream[:16]))
    check('3 boots fit: three markers', out.count(b'=== WT86047LOG boot ') == 3,
          str(out.count(b'=== WT86047LOG boot ')))
    check('3 boots fit: newest boot complete',
          out.endswith(b'EARLY-3 ' + boundary(3)), repr(out[-40:]))

    # 2. many boots, ring wraps: only the newest `size` bytes remain
    size = 512
    stream = b''
    ring = Ring(size)
    for n in range(1, 21):
        early = ('EARLY-BOOT-%02d ' % n).encode() * 3
        mark = ('\n=== WT86047LOG boot %d continued ===\n' % n).encode()
        stream += early + mark
        ring.boot(early, mark)
    path = os.path.join(TMP, 'm2.bin')
    dump(ring, path)
    rc, out, err = decode(path)
    check('20 boots wrap: exact tail', out == stream[-size:],
          '%d bytes, match %s' % (len(out), out == stream[-size:]))
    check('20 boots wrap: newest boot present', b'boot 20' in out,
          repr(out[-60:]))

    # 3. staging overflow is counted, not silently dropped
    ring = Ring(4096, early_cap=16)
    ring.boot(b'0123456789ABCDEF' * 2, boundary(1))
    check('staging overflow counted',
          ring.lost == 16 and bytes(ring.staged) == (b'0123456789ABCDEF' * 2)[:16],
          'lost=%d' % ring.lost)

    # 4. boot 2 starts exactly where boot 1 ended
    ring = Ring(1024)
    ring.boot(b'S1' * 100, boundary(1))
    end1 = ring.total
    ring.boot(b'S2' * 100, boundary(2))
    check('boot 2 resumes at boot 1 end',
          bytes(ring.buf[end1:end1 + 10]) == b'S2' * 5,
          repr(bytes(ring.buf[end1:end1 + 10])))

    print()
    if fail:
        print('FAILED: ' + ', '.join(fail))
        return 1
    print('all console log ring model tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
