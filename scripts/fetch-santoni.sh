#!/usr/bin/env bash
# fetch-santoni.sh — 拉取/更新 ios-santoni 参考仓库（santoni 分支）。
# 只读 GitHub，不改手机。默认走本机代理 127.0.0.1:7897。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/ios-santoni"
BRANCH="${1:-santoni}"
PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"

export HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" NO_PROXY="localhost,127.0.0.1,::1"

if [ -d "$DEST/.git" ]; then
	echo "已存在，更新到最新 $BRANCH"
	git -C "$DEST" fetch --depth=1 origin "$BRANCH"
	git -C "$DEST" checkout -f FETCH_HEAD
else
	git clone --depth=1 --branch "$BRANCH" \
		https://github.com/console-ramoops/ios-santoni.git "$DEST"
fi

echo
echo "commit: $(git -C "$DEST" rev-parse --short HEAD)  $(git -C "$DEST" log -1 --format=%s)"
echo "补丁数: $(ls "$DEST/xnu/patches"/*.patch | wc -l)"
echo
echo "注意：README 明确说当前状态是「identified hardware builds + 大量 unproven」。"
echo "锁定一个 commit 再干活，别让上游更新把你正在跑的组合换掉。"
