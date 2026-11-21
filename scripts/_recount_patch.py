import re, sys

P = sys.argv[1]
raw = open(P, encoding='utf-8', newline='').read()
crlf = '\r\n' in raw
lines = raw.replace('\r\n', '\n').split('\n')

out = []
i = 0
fixed = 0
while i < len(lines):
    ln = lines[i]
    m = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$', ln)
    if not m:
        out.append(ln)
        i += 1
        continue
    ostart, _, nstart, _, tail = m.groups()
    body = []
    j = i + 1
    while j < len(lines):
        b = lines[j]
        if re.match(r'^@@ ', b) or b.startswith('diff --git') or b.startswith('--- a/'):
            break
        body.append(b)
        j += 1
    while body and body[-1] == '':
        body.pop()
        j -= 1
    old_c = sum(1 for b in body if b.startswith('-') or b.startswith(' '))
    new_c = sum(1 for b in body if b.startswith('+') or b.startswith(' '))
    out.append('@@ -%s,%d +%s,%d @@%s' % (ostart, old_c, nstart, new_c, tail))
    out.extend(body)
    fixed += 1
    i = j

s = '\n'.join(out)
if crlf:
    s = s.replace('\n', '\r\n')
open(P, 'w', encoding='utf-8', newline='').write(s)
print('recounted hunks:', fixed)
