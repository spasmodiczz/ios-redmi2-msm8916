# 红米2（wt86047 / MSM8916）移植 iOS —— 实施方案 v3.0（源码实测修订版）

> 日期：2026-11-20
> 基线：`ios-santoni` @ `531aeac`（santoni 分支，2026-11-11 状态）
> 设备：Redmi 2 移动4G增强版 `WT86047` / 2014813 / 2GB+16GB / 当前在 fastboot，serialno `70c085ae7d22`
> 与 v2.0 的关系：v2.0 的结论**有一半被源码推翻**，详见第一章。

---

## 一、v2.0 哪里错了（先认错，再干活）

v2.0 是基于"补丁文件名"推的。这次把仓库拉下来读了实现，四处结论要改。

| # | v2.0 说法 | 实际（源码/DTB 实测） | 影响 |
|---|---|---|---|
| 1 | 「项目补丁全是 UFS，eMMC 要自研，阶段 2 是最大新增工作量（高）」 | **仓库里有 `0142-santoni-sdhci-emmc-driver.patch`，2813 行，轮询式 SDHCI + GPT 解析 + `emmcroot0` 块设备 + HFS+ 根**。它把 UFS 路线整个替换掉了 | 阶段 2 从"从零写驱动"降为"改寄存器基址+验证"，风险 高→**中低** |
| 2 | 「SMP 用 PSCI，改 cpus=4 即可」 | 8916 DTB 里 4 个 CPU 全是 `enable-method = "qcom,arm-cortex-acc"`，**原厂固件没有 PSCI**（msm8937 有，8916 没有）。`0065` 走 SMC `0xC4000003` 会返回错误，而代码在失败时 `panic("NOT alive")` | **新增风险，v2.0 完全没提**。必须先单核点亮 |
| 3 | 「framebuffer 复用 0003/0056/0079」 | santoni 的 `FB_BASE=0x90001000`；8916 的 splash 区在 **`0x83000000`，20MB**（DTB `splash_region`），两者差 0x1000 万 | 一个常量的事，但找错了就是黑屏 |
| 4 | 「串口 BLSP1_UART2 (GPIO4/5)」 | **对**。DTB `serial@78b0000` 就是 BLSP1_UART2（8916 的 UART1 在 `0x78af000`，DTB 里 `status=disabled`），引脚 GPIO4/5、func2、IRQ 108 | 零改动（我中途一度误判成 UART1，已核对纠正） |

另外一条 v2.0 没说、但决定"能不能开工"的：**XNU 8796 只能在 macOS + Xcode 16.0 上编**。没有 Mac 就走 GitHub Actions（仓库自带 `xnu.yml`），见第六章。

---

## 二、实测硬件基线（证据：本机官方 `boot.img` 解出的 DTB）

来源：`wt86047_pro_images_V9.2.3.0.LHJCNEK_20171229/images/boot.img`，内含 3 个 DTB，`msm-id=206` 即 MSM8916。用 `scripts/dtb_probe.py` 可复现。

| 子系统 | 实测 | 与 santoni（msm8937）比对 |
|---|---|---|
| 中断控制器 | `qcom,msm-qgic2`：GICD `0x0b000000`(4K) + GICC `0x0b002000`(4K)，3 cells | **完全一致** → `0017` 补丁零改动 |
| eMMC | `sdhci@07824000`：core `0x07824900`(0x11c) + vendor `0x07824000`(0x800)，8-bit，`qcom,nonremovable`，IRQ 123/138 | **地址完全一致** → `0142` 近乎零改动 |
| SD 卡 | `sdhci@07864000`：`0x07864900`/`0x07864000`，4-bit | 另一个控制器，不用 |
| 调试串口 | `serial@78b0000` = **BLSP1_UART2**，`0x200`，IRQ 108，GPIO4/5 func2，19.2MHz | 同基址 |
| 显示 | `mdss_mdp@1a00000`(0x90000)+`0x1ac8000`(0x3000)；`mdss_dsi@1a98000`；PLL `0x1a98300` | 同代 MDP5，寄存器布局需实测核对 |
| 帧缓冲 | `splash_region@0x83000000`，20MB（LK 已经把面板点亮并留在自刷新态） | santoni 是 `0x90001000` |
| 面板 | 全部 720×1280（0x2d0×0x500）：`nt35521`(AUO/BOE/OFILM)、`otm1285a`(EBBG)、`r61308`(AUO)、`r69431`(sharp)，按 panel id 在启动时选 | 同分辨率 |
| 触控 | `i2c@78b9000/focaltech@38`，`compatible="focaltech,5x06"`，IRQ GPIO13 | **同族 FT5x06** → `0060/0063` 可用 |
| PMIC | PM8916（`spmi@200f000`，`power-on@800`），音量键走 PMIC | 同 |
| SMEM/RPM | `0x86300000`(1MB)，RPM msg ram `0x60000` | 同结构 |
| 内存 | 4×A53，`qcom,arm-cortex-acc`；2GB DDR 起 `0x80000000`；carveout：modem/adsp `0x86800000`+86MB、外部 `0x86000000`+8MB、pstore `0x8c400000`+1MB | **无 PSCI**，这是最大差异 |
| boot.img 头部 | base `0x80000000`，kernel_off `0x8000`，ramdisk_off `0x02000000`，tags_off `0x100`，page 2048 | 打包参数直接用这组 |
| 分区 | `userdata` = `0x2c11e7e00` ≈ 11.03 GiB（fastboot getvar 实测） | 放 iOS rootfs 够 |

