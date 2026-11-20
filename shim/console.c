/*
 * Minimal text console on the framebuffer LK leaves behind on wt86047
 * (MSM8916). 720x1280 panel, 24-bit, stride 2160 bytes.
 *
 * Two things measured on the device, not assumed:
 *
 *  1. Channel order is G,R,B. Writing (r,g,b) as bytes 0,1,2 makes a "red"
 *     fill come out green; byte 0 lands on green, byte 1 on red, byte 2 on
 *     blue. A probe that painted (ff,00,00)/(00,ff,00)/(00,00,ff) side by
 *     side showed green/red/blue on screen.
 *
 *  2. Only the LAST 320 lines of the buffer are actually scanned out, and they
 *     land on the top 320 rows of the panel. A probe painting 16 numbered
 *     80-line blocks showed only blocks 12..15 (lines 960..1279); lines 0..959
 *     never appear anywhere. So the console lives at VIS_TOP.
 *
 * With the MMU off every access is Device memory: byte writes keep alignment
 * trivial and avoid unaligned 32-bit stores.
 */
#include "console.h"
#include "font.h"

#define SCALE		2
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		8
#define LABEL_COLS	8

/* Visible window inside the splash buffer (measured, see header comment) */
#define VIS_TOP		960
#define VIS_ROWS	320
#define LINES		(VIS_ROWS / CH_H)

volatile uint8_t *con_fb = (volatile uint8_t *)FB_BASE;
static uint32_t con_bg;
static int row;

static inline void put_pixel24(int x, int y, uint32_t c)
{
	if (x < 0 || x >= FB_W || y < 0 || y >= VIS_ROWS)
		return;
	volatile uint8_t *p = con_fb + ((VIS_TOP + y) * FB_STRIDE) + (x * 3);
	p[0] = (uint8_t)((c >> 8) & 0xff);	/* green */
	p[1] = (uint8_t)((c >> 16) & 0xff);	/* red */
	p[2] = (uint8_t)(c & 0xff);		/* blue */
}

static void fill_rows(int y0, int y1, uint32_t c)
{
	for (int y = y0; y < y1; y++)
		for (int x = 0; x < FB_W; x++)
			put_pixel24(x, y, c);
}

void con_clear(uint32_t bg)
{
	fill_rows(0, VIS_ROWS, bg);
	con_bg = bg;
	row = 0;
}

/* Shift the window up one text line so the newest line is always visible. */
static void scroll(void)
{
	for (int y = 0; y + CH_H < VIS_ROWS; y++)
		for (int x = 0; x < FB_W * 3; x++)
			con_fb[(VIS_TOP + y) * FB_STRIDE + x] =
				con_fb[(VIS_TOP + y + CH_H) * FB_STRIDE + x];
	fill_rows(VIS_ROWS - CH_H, VIS_ROWS, con_bg);
	row = LINES - 1;
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	if (x0 + CH_W > FB_W)
		return;
	for (int gy = 0; gy < 16; gy++) {
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
				for (int sx = 0; sx < SCALE; sx++)
					put_pixel24(x0 + gx * SCALE + sx, y0 + gy * SCALE + sy, c);
			}
		}
	}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

static void newline(void)
{
	row++;
	if (row >= LINES)
		scroll();
}

void con_skip(void)
{
	newline();
}

void con_rewind(int n)
{
	row -= n;
	if (row < 0)
		row = 0;
}

void con_str(const char *label, const char *s)
{
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	newline();
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	newline();
}
