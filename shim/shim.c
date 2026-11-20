/*
 * santoni bare-metal shim: entry and diagnostic inspector for MSM8937 (Cortex-A53)
 *   - CPU feature registers (4K granule, ID_AA64MMFR0_EL1, MIDR_EL1)
 *   - Stage-1 MMU bring-up (4K granule, 36-bit physical addressing)
 *   - Continuous splash 720x1280 24-bit simplefb console at 0x90001000
 *   - Mach-O payload segment loader
 * Any unexpected exception paints a blue panic screen.
 */
#include <stdint.h>
#include "console.h"
#include "mmu.h"
#include "loader.h"
#include "gic.h"
#include "timer.h"
#include "wdt.h"

static volatile uint32_t irq_count;
static volatile uint32_t last_intid;

#define IMEM_RESTART_REASON	0x0c12565cUL	/* qcom,msm-imem@c125000 restart_reason@65c */
#define RESTART_BOOTLOADER	0x77665500U
#define PSHOLD			0x0440b000UL

#define PSCI_VERSION		0x84000000UL
#define PSCI_CPU_SUSPEND	0xc4000001UL
#define PSCI_CPU_OFF		0x84000002UL
#define PSCI_CPU_ON		0xc4000003UL
#define PSCI_MIGRATE_INFO_TYPE	0x84000006UL
#define PSCI_SYSTEM_RESET	0x84000009UL
#define PSCI_FEATURES		0x8400000aUL
#define PSCI_SYSTEM_SUSPEND	0xc400000eUL
#define PSCI_SYSTEM_RESET2	0xc4000012UL
#define SMCCC_VERSION		0x80000000UL

#define NR_CPUS			8

#define rsr(r) ({ uint64_t _v; __asm__ volatile("mrs %0, " r : "=r"(_v)); _v; })
#define wsr(r, v) __asm__ volatile("msr " r ", %0" : : "r"((uint64_t)(v)))
#define isb() __asm__ volatile("isb" ::: "memory")

