import re, sys

P = 'xnu/patches/0003-sweet-framebuffer-serial-console.patch'
raw = open(P, encoding='utf-8', newline='').read()
crlf = '\r\n' in raw
s = raw.replace('\r\n', '\n')

OLD_CLEAR = """+static void
+fbcon_clear_row(uint32_t row)
+{
+	for (uint32_t y = row * 16 * FBCON_SCALE; y < (row + 1) * 16 * FBCON_SCALE; y++) {
+		volatile uint32_t *line = fbcon_base + y * (fbcon_stride / 4);
+		for (uint32_t x = 0; x < fbcon_width; x++) {
+			line[x] = FBCON_BG;
+		}
+	}
+}"""

NEW_CLEAR = """+static void
+fbcon_clear_row(uint32_t row)
+{
+	/* wt86047: 24-bit panel, so one pixel is three bytes, not a uint32_t. */
+	uint32_t bytes = fbcon_width * 3;
+	for (uint32_t y = row * 16 * FBCON_SCALE; y < (row + 1) * 16 * FBCON_SCALE; y++) {
+		volatile uint8_t *p = (volatile uint8_t *)fbcon_base + y * fbcon_stride;
+		for (uint32_t i = 0; i < bytes; i++) {
+			p[i] = 0x00;
+		}
+	}
+}"""

OLD_GLYPH = """+	const unsigned char *glyph = iso_font + c * 16;
+	uint32_t x0 = fbcon_col * 8 * FBCON_SCALE, y0 = fbcon_row * 16 * FBCON_SCALE;
+	for (uint32_t gy = 0; gy < 16; gy++) {
+		for (uint32_t sy = 0; sy < FBCON_SCALE; sy++) {
+			volatile uint32_t *line = fbcon_base + (y0 + gy * FBCON_SCALE + sy) * (fbcon_stride / 4) + x0;
+			for (uint32_t gx = 0; gx < 8; gx++) {
+				uint32_t px = (glyph[gy] & (1u << gx)) ? FBCON_FG : FBCON_BG;
+				for (uint32_t sx = 0; sx < FBCON_SCALE; sx++) {
+					line[gx * FBCON_SCALE + sx] = px;
+				}
+			}
+		}
+	}
+	fbcon_col++;"""

NEW_GLYPH = """+	const unsigned char *glyph = iso_font + c * 16;
+	uint32_t x0 = fbcon_col * 8 * FBCON_SCALE, y0 = fbcon_row * 16 * FBCON_SCALE;
+	for (uint32_t gy = 0; gy < 16; gy++) {
+		for (uint32_t sy = 0; sy < FBCON_SCALE; sy++) {
+			volatile uint8_t *line = (volatile uint8_t *)fbcon_base +
+			    (y0 + gy * FBCON_SCALE + sy) * fbcon_stride;
+			for (uint32_t gx = 0; gx < 8; gx++) {
+				uint8_t v = (glyph[gy] & (1u << gx)) ? 0xff : 0x00;
+				for (uint32_t sx = 0; sx < FBCON_SCALE; sx++) {
+					volatile uint8_t *p = line + (x0 + gx * FBCON_SCALE + sx) * 3;
+					p[0] = v; p[1] = v; p[2] = v;
+				}
+			}
+		}
+	}
+	fbcon_col++;"""

for old, new, tag in ((OLD_CLEAR, NEW_CLEAR, 'clear_row'), (OLD_GLYPH, NEW_GLYPH, 'glyph')):
    if old not in s:
        print('MISS', tag); sys.exit(1)
    s = s.replace(old, new, 1)

# 重算每个 hunk 的声明行数（只统计 body，不改文件/行号）
lines = s.split('\n')
out = []
i = 0
fixed = 0
while i < len(lines):
    ln = lines[i]
    m = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$', ln)
    if not m:
        out.append(ln); i += 1; continue
    ostart, ocount, nstart, ncount, tail = m.groups()
    body = []
    j = i + 1
    while j < len(lines):
        b = lines[j]
        if re.match(r'^@@ ', b) or b.startswith('--- ') or b.startswith('diff --git') or b.startswith('+++ ') and body:
            break
        if b.startswith('--- a/') or b.startswith('+++ b/') or b.startswith('diff --git'):
            break
        body.append(b)
        j += 1
    # 去掉尾部空行属于分隔，不算
    while body and body[-1] == '':
        body.pop()
        j -= 1
    old_c = sum(1 for b in body if b.startswith('-') or b.startswith(' ') or b == '')
    new_c = sum(1 for b in body if b.startswith('+') or b.startswith(' ') or b == '')
    old_c = sum(1 for b in body if b.startswith('-') or b.startswith(' '))
    new_c = sum(1 for b in body if b.startswith('+') or b.startswith(' '))
    out.append('@@ -%s,%d +%s,%d @@%s' % (ostart, old_c, nstart, new_c, tail))
    fixed += 1
    out.extend(body)
    i = j

s = '\n'.join(out)
if crlf:
    s = s.replace('\n', '\r\n')
open(P, 'w', encoding='utf-8', newline='').write(s)
print('hunks recounted:', fixed)
