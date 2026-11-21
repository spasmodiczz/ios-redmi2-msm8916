#!/usr/bin/env bash
# check-device.sh — 红米2 (wt86047) 移植前的设备体检。
#
# 只读：不发任何 flash / erase / boot 命令，只 getvar 和 adb 查询。
# 用法:  bash scripts/check-device.sh
set -uo pipefail

ok()   { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }
say()  { printf '\n== %s\n' "$*"; }

FAIL=0

say "fastboot 设备"
FB_DEVS=$(fastboot devices 2>/dev/null | awk 'NF{print $1}')
ADB_DEVS=$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device"{print $1}')

if [ -n "$FB_DEVS" ]; then
	ok "fastboot: $FB_DEVS"
	PRODUCT=$(fastboot getvar product 2>&1 | sed -n 's/^product: *//p' | head -1)
	SERIAL=$(fastboot getvar serialno 2>&1 | sed -n 's/^serialno: *//p' | head -1)
	UDATA=$(fastboot getvar partition-size:userdata 2>&1 | sed -n 's/^partition-size:userdata: *//p' | head -1)
	echo "        product=$PRODUCT  serialno=$SERIAL  userdata=$UDATA"

	case "$PRODUCT" in
		WT86047|wt86047) ok "机型是 wt86047（移动4G增强版）" ;;
		WT88047|wt88047) bad "这是 wt88047（联通/电信版），方案里的分区与面板假设要重算" ;;
		"") warn "读不到 product（老 aboot 有时只回 product 不带值）" ;;
		*)  bad "机型不匹配: $PRODUCT" ;;
	esac

	if [ -n "$UDATA" ] && [ "$UDATA" != "0x0" ]; then
		BYTES=$((UDATA))
		GB=$((BYTES / 1024 / 1024 / 1024))
		if [ "$GB" -ge 10 ]; then
			ok "userdata ${GB}GB，足够放 iOS HFS+ root（约 6-8GB）"
		else
			warn "userdata 只有 ${GB}GB，rootfs 会很紧"
		fi
		FAIL=$((FAIL+1-1))
	fi
else
	warn "fastboot 下没有设备（手机在当前模式下不可见）"
	FAIL=$((FAIL+1))
fi

say "adb 设备（Android / TWRP 下才有）"
if [ -n "$ADB_DEVS" ]; then
	ok "adb: $ADB_DEVS"
	echo "        ro.product.device = $(adb shell getprop ro.product.device 2>/dev/null | tr -d '\r')"
	echo "        ro.product.model  = $(adb shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
	MEM=$(adb shell cat /proc/meminfo 2>/dev/null | awk '/MemTotal/{print $2}')
	[ -n "${MEM:-}" ] && {
		MIB=$((MEM / 1024))
		if [ "$MIB" -gt 1700 ]; then ok "内存 ${MIB}MiB（2GB 版，符合要求）"
		else bad "内存只有 ${MIB}MiB（1GB 版，不要投入）"; fi
	}
	PST=$(adb shell "su -c 'ls -l /dev/block/bootdevice/by-name/'" 2>/dev/null | wc -l)
	[ "$PST" -gt 5 ] && ok "分区表可读（$PST 项）" || warn "分区表读不到，检查 root"
	echo "        当前 slot/系统: $(adb shell getprop ro.build.version.release 2>/dev/null | tr -d '\r')"
else
	warn "adb 下没有设备（正常，如果手机在 fastboot 或已变砖）"
fi

say "本地必需文件"
cd "$(dirname "$0")/.." || exit 1
for f in patches-8916/0142-wt86047-sdhci-emmc-driver.patch \
         ios-santoni/shim/build.sh \
         ios-santoni/tools/mkpayload.py \
         patches-8916/MANUAL-ADJUST.md; do
	[ -f "$f" ] && ok "$f" || bad "缺 $f"
done
STOCK="../wt86047_pro_images_V9.2.3.0.LHJCNEK_20171229.0000.00_5.1_cn/images/boot.img"
[ -f "$STOCK" ] && ok "官方 boot.img 在位（用于抽 DTB / 刷回）" || bad "缺官方 boot.img"

say "结论"
if grep -q "wt88047" <<<"$(fastboot getvar product 2>&1)"; then
	bad "机型是 wt88047，停止。方案按 wt86047 写"
	exit 2
fi
ok "设备侧检查通过；下一步 bash scripts/build-shim.sh"