static inline uint32_t rd32(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline void wr32(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

extern char secondary_entry[];

static const char *stage;	/* set at runtime: no absolute pointers in data */
static inline uint64_t cpu_mpidr(int i) { return (uint64_t)(i >> 2) << 8 | (i & 3); }

static volatile uint64_t sec_mpidr[NR_CPUS];

static uint64_t smc(uint64_t fn, uint64_t a1, uint64_t a2, uint64_t a3)
{
	register uint64_t x0 __asm__("x0") = fn;
	register uint64_t x1 __asm__("x1") = a1;
	register uint64_t x2 __asm__("x2") = a2;
	register uint64_t x3 __asm__("x3") = a3;
	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
			 :
			 : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11",
			   "x12", "x13", "x14", "x15", "x16", "x17", "memory");
	return x0;
}

static uint64_t now(void)
{
	isb();
	return rsr("cntpct_el0");
}

static void delay_ms(unsigned ms)
{
	uint64_t end = now() + rsr("cntfrq_el0") / 1000 * ms;
	while (now() < end)
		;
}

static void __attribute__((noreturn)) reset(unsigned after_s)
{
	con_skip();
	con_str("RESET", after_s > 20 ? "in 40s" : "in 20s");
	delay_ms(after_s * 1000);
	wr32(IMEM_RESTART_REASON, RESTART_BOOTLOADER);
	__asm__ volatile("dsb sy" ::: "memory");
	smc(PSCI_SYSTEM_RESET, 0, 0, 0);
	wr32(PSHOLD, 0);
	for (;;)
		__asm__ volatile("wfe");
}

/* ---- exceptions ---- */

void exc_handler(uint64_t slot, uint64_t *regs)
{
	if (slot == 5) {	/* IRQ from current EL, SPx */
		uint32_t iar = gic_ack();
		uint32_t intid = iar & 0x3ff;
		if (intid < 1020) {
			irq_count++;
			last_intid = intid;
			/* Rearm timer for next 100 Hz tick (192,000 ticks at 19.2 MHz) */
			timer_arm_ticks(192000);
			gic_eoi(iar);
		}
		return;
	}
	con_clear(BLUE);
	switch (slot & 3) {
	case 0: con_str("PANIC", "SYNC"); break;
	case 1: con_str("PANIC", "IRQ"); break;
	case 2: con_str("PANIC", "FIQ"); break;
	default: con_str("PANIC", "SERROR"); break;
	}
	con_hex2("SLOT", "n=", slot, "cpu=", rsr("mpidr_el1"));
	con_str("STAGE", stage);
	con_hex("ESR", rsr("esr_el1"));
	con_hex("ELR", rsr("elr_el1"));
	con_hex("FAR", rsr("far_el1"));
	con_hex("SPSR", rsr("spsr_el1"));
	con_hex("X0", regs[0]);
	con_hex("X1", regs[1]);
	con_hex("X30", regs[29]);	/* stashed lr is just above the frame */
	reset(40);
}

/* ---- SMP secondary ---- */

void secondary_main(uint64_t idx)
{
	if (idx < NR_CPUS)
		sec_mpidr[idx] = rsr("mpidr_el1") | (1ULL << 63);
	__asm__ volatile("dsb sy" ::: "memory");
	smc(PSCI_CPU_OFF, 0, 0, 0);
}

static void __attribute__((unused)) test_smp(void)
{
	stage = "smp";
	uint64_t self = rsr("mpidr_el1") & 0xffffff;
	int64_t ret[NR_CPUS];

	for (int i = 1; i < NR_CPUS; i++)
		ret[i] = smc(PSCI_CPU_ON, cpu_mpidr(i), (uintptr_t)secondary_entry, i);
	delay_ms(500);
	for (int i = 1; i < NR_CPUS; i++) {
		char label[] = "CPU0";
		label[3] = '0' + i;
		if (cpu_mpidr(i) == self)
			con_str(label, "is boot cpu");
		else
			con_hex2(label, "ret=", (uint32_t)ret[i], "mpidr=",
				 (uint32_t)sec_mpidr[i] | (sec_mpidr[i] ? 0 : 0xdead0000));
	}
}

/* ---- performance & memory test helpers ---- */

static uint64_t scratch[8192];	/* 64KB, lives in our image */

static uint64_t ticks_fill(uint32_t c)
{
	uint64_t t = now();
	con_clear(c);
	return now() - t;
}

static uint64_t ticks_mem(void)
{
	uint64_t t = now();
	for (int pass = 0; pass < 16; pass++)
		for (int i = 0; i < 8192; i++)
			scratch[i] = scratch[i] * 3 + i;
	return now() - t;
}

static uint32_t to_us(uint64_t ticks)
{
	return ticks * 10 / 192;	/* 19.2 MHz */
}

/* ---- main ---- */

static int str_is(const char *a, const char *b)
{
	while (*a && *a == *b) {
		a++;
		b++;
	}
	return *a == *b;
}

#ifdef SHIM_FB_PROBE
/*
 * 帧缓冲探针：不确定 8916 的 LK splash 用什么通道顺序 / stride 时用它。
 * 四等分竖带按 **RGB 顺序**逐字节写（p[0]=R, p[1]=G, p[2]=B）：
 *   x<180 FF0000 | x<360 00FF00 | x<540 0000FF | 其余 FFFFFF
 * 每 64 行画 3 行黑色标记，数标记条数即可反推 stride 是否被压缩/拉伸。
 * 画完停住，不重启（先关看门狗）。
 */
#include "font.h"

static void probe_px(int x, int y, uint8_t r, uint8_t g, uint8_t b)
{
	if (x < 0 || x >= FB_W || y < 0 || y >= FB_H)
		return;
	volatile uint8_t *p = (volatile uint8_t *)FB_BASE + y * FB_STRIDE + x * 3;
	/* wt86047 实测：字节 0 → 绿，字节 1 → 红，字节 2 → 蓝 */
	p[0] = g; p[1] = r; p[2] = b;
}

static void __attribute__((unused)) probe_text(int x0, int y0, const char *s)
{
	for (; *s; s++, x0 += 16) {
		char ch = *s;
		if (ch < 0x20 || ch > 0x7e)
			ch = '?';
		const unsigned char *g = font8x16[ch - 0x20];
		for (int gy = 0; gy < 16; gy++)
			for (int gx = 0; gx < 8; gx++)
				if (g[gy] & (0x80 >> gx))
					for (int sy = 0; sy < 2; sy++)
						for (int sx = 0; sx < 2; sx++)
							probe_px(x0 + gx * 2 + sx, y0 + gy * 2 + sy, 0, 0, 0);
	}
}

static void __attribute__((noreturn)) probe_fb(void)
{
	struct wdt_state w0, w1;
	wdt_quiesce(&w0, &w1);		/* 别让看门狗把我们打回 LK */

	/*
	 * 16 个 80 行色块，每块写上块号。用来一次定死三件事：
	 *   1) 可见窗口到底是缓冲区的哪几行（看得到哪几个块号）
	 *   2) 通道顺序（块 0 是红就说明 G/R 交换已经修对）
	 *   3) 字形能不能渲染
	 */
	static const uint8_t pal[16][3] = {
		{255, 0, 0},     {0, 255, 0},     {0, 0, 255},     {255, 255, 0},
		{0, 255, 255},   {255, 0, 255},   {255, 255, 255}, {0, 0, 0},
		{128, 0, 0},     {0, 128, 0},     {0, 0, 128},     {128, 128, 0},
		{0, 128, 128},   {128, 0, 128},   {128, 128, 128}, {192, 192, 192},
	};

	for (int b = 0; b < 16; b++) {
		int y0 = b * 80;
		uint8_t fg = (b == 6 || b == 15) ? 0 : 255;	/* 亮块用黑字 */
		for (int y = y0; y < y0 + 80; y++)
			for (int x = 0; x < FB_W; x++)
				probe_px(x, y, pal[b][0], pal[b][1], pal[b][2]);

		char tag[5];
		tag[0] = 'R';
		tag[1] = '0' + b / 10;
		tag[2] = '0' + b % 10;
		tag[3] = 0;
		/* fg==0 表示画黑字，这里直接调 probe_px 画（无背景） */
		for (char *s = tag; *s; s++) {
			const unsigned char *g = font8x16[*s - 0x20];
			int x0 = 16 + (int)(s - tag) * 24;
			for (int gy = 0; gy < 16; gy++)
				for (int gx = 0; gx < 8; gx++)
					if (g[gy] & (0x80 >> gx))
						for (int sy = 0; sy < 3; sy++)
							for (int sx = 0; sx < 3; sx++)
								probe_px(x0 + gx * 3 + sx, y0 + 16 + gy * 3 + sy, fg, fg, fg);
		}
	}

	for (;;)
		__asm__ volatile("wfe");
}
#endif

void shim_main(uint64_t dtb)
{
	struct mmu_info mi;

#ifdef SHIM_FB_PROBE
	probe_fb();
#endif

	stage = "loader";
	con_clear(RED);
	con_str("SHIM", "santoni bare-metal shim");

	uint64_t mmfr0 = rsr("id_aa64mmfr0_el1");
	con_hex2("MMFR0", "lo=", (uint32_t)mmfr0, "tg16=", (mmfr0 >> 20) & 0xf);
	con_hex2("MIDR", "el1=", (uint32_t)rsr("midr_el1"), "mpidr=", (uint32_t)rsr("mpidr_el1"));

	/*
	 * Report and stop the MSM8937 APPS watchdog before the payload copy.
	 * LK itself never touches it, so this is the only chance to see what
	 * the boot chain left armed.
	 */
	stage = "wdt";
	struct wdt_state w0, w1;
	uint32_t wdt_was = wdt_quiesce(&w0, &w1);
	con_hex2("WDT", "en=", w0.en, "sts=", w0.sts);
	con_hex2("WDT time", "bark=", w0.bark, "bite=", w0.bite);
	con_hex2("WDT after", "en=", w1.en, "was=", wdt_was);
	con_skip();

	const char *why = loader_boot(dtb);
	con_str("LOADER", why);
	con_skip();

	/*
	 * A payload that failed validation must stay on screen instead of being
	 * scrolled away by the benchmarks. "Nothing to load" is not an error:
	 * without a payload the shim is just a hardware diagnostic, which is
	 * exactly what stage 1 needs before XNU exists. loader_boot() reports that
	 * as either "no initrd in /chosen" or "no SWEETXNU payload".
	 */
	if (why) {
		int nothing_to_load = str_is(why, "no initrd in /chosen") ||
				      str_is(why, "no SWEETXNU payload");
		if (!nothing_to_load) {
			con_str("HALT", "payload load failed");
			for (;;)
				__asm__ volatile("wfe");
		}
		delay_ms(500);
	}

	stage = "fill off";
	uint64_t fill_off = ticks_fill(RED);
	uint64_t mem_off = ticks_mem();

	stage = "mmu enable";
	int rc = mmu_enable(&mi);

	stage = "fill on";
	uint64_t fill_on = rc ? 0 : ticks_fill(RED);
	uint64_t mem_on = rc ? 0 : ticks_mem();
	if (rc)
		con_clear(RED);

	con_str("SHIM", "santoni phase 2");
	con_skip();
	con_hex2("DTB", "at=", dtb, "magic=", dtb ? __builtin_bswap32(rd32(dtb)) : 0);
	con_hex2("IMAGE", "lo=", mi.img_lo, "hi=", mi.img_hi);
	con_hex2("MMU", "rc=", rc, "l3=", mi.n_l3);
	con_hex2("TTBR0", "pa=", mi.ttbr, "pa_hi=", mi.ttbr >> 32);
	con_hex("SCTLR0", mi.sctlr_before);
	con_hex("SCTLR1", mi.sctlr_after);
	con_hex("TCR", rsr("tcr_el1"));
	con_hex2("PARANGE", "mmfr0=", rsr("id_aa64mmfr0_el1") & 0xf, "tg16=", (rsr("id_aa64mmfr0_el1") >> 20) & 0xf);
	con_skip();
	con_hex("PAR img", mmu_probe((uintptr_t)shim_main));
	con_hex("PAR fb", mmu_probe(FB_BASE + 0x1000));
	con_hex("PAR ram", mmu_probe(0x80000000UL));
	con_skip();
	con_hex2("FILL us", "off=", to_us(fill_off), "on=", to_us(fill_on));
	con_hex2("MEM us", "off=", to_us(mem_off), "on=", to_us(mem_on));
	con_skip();

	stage = "psci";
	con_hex2("PSCI", "ver=", smc(PSCI_VERSION, 0, 0, 0), "smccc=", smc(SMCCC_VERSION, 0, 0, 0));
	con_skip();

	stage = "gic & timer";
	gic_dist_init();
	gic_cpu_init();
	gic_enable_int(20);
	gic_enable_int(27);
	timer_arm_ticks(192000);
	__asm__ volatile("msr daifclr, #2" ::: "memory");

	con_hex2("GIC", "dist=", rd32(GICD_BASE + GICD_CTLR), "cpu=", rd32(GICC_BASE + GICC_CTLR));
	for (int i = 0; i < 50; i++) {
		con_hex2("TIMER", "ticks=", irq_count, "intid=", last_intid);
		delay_ms(100);
		if (i < 49)
			con_rewind(1);
	}
	con_skip();

	stage = "done";
	reset(40);
}
