#!/usr/bin/env bash
# build-shim.sh — 为 wt86047 (MSM8916) 构建 shim → XNU 的 fastboot 镜像。
#
# 全程只在本机编译，不接触手机。产物：build/wt86047/shim-boot.img
#
# 与 ios-santoni/shim/build.sh 的差异（全部是 8916 实测值）：
#   FB_BASE            0x90001000 → 0x83000000   (DTB splash_region@83000000, 20MB)
#   boot.img 参数        base 0x80000000 / kernel_offset 0x8000 / ramdisk_offset 0x02000000
#                       tags_offset 0x100 / pagesize 2048   ← 从官方 boot.img 头部读出
#   内核命令行          加 cpus=1（8916 无 PSCI，多核必失败），rd 默认 md0
#
# 依赖：clang + llvm-objcopy/nm/readelf + ld.lld（Linux/WSL2 或 macOS）
# 用法：
#   bash scripts/build-shim.sh                       # 只编 shim（诊断模式，ramdisk 空）
#   bash scripts/build-shim.sh kernel.macho          # 带 XNU payload
#   bash scripts/build-shim.sh kernel.macho --rd-emmc-ro  # 阶段 2 只读门（零写入）
#   bash scripts/build-shim.sh kernel.macho --rd-emmc     # 阶段 2 正式（可写 userdata）
#
# 环境变量：
#   PROBE=1        出帧缓冲探针镜像
#   PERSISTLOG=1   加 sweetlog=1（0253 的 16 MiB 环写进 system 分区开头 32769 扇区）
#   SWEET_D22AP_ADT=<path>  iPhone X 设备树，供 mkadt.py 生成 /product 节点
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/ios-santoni"
BUILD="$ROOT/build/wt86047"
FB_BASE="0x83000000"

# iPhone X（iPhone10,3 / D22AP）的真设备树，mkadt.py 用它生成 /product 节点
# （MobileGestalt 的机型、屏幕、图形属性都从这里来）。属 Apple 产物，不进 git。
# 默认放在工作区根下的 ipsw-x/，也可用 SWEET_D22AP_ADT 指定。
D22AP_ADT="${SWEET_D22AP_ADT:-$ROOT/../ipsw-x/d22ap.adt}"

PAYLOAD="${1:-}"
RD_MODE="${2:-}"

CC="${CC:-clang}"
OBJCOPY="${OBJCOPY:-llvm-objcopy}"
READELF="${READELF:-llvm-readelf}"
NM="${NM:-llvm-nm}"
LD="${LD:-ld.lld}"
HOST_CC="${HOST_CC:-clang}"

for t in "$CC" "$OBJCOPY" "$READELF" "$NM" "$LD"; do
	command -v "$t" >/dev/null || { echo "缺工具: $t（装 llvm/clang 或改用 macOS）" >&2; exit 1; }
done
[ -d "$SRC/shim" ] || { echo "缺 $SRC/shim，先跑 scripts/fetch-santoni.sh" >&2; exit 1; }

# ---------------------------------------------------------------- 准备构建树
# 复制而不是原地改：克隆下来的 ios-santoni 保持干净，方便 git diff / 回滚
rm -rf "$BUILD"
mkdir -p "$BUILD"
cp -r "$SRC/shim" "$BUILD/shim"
cp -r "$SRC/tools" "$BUILD/tools"
mkdir -p "$BUILD/shim/out"

# ---------------------------------------------------------------- QCDT
# wt86047 的 LK 是 Qualcomm boot_img_hdr v0：0x28 是 dt_size 不是 header_version，
# 缺了它 fastboot boot 直接回 "dtb not found"。这里从官方 boot.img 抽出追加式
# QCDT，mkbootimg.py --dt 负责写 dt_size 并把 blob 补页对齐追加到镜像尾部。
QCDT_SRC="${QCDT_SRC:-$ROOT/../wt86047_pro_images_V9.2.3.0.LHJCNEK_20171229.0000.00_5.1_cn/images/boot.img}"
QCDT="$BUILD/qcdt.bin"
[ -f "$QCDT_SRC" ] || { echo "缺官方 boot.img: $QCDT_SRC（可用 QCDT_SRC=... 覆盖）" >&2; exit 1; }
python3 "$ROOT/scripts/extract-qcdt.py" "$QCDT_SRC" "$QCDT"

