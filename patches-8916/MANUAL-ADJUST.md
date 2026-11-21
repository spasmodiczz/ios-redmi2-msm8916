# patches-8916 生成说明

源：`ios-santoni/xnu/patches`（236 个补丁）
目标：`patches-8916/`，全部按原编号顺序应用，**不删不改序**。

## 文本级替换

- `ARM64_BOARD_CONFIG_SANTONI` → `ARM64_BOARD_CONFIG_WT86047`
- `MASTER.arm64.santoni` → `MASTER.arm64.wt86047`
- `MACHINE_FLAGS_ARM64_SANTONI` → `MACHINE_FLAGS_ARM64_WT86047`
- `CONFIG_SANTONI` → `CONFIG_WT86047`
- `SANTONI.h` → `WT86047.h`
- `SANTONI` → `WT86047`
- `santoni-init.S` → `wt86047-init.S`
- `santoni` → `wt86047`
- `Snapdragon 435 / MSM8937` → `Snapdragon 410 / MSM8916`
- `MSM8937` → `MSM8916`
- `Snapdragon 435` → `Snapdragon 410`

## 数值改动（8 核 → 4 核）

- 正则 `(#define\s+SWEET_SMP_MAX_CPUS\s+)8\b` → `\g<1>4`
- 正则 `(#define\s+SWEET_SMP_MAX_CPUS\s+)6\b` → `\g<1>4`
- 正则 `(#define\s+MAX_CPUS\s+)8\b` → `\g<1>4`

## 故意没改的 `Santoni`

剩下 101 处 `Santoni`（首字母大写）分布在 GPU 路径的注释和 `"SantoniGraphicsClient"` 这个 IOKit user client 类名里。该类名被 `tools/santoni-graphics/` 的用户态程序按字符串匹配，8916 不做 GPU，改名只会引入不一致，所以保持原样。


## 必须人工改逻辑的补丁

### `0017-santoni-gicv2-rtclock.patch`

零改动。GICD 0x0b000000 / GICC 0x0b002000 与 8916 DTB 的 qcom,msm-qgic2 完全一致；virtual-timer-intid=20 从 FDT 读，8916 同样是 20。

### `0065-sweet-smp-psci.patch`

★必须改。8916 原厂固件 enable-method="qcom,arm-cortex-acc"，没有 PSCI。sweet_psci_cpu_on() 走 SMC 0xC4000003，在红米2 上会返回错误，而当前代码 在失败时 panic("NOT alive")。改法：失败只 IOLog 不 panic；不带 cpus= 启动参数时该补丁默认单核（cpus_boot_arg=1），可以先单核点亮。

### `0085-santoni-board-target.patch`

文本替换即可（SANTONI→WT86047、MAX_CPUS 8→4、SWEET_SMP_MAX_CPUS 8→4）。另需在 MakeInc.def 的 SUPPORTED_ARM64_MACHINE_CONFIGS 加 WT86047。

### `0142-santoni-sdhci-emmc-driver.patch`

几乎零改动。SDCC1: core 0x07824900 + vendor 0x07824000，8916 DTB 实测一致；驱动按 LK 交接态工作、不自己配 GCC/SPMI，正好绕开 8916 与 8937 的时钟树差异。需核对 vendor 寄存器偏移（CORE_HC_MODE +0x78、CORE_VENDOR_SPEC +0x10c）在 8916 上是否同值——两者都是 sdhci-msm v4，预期一致，用串口读回校验。

### `0143-santoni-persistent-console-log.patch`

依赖 0142 的 system 分区日志区（8193 扇区）。8916 的 system 分区起始扇区不同，必须按 gpt_main0.bin 的实际分区表改偏移，否则会写坏 system。

### `0098-santoni-early-console.patch`

UART 基址核对：8916 console = BLSP1_UART2 @ 0x78B0000（DTB: serial@78b0000，GPIO4/5 func2）。santoni 同为 0x78B0000，预期零改动，但要在 0098 里确认写死的地址。

### `0168-santoni-panel-copy.patch`

FB 基址必须改：santoni 连续 splash 区 0x90001000，8916 是 0x83000000（DTB splash_region@83000000, size 0x1400000=20MB）。shim/console.h 的 FB_BASE 同样要改，见 scripts/build-shim.sh。

### `0092-topaz-direct-mdp-scanout.patch`

topaz(msm8953) 的 MDP 直扫路径，8916 的 mdss_mdp@1a00000 版本不同。第一阶段不要用：直接往 LK 留下的 splash 帧缓冲里画（CPU 渲染），这条只在需要 MDP 硬件合成时才动。