---

## 三、"绝不干扰现有系统"的操作边界

这条是硬约束，写进流程里，不是口号。

| 阶段 | 动作 | 会在手机上留下什么 | 风险 |
|---|---|---|---|
| 0 准备 | 备份分区、fastboot getvar、读 DTB | 无 | **零** |
| 1 引导 | `fastboot boot shim-boot.img` | 只在内存里跑；断电重启回到 MIUI。boot 分区、分区表都不动 | **零**（最坏情况：卡死→长按电源 10 秒） |
| 2 rootfs | 写 iOS rootfs 到 `userdata` 分区 | **清空 Android 用户数据**（照片/App/账号）。system、boot、persist 不动 | **高：这一步必然毁数据** |
| 3-4 用户空间 | 继续 `fastboot boot` | 无（只读 userdata 里的 rootfs） | 零 |
| 5 稳定 | 同上 | 无 | 零 |

所以：
- **阶段 0/1 可以现在做，一点不伤系统。**
- 阶段 2 有两条路：
  - **A. RAM root（推荐先做）**：`rd=md0`，rootfs 打成一个 ramdisk 塞进内存，全程零写入。2GB 内存下只能跑个最小用户空间，够验证到 launchd 甚至 SpringBoard 首帧，但重启即失。
  - **B. eMMC root**：`rd=emmcroot0`，往 userdata 写 HFS+。要真用 iOS，只能这样。**做之前必须先确认你愿意丢掉这台机器上的 Android 数据**（`1_8g备份/` 那套 dump 里 userdata 是不是完整备份，得先核实）。

另外两个"看起来无害、其实会写盘"的补丁，必须在配置里关掉：

- `0143-santoni-persistent-console-log`：把内核日志写进 **`system` 分区的开头 8193 扇区（4MB）**。这会破坏 Android 系统镜像。**默认禁用**，要开就必须先按本机 GPT 重算偏移并确认那块区域没被用。
- `0142` 的 `emmcrw=1`：这是唯一允许写 userdata 的开关。**阶段 1 绝对不能带**，带了就是往 Android 的 userdata 里写垃圾。

---

## 四、分阶段方案 v3.0

### 阶段 0：准备（半天，零风险）

1. 设备确认：`bash scripts/check-device.sh`（只读）。
2. 备份：`boot` / `recovery` / `persist` / `modemst1,2` / `fsg` / 分区表。已有 `备份_20261114/`，补一次当前状态的 boot。
3. 抽 DTB：`python3 scripts/dtb_probe.py <官方boot.img> --dump 0`，留档 `dtb_0.dtb`（后面核对寄存器用）。
4. 拉源码：`bash scripts/fetch-santoni.sh`，**记下 commit** `531aeac`，之后所有结论绑这个 commit。
5. 生成 8916 补丁集：`python3 scripts/port-patches.py` → `patches-8916/`（236 个补丁，全部保留顺序）。
6. 串口线：BLSP1_UART2 = GPIO4/5。红米2 这组点没有现成焊盘，需要拆后盖找测试点或用 USB-TTL 夹。**没有串口就只能靠 framebuffer 上的文字判断**，调试效率差 10 倍。