# 帧缓冲基址：santoni 的连续 splash 区换成 8916 的
sed -i.bak -E "s@(#define[[:space:]]+FB_BASE[[:space:]]+)0x[0-9a-fA-F]+@\1$FB_BASE@" "$BUILD/shim/console.h"
grep -q "$FB_BASE" "$BUILD/shim/console.h" || { echo "FB_BASE 替换失败" >&2; exit 1; }
echo "FB_BASE → $(grep -E '^#define[[:space:]]+FB_BASE' "$BUILD/shim/console.h")"

cd "$BUILD/shim"

CFLAGS="-target aarch64-linux-gnu -O2 -ffreestanding -nostdlib -fno-pic -fno-jump-tables \
        -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"

# PROBE=1：只画帧缓冲探针（四色竖带 + stride 标记）然后停住，用来实测面板
# 通道顺序和 stride。见 shim.c 的 SHIM_FB_PROBE 段。
IMG_NAME="shim-boot.img"
if [ "${PROBE:-0}" = 1 ]; then
	CFLAGS="$CFLAGS -DSHIM_FB_PROBE"
	IMG_NAME="shim-probe-boot.img"
	echo "PROBE 模式：产物 $IMG_NAME"
fi

# ---------------------------------------------------------------- 编译 shim
for f in start.S shim.c console.c mmu.c fdt.c loader.c gic.c timer.c wdt.c; do
	$CC $CFLAGS -c "$f" -o "out/${f%.*}.o"
done

ABS=$($READELF -r out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/gic.o out/timer.o out/wdt.o \
      | grep -c ABS64 || true)
[ "$ABS" = 0 ] || { echo "error: $ABS 个绝对重定位，shim 会被装载到错误地址" >&2; exit 1; }

$LD -T shim.ld --build-id=none -N \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/gic.o out/timer.o out/wdt.o \
	-o out/shim.elf
$OBJCOPY -O binary out/shim.elf out/Image

SIZE=$((16#$($NM out/shim.elf | awk '$3=="_end"{print $1}')))
truncate -s "$SIZE" out/Image
echo "shim Image: $(stat -c%s out/Image) 字节"

# PID 1 测试程序（静态 Mach-O）
$CC -target arm64-apple-ios16.0 -c ../tools/wt86047-init.S -o out/init.o 2>/dev/null \
	|| $CC -target arm64-apple-ios16.0 -c ../tools/santoni-init.S -o out/init.o
python3 ../tools/mkinit.py out/init.o out/init.macho

# ---------------------------------------------------------------- 命令行
# 注意 sweetlog 不在默认值里：0253 的日志线程会把 16 MiB 环写进 "system" 分区
# 开头 32769 个扇区。默认关闭，任何一次普通启动（含阶段 2 的只读门）才不会写盘。
# 需要持久日志时显式 PERSISTLOG=1，之后用 tools/sweet-log-pull.sh 取回。
CMDLINE="debug=0x14e serial=3 disable_kprintf_output=0 \
sweetfb_native=1 sweetfb_noconsole=1 sweetfb_hz=30 sweetfb_stayon=1 sweethidtest=0 \
cpus=1 sweetcpufreq=0 sweetbattery=0 \
unentitled_ios_sim_launch=1 launchd_unsecure_cache=1 wdt=-1 wdt_test=no_user_checkin ignition_level=1 \
-v keepsyms=1 wt86047"

if [ "${PERSISTLOG:-0}" = 1 ]; then
	# 0143 走 wt86047_sdhci_write_log_blocks()，它有自己的 fence，不受 emmcrw
	# 约束。所以"只读门 + sweetlog"会真的写 system，字面上就不再是零写入了。
	if [ "$RD_MODE" = "--rd-emmc-ro" ]; then
		echo "error: --rd-emmc-ro 与 PERSISTLOG=1 互斥（后者会写 system 分区）" >&2
		exit 1
	fi
	CMDLINE="$CMDLINE sweetlog=1"
	echo "PERSISTLOG=1：会写 system 分区开头 32769 扇区（16 MiB 环 + 512B 头）"
	echo "  取回：fastboot boot twrp.img 后 sh ios-santoni/tools/sweet-log-pull.sh log.txt"
fi

# 根设备三档：
#   md0          根在内存里，不碰 eMMC（阶段 1 / 诊断）
#   emmcroot0    + rd-emmc-ro：只读挂载尝试。驱动照常注册并打印探针，但
#                se.writable=false（没 emmcrw）→ 任何写路径都返回 EROFS，
#                sweetlog 也未启用 → 真正的零块写入。阶段 2 的 Gate-4 与
#                rootfs 首启都用这一档。
#   emmcroot0    + rd-emmc：emmcrw=1 才允许写，且被 fence 限制在 userdata
#                分区内；rootrw=1 让 HFS+ 根以读写挂载（0031，WT86047 有效）。
case "$RD_MODE" in
	--rd-emmc)    CMDLINE="$CMDLINE rd=emmcroot0 emmcrw=1 rootrw=1" ;;
	--rd-emmc-ro) CMDLINE="$CMDLINE rd=emmcroot0" ;;
	*)            CMDLINE="$CMDLINE rd=md0" ;;
