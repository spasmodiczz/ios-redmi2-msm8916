#!/usr/bin/env python3
"""Apply a unified diff to a pristine source tree and show the changed region.

Why this exists: check_patch.py only counts hunk lines, and compiling an
isolated block says nothing about where the hunk lands. Both checks passed on a
patch that inserted helpers between a function's 'void' and its parameter list,
and the CI build failed 26 minutes later. This tool applies the patch for real
and prints the result, so the mistake is visible in seconds.

Usage:
  python scripts/apply-patch-check.py <patch> <source-root> [--show N]

<source-root> is a tree containing the patched files at their repo-relative
paths (e.g. upstream/xnu-xnu-8796.101.5).
"""
import argparse
import os
import re
import sys


def parse_hunks(text):
    hunks = []
    cur = None
    for line in text.split('\n'):
        m = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if m:
            cur = {'start': int(m.group(1)), 'lines': []}
            hunks.append(cur)
            continue
        if cur is None:
            if line.startswith(('--- ', '+++ ', 'diff ', 'index ')):
                continue
            continue
        if line.startswith('\\'):
            continue
        if line == '':
            continue
        # ' ' context, '-' removal, '+' addition -- keep the marker
        if line[0] in ' +-':
            cur['lines'].append(line)
    return hunks


def split_files(patch_text):
    files = []
    cur = None
    for line in patch_text.split('\n'):
        if line.startswith('diff --git '):
            m = re.match(r'^diff --git a/(\S+) b/(\S+)', line)
            cur = {'path': m.group(2) if m else None, 'hunks': []}
            files.append(cur)
            continue
        if cur is None:
            continue
        m = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)', line)
        if m:
            cur['hunks'].append({'start': int(m.group(1)), 'lines': [], 'tail': m.group(5)})
            continue
        if cur['hunks'] and line[:1] in (' ', '+', '-') and not line.startswith(('--- ', '+++ ')):
            cur['hunks'][-1]['lines'].append(line)
    return files


def apply_file(path, hunks, dry):
    if os.path.exists(path):
        lines = open(path, encoding='utf-8', errors='surrogateescape').read().split('\n')
        # split() leaves an empty string for a file that ends in a newline.
        # Keeping it invents a trailing blank line that git apply will demand
        # and not find, so drop it and re-add the newline when writing.
        if lines and lines[-1] == '':
            lines.pop()
    else:
        # A patch may create the file (0010 does); treat it as empty so the
        # hunk with an empty old side appends the whole body.
        lines = ['']
    orig = list(lines)
    offset = 0
    reports = []
    for h in hunks:
        old = [l[1:] for l in h['lines'] if l[0] in ' -']
        new = [l[1:] for l in h['lines'] if l[0] in ' +']
        start = h['start'] - 1 + offset
        # exact match at expected place, else search nearby
        pos = None
        if lines[start:start + len(old)] == old:
            pos = start
        else:
            for cand in range(max(0, start - 200), min(len(lines), start + 200)):
                if lines[cand:cand + len(old)] == old:
                    pos = cand
                    break
        if pos is None:
            return None, reports + [(h['start'], 'FAILED: context not found')]
        if not dry:
            lines[pos:pos + len(old)] = new
        offset += len(new) - len(old)
        reports.append((h['start'], 'ok at line %d' % (pos + 1)))
    return lines, reports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('patch')
    ap.add_argument('root')
    ap.add_argument('--show', type=int, default=0,
                    help='print N lines around each hunk in the result')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--write', action='store_true',
                    help='write the patched result back (for chaining patches)')
    args = ap.parse_args()

    text = open(args.patch, encoding='utf-8', errors='surrogateescape').read()
    files = split_files(text)
    if not files:
        print('no file sections found')
        return 2

    bad = 0
    for f in files:
        target = os.path.join(args.root, f['path'])
        # A file the patch creates has no source; apply_file starts from empty.
        if not os.path.exists(target) and not any(
                h['lines'] and all(l[0] == '+' for l in h['lines'])
                for h in f['hunks']):
            print('MISSING %s' % target)
            bad += 1
            continue
        result, reports = apply_file(target, f['hunks'], args.dry)
        for start, msg in reports:
            print('%-50s @@ -%d : %s' % (f['path'], start, msg))
            if msg.startswith('FAILED'):
                bad += 1
        if result is None or args.dry:
            continue
        if args.write:
            os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
            while result and result[-1] == '':
                result.pop()
            with open(target, 'w', encoding='utf-8', errors='surrogateescape',
                      newline='') as fh:
                fh.write('\n'.join(result) + '\n')
        if args.show:
            for h in f['hunks']:
                lo = max(0, h['start'] - 1 - 3)
                hi = min(len(result), h['start'] - 1 + len(h['lines']) + 3)
                print('--- %s around -%d ---' % (f['path'], h['start']))
                for i in range(lo, hi):
                    print('%5d| %s' % (i + 1, result[i]))

    print('\n%s' % ('APPLY OK' if bad == 0 else '%d problem(s)' % bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