**Go/No-Go**：`check-device.sh` 全绿 + 串口能出字符 + 补丁集生成成功。

### 阶段 1：shim → XNU 早期日志（1-2 周，零风险）

目标：`LK → shim → XNU` 打出第一行内核日志，GIC/timer 起来，单核。

1. 先只编 shim（不带 payload）：`bash scripts/build-shim.sh`，`fastboot boot` 验证 shim 自己的串口输出和屏幕文字。这一步就能确认：串口通不通、FB 基址对不对、GIC 能不能 ack。
2. 打开 `wdt=-1`（关狗）、`debug=0x14e`。shim 会在 192000 tick 上打心跳。
3. 编 XNU：`machine_config=WT86047`（`patches-8916/0085` 已把 MASTER 配置、板级头、MakeInc 全改好），`kernel_config=DEVELOPMENT`。
4. **改 `0065` 的 PSCI 失败处理**：`panic("SWEET CPU: cpu=%u NOT alive")` → `IOLog`。不带 `cpus=` 时它默认单核，但这条 panic 会拦住任何多核尝试，先拆掉。
5. `bash scripts/build-shim.sh kernel.macho` 打包，`fastboot boot`。

**M1 验收**：串口出现 XNU 早期日志（`AppleACPIPlatformExpert`/`pmap`/`machine_startup` 任一），shim 心跳不中断，10 分钟不重启。

**卡点与对策**
- GIC 初始化就挂 → 用 `shim/gic_test.c` 的主机测试先排除代码问题，再怀疑 8916 QGIC2 的私有寄存器（`APQ8016E` TRM 对照）。
- 早期 panic 在 `pexpert` 之前 → 十有八九是 4K 页补丁（`0082/0084/0093`）没生效，检查 `ARM64_BOARD_CONFIG_WT86047` 是否在 `board_config.h` 里被包含。

### 阶段 2：存储与根文件系统（2-4 周，这一步才开始动数据）

**路线 A（RAM root，先做）**
- rootfs 打成 cpio/ramdisk，`rd=md0`。2GB 内存只能塞精简用户空间。
- 用于验证：HFS+ 挂载链路、launchd 能不能起。
- 零 flash 写入，随时回退。

**路线 B（eMMC root，正式方案）**
1. 在 `userdata` 建 HFS+ 卷：`tools/mkiosroot.sh` + `sweet-root-tweaks.py`，塞 iPhone X（iPhone10,3 `d22ap`）iOS 16.7.x 的用户空间。
2. 用 `0142` 的 `rd=emmcroot0 emmcrw=1` 挂根。
3. **写之前必须做的核对**：`0142` 按 GPT 名找分区（默认 `userdata`），写有 fence；`0143` 的 system 日志区必须先禁用或重算偏移。
4. 驱动侧要核的只有几项（都是"确认一致"级别，不是"重写"级别）：vendor `CORE_HC_MODE(+0x78)`、`CORE_VENDOR_SPEC(+0x10c)`、`SDHCI_RESET_ALL` 时机。8916 与 8937 同为 sdhci-msm **v4**，预期同值，用串口读回打一次就能确认。

**M2 验收**：日志出现 `BSD root: emmcroot0`，能读到 `/System/Library/CoreServices/SystemVersion.plist`。

### 阶段 3：用户空间（1-2 周）

全套替身补丁基本都是现成的（pthread/BearSSL/AMFI/bindfs/AppleKeyStore/IOSurface/Sandbox/HID/iPhone X 身份/CoreAnimation work interval），主要工作是参数适配 + 跟着 panic 补。

**M3 验收**：`launchd` 起来，`backboardd` 起来，framebuffer 出现第一帧。

### 阶段 4：显示与输入（1-2 周）

1. **显示**：第一阶段就画 LK 留下的 `0x83000000`。先实测格式：写纯色块看颜色对不对（8916 的 LK splash 常见 24bpp RGB888，santoni 也有 `0147-santoni-24bpp-iomfb` 先例）。MDP 直扫（`0092`）是 topaz 的，**不要用**，8916 的 MDP 版本不同。
2. **触控**：`focaltech,5x06` @ i2c `0x78b9000` 地址 `0x38`，IRQ GPIO13。`0060/0063` 是同族驱动，主要核对 I²C 时序（`0163` 那条 spmi-v2 i2c timing 的经验可以借）。
3. **按键**：PM8916 `power-on@800`，电源键走 PON，音量键走 PMIC GPIO。`0166/0170` 直接改。