esac

# ---------------------------------------------------------------- payload
RAMDISK="out/ramdisk.bin"
if [ -n "$PAYLOAD" ] && [ -f "$PAYLOAD" ]; then
	MAGIC=$(python3 -c 'import sys;print(open(sys.argv[1],"rb").read(4).hex())' "$PAYLOAD")
	if [ "$MAGIC" = "cffaedfe" ] || [ "$MAGIC" = "feedfacf" ]; then
		echo "检测到 Mach-O 内核，生成 ADT 并打包 payload"
		# mkadt.py 的默认路径相对它自己算，而它此刻在 build/wt86047/tools/ 下，
		# 会去找 build/wt86047/research/ipsw-x/d22ap.adt —— 那里没有。必须显式传。
		if [ -f "$D22AP_ADT" ]; then
			export SWEET_D22AP_ADT="$D22AP_ADT"
			echo "D22AP 设备树 → $D22AP_ADT"
		else
			echo "警告：缺 $D22AP_ADT" >&2
			echo "      ADT 将没有 /product 节点，MobileGestalt 拿不到机型/屏幕信息。" >&2
			echo "      从 IPSW 提取 DeviceTree.d22ap.im4p 后放该路径，或设 SWEET_D22AP_ADT。" >&2
		fi
		python3 ../tools/mkadt.py out/wt86047.adt --native
		python3 ../tools/mkpayload.py "$PAYLOAD" out/wt86047.adt out/payload.bin \
			--cmdline "$CMDLINE" --ramdisk out/init.macho
		RAMDISK="out/payload.bin"
	else
		RAMDISK="$PAYLOAD"
	fi
else
	# 空 newc cpio：只验证 shim 自己能不能跑起来看到串口/framebuffer
	python3 - <<'PY'
import gzip
h=b"070701" + b"0"*104 + b"TRAILER!!!\0\0\0\0"
open("out/ramdisk.bin","wb").write(gzip.compress(h + b"\0"*(512-len(h)), compresslevel=9))
PY
fi

# ---------------------------------------------------------------- 打包
# 8916 官方 boot.img 参数：base 0x80000000, koff 0x8000, roff 0x02000000,
# tags 0x100, pagesize 2048（实测自 wt86047_pro_images_V9.2.3.0）
python3 ../tools/mkbootimg.py \
	--kernel out/Image \
	--ramdisk "$RAMDISK" \
	--dt "$QCDT" \
	--output "out/$IMG_NAME" \
	--base 0x80000000 \
	--kernel_offset 0x8000 \
	--ramdisk_offset 0x02000000 \
	--tags_offset 0x100 \
	--pagesize 2048 \
	--header_version 0 \
	--cmdline "$CMDLINE"

# 主机侧自测（不影响产物，失败就说明 shim 源码被改坏了）
$HOST_CC -O2 -Wall -I. -o out/gic_test gic_test.c && ./out/gic_test
$HOST_CC -O2 -Wall -I. -o out/loader_test loader_test.c loader.c && ./out/loader_test

echo
echo "产物: $BUILD/shim/out/$IMG_NAME"
echo "cmdline: $CMDLINE"
echo
echo "验证（不写 flash，只临时加载）："
echo "  fastboot boot \"$BUILD/shim/out/$IMG_NAME\""
echo "恢复：长按电源键强制重启，boot 分区从未被改写"
