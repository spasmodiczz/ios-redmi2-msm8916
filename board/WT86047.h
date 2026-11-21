#ifndef _PEXPERT_ARM64_WT86047_H
#define _PEXPERT_ARM64_WT86047_H

/*
 * Xiaomi Redmi 2 移动4G增强版 (wt86047 / 2014813), Qualcomm MSM8916
 * (Snapdragon 410), 4x Cortex-A53 @1.2GHz, 2GB LPDDR3, eMMC 4.5,
 * 720x1280 panel, Adreno 306.
 *
 * 这份头文件是 ios-santoni 的 pexpert/pexpert/arm64/SANTONI.h 的 8916 版本。
 * 结构保持完全一致——多出来的宏只做文档用途，不参与条件编译，避免和
 * 补丁链里已有的分支打架。
 *
 * 下面所有地址都来自本机实测 DTB（wt86047_pro_images_V9.2.3.0 的 boot.img，
 * msm-id 206 = MSM8916），不是抄文档：
 *   中断控制器 qcom,msm-qgic2  GICD 0x0b000000(4K) / GICC 0x0b002000(4K)
 *   eMMC SDCC1                 core 0x07824900(0x11c) / vendor 0x07824000(0x800), 8-bit
 *   调试串口 serial@78b0000    BLSP1_UART2, 0x200, IRQ 108, GPIO4/5 func2
 *   显示  mdss_mdp@1a00000     0x1a00000(0x90000) + 0x1ac8000(0x3000)
 *         mdss_dsi@1a98000     0x1a98000(0x25c) + 0x1a98500(0x2b0)
 *         splash_region        0x83000000, 20MB（LK 留下的帧缓冲区）
 *   PMIC  PM8916               spmi@200f000, power-on@800
 *   SMEM                       0x86300000(1MB), RPM msg ram 0x60000
 *   触控  i2c@78b9000/focaltech@38  focaltech,5x06, IRQ GPIO13
 */

#define NO_MONITOR                    1
#define NO_ECORE                      1

#define WT86047                       1
#define SWEET                         1
#define SWEET_SMP_MAX_CPUS            4

#define ARM_ARCH_TIMER
#define __ARM_ARCH__                  8
#define __ARM_VMSA__                  8
#define __ARM_VFP__                   4
#define __ARM_COHERENT_CACHE__        1
#define __ARM_DEBUG__                 7
#define __ARM_ENABLE_SWAP__           1
#define __ARM_V8_CRYPTO_EXTENSIONS__  1
/*
 * Cortex-A53 是 ARMv8.0：没有 PAN，没有 LSE 原子指令，没有 PAC。
 * 所以和 santoni 一样按 arm64（非 arm64e）编译，并关掉 PAN 相关路径。
 */
#define __ARM_PAN_AVAILABLE__         0

/* MSM8916 与 MSM8937 一样是 GICv2（qcom,msm-qgic2），不是 GICv3。 */
#define HAS_GIC_V3                    0
#define SWEET_VTIMER_INTID            20
#define SWEET_IPI_SGI                 1

#ifndef ASSEMBLER
#define PLATFORM_PANIC_LOG_DISABLED
#endif

/*
 * 仅供人读的参考常量（不参与条件编译）。改这些不会生效，真正生效的地址
 * 在 shim/*.h 与各补丁里，改的时候按这里的值对齐。
 */
#define WT86047_GICD_BASE             0x0b000000UL
#define WT86047_GICC_BASE             0x0b002000UL
#define WT86047_UART_BASE             0x078b0000UL
#define WT86047_SDCC1_CORE_BASE       0x07824900UL
#define WT86047_SDCC1_VENDOR_BASE     0x07824000UL
#define WT86047_MDP_BASE              0x1a00000UL
#define WT86047_DSI_BASE              0x1a98000UL
#define WT86047_SPLASH_FB_BASE        0x83000000UL
#define WT86047_SPLASH_FB_SIZE        0x1400000UL

#endif /* _PEXPERT_ARM64_WT86047_H */
