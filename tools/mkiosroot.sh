#!/bin/bash
# Build the wt86047 iOS root: one case-sensitive HFS+ image holding an iOS OS
# root filesystem plus its cryptexes, for the GPT "userdata" partition.
#
#   mkiosroot.sh OS.dmg SYSTEMOS.dmg APPOS.dmg OUT.img SIZE
#
#   SIZE is mandatory (see "Capacity" below). Accepts 11200, 11200M or 10G.
#
#   MEASURE=1    only mount the three images and report their sizes; no output
#                image is created. Run this first to pick SIZE.
#   XATTR=1      additionally copy extended attributes. Linux hfsplus has its
#                own xattr namespace and XNU expects Apple's, so this is
#                experimental and off by default.
#   NOJOURNAL=1  build without an HFS+ journal (saves ~2% of the volume). XNU
#                then cannot replay a journal after a panic; only for tight
#                size budgets.
#
# The IPSW images are raw APFS with LZBITMAP-compressed files: they are read
# through apfs-fuse (decompressing), so the HFS+ copy is uncompressed. On a
# device /private/preboot is its own volume holding the cryptexes, which
# /System/Cryptexes/{OS,App} link to; here they live inside the one volume.
# Owners, modes, symlinks and hard links are preserved (rsync -aH as root).
#
# The volume is journaled (XNU replays the journal after a panic; Linux only
# mounts it read-write with -o force and leaves the journal empty). SWEET
# additions: /System/Library/Caches/com.apple.dyld hard-links the SystemOS
# cryptex shared cache (ignition_level=1 looks there),
# /private/preboot/<boot-manifest-hash>/usr/standalone/firmware is the bindfs
# source that /sbin/mount -P 1 mounts over /usr/standalone/firmware, and
# /private/var/keybags/systembag.kb is the plain-plist system keybag that
# keybagd reads with /defaults/no-effaceable-storage (tools/mkadt.py).
#
# Capacity (measured on this device, do not re-derive by hand):
#   GPT "userdata" = 0x2c11e7e00 bytes = 11,829,935,616 B = 11.0 GiB = 11742 MiB
#   first LBA 3670016. A larger image cannot be flashed; a much smaller one
#   wastes the partition. 11200 MiB leaves room for the 1.06 fudge factor
#   (catalog/extents B-tree, allocation bitmap, journal).
#
# Case sensitivity matters: this volume holds both /System and /system as
# distinct names, so never let a case-insensitive tool (or a Windows mount)
# touch it.
#
# Root device: this device has no UFS. The kernel mounts the image as
#   rd=emmcroot0            (patch 0142, BSD device "emmcroot0")
# with emmcrw=1 only when the root must be writable (patch 0143 logs into the
# "system" partition, and 0031 turns rootrw=1 into a read-write root).
set -euo pipefail

MAX_MIB=11742                  # 11,829,935,616 B / 1 MiB = 11281.6, minus slack
MEASURE=${MEASURE:-0}
XATTR=${XATTR:-0}
NOJOURNAL=${NOJOURNAL:-0}

usage() { sed -n '2,/^set -euo/{/^set -euo/d;p}' "$0"; exit 2; }

[[ $# -ge 5 ]] || usage
os=$(realpath "$1") sysos=$(realpath "$2") appos=$(realpath "$3") out=$4 size_arg=$5

parse_mib() {
	case "$1" in
	*[Gg]) echo $(( ${1%[Gg]} * 1024 )) ;;
	*[Mm]) echo "$(( ${1%[Mm]} ))" ;;
	*[Kk]) echo $(( ${1%[Kk]} / 1024 )) ;;
	*[!0-9]*) echo "mkiosroot: bad SIZE '$1'" >&2; exit 2 ;;
	*) echo "$1" ;;
	esac
}
SIZE_MIB=$(parse_mib "$size_arg")
if (( SIZE_MIB < 512 || SIZE_MIB > MAX_MIB )); then
	echo "mkiosroot: SIZE $SIZE_MIB MiB outside [512, $MAX_MIB]." >&2
	echo "  userdata is 11,829,935,616 B = 11742 MiB; anything bigger cannot be flashed." >&2
	exit 2
fi

