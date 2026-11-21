#!/usr/bin/env bash
# mkroot.sh —— WSL 侧包装：从 IPSW 造 HFS+ rootfs 镜像，再送到 Windows 侧。
#
# 为什么需要包装：tools/mkiosroot.sh 要 mount -t hfsplus -o loop，而 loop 设备
# 只能作用于 ext4；WSL 的 /mnt/d（DrvFs/9p）不支持 losetup。所以先在 ext4 的
# WORK 目录里建镜像，最后 cp 到 build/rootfs/（Windows 侧可见，fastboot 要用）。
# WSL2 没有 USB 直通，fastboot 只能在 Windows 里跑。
#
# 用法（在 WSL 里）：
#   scripts/mkroot.sh --measure                  # 只测量，定 SIZE
#   scripts/mkroot.sh 11200                      # 建镜像，11200 MiB
#   scripts/mkroot.sh 11200 /path/OS.dmg /path/SYSTEMOS.dmg /path/APPOS.dmg
#   NOJOURNAL=1 scripts/mkroot.sh 10240          # 空间紧时去掉 journal
#
# 三个 dmg 不给时从 IPSW 的 BuildManifest.plist 里按 Manifest.OS / SystemOS /
# App 解析（iOS 16 的 dmg 文件名是哈希，按名字猜会错）。设备默认 iPhone10,3。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/ios-santoni/tools"
IPSW_DIR="${IPSW_DIR:-$ROOT/../ipsw-x}"
WORK="${WORK:-/root/rootfs-work}"
OUT_DIR="$ROOT/build/rootfs"
DEVICE="${DEVICE:-iPhone10,3}"
MAX_MIB=11742

die() { echo "mkroot: $*" >&2; exit 1; }

command -v mkfs.hfsplus >/dev/null || die "缺 mkfs.hfsplus（apt install hfsprogs）"
command -v apfs-fuse >/dev/null || die "缺 apfs-fuse（见 PLAN §六 G2-3）"
grep -qw hfsplus /proc/filesystems || die "当前 WSL 内核没有 hfsplus（先按 PLAN §六 G2-2 建内核）"

# ---------------------------------------------------------------- 参数
MEASURE_ONLY=0
if [[ ${1:-} == --measure ]]; then
	MEASURE_ONLY=1
	shift
fi
SIZE_MIB="${1:-${SIZE_MIB:-11200}}"
[[ $MEASURE_ONLY == 1 ]] || shift || true

OS_DMG="${1:-}" SYSOS_DMG="${2:-}" APPOS_DMG="${3:-}"
(( SIZE_MIB >= 512 && SIZE_MIB <= MAX_MIB )) ||
	die "SIZE $SIZE_MIB MiB 超出 [512, $MAX_MIB]（userdata 只有 11742 MiB）"

# ---------------------------------------------------------------- 找 dmg
resolve_dmgs() {
	local manifest
	manifest=$(find "$IPSW_DIR" -maxdepth 2 -name 'BuildManifest.plist' | head -1)
	[[ -n $manifest ]] || die "找不到 BuildManifest.plist（IPSW_DIR=$IPSW_DIR）"
	python3 - "$manifest" "$DEVICE" "$IPSW_DIR" <<'PY'
import os, plistlib, sys
manifest, device, root = sys.argv[1], sys.argv[2], sys.argv[3]
with open(manifest, 'rb') as f:
    m = plistlib.load(f)
ids = [i for i in m.get('BuildIdentities', [])
       if str(i.get('Info', {}).get('ProductType', '')).startswith(device.split(',')[0])
       or device in str(i.get('Ap,ProductType', ''))]
if not ids:
    sys.exit(f'mkroot: BuildManifest 里没有 {device} 的 BuildIdentity')
ident = ids[0]
man = ident.get('Manifest', {})
paths = {}
for role in ('OS', 'SystemOS', 'App'):
    info = man.get(role, {})
    p = info.get('Info', {}).get('Path')
    if not p:
        sys.exit(f'mkroot: BuildIdentity 缺 Manifest.{role}.Info.Path')
    full = os.path.join(root, p)
    if not os.path.exists(full):
        sys.exit(f'mkroot: 期望的 {role} 镜像不存在: {full}')
    paths[role] = full
print('\n'.join(f'{k}={v}' for k, v in paths.items()))
PY
}

if [[ -z $OS_DMG || -z $SYSOS_DMG || -z $APPOS_DMG ]]; then
	echo "从 BuildManifest.plist 解析三个 dmg（$DEVICE）..."
	resolved=$(resolve_dmgs)
	OS_DMG=$(sed -n 's/^OS=//p' <<<"$resolved")
	SYSOS_DMG=$(sed -n 's/^SystemOS=//p' <<<"$resolved")
	APPOS_DMG=$(sed -n 's/^App=//p' <<<"$resolved")
fi
for f in "$OS_DMG" "$SYSOS_DMG" "$APPOS_DMG"; do
	[[ -f $f ]] || die "不存在: $f"
done

echo "OS      = $OS_DMG"
echo "SystemOS= $SYSOS_DMG"
echo "App     = $APPOS_DMG"

mkdir -p "$WORK"
if [[ $MEASURE_ONLY == 1 ]]; then
	echo "模式: MEASURE（只测占用，不建镜像）"
	MEASURE=1 bash "$TOOLS/mkiosroot.sh" "$OS_DMG" "$SYSOS_DMG" "$APPOS_DMG" \
		"$WORK/measure.img" 11200
	exit 0
fi

IMG="$WORK/rootfs.img"
rm -f "$IMG" "$IMG.log"
echo "模式: BUILD  SIZE=${SIZE_MIB} MiB  WORK=$WORK"
bash "$TOOLS/mkiosroot.sh" "$OS_DMG" "$SYSOS_DMG" "$APPOS_DMG" "$IMG" "$SIZE_MIB"

mkdir -p "$OUT_DIR"
echo "拷贝到 Windows 侧（$OUT_DIR）..."
cp "$IMG" "$OUT_DIR/rootfs.img"
cp "$IMG.log" "$OUT_DIR/rootfs-build.log"
sync
echo
echo "完成：$OUT_DIR/rootfs.img  $(stat -c%s "$OUT_DIR/rootfs.img") 字节"
echo "烧写前核对（Windows 侧）："
echo "  sha256sum build/rootfs/rootfs.img   # 应等于 build/rootfs/rootfs-build.log 里的值"
echo "  fastboot getvar partition-size:userdata   # 应为 0x2c11e7e00"
echo "  fastboot flash userdata build/rootfs/rootfs.img"
echo "首启只读：bash scripts/build-shim.sh <kernel> --rd-emmc-ro"
