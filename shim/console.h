#pragma once
#include <stdint.h>

#define FB_BASE		0x90001000UL	/* santoni continuous splash region */
#define FB_W		720
#define FB_H		1280
#define FB_STRIDE	2160		/* 720 * 3 bytes, r8g8b8 matching mainline simplefb */

/*
 * Measured on wt86047: LK leaves the panel scanning only lines 960..1279
 * of the splash buffer, and those land on the top 320 panel rows. Anything
 * drawn above line 960 is never displayed.
 */
#define FB_VIS_TOP	960
#define FB_VIS_ROWS	320

#define RED		0x00ff0000U
#define BLUE		0x000000a0U
#define WHITE		0x00ffffffU
#define YELLOW		0x00ffff00U
#define GREEN		0x0000ff00U

extern volatile uint8_t *con_fb;

void con_clear(uint32_t bg);
void con_skip(void);
void con_rewind(int n);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
/* label, then "a=xxxxxxxx b=xxxxxxxx" style pairs of 32-bit values */
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);
