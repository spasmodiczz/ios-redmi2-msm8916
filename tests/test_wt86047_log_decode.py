#!/usr/bin/env python3
"""Host test for tools/sweet-log.py (WT86047 / SANTONI / SWEET console log).

Builds synthetic log areas and checks that the decoder reconstructs them:

  * v2 ("WT86047LOG2", patch 0253): header CRC, cumulative total, boot
    boundaries, wrap-around, --list / --boot N / --ring-size
  * v1 ("WT86047LOG1", patch 0143) and a blank SYSTEM partition
  * a 16 MiB area holding a 620 KB boot log, byte-exact round trip

Host-only: no device, no XNU checkout.  Run directly or via tests/run_tests.sh.
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
TMP = tempfile.mkdtemp(prefix='wt86047-log-')
atexit.register(shutil.rmtree, TMP, True)

V2 = struct.Struct('<12sIIQQIIQII')
fail = []


def emit(path, hdr_bytes, ring, block):
    with open(path, 'wb') as f:
        f.write(hdr_bytes.ljust(block, b'\x00'))
        f.write(bytes(ring))


def make_v2(path, stream, size, boots=3, flags=0, corrupt_crc=False,
            uptime_ms=12345):
    hdr = bytearray(V2.size)
    struct.pack_into('<12sIIQQIIQII', hdr, 0, b'WT86047LOG2', 2, size,
                     len(stream), 7, boots, flags, uptime_ms, 0, 0)
    crc = zlib.crc32(bytes(hdr)) & 0xffffffff
    if corrupt_crc:
        crc ^= 0xdeadbeef
    struct.pack_into('<I', hdr, 52, crc)
    ring = bytearray(size)
    tail = stream[-size:] if len(stream) > size else stream
    base = (len(stream) - len(tail)) % size
    for i, byte in enumerate(tail):
        ring[(base + i) % size] = byte
    emit(path, bytes(hdr), ring, 512)


def make_v1(path, stream, size=4 << 20):
    hdr = bytearray(512)
    struct.pack_into('<12sIIQQ', hdr, 0, b'WT86047LOG1', 1, size,
                     len(stream), 3)
    ring = bytearray(size)
    tail = stream[-size:] if len(stream) > size else stream
    base = (len(stream) - len(tail)) % size
    for i, byte in enumerate(tail):
        ring[(base + i) % size] = byte
    emit(path, bytes(hdr), ring, 512)


def norm(data):
    return data.replace(b'\r\n', b'\n')


def run(*args):
    result = subprocess.run([PY, TOOL] + list(args), capture_output=True)
    return (result.returncode, norm(result.stdout),
            result.stderr.decode('utf-8', 'replace'))


def check(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name +
          (('  | ' + detail) if not cond else ''))
    if not cond:
        fail.append(name)


def boot(n, tag, marker_tag):
    return ('\n=== WT86047LOG boot %d %s, resume 0x0, carried 0 B, '
            'ring 16384 KiB ===\n' % (n, marker_tag)).encode() + \
        tag * 900 + b'\n'


def main():
    # 1. first boot, total < ring: the text comes back verbatim
    stream = (b'\n=== WT86047LOG boot 1 cold, resume 0x0, carried 0 B, '
              b'ring 16384 KiB ===\nhello\nworld\n')
    path = os.path.join(TMP, 'a.bin')
    make_v2(path, stream, 16 << 20, boots=1, flags=1)
    rc, out, err = run(path)
    check('v2 short stream', rc == 0 and out == stream,
          '%d vs %d' % (len(out), len(stream)))
    check('v2 header summary', 'boot 1' in err and 'header CRC ok' in err, err)
    check('v2 cold flag', 'cold' in err, err)

    # 2. wrapped stream: only the tail survives
    boot1 = boot(1, b'A', 'cold')
    boot2 = boot(2, b'B', 'continued')
    path = os.path.join(TMP, 'b.bin')
    make_v2(path, boot1 + boot2, 1024, boots=2)
    rc, out, err = run(path)
    check('v2 wrap rc', rc == 0 and 'wrapped' in err, err)
    check('v2 wrap keeps tail', out.rstrip().endswith(b'B' * 10), repr(out[-20:]))
    check('v2 wrap drops head', out.count(b'A') < 901, str(out.count(b'A')))

    # 3. two boots that both fit: the split must be exact
    path2 = os.path.join(TMP, 'h.bin')
    make_v2(path2, boot1 + boot2, 8192, boots=2)
    rc, out, err = run(path2, '--list')
    check('--list shows two boots',
          rc == 0 and 'boot 2' in err and 'boot 1' in err, err)
    rc, out, err = run(path2, '--boot', '2')
    check('--boot 2 selects segment',
          rc == 0 and out.count(b'B') == 900 and b'A' not in out,
          str(out.count(b'B')))
    rc, out, err = run(path2, '--boot', '1')
    check('--boot 1 selects segment',
          rc == 0 and out.count(b'A') == 900 and b'B' not in out,
          str(out.count(b'A')))
    rc, out, err = run(path2, '--boot', '9')
    check('--boot 9 fails cleanly', rc != 0 and 'not found' in err, err)

    # 4. a CRC mismatch is reported but not fatal
    path = os.path.join(TMP, 'c.bin')
    make_v2(path, stream, 4096, corrupt_crc=True)
    rc, out, err = run(path)
    check('crc mismatch reported', rc == 0 and 'MISMATCH' in err, err)

    # 5. --ring-size reads the 512-byte header only
    path = os.path.join(TMP, 'd.bin')
    make_v2(path, stream, 16 << 20)
    rc, out, err = run('--ring-size', path)
    check('--ring-size v2', rc == 0 and out.strip().decode() == str(16 << 20),
          out.decode('latin1') + err)

    # 6. blank partition: warn, and decode must refuse
    path = os.path.join(TMP, 'e.bin')
    open(path, 'wb').write(b'\x00' * 4096)
    rc, out, err = run('--ring-size', path)
    check('blank area warns',
          'no WT86047 log header' in err and out.strip().isdigit(), err)
    rc, out, err = run(path)
    check('blank area decode fails',
          rc != 0 and 'no console log header' in err, err)

    # 7. a v1 dump from 0143 still decodes
    path = os.path.join(TMP, 'f.bin')
    make_v1(path, b'legacy v1 line\nlegacy v1 line 2\n')
    rc, out, err = run(path)
    check('v1 compatible',
          rc == 0 and 'WT86047LOG1' in err and b'legacy v1 line 2' in out, err)

    # 8. the marker may lose its trailing space (the kernel image stores the
    #    literal split), so parsing must not depend on it
    tight = (b'\n=== WT86047LOG boot4 continued, resume 0x0 ===\n' +
             b'C' * 50 + b'\n')
    path = os.path.join(TMP, 'i.bin')
    make_v2(path, tight, 4096, boots=4)
    rc, out, err = run(path, '--boot', '4')
    check('marker without trailing space',
          rc == 0 and out.count(b'C') == 50, str(out.count(b'C')))

    # 9. real 16 MiB area, 620 KB boot log: byte-exact round trip
    long_stream = b''.join(b'line %06d of a long boot log\n' % i
                           for i in range(20000))
    path = os.path.join(TMP, 'g.bin')
    make_v2(path, long_stream, 16 << 20)
    rc, out, err = run(path)
    check('16 MiB area round-trip', rc == 0 and out == long_stream,
          '%d vs %d' % (len(out), len(long_stream)))

    print()
    if fail:
        print('FAILED: ' + ', '.join(fail))
        return 1
    print('all console log decoder tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
