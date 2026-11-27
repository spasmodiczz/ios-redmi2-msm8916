#!/bin/sh
# Read the WT86047 / SWEET / SANTONI console log from a phone booted into
# recovery (fastboot boot twrp.img; adb root shell).
#   tools/sweet-log-pull.sh OUT.txt
#
# WT86047 (patches 0143 / 0253) and Santoni keep the log at the start of the
# idle SYSTEM partition: sector 0 is a 512-byte header that names the ring
# size, so the same script serves the 4 MiB v1 ring and the 16 MiB v2 ring --
# it reads the header, then exactly as much as the header promises.
# Sweet (patch 0047) keeps it in the last 1025 4 KiB blocks of USERDATA.
set -eu
out=${1:?usage: sweet-log-pull.sh OUT.txt}
here=$(cd "$(dirname "$0")" && pwd)
dev=$(adb shell 'for d in /dev/block/bootdevice/by-name/system /dev/block/by-name/system /dev/block/bootdevice/by-name/userdata /dev/block/by-name/userdata; do if [ -e "$d" ]; then printf "%s\n" "$d"; break; fi; done' | tr -d '\r')
[ -n "$dev" ] || { echo "system or userdata block device not found" >&2; exit 1; }

# Keep the by-name path: readlink turns SYSTEM into e.g. mmcblk0p23, which
# previously selected the USERDATA-tail branch and pulled the wrong log area.
case "$dev" in
/dev/block/bootdevice/by-name/system|/dev/block/by-name/system)
    adb exec-out "dd if=$dev bs=512 count=1 2>/dev/null" > "$out.hdr"
    ring=$(python3 "$here/sweet-log.py" --ring-size "$out.hdr")
    blocks=$((1 + ring / 512))
    echo "Found SYSTEM ($dev): ring $ring bytes -> $blocks blocks" >&2
    # One adb dd of the whole ring comes back short (seen: 9.5 MB of 16.7 MB),
    # which silently truncates the newest boot's early output and has produced
    # several wrong "the driver never ran" conclusions. Read it in 2 MiB chunks
    # and insist on the exact byte count; a short chunk is retried.
    want=$((blocks * 512))
    fsize() { python3 -c 'import os,sys; print(os.path.getsize(sys.argv[1]))' "$1"; }
    ftrunc() { python3 -c 'import os,sys; os.truncate(sys.argv[1], int(sys.argv[2]))' "$1" "$2"; }
    : > "$out.bin"
    off=0
    chunk=4096                     # 2 MiB per dd
    while [ "$off" -lt "$blocks" ]; do
        n=$((blocks - off))
        [ "$n" -gt "$chunk" ] && n=$chunk
        need=$((n * 512))
        tries=0
        while :; do
            adb exec-out "dd if=$dev bs=512 skip=$off count=$n 2>/dev/null" >> "$out.bin"
            got=$(fsize "$out.bin")
            [ "$got" -ge $((want)) ] && break
            have=$((got - off * 512))
            if [ "$have" -ge "$need" ]; then
                break
            fi
            ftrunc "$out.bin" $((off * 512))
            tries=$((tries + 1))
            if [ "$tries" -ge 4 ]; then
                echo "chunk at $off blocks failed after $tries tries ($have/$need bytes)" >&2
                rm -f "$out.hdr"
                exit 1
            fi
            sleep 1
        done
        off=$((off + n))
    done
    size=$(fsize "$out.bin")
    echo "$dev: read $blocks blocks in chunks ($size bytes)" >&2
    rm -f "$out.hdr"
    [ "$size" -eq "$want" ] || { ftrunc "$out.bin" "$want"; echo "trimmed to $want bytes" >&2; }
    ;;
/dev/block/bootdevice/by-name/userdata|/dev/block/by-name/userdata)
    bytes=$(adb shell blockdev --getsize64 "$dev" | tr -d '\r')
    skip=$((bytes / 4096 - 1025))
    adb exec-out "dd if=$dev bs=4096 skip=$skip count=1025 2>/dev/null" > "$out.bin"
    size=$(python3 -c 'import os,sys; print(os.path.getsize(sys.argv[1]))' "$out.bin")
    echo "$dev: $bytes bytes, read blocks $skip.. ($size bytes)" >&2
    [ "$size" -eq 4198400 ] || { echo "short USERDATA log read" >&2; exit 1; }
    ;;
*) echo "unexpected log partition path" >&2; exit 1 ;;
esac

python3 "$here/sweet-log.py" "$out.bin" "$out"