**M4 验收**：锁屏可见 + 滑动解锁 + 截图。

### 阶段 5：内存与稳定（1-2 周）

2GB 的先例是 redmi4X 的 2GB 版能跑。裁剪 daemon、降帧率、关 AMFI/沙箱、`launchd_unsecure_cache=1`。

**M5 验收**：主屏连续 10 分钟不 panic。

### 阶段 6（可选，不建议）

音频 → WiFi → GPU。Adreno 306 比 A505 更老，Mesa freedreno 支持更差，santoni 那套 GPU 工作（`0180`-`0230` 共 50 个补丁）在 8916 上**基本不可用**，别指望。

---

## 五、SMP 专项（v2.0 漏掉的真风险）

8916 原厂固件不实现 PSCI，三条路：

| 路线 | 做法 | 代价 | 建议 |
|---|---|---|---|
| **A. 单核** | `cpus=1`（`0065` 默认就是 1），把失败 panic 改 IOLog | SpringBoard 会非常慢，但能亮 | **先做这个**，够到 M3/M4 |
| B. lk2nd + qhypstub | 用 `fastboot boot lk2nd.img` 或刷 boot 分区跑 lk2nd，再 `fastboot flash qhypstub qhypstub.bin`，让 8916 有标准 PSCI | 要动 boot/hyp 分区，**违反"不干扰系统"**；qhypstub 是 EL2 stub，还会改变 TZ 行为 | 想上四核再谈，且必须你明确同意 |
| C. 自己实现 ACC 释放 | 按 `qcom,arm-cortex-acc` 走 SCM/ACC 释放核，参考 lk2nd 的 msm8916 实现 | 纯自研，坑深 | 只在 A/B 都不满意时考虑 |

**先 A。** 单核能亮屏，就说明整条链路（引导、GIC、存储、用户空间、显示）都通了，多核只是性能问题，不是正确性问题。

---

## 六、构建环境（没 Mac 也能干）

XNU 8796 的编译链要求：macOS + Xcode 16.0 + `iig --deployment-target 13.5`。仓库 CI 就是 `macos-15` 跑。

**推荐路径（零 Mac 成本）**：
1. Fork `console-ramoops/ios-santoni`。
2. **不用**再拷补丁：`ios-santoni/xnu/patches/` 已经是 8916 版（236 个），CI 的 `xnu.yml:115` 直接
   `cp xnu/patches/*.patch xnu/patches/*.sh dxb/patches/`。
   ⚠️ `patches-8916/` 是 `xnu/patches/` 在 2026-11-20 21:27 的**旧快照**，覆盖回来会让阶段 1 的
   12 小时修复全部失效（`0098` 退回 `0x90001000`、`0065` 退回 `panic`）。
   详见 `patches-8916/README-DEPRECATED.md`。
3. 跑 `.github/workflows/xnu.yml`，输入 `machine_config=WT86047`、`kernel_config=DEVELOPMENT`。
4. 下载 artifact（`kernel.*.vmapple`），回到本机 `bash scripts/build-shim.sh kernel.macho` 打包。

**本地路径（有 Mac/Linux 交叉工具链）**：`blacktop/darwin-xnu-build` @ `663906528d1cd5ea8fde7de6f16de73db9511549`，按 `xnu.yml` 里的步骤复现。Linux 上只能交叉编 shim（`-target aarch64-linux-gnu`，脚本已配好），XNU 本体编不了。

**还缺的东西（仓库不分发，得自己搞）**：
- iPhone X（`iPhone10,3`）iOS 16.7.x 的 IPSW → 取 `dyld_shared_cache`、`/System`、`/usr/lib`。
- `resources/` 里的设备 dump（被 `.gitignore` 排除了，作者的私有件）。

---

## 七、补丁移植策略

**原则：不删、不重排。** 236 个补丁是顺序 `git apply` 的，删一个会让后面的 context 对不上。