[[ $MEASURE == 1 ]] || [[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 1; }

work=$(mktemp -d)
mounts=()
cleanup() {
	local busy=0
	for m in "${mounts[@]}"; do
		if mountpoint -q "$m"; then
			sudo umount "$m" 2>/dev/null || true
		fi
		mountpoint -q "$m" && busy=1
	done
	# Never recurse into a directory that is still mounted (the root is writable).
	if [[ $busy == 0 ]]; then
		rm -f "$work"/*.dmg
		rmdir "$work"/* "$work" 2>/dev/null || true
	else
		echo "still mounted under $work: left in place" >&2
	fi
}
trap cleanup EXIT

# apfs-fuse splits its options on ',' (e.g. in iPhone10,3 paths): use links.
# Keep the .dmg name: apfs-fuse only parses UDIF images (the OS one is UDIF,
# the cryptexes are raw APFS) when the file name ends in .dmg.
apfs_mount() {
	ln -s "$1" "$work/$2.dmg"
	mkdir "$work/$2"
	# As root: a user's FUSE mount is unreadable to the root rsync below.
	sudo "$(command -v apfs-fuse)" "$work/$2.dmg" "$work/$2"
	mounts+=("$work/$2")
}
apfs_mount "$os" os
apfs_mount "$sysos" sysos
apfs_mount "$appos" appos

if [[ $MEASURE == 1 ]]; then
	echo "=== MEASURE: 三卷实际占用（apparent size，字节） ==="
	for v in os sysos appos; do
		[[ -d $work/$v/root ]] || { echo "  $v: 无 root/ 目录" >&2; exit 1; }
		n=$(sudo find "$work/$v/root" -type f 2>/dev/null | wc -l)
		b=$(sudo du -sB1 --apparent-size "$work/$v/root" | cut -f1)
		printf '  %-6s %12s B  (%s MiB)  %s files\n' "$v" "$b" "$((b / 1048576))" "$n"
	done
	echo
	echo "提示：--apparent-size 偏小。HFS+ 与 ext4 同为 4 KiB 块，真实上限要看"
	echo "      allocated（用 rsync 拷到 ext4 staging 后 du -sB1）。"
	echo "      定 SIZE：ceil(allocated × 1.06 / 1 MiB)，且 ≤ $MAX_MIB。"
	exit 0
fi

truncate -s "${SIZE_MIB}M" "$out"
if [[ $NOJOURNAL == 1 ]]; then
	mkfs.hfsplus -s -v iOSRoot "$out"
else
	mkfs.hfsplus -s -J -v iOSRoot "$out"
fi
mkdir "$work/root"
sudo mount -t hfsplus -o loop,force "$out" "$work/root"
mounts=("$work/root" "${mounts[@]}")

# -a covers -p -o -g -t -l -D; --numeric-ids is required, otherwise id 272
# (_logd) and friends get mapped to nobody and logd refuses its own stores.
RSYNC_OPTS=(-aH --numeric-ids)
[[ $XATTR == 1 ]] && RSYNC_OPTS+=(-X)

copy() {
	echo "copying $1 -> ${2#"$work/root"}"
	sudo mkdir -p "$2"
	sudo rsync "${RSYNC_OPTS[@]}" "$1/" "$2/"
}
copy "$work/os/root" "$work/root"
copy "$work/sysos/root" "$work/root/private/preboot/Cryptexes/OS"
copy "$work/appos/root" "$work/root/private/preboot/Cryptexes/App"

# /sbin/mount -P 1 bind-mounts /private/preboot/<boot-manifest-hash>/usr/
# standalone/firmware over /usr/standalone/firmware and exits 74 (EX_IOERR) if
# that bind fails, which panics launchd's mount-phase-1 boot task. The hash is
# the ADT's /chosen/boot-manifest-hash, printed by /sbin/mount as upper-case
# hex; the volume is case-sensitive, so create that exact name.
#
# Ask mkadt.py instead of restating the digest: the two must stay equal, and a
# copied literal silently drifts.
here=$(cd "$(dirname "$0")" && pwd)
pbhash=$(python3 "$here/mkadt.py" --print-hash)
[[ ${#pbhash} == 96 ]] || { echo "mkiosroot: bad boot-manifest-hash '$pbhash'" >&2; exit 1; }
sudo install -d -o 0 -g 0 -m 0755 \
	"$work/root/private/preboot/$pbhash/usr/standalone/firmware"

# Shared cache where dyld looks at ignition level 1: hard links, no extra space.
dsc=private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld
sudo mkdir -p "$work/root/System/Library/Caches/com.apple.dyld"
for f in "$work/root/$dsc"/*; do
	[[ -e $f ]] || continue
	sudo ln "$f" "$work/root/System/Library/Caches/com.apple.dyld/${f##*/}"
done

# dyld does not search the cryptexes at ignition level 1: hard-link every
# cryptex file that has no counterpart in the OS root (libobjc-trampolines,
# libffi-trampolines, libstdc++, ...) to its root path. Hard links are what
# keeps this inside the partition budget -- never let a failure degrade into a
# copy (that adds gigabytes).
for c in OS App; do
	(cd "$work/root/private/preboot/Cryptexes/$c" && sudo find . ! -type d ! -path './System/Library/Caches/com.apple.dyld/*') |
	while IFS= read -r f; do
		f=${f#./}
		if ! sudo test -e "$work/root/$f" && ! sudo test -L "$work/root/$f"; then
			sudo mkdir -p "$work/root/$(dirname "$f")"
			src="$work/root/private/preboot/Cryptexes/$c/$f"
			# Linux hfsplus cannot hard-link symlinks: copy those.
			if sudo test -L "$src"; then sudo cp -P "$src" "$work/root/$f"; else sudo ln "$src" "$work/root/$f"; fi
			echo "linked $c:/$f"
		fi
	done
done

# logd's log stores, normally made at restore: it checks owner _logd (272)
# and mode, plus a .migrated marker, else asks a helper and crashes
# ("BUG IN LIBTRACE: Failed to migrate directory").
db="$work/root/private/var/db"
sudo install -d -o 272 -g 272 -m 0751 "$db/diagnostics"
sudo install -d -o 272 -g 272 -m 0755 "$db/uuidtext" "$db/diagnostics/timesync"
for d in diagnostics uuidtext diagnostics/timesync; do
	sudo install -o 272 -g 272 -m 0644 /dev/null "$db/$d/.migrated"
done

# System keybag for the built-in AppleKeyStore (no passcode).
sudo install -d -m 0700 -o 0 -g 0 "$work/root/private/var/keybags"
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
	'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	'<plist version="1.0"><dict><key>KeyBagKeys</key><data>U1dFRVQ=</data></dict></plist>' |
	sudo install -m 0600 -o 0 -g 0 /dev/stdin "$work/root/private/var/keybags/systembag.kb"

# --- pre-umount self-checks: a mount that is "clean" but missing launchd is
# --- not usable, and finding that out on the device costs a 10 GB flash.
echo "=== 自检 ==="
rc=0
check() {
	if sudo test -e "$work/root/$1"; then
		echo "  ok   /$1"
	else
		echo "  MISS /$1  <-- $2" >&2
		rc=1
	fi
}
check sbin/launchd                          "launchd 缺失，M2 不可能达成"
check System/Library/CoreServices/SystemVersion.plist "M2 的验收文件"
check "private/preboot/$pbhash/usr/standalone/firmware" "mount -P 1 的 bindfs 源"
if sudo test -d "$work/root/System/Library/Caches/com.apple.dyld" &&
   sudo find "$work/root/System/Library/Caches/com.apple.dyld" -type f -name 'dyld_shared_cache*' | grep -q .; then
	echo "  ok   dyld_shared_cache under Caches/com.apple.dyld"
else
	echo "  MISS dyld_shared_cache  <-- dyld 会在 ignition level 1 失败" >&2
	rc=1
fi
df -h "$work/root"
[[ $rc == 0 ]] || { echo "自检失败，镜像不保留" >&2; sudo umount "$work/root" && rm -f "$out"; exit 1; }

sudo umount "$work/root"
mounts=("${mounts[@]:1}")        # 根卷刚才 prepend 到 index 0

# Linux hfsplus decomposes some non-ASCII names (localized Shortcuts examples)
# differently from Apple's tables; let fsck_hfs rewrite them, then verify.
fsck.hfsplus -f -y "$out" | grep -v 'Illegal name' || true
fsck.hfsplus -f -n "$out" | tee /dev/stderr | grep -qi 'clean' ||
	echo "warning: fsck 没有报告 clean，XNU 可能拒绝只读挂载（见 PLAN 十一 #6）" >&2

{
	echo "size_mib=$SIZE_MIB"
	echo "bytes=$(stat -c%s "$out")"
	echo "journal=$([[ $NOJOURNAL == 1 ]] && echo off || echo on)"
	echo "xattr=$XATTR"
	echo "boot_manifest_hash=$pbhash"
	echo "built=$(date -Is)"
	sha256sum "$out"
} > "$out.log"
echo "镜像: $out ($(stat -c%s "$out") 字节)"
echo "记录: $out.log"
cat "$out.log"
echo
echo "烧写（只重写 userdata 分区，boot/system/persist 不动）："
echo "  fastboot flash userdata $(basename "$out")"
echo "首启必须只读：bash scripts/build-shim.sh <kernel> --rd-emmc-ro"
