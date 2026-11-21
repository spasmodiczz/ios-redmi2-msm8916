# wt86047 / MSM8916 硬件实测表

全部数据来自**本机官方线刷包**里的 `boot.img`（内含 DTB），不是抄文档。

复现：

```bash
python3 scripts/dtb_probe.py "../wt86047_pro_images_V9.2.3.0.LHJCNEK_20171229.0000.00_5.1_cn/images/boot.img"
python3 scripts/dtb_probe.py <同上> -m sdhci
python3 scripts/dtb_probe.py <同上> --dump 0      # 落地 dtb_0.dtb
```

`boot.img` 内有 3 个 DTB：

| # | 偏移 | msm-id | board-id | model |
|---|---|---|---|---|
| 0 | 0x7ca800 | 206 (MSM8916) | 65547:12, 65803:12, 196875:12 | MSM 8916 QRD SKUI |
| 1 | 0x7f6000 | 206 (MSM8916) | 65547:13, 65803:13, 196875:13 | MSM 8916 QRD SKUI |
| 2 | 0x83e000 | 239 (MSM8939v3) | 65547:11, 131083:11 | MSM8939v3.0 QRD SKUK |

---

## 1. 中断控制器

```
/soc/interrupt-controller@b000000
  compatible = qcom,msm-qgic2
  reg = 0xb000000 0x1000  0xb002000 0x1000     # GICD, GICC
  #interrupt-cells = 3
```

**结论**：与 `shim/gic.h` 的 `GICD_BASE 0x0b000000` / `GICC_BASE 0x0b002000` 完全一致。

## 2. 定时器

```
/soc/timer  compatible = arm,armv7-timer, clock-frequency = 0x124f800 (19.2 MHz)
            interrupts: PPI 13/14/11/10 (hyp/virt/secure/phys)
/soc/timer@b020000  compatible = arm,armv7-timer-mem, 0xb020000, 19.2 MHz
```

shim 里 `timer_arm_ticks(192000)` = 10ms @19.2MHz，成立。虚拟定时器 INTID 20（PPI 4 + 16）与 `SWEET_VTIMER_INTID 20` 一致。

## 3. eMMC（关键）

```
/soc/sdhci@07824000
  compatible = qcom,sdhci-msm
  reg = 0x7824900 0x11c  0x7824000 0x800      # core(标准 SDHCI), vendor
  interrupts = SPI 123, SPI 138
  qcom,bus-width = 8
  qcom,nonremovable
  qcom,clk-rates = 400000, 25000000, 50000000, 100000000, 177770000
  qcom,bus-speed-mode = HS200_1p8v, DDR_1p8v
  status = ok

/soc/sdhci@07864000                              # SD 卡
  reg = 0x7864900 0x11c  0x7864000 0x800, bus-width 4
```

对照 `0142` 驱动头部注释：`maps the single 0x1000 MMIO window at 0x07824000 (vendor at +0x000, standard SDHCI at +0x900)` → 0x07824000+0x900 = **0x07824900**。逐字节吻合。

## 4. 串口

```
/soc/serial@78b0000
  compatible = qcom,msm-lsuart-v14
  reg = 0x78b0000 0x200
  interrupts = SPI 108
  pinctrl-0 → pmx-uartconsole/uart-console
     qcom,pins = <&gp 4>, <&gp 5>   qcom,pin-func = 2   drive-strength 2

/soc/uart@78af000   compatible = qcom,msm-hsuart-v14   status = disabled
```

**结论**：调试口是 **BLSP1_UART2 @ 0x78B0000，GPIO4/5，func2，IRQ 108**。
（8916 的 BLSP1_UART1 在 0x78AF000，DTB 里是 disabled，别接错。）

## 5. 显示

```
/soc/qcom,mdss_mdp@1a00000      reg = 0x1a00000 0x90000, 0x1ac8000 0x3000   irq 72
/soc/qcom,mdss_dsi@1a98000      reg = 0x1a98000 0x25c, 0x1a98500 0x2b0, 0x193e000 0x30
/soc/qcom,mdss_dsi_pll@1a98300  compatible = qcom,mdss_dsi_pll_8916
memory/splash_region@83000000   reg = 0x83000000, 0x1400000        # 20 MB
```

