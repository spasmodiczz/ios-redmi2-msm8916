#!/usr/bin/env bash
# Dry-run a single XNU patch against a pristine upstream tree.
#
# Why: dxb only *warns* when a patch fails to apply ("Skipping patch ..."), and
# a partially-applied set still produces a kernel, so a broken hunk costs a
# full CI round (10-15 min) before anyone notices. This catches it in seconds.
#
# Caveat: only valid for a patch whose target files are not modified by an
# earlier patch in the set. If they are, apply the earlier ones first (pass
# them as extra arguments, in order) -- the script applies each in sequence.
#
# Usage:
#   scripts/dryrun-patch.sh xnu/patches/0010-sweet-early-framebuffer-debug.patch
#   scripts/dryrun-patch.sh xnu/patches/0012-....patch xnu/patches/0010-....patch
#     (arguments are applied in the order given; the LAST one is the one under test)

set -u

UPSTREAM_TREE="${UPSTREAM_TREE:-$HOME/../d/01_手机与刷机/红米2/ios-port/upstream/xnu-xnu-8796.101.5}"
if [ ! -d "$UPSTREAM_TREE" ]; then
    # Fall back to a path relative to this script, for portability.
    here="$(cd "$(dirname "$0")/.." && pwd)"
    UPSTREAM_TREE="$here/upstream/xnu-xnu-8796.101.5"
fi
if [ ! -d "$UPSTREAM_TREE" ]; then
    echo "upstream tree not found; set UPSTREAM_TREE to an extracted xnu-8796 tree" >&2
    exit 2
fi

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <patch> [earlier patches ...]" >&2
    exit 2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp -r "$UPSTREAM_TREE" "$work/xnu"

rc=0
for p in "$@"; do
    if [ ! -f "$p" ]; then
        echo "no such patch: $p" >&2
        exit 2
    fi
    out="$( cd "$work/xnu" && patch -p1 --dry-run --forward < "$(cd "$(dirname "$p")" && pwd)/$(basename "$p")" 2>&1 )"
    st=$?
    if [ "$st" -ne 0 ] || echo "$out" | grep -qi "fail\|rej"; then
        echo "FAIL  $(basename "$p")"
        echo "$out" | egrep -i "fail|rej|Hunk" | head -10
        rc=1
        break
    fi
    echo "ok    $(basename "$p")"
done

exit "$rc"
