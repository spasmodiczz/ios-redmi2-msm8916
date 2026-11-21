#!/usr/bin/env python3
"""
dtb_probe.py — 从高通 boot.img / dt.img 中提取并检索 FDT 节点。

用途：在没有串口、设备只有 fastboot 的情况下，本地确认 msm8916 的
SDCC / GIC / UART / MDP / SPMI / RPM 基址与面板参数，替代 dmesg。

用法:
  python dtb_probe.py boot.img                     # 列出所有 DTB 的平台标识
  python dtb_probe.py boot.img -m sdhci            # 检索名字含 sdhci 的节点
  python dtb_probe.py boot.img -m qcom,mdss_dsi    # 检索面板节点
  python dtb_probe.py boot.img --dump 3            # 完整导出第 3 个 DTB

依赖：仅标准库。
"""
import argparse
import struct
import sys

FDT_MAGIC = b"\xd0\x0d\xfe\xed"


# --------------------------------------------------------------------------- FDT
class FDT:
    def __init__(self, data: bytes, off: int = 0):
        self.buf = data[off:]
        magic, totalsize, off_struct, off_strings, off_rsv, version, last_comp, \
            boot_cpuid, size_strings, size_struct = struct.unpack(">10I", self.buf[:40])
        if magic != 0xD00DFEED:
            raise ValueError("not an FDT")
        self.total = totalsize
        self.struct_blk = self.buf[off_struct:off_struct + size_struct]
        self.strings = self.buf[off_strings:off_strings + size_strings]

    def s(self, nameoff: int) -> str:
        end = self.strings.find(b"\x00", nameoff)
        return self.strings[nameoff:end].decode("ascii", "replace")

    def nodes(self):
        """返回 [(path, props:dict[str, bytes])]，全部解析完再返回。"""
        out = []
        path = []
        i = 0
        props = None
        while i < len(self.struct_blk):
            tok = struct.unpack(">I", self.struct_blk[i:i + 4])[0]
            i += 4
            if tok == 1:  # FDT_BEGIN_NODE
                end = self.struct_blk.find(b"\x00", i)
                nm = self.struct_blk[i:end].decode("ascii", "replace")
                i = (end + 4) & ~3
                path.append(nm)
                props = {}
                out.append(("/".join(p for p in path if p) or "/", props))
            elif tok == 2:  # FDT_END_NODE
                if path:
                    path.pop()
                props = None
            elif tok == 3:  # FDT_PROP
                ln, nameoff = struct.unpack(">II", self.struct_blk[i:i + 8])
                i += 8
                val = self.struct_blk[i:i + ln]
                i = (i + ln + 3) & ~3
                if props is not None:
                    props[self.s(nameoff)] = val
            elif tok == 4:  # FDT_NOP
                continue
            elif tok == 9:  # FDT_END
                break
            else:
                raise ValueError(f"bad token {tok} at {i}")
        return out


def fmt_prop(name: str, val: bytes) -> str:
    if name in ("compatible", "status", "label", "qcom,mdss-dsi-panel-name",
                "qcom,mdss-dsi-panel-type", "qcom,mdss-pan-physical-width-dimension",
                "qcom,mdss-pan-physical-height-dimension", "model", "qcom,board-id",
                "qcom,msm-id", "qcom,pmic-id"):
        return " ".join(x.decode("ascii", "replace") for x in val.split(b"\x00") if x)
    if name in ("reg", "<ranges>", "interrupts", "qcom,mdss-dsi-panel-width",
                "qcom,mdss-dsi-panel-height", "qcom,mdss-dsi-h-front-porch",
                "qcom,mdss-dsi-h-back-porch", "qcom,mdss-dsi-h-pulse-width",
                "qcom,mdss-dsi-v-front-porch", "qcom,mdss-dsi-v-back-porch",
                "qcom,mdss-dsi-v-pulse-width", "clock-frequency", "interrupt-parent"):
        n = len(val) // 4
        if n and len(val) % 4 == 0:
            words = struct.unpack(f">{n}I", val)
            lines = []
            for k in range(0, n, 4):
                lines.append(" ".join(f"0x{w:x}" for w in words[k:k + 4]))
            return "\n" + "\n".join(lines)
    if len(val) <= 8:
        return " ".join(f"{b:02x}" for b in val)
    return f"<{len(val)} bytes> " + val[:32].hex()