面板节点全部 `qcom,mdss-dsi-panel-width = 0x2d0 (720)`、`height = 0x500 (1280)`：

- `mdss_dsi_nt35521_720p_video` — `nt35521_HD720p_video_AUO5`
- `mdss_dsi_nt35521s_720p_video` — `nt35521s_HD720p_video_BOE`
- `mdss_dsi_nt35521_ofilm_720p_video` — `nt35521_HD720p_video_OFILM`
- `mdss_dsi_otm1285a_720p_video` / `_otp_` — `otm1285a_HD720p_video_EBBG`
- `mdss_dsi_r61308_720p_video` / `_s88047a1_` — `r61308_HD720p_video_AUO3.5`
- `mdss_dsi_r69431_720p_video` — `r69431_HD720p_video_sharp`

具体是哪一块由 LK 读 panel id 决定，跑起来后从 `/chosen` 或 LK 日志看。**对移植的意义**：分辨率固定 720×1280，帧缓冲 720×1280×3 = 2.76MB / ×4 = 3.69MB，20MB 的 splash 区够双缓冲。

## 6. 触控 / 按键 / PMIC

```
/soc/i2c@78b9000/focaltech@38   compatible = focaltech,5x06   reg = 0x38
                                interrupts = GPIO 13, IRQ_TYPE_EDGE_FALLING
/soc/qcom,spmi@200f000          compatible = qcom,spmi-pmic-arb, irq SPI 190
  /qcom,pm8916@0/qcom,power-on@800   compatible = qcom,qpnp-power-on
  /qcom,pm8916@1/msm8x16_wcd_codec@f000 / @f100
  /qcom,pm8916@1/qcom,vibrator@c000   status = okay
```

## 7. 内存与 SMEM

```
/memory reg = <0 0>                              # 动态，由 LK 填
memory/external_image__region@0  0x86000000 0x800000    (8 MB)
memory/modem_adsp_region@0       0x86800000 0x5600000   (86 MB)
memory/pheripheral_region@0      0x8be00000 0x600000    (6 MB)
memory/pstore_reserve_mem_region@0 0x8c400000 0x100000  (1 MB)
memory/splash_region@83000000    0x83000000 0x1400000   (20 MB)

/soc/qcom,smem@86300000  reg = 0x86300000 0x100000, 0xb011008 0x4, 0x60000 0x8000, 0x193d000 0x8
/soc/qcom,rpm-smd        compatible = qcom,rpm-smd
```

DDR 从 `0x80000000` 起，2GB 到 `0x100000000`。carveout 全部落在 `0x8D400000` 以下，XNU 的 physmap 要避开这些洞。

## 8. CPU / SMP（最大的坑）

```
/cpus/cpu@0..3   compatible = arm,armv8
                 enable-method = qcom,arm-cortex-acc
                 next-level-cache = <&L2_0>
```

**没有 PSCI。** msm8937（santoni）有，msm8916 没有 —— 原厂固件让 TZ 通过私有 SMC 起核。所以 `0065-sweet-smp-psci.patch` 的 `smc 0xC4000003` 在红米2 上不会成功。

## 9. boot.img 头部（打包参数）

从官方 `boot.img` 直接读：

| 字段 | 值 |
|---|---|
| base | `0x80000000` |
| kernel_addr | `0x80008000` (offset `0x8000`) |
| ramdisk_addr | `0x82000000` (offset `0x02000000`) |
| tags_addr | `0x80000100` (offset `0x100`) |
| page_size | 2048 |
| dt_size | `0xC6000` (811008) |
| cmdline | `androidboot.hardware=qcom user_debug=30 msm_rtb.filter=0x3F ehci-hcd.park=3 androidboot.bootdevice=7824900.sdhci lpm_levels.sleep_disabled=1` |

注意 cmdline 里的 `7824900.sdhci` —— 内核侧的 eMMC 就是这个地址，和 `0142` 驱动一致。
