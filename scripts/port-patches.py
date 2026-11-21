#!/usr/bin/env python3
"""
port-patches.py — 把 ios-santoni 的 XNU 补丁链改造成 msm8916 / wt86047 版本。

设计原则（很重要，别乱改）：
  1. **不删补丁、不重排顺序**。XNU 补丁是顺序 `git apply` 的，后面的补丁的
     context 依赖前面的补丁已应用。删一个 0028 就会让 0031 之类的 apply 失败。
  2. 因此本脚本只做 **机械文本替换**（board 宏 / 机型名 / 核心数），把
     santoni 变成 wt86047，santoni 的全套补丁照旧按 0001..0230 顺序应用。
  3. 真正需要改逻辑的补丁（SDHCI 基址、PSCI→ACC、FB 基址）单独列在
     ADJUST 表里，输出到 patches-8916/MANUAL-ADJUST.md，由人确认后再动手。

用法:
  python scripts/port-patches.py                     # 生成 patches-8916/
  python scripts/port-patches.py --dry-run           # 只打印会改什么
  python scripts/port-patches.py --report out.csv    # 额外导出补丁清单
"""
import argparse
import csv
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ios-santoni" / "xnu" / "patches"
DST = ROOT / "patches-8916"

# ---------------------------------------------------------------- 文本替换表
# 顺序敏感：先长后短，避免 "SANTONI" 被 "santoni" 规则吃掉（大小写不同，安全）
SUBS = [
    ("ARM64_BOARD_CONFIG_SANTONI", "ARM64_BOARD_CONFIG_WT86047"),
    ("MASTER.arm64.santoni", "MASTER.arm64.wt86047"),
    ("MACHINE_FLAGS_ARM64_SANTONI", "MACHINE_FLAGS_ARM64_WT86047"),
    ("CONFIG_SANTONI", "CONFIG_WT86047"),
    ("SANTONI.h", "WT86047.h"),
    ("SANTONI", "WT86047"),
    ("santoni-init.S", "wt86047-init.S"),
    ("santoni", "wt86047"),
    ("Snapdragon 435 / MSM8937", "Snapdragon 410 / MSM8916"),
    ("MSM8937", "MSM8916"),
    ("Snapdragon 435", "Snapdragon 410"),
]

# 板级数值：santoni 8 核 -> 8916 4 核。只改“定义”，不改逻辑。
NUMERIC = [
    (r"(#define\s+SWEET_SMP_MAX_CPUS\s+)8\b", r"\g<1>4"),
    (r"(#define\s+SWEET_SMP_MAX_CPUS\s+)6\b", r"\g<1>4"),
    (r"(#define\s+MAX_CPUS\s+)8\b", r"\g<1>4"),
]

# ------------------------------------------------- 需要人工改逻辑的补丁清单
# 这些不是文本替换能解决的，必须在应用后逐个改代码/核对。
ADJUST = [
    ("0017-santoni-gicv2-rtclock.patch",
     "零改动。GICD 0x0b000000 / GICC 0x0b002000 与 8916 DTB 的 qcom,msm-qgic2 "
     "完全一致；virtual-timer-intid=20 从 FDT 读，8916 同样是 20。"),
    ("0065-sweet-smp-psci.patch",
     "★必须改。8916 原厂固件 enable-method=\"qcom,arm-cortex-acc\"，没有 PSCI。"
     "sweet_psci_cpu_on() 走 SMC 0xC4000003，在红米2 上会返回错误，而当前代码 "
     "在失败时 panic(\"NOT alive\")。改法：失败只 IOLog 不 panic；不带 cpus= "
     "启动参数时该补丁默认单核（cpus_boot_arg=1），可以先单核点亮。"),
    ("0085-santoni-board-target.patch",
     "文本替换即可（SANTONI→WT86047、MAX_CPUS 8→4、SWEET_SMP_MAX_CPUS 8→4）。"
     "另需在 MakeInc.def 的 SUPPORTED_ARM64_MACHINE_CONFIGS 加 WT86047。"),
    ("0142-santoni-sdhci-emmc-driver.patch",
     "几乎零改动。SDCC1: core 0x07824900 + vendor 0x07824000，8916 DTB 实测一致；"
     "驱动按 LK 交接态工作、不自己配 GCC/SPMI，正好绕开 8916 与 8937 的时钟树差异。"
     "需核对 vendor 寄存器偏移（CORE_HC_MODE +0x78、CORE_VENDOR_SPEC +0x10c）"
     "在 8916 上是否同值——两者都是 sdhci-msm v4，预期一致，用串口读回校验。"),
    ("0143-santoni-persistent-console-log.patch",
     "依赖 0142 的 system 分区日志区（8193 扇区）。8916 的 system 分区起始扇区不同，"
     "必须按 gpt_main0.bin 的实际分区表改偏移，否则会写坏 system。"),
    ("0098-santoni-early-console.patch",
     "UART 基址核对：8916 console = BLSP1_UART2 @ 0x78B0000（DTB: serial@78b0000，"
     "GPIO4/5 func2）。santoni 同为 0x78B0000，预期零改动，但要在 0098 里确认写死的地址。"),
    ("0168-santoni-panel-copy.patch",
     "FB 基址必须改：santoni 连续 splash 区 0x90001000，8916 是 0x83000000"
     "（DTB splash_region@83000000, size 0x1400000=20MB）。shim/console.h 的 FB_BASE "
     "同样要改，见 scripts/build-shim.sh。"),
    ("0092-topaz-direct-mdp-scanout.patch",
     "topaz(msm8953) 的 MDP 直扫路径，8916 的 mdss_mdp@1a00000 版本不同。"
     "第一阶段不要用：直接往 LK 留下的 splash 帧缓冲里画（CPU 渲染），"
     "这条只在需要 MDP 硬件合成时才动。"),
]


