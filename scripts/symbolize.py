#!/usr/bin/env python3
"""Map kernel text addresses from a panic backtrace to symbols.

Reads the Mach-O symbol table of a kernel.development.wt86047 image and prints
the containing symbol plus the offset for each address given on the command
line. Return addresses are resolved against symbol-1 (a return address points
just past the call), which is what makes the printed name the real caller.

Usage:
    python scripts/symbolize.py 0xfffffff00743804c 0xfffffff007123456
    python scripts/symbolize.py --image kernel-out19/kernel.development.wt86047 0xfffffff00743804c

Notes:
    * Accepts bare hex (no 0x) too, and the kernel's KASLR slide is 0 on this
      port: the image is loaded at the link address, so panic addresses can be
      used verbatim.
    * Symbols come from the stripped image shipped in the artifact zip; it
      still carries LC_SYMTAB (the keepsyms boot-arg keeps linkedit).
"""

import argparse
import bisect
import struct
import sys


def load_symbols(path):
    data = open(path, "rb").read()
    magic = struct.unpack("<I", data[:4])[0]
    if magic not in (0xFEEDFACF, 0xFEEDFACE):
        raise SystemExit("not a Mach-O file: %s" % path)
    if magic == 0xFEEDFACE:
        raise SystemExit("32-bit Mach-O is not expected here")

    ncmds = struct.unpack("<I", data[16:20])[0]
    off = 32
    symtab = None
    for _ in range(ncmds):
        cmd, size = struct.unpack("<II", data[off:off + 8])
        if cmd == 0x2:  # LC_SYMTAB
            symtab = struct.unpack("<IIII", data[off + 8:off + 24])
        off += size
    if not symtab:
        raise SystemExit("no LC_SYMTAB in %s (image is fully stripped)" % path)
    symoff, nsyms, stroff, strsize = symtab

    syms = []
    for i in range(nsyms):
        base = symoff + i * 16
        n_strx = struct.unpack("<I", data[base:base + 4])[0]
        n_type = data[base + 4]
        n_value = struct.unpack("<Q", data[base + 8:base + 16])[0]
        if n_type & 0x0E != 0x0E:  # N_SECT only
            continue
        if n_strx >= strsize or n_value == 0:
            continue
        end = data.index(b"\0", stroff + n_strx)
        name = data[stroff + n_strx:end].decode("utf-8", "replace")
        syms.append((n_value, name))

    syms.sort()
    return [s[0] for s in syms], syms


def resolve(addrs, vals, syms, adjust):
    out = []
    for a in addrs:
        probe = a - adjust
        i = bisect.bisect_right(vals, probe) - 1
        if i < 0:
            out.append((a, None, None))
        else:
            out.append((a, syms[i][1], probe - syms[i][0]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("addrs", nargs="+")
    ap.add_argument("--image", default="kernel-out19/kernel.development.wt86047",
                    help="kernel Mach-O with a symbol table")
    ap.add_argument("--no-adjust", action="store_true",
                    help="do not subtract 1 from the address (use for PCs, not return addresses)")
    args = ap.parse_args()

    vals, syms = load_symbols(args.image)
    print("image %s: %d symbols" % (args.image, len(syms)))

    parsed = []
    for s in args.addrs:
        parsed.append(int(s, 16) if not s.startswith("0x") else int(s, 16))

    for a, name, delta in resolve(parsed, vals, syms, 0 if args.no_adjust else 1):
        if name is None:
            print("%#x  <below first symbol>" % a)
        else:
            print("%#x  %s + %#x" % (a, name, delta))


if __name__ == "__main__":
    sys.exit(main())