`scripts/port-patches.py` 做的事：
- 机械替换：`SANTONI→WT86047`、`santoni→wt86047`、`MSM8937→MSM8916`、`MAX_CPUS 8→4`、`SWEET_SMP_MAX_CPUS 8→4`。
- 分类留档（`patches-8916/manifest.csv`）：174 个通用/板级（文本替换即可）、50 个 topaz/GPU（保留但编译期隔离）、4 个 UFS（保留但死代码，根设备走 SDHCI）。
- 8 个必须人工改逻辑的列在 `patches-8916/MANUAL-ADJUST.md`：`0065`(PSCI)、`0143`(system 日志区)、`0142`(寄存器核对)、`0168`(FB 基址)、`0098`(UART)、`0092`(别用)、`0085`(已处理)、`0017`(零改动)。

**验证方式**：改完先 `git apply --check` 全部过，再编 `DEVELOPMENT` 配置看有没有新 warning。补丁链本身有 `tools/check_patch.py` 和 `tests/run_tests.sh` 可以跑。

---

## 八、里程碑与最短路径

```
现在 →  [1天] check-device + fetch + port-patches          （零风险）
      →  [1天] build-shim 只编 shim + fastboot boot        （零风险，看串口有没有字）
      →  [1-2周] 编 XNU(CI) + 单核启动                      （零风险）
      →  [M1]  XNU 早期日志 + GIC/timer 正常
      →  [2-4周] 存储：先 md0 内存 root，再谈 eMMC          （md0 零风险；eMMC 毁数据）
      →  [M2]  rootfs 挂载
      →  [M3/M4] launchd/SpringBoard + 720p 锁屏 + 触控
```

**最快能看到东西的顺序**：shim 单独跑（半小时）→ 串口有字 → 才值得去搞 XNU 编译。别一上来就啃 XNU，编译一次 40 分钟，早期失败的反馈太慢。

---

## 九、风险表 v3.0

| 风险 | v2.0 | **v3.0** | 依据/对策 |
|---|---|---|---|
| eMMC 存储 | 🔴 高 | 🟢 **低** | `0142` 现成驱动，SDCC1 地址与 8916 DTB 完全一致，且不自己配时钟（吃 LK 交接态） |
| SMP / 多核 | 未提 | 🟠 **中（新增）** | 8916 无 PSCI。先单核，多核要 lk2nd+qhypstub |
| GICv2 | 🔴 高 | 🟢 低 | 地址逐字节一致 |
| 显示 | 🟠 中 | 🟢 低-中 | LK 已点亮面板，只改 FB 基址；格式需实测 |
| 触控 | 🟠 中 | 🟢 低 | FT5x06 同族，地址/中断都确认 |
| 2GB 内存 | 🟠 中 | 🟠 中 | 2GB 版 redmi4X 有先例；阶段 5 裁剪 |
| 上游项目未验证 | 🟠 中 | 🟠 **中偏高** | README 原话：`identified hardware builds`、`unproven`、最新用户报告是"黑屏无 logo"且日志待查。**锁定 commit，别跟上游漂** |
| 破坏 Android 数据 | — | ⚠️ **明确** | 阶段 2 写 userdata 必毁数据；`0143` 会写 system；`emmcrw=1` 是唯一写开关 |
| 构建环境 | — | 🟠 中 | 必须 macOS；走 CI 可绕 |

---

## 十、这份方案包的产物

```
ios-port/
├─ PLAN-msm8916-v3.md          ← 本文
├─ README.md                   ← 工作区导览 + 三条命令上手
├─ board/
│   ├─ WT86047.h               ← XNU 板级头（4 核、GICv2、无 PAN、地址注释）
│   └─ hw-facts.md             ← DTB 实测证据表（可复现命令）
├─ patches-8916/               ← 236 个已改名的补丁 + manifest.csv + MANUAL-ADJUST.md
├─ ios-santoni/                ← 上游源码（只读参考，别在里面改）
└─ scripts/
    ├─ dtb_probe.py            ← 从 boot.img 抽 DTB / 查节点（已跑通）
    ├─ port-patches.py         ← santoni → wt86047 补丁移植（已跑通）
    ├─ check-device.sh         ← 设备体检（只读）
    ├─ fetch-santoni.sh        ← 拉/更新上游
    └─ build-shim.sh           ← 编 shim + 打 fastboot 镜像（不碰设备）
```