# ------------------------------------------------------------------- boot image
def find_dtbs(data: bytes):
    """返回 [(offset, size)]，按 FDT totalsize 切片；相邻表可能重叠，做去重。"""
    out, pos = [], 0
    while True:
        pos = data.find(FDT_MAGIC, pos)
        if pos < 0:
            break
        try:
            total = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        except struct.error:
            break
        if 0x40 < total < 4 * 1024 * 1024 and pos + total <= len(data):
            out.append((pos, total))
            pos += total
        else:
            pos += 4
    return out


def describe(fdt: FDT) -> str:
    for path, props in fdt.nodes():
        if path == "/":
            mid = props.get("qcom,msm-id", b"")
            bid = props.get("qcom,board-id", b"")
            model = props.get("model", b"")
            ids = []
            if mid:
                n = len(mid) // 4
                w = struct.unpack(f">{n}I", mid)
                ids.append("msm-id=" + ",".join(f"{a}:{b}" for a, b in zip(w[::2], w[1::2])))
            if bid:
                n = len(bid) // 4
                w = struct.unpack(f">{n}I", bid)
                ids.append("board-id=" + ",".join(f"{a}:{b}" for a, b in zip(w[::2], w[1::2])))
            if model:
                ids.append("model=" + " ".join(x.decode("ascii", "replace") for x in model.split(b"\x00") if x))
            return " | ".join(ids) or "(no ids)"
    return "(unparsable)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("-m", "--match", default="", help="只显示路径或属性含该子串的节点")
    ap.add_argument("-p", "--prop", default="", help="只显示属性名含该子串的属性")
    ap.add_argument("--dump", type=int, default=-1, help="完整导出第 N 个 DTB")
    a = ap.parse_args()

    data = open(a.image, "rb").read()
    dtbs = find_dtbs(data)
    if not dtbs:
        print("未找到 FDT 魔数，可能不是 v0/v2 boot.img", file=sys.stderr)
        return 1

    print(f"共找到 {len(dtbs)} 个 DTB\n")
    for idx, (off, size) in enumerate(dtbs):
        try:
            f = FDT(data, off)
        except Exception as e:  # noqa: BLE001
            print(f"[{idx}] @0x{off:x} 解析失败: {e}")
            continue
        print(f"[{idx}] @0x{off:x} size=0x{size:x}  {describe(f)}")

    if a.dump >= 0:
        off, size = dtbs[a.dump]
        sys.stdout.buffer.write(data[off:off + size] if False else b"")
        with open(f"dtb_{a.dump}.dtb", "wb") as fh:
            fh.write(data[off:off + size])
        print(f"\n已写出 dtb_{a.dump}.dtb")
        return 0

    if not a.match and not a.prop:
        return 0

    print("\n" + "=" * 70)
    for idx, (off, size) in enumerate(dtbs):
        try:
            f = FDT(data, off)
        except Exception:  # noqa: BLE001
            continue
        head = describe(f)
        hits = []
        for path, props in f.nodes():
            if a.match and a.match.lower() in path.lower():
                hits.append((path, props))
                continue
            if a.prop:
                sel = {k: v for k, v in props.items() if a.prop.lower() in k.lower()}
                if sel:
                    hits.append((path, sel))
        if hits:
            print(f"\n### DTB[{idx}] @0x{off:x}  {head}")
            for path, props in hits:
                print(f"\n  /{path}")
                for k, v in props.items():
                    print(f"    {k} = {fmt_prop(k, v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
