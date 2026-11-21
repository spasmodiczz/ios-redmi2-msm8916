#!/usr/bin/env python3
"""
从官方 Qualcomm boot.img 抽出追加式 QCDT（device tree blob 表），补页对齐后落地。

为什么需要：wt86047 的 LK（aboot）用的是 **Qualcomm 老式 boot_img_hdr v0 布局**，
offset 0x28 那个字段不是 AOSP 的 header_version，而是 dt_size。它会：
  1. 要求 dt_size != 0，否则 fastboot boot 直接回 "dtb not found"
  2. 按 page_align(hdr)+page_align(kernel)+page_align(ramdisk)+page_align(second)
     算出 dt 起点，从这里读 QCDT 表
  3. 还要求 dt_size 按 2048 对齐，否则回 "bootimage: incomplete or not signed"

用法：
  python3 scripts/extract-qcdt.py <官方boot.img> <输出qcdt.bin>
"""
import struct
import sys

PAGE_DEFAULT = 2048


def align(x, p):
    return (x + p - 1) // p * p


def main():
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    src, dst = sys.argv[1], sys.argv[2]
    with open(src, "rb") as f:
        d = f.read()

    if d[:8] != b"ANDROID!":
        print(f"{src}: not an Android boot image", file=sys.stderr)
        return 1

    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr,
     second_size, second_addr, tags_addr, page_size, dt_size) = struct.unpack("<9I", d[8:44])

    if page_size not in (2048, 4096, 8192, 16384):
        print(f"{src}: implausible page_size {page_size}", file=sys.stderr)
        return 1
    if dt_size == 0:
        print(f"{src}: dt_size is 0, image carries no appended QCDT", file=sys.stderr)
        return 1

    off = (align(1632, page_size) + align(kernel_size, page_size)
           + align(ramdisk_size, page_size) + align(second_size, page_size))

    if off + dt_size > len(d):
        print(f"{src}: dt blob {off:#x}+{dt_size:#x} exceeds file ({len(d):#x})",
              file=sys.stderr)
        return 1
    if d[off:off + 4] != b"QCDT":
        print(f"{src}: no QCDT magic at {off:#x} (found {d[off:off + 4]!r})", file=sys.stderr)
        return 1

    blob = d[off:off + dt_size]
    pad = (-len(blob)) % page_size
    blob += b"\0" * pad

    with open(dst, "wb") as f:
        f.write(blob)

    print(f"QCDT: {off:#x}..{off + dt_size:#x} ({dt_size}B) + {pad}B pad -> {dst} ({len(blob)}B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