def classify(name: str) -> tuple[str, str]:
    """返回 (动作, 理由)"""
    n = name
    if n.startswith("topaz-"):
        return "keep-disabled", "topaz(msm8953) 专用，编译期被 #if 隔离；保留以维持补丁顺序"
    if re.search(r"gpu|a50x|a505|ir3|mesa", n):
        return "keep-disabled", "Adreno 505/GPU 路径；8916 是 Adreno 306，不适用，保留但不启用"
    if "ufs" in n:
        return "keep-dead", "UFS 驱动；8916 无 UFS，代码保留不调用（根设备走 0142 SDHCI）"
    if n.startswith("santoni-") or n.startswith("sweet-"):
        return "adapt", "通用/板级补丁，本脚本做文本替换"
    return "adapt", "通用补丁"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    if not SRC.is_dir():
        sys.exit(f"找不到补丁目录: {SRC}\n先执行 scripts/fetch-santoni.sh")

    patches = sorted(p for p in SRC.iterdir() if p.suffix == ".patch")
    if not patches:
        sys.exit("补丁目录为空")

    if not a.dry_run:
        DST.mkdir(exist_ok=True)

    rows, changed_total, manual_hits = [], 0, []
    for p in patches:
        raw = p.read_text(encoding="utf-8", errors="surrogateescape")
        new = raw
        for old, rep in SUBS:
            new = new.replace(old, rep)
        for pat, rep in NUMERIC:
            new = re.sub(pat, rep, new)

        action, why = classify(p.name)
        for adj_name, adj_note in ADJUST:
            if p.name == adj_name or p.name == adj_name.replace("santoni", "wt86047"):
                action, why = "manual", adj_note
                manual_hits.append((p.name, adj_note))
                break

        out_name = p.name
        for old, rep in SUBS:
            out_name = out_name.replace(old, rep)

        delta = sum(1 for x, y in zip(raw.splitlines(), new.splitlines()) if x != y)
        changed_total += delta

        if not a.dry_run:
            (DST / out_name).write_text(new, encoding="utf-8", errors="surrogateescape")

        rows.append((p.name, out_name, action, delta, why))

    # 说明文件
    if not a.dry_run:
        lines = ["# patches-8916 生成说明", "",
                 f"源：`ios-santoni/xnu/patches`（{len(patches)} 个补丁）",
                 f"目标：`patches-8916/`，全部按原编号顺序应用，**不删不改序**。", "",
                 "## 文本级替换", ""]
        lines += [f"- `{o}` → `{r}`" for o, r in SUBS]
        lines += ["", "## 数值改动（8 核 → 4 核）", ""]
        lines += [f"- 正则 `{p}` → `{r}`" for p, r in NUMERIC]
        lines += ["", "## 故意没改的 `Santoni`", "",
                  "剩下 101 处 `Santoni`（首字母大写）分布在 GPU 路径的注释和 "
                  "`\"SantoniGraphicsClient\"` 这个 IOKit user client 类名里。"
                  "该类名被 `tools/santoni-graphics/` 的用户态程序按字符串匹配，"
                  "8916 不做 GPU，改名只会引入不一致，所以保持原样。", ""]
        lines += ["", "## 必须人工改逻辑的补丁", ""]
        for name, note in ADJUST:
            lines += [f"### `{name}`", "", note, ""]
        (DST / "MANUAL-ADJUST.md").write_text("\n".join(lines), encoding="utf-8")

        with open(DST / "manifest.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["原补丁", "输出补丁", "动作", "改动行数", "理由"])
            w.writerows(rows)

    if a.report:
        with open(a.report, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["原补丁", "输出补丁", "动作", "改动行数", "理由"])
            w.writerows(rows)

    by_action = {}
    for r in rows:
        by_action[r[2]] = by_action.get(r[2], 0) + 1
    print(f"补丁总数 {len(patches)}，改动行 {changed_total}")
    for k, v in sorted(by_action.items()):
        print(f"  {k:14s} {v}")
    print(f"需人工改逻辑: {len(manual_hits)} 个 → patches-8916/MANUAL-ADJUST.md")
    if a.dry_run:
        print("(dry-run，未写文件)")


if __name__ == "__main__":
    main()
