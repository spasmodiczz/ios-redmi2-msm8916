import io, sys

p = '.github/workflows/xnu.yml'
t = open(p, encoding='utf-8', newline='').read()
t = t.replace('\r\n', '\n')

MARK = '      # 把内核和编译日志推到 artifacts 分支'
i = t.find(MARK)
if i < 0:
    print('block not found')
    sys.exit(1)
# 回退到该行行首（保留前一个换行）
start = t.rfind('\n', 0, i) + 1
block = t[start:].rstrip('\n')
t = t[:start].rstrip('\n') + '\n'
print('block lines:', block.count('\n') + 1)

job = '  graphics_userspace:'
j = t.find(job)
if j < 0:
    print('job anchor not found; tail:', repr(t[-80:]))
    sys.exit(1)
head = t[:j].rstrip('\n')
tail = t[j:]
out = head + '\n\n' + block + '\n\n' + tail

open(p, 'w', encoding='utf-8', newline='\n').write(out)
print('inserted before graphics_userspace at', j)
