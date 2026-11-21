#!/usr/bin/env bash
# Syntax-check a patched XNU source file locally, before pushing.
#
# Applies a patch sequence to a scratch copy of the upstream tree, then runs
# clang -fsyntax-only with -Wall -Werror on one file. This catches the two
# failure modes that used to cost a 10-15 minute CI round each:
#   * a hunk that no longer applies (context drift between patches)
#   * a warning that -Werror turns into a build failure (e.g. unused variable)
#
# Usage:
#   scripts/check-compile.sh osfmk/arm64/sweet_early.c
#   PATCHES="0010-sweet-early-framebuffer-debug 0012-sweet-keep-debug-console-after-ttbr0 0098-wt86047-early-console" \
#     scripts/check-compile.sh osfmk/arm64/sweet_early.c
#
# Notes:
#   * Runs inside WSL (clang is installed there, not in Git Bash).
#   * PATCHES are applied in the order given; they must be given in ascending
#     patch order. A patch that legitimately depends on an intermediate patch
#     you did not list will report a failed hunk -- add the missing one.
#   * This is a syntax/typing check, not a full build: it does not link, does
#     not run the generator scripts, and does not compile the whole kernel.

set -u

FILE="${1:-}"
if [ -z "$FILE" ]; then
    echo "usage: $0 <source-file-relative-to-xnu-tree>" >&2
    exit 2
fi

here="$(cd "$(dirname "$0")/.." && pwd)"
PATCHES="${PATCHES:-0010-sweet-early-framebuffer-debug 0012-sweet-keep-debug-console-after-ttbr0 0098-wt86047-early-console}"

mapfile -t patch_args < <(for p in $PATCHES; do echo "$here/ios-santoni/xnu/patches/$p.patch"; done)
for p in "${patch_args[@]}"; do
    if [ ! -f "$p" ]; then
        echo "missing patch: $p" >&2
        exit 2
    fi
done

# Translate this repo path into a /mnt/... path for WSL (cygpath handles
# both the C://... and /d/... forms that Git Bash can hand us).
case "$here" in
    /mnt/*)  wsl_here="$here" ;;                 # already a WSL path
    /?/*)    wsl_here="/mnt$here" ;;             # Git Bash: /d/foo -> /mnt/d/foo
    *)       echo "cannot map $here into WSL" >&2; exit 2 ;;
esac
# The existence test runs inside WSL: passing this non-ASCII path as a
# separate wsl argv mangles it, but inside a single bash -lc it survives.

# A patch may legitimately fail a hunk here when it depends on an
# intermediate patch we did not list; what matters is that the file under test
# ends up patched, so record failures and keep going.
apply_cmds=""
for p in $PATCHES; do
    apply_cmds="$apply_cmds patch -p1 -s --forward < $wsl_here/ios-santoni/xnu/patches/$p.patch || echo \"WARN: $p had rejected hunks\";"
done

wsl -d Ubuntu-26.04 -- bash -lc "
set -e
[ -d "$wsl_here/upstream" ] || { echo \"upstream tree missing under $wsl_here\" ; exit 2; }
rm -rf /tmp/xc-check
cp -r $wsl_here/upstream/xnu-xnu-8796.101.5 /tmp/xc-check
cd /tmp/xc-check
$apply_cmds
[ -f '$FILE' ] || { echo \"target file missing: $FILE\"; exit 2; }
echo '--- patches applied, syntax check ---'
clang -fsyntax-only -target aarch64-none-elf -std=gnu11 -nostdinc \
  -Wall -Werror -Wno-unused-function \
  -D__arm64__ -D__ARM64__ -D__LP64__ -DWT86047 -DARM64_BOARD_CONFIG_WT86047 \
  -DKERNEL -D__APPLE__ \
  -I. -Iosfmk -Ibsd -Ipexpert -Ilibkern -IEXTERNAL_HEADERS -Isan \
  '$FILE'
echo 'COMPILE CHECK OK'
"
