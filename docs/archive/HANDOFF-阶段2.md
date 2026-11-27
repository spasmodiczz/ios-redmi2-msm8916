# 阶段 2 交接报告 — MSM8916（Redmi 2 / wt86047）移植 iOS（XNU）

**日期**：2026-11-21 11:15
**当前阶段**：阶段 1 完成（M1、M2 达成），阶段 2 未开始
**一句话**：XNU 已经在真机上跑过设备树平台层，自己接管了屏幕、注册了 HID 和电池，
**全程无 panic**；现在卡在「等 root device」，需要把 eMMC 从轮询式探测改成能在等待之前发布 IOMedia。

---

## 一、成果概览（都已上机验证）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | shim（裸机载荷加载器）在真机跑通，屏幕出日志 | ✅ |
| M1 | XNU 内核被 shim 装载并跳转执行，早期日志出现在屏幕上 | ✅ |
| M2 | XNU 越过设备树平台层，`platform expert registered`，`AppleCLCD` 发布面板并接管显示 | ✅ |
| M3 | root device 挂载 → 内核态完成初始化 | ⛔ **当前卡点** |
| M4 | 用户态（launchd 等） | 未开始 |

最后一屏日志（2026-11-21 11:07）：

```
…（滚动前的历史）
Security policy loaded: Guarded mode policy (vnguard)
SWEET: trust caches unavailable (Image4/AMFI not installed)
keepsyms boot arg specified: keeping linkedit segment for symbols.
No POST boot-arg set.
SWEET: starting device-tree platform expert
SWEET AKS: AppleKeyStore registered (no passcode, unlocked)
SWEET WDT: IOWatchdog registered (accepts check-ins, never fires)
SWEET: platform expert registered
SWEET FB: empty IOAcceleratorES published (no 30 s wait in CAMetalContextCreate)
SWEET FB: AppleCLCD 720x320 panel stride 2160 24-bpp at 0x831fa400,
          frame stride 2880, default surface 1, presented as 720x320
SWEET SURF: IOCoreSurfaceRoot registered
SWEET HID: buttons registered (test every 0 s)
SWEET HID: touch registered
SWEET CPUFREQ: no cpufreq-hw on this board; skipped
SWEET BATTERY: static fallback, 100% 4200mV external=1 charging=0
```

---

## 二、硬件实测事实（**不要重测，直接用**）

### 2.1 显示屏

| 项 | 数值 | 备注 |
|---|---|---|
| 面板分辨率 | 720 × 1280 | |
| framebuffer 基址 | `0x83000000` | DTB 的 `splash_region`，20 MiB |
| 行跨度（stride） | **2160 字节**（720 × 3） | 24 bpp，无 padding |
| 像素格式 | **24 bpp，字节序 G / R / B** | 实测：写 `(ff,00,00)` 显示**绿**；写 `(00,ff,00)` 显示**红** |
| **被扫描输出的行** | **仅 960 ~ 1279**（共 320 行） | LK 留给我们的窗口；其余 960 行是 LK 的启动图 |
| 可见区起始物理地址 | **`0x831FA400`** = `0x83000000 + 960 × 2160` | 注意不是 `0x831FA800`（差 1024 字节 = 341 px） |
| 可见区映射位置 | 屏幕**最上方** 320 行 | |

> **踩过的坑**：`0x1FA800` 与 `0x1FA400` 只差 1024 字节，但会造成文字右移 341 px 并裁掉右侧内容。
> shim 里的算法一直是 `FB_BASE + FB_VIS_TOP * FB_STRIDE`，XNU 侧当初没照抄，代价是四轮构建。

### 2.2 CPU

| 项 | 数值 |
|---|---|
| 核心 | 4 × Cortex-A53（MSM8916），单 cluster |
| `MIDR_EL1` | `0x410FD030` |
| 启动核 `MPIDR_EL1` | `0x80000000` ⇒ **Aff0=0, Aff1=0 ⇒ phys id 0** |
| ADT 里的逻辑编号 | **必须** phys `0x000~0x003` → logical cpu0~cpu3，且 cpu0 为 `running` |
| **PSCI** | **不存在**。`PSCI_VERSION` / `SMCCC` 均返回 `0xFFFFFFFF`（SMC 直接失败） |
| SMP 现状 | 单核可跑；四核的 `CPU_ON` 无 PSCI 可用，`startCPU` 会 `IOLog` + `KERN_FAILURE`，不 panic |

> 原 santoni 补丁把 ADT 写成 8 核（`0x100~0x103` 当 cpu0~3），导致**真正在跑的核是 logical cpu4**，
> 触发 `machine_routines.c` 的 `cpu_id < ml_get_cpu_count()` 断言并崩溃。改回 4 核后断言消失。

### 2.3 看门狗与其它

| 项 | 数值 |
|---|---|
| LK 是否开 WDT | **没有**（`en=00000000`），shim 实测 |
| WDT bark / bite | `0007FFFF` / `000FFFFF` |
| MMU 收益 | 开 MMU 后内存基准 21802 µs → 854 µs（约 25×） |
| GIC | GICD/GICC 均使能，100 Hz 定时器实测 5 秒 514 次中断，INTID 20 |
| 无 PSCI 时复位方式 | 靠 WDT bite（约 11 秒）回 LK |

---

## 三、构建与部署链路

### 3.1 仓库与 CI

| 项 | 值 |
|---|---|
| 代码仓库 | `git@github.com:spasmodiczz/ios-redmi2-msm8916.git`（用户 fork，**只有 main**） |
| 本地分支 | `ios-port/ios-santoni` 仓库内分支 `wt86047`，远端名 `mine` |
| 工作流 | `.github/workflows/xnu.yml` |
| 运行器 | **`macos-15-intel`**（arm64 池排队 15 分钟就会被丢弃，勿改回） |
| Xcode | 优先 `Xcode_16.0.app`，否则最新 `Xcode_16*.app`，只要求主版本 16 |
| 单轮构建 | 8 ~ 16 分钟（ccache 命中时约 9 分钟） |
| 产物回推 | 构建末尾把精简内核 + `build.log.gz` 推到 **`artifacts` 分支** |

**取内核（不需要 token、不需要网页下载）**：

```bash
cd ios-port/ios-santoni
git fetch -f mine artifacts
git show FETCH_HEAD:kernel.development.wt86047 > ../kernel-outN/kernel.development.wt86047
```

### 3.2 打包与上机

```bash
# 1) 进 fastboot：长按电源 10 秒关机 → 音量下 + 电源
# 2) 打包（内核作为 payload 塞进 ramdisk，注意传绝对路径）
wsl -d Ubuntu-26.04 -- bash -lc \
  "cd /mnt/d/01_手机与刷机/红米2/ios-port && \
   bash scripts/build-shim.sh /mnt/d/01_手机与刷机/红米2/ios-port/kernel-outN/kernel.development.wt86047"
# 3) 纯内存运行，不写任何分区
fastboot boot build/wt86047/shim/out/shim-boot.img
```

- `fastboot boot` 跑完不会回 fastboot，会复位进 Android 系统；要继续测必须手动循环进 fastboot。
- 如果内核 hang 住，USB 会消失（既不在 fastboot 也不在 adb），这是预期现象，不是砖。
- 全程 `boot` 分区未动，设备可随时正常开机。

### 3.3 ccache

`key: ccache-${MACHINE}-${KERNEL}-${run_id}` + `restore-keys` 前缀，**并且有显式的
`actions/cache/save` 步骤**（`if: always()`）。原先 `actions/cache` 的 post 步骤在失败时不保存，
导致每轮都冷编；这是"每轮 15 分钟"的直接原因。

---

## 四、启动链路（shim → XNU）

shim 是我们自己的裸机加载器（ARM64，运行在 EL1，MMU 关闭起步），职责：

1. 读 LK 传过来的 DTB 与 initrd
2. 解析 ramdisk 里的 cpio，取出 `SWEETXNU` payload（Mach-O 内核）
3. 按 Mach-O 的 11 个 segment 铺到内存（`PHYS base=0x98000000 size=0x28000000`）
4. 生成 ADT（`tools/mkadt.py`）与 `boot_args`，虚拟基址 `0xFFFFFFF006000000`
5. 跳到内核入口（实测 `ENTRY 0xFFFFFFF0074454B0`，物理 `0x994454B0`）
6. 跑 MMU/GIC/定时器自检（`MEM / PSCI / GIC / TIMER` 那几行日志），然后 `reset(40)`

**shim 自己的控制台**（`shim/console.c`）：24 bpp、G/B/R→实际 G/R/B、从 `0x831FA400` 起、
320 行窗口、8×16 字形、90 列、带滚动。它是**唯一可信的显示基准**，XNU 的显示参数都以它为准。

---

## 五、补丁集结构（236 个）

```
ios-port/ios-santoni/xnu/patches/
  0001 ~ 0236   *.patch    （全部改名为 wt86047，顺序与 santoni 版一致）
  0019/0020/0026 *.sh      编译前现场从 Apple 开源库拉取并展开 libpthread / bearssl / hfs
```

### 5.1 关键补丁

| 补丁 | 作用 | 状态 |
|---|---|---|
| `0010-sweet-early-framebuffer-debug` | 创建 `sweet_early.c`：8×16 字形早期控制台 | 核心 |
| `0012-sweet-keep-debug-console-after-ttbr0` | TTBR0 拆除后给 framebuffer 装 2 MB L2 别名，`sweet_early_relocate()` | 核心 |
| `0082-wt86047-4k-entry` | start.s：4K 入口、stage-0/1 标记、两块 2 MB Normal-NC 映射 | 核心 |
| `0098-wt86047-early-console` | WT 专用：24 bpp、`EARLY_W/H/STRIDE`、上滚、`FB_X_BIAS` | 核心 |
| `0140-wt86047-kprintf-early-console` | **让 kprintf 直接走 `sweet_early_putc`**（`fbcon_probe` 提前 return） | 核心 |
| `0141-wt86047-disable-topaz-battery-cpufreq` | 把 topaz 的 cpufreq/i2c 全部编掉，改静态兜底 | 已完成 |
| `0142-wt86047-sdhci-emmc-driver` | 轮询式 SDHCI/eMMC 驱动 + `IOKitBSDInit` 里的探测调用 | **未生效，见 §七** |
| `0003-sweet-framebuffer-serial-console` | pe_serial 的 fbcon 控制台 | **死代码**（0140 让它提前 return） |
| `0065-sweet-smp-psci` | PSCI `CPU_ON` 失败改为 `IOLog` + `KERN_FAILURE` | 按无 PSCI 改写 |
| `0173-wt86047-secondary-entry` | 从核入口 | 未使用（无 PSCI） |
| `0176-wt86047-physical-cpu-clusters` | CPU 拓扑 | 与 ADT 一致 |

### 5.2 显示链路的真实路径（重要）

```
kprintf → pe_serial → wt86047_early_serial_functions.transmit_data
                    → sweet_early_putc()            ← 唯一在画屏幕的函数
                    → early_pixel24(x, y, c)
                    → early_fb + y*2160 + x*3       ← 24bpp, G/R/B
```

- `early_fb` 初值 `0x831FA400`（MMU 关闭，物理直写）
- `sweet_early_relocate()` 之后 = `(base & ~0x1FFFFF) + 0x1FA400 - 192`
- **`0003` 的 fbcon 是死代码**，改它不会有效果（这是四轮无效修改的根源）

### 5.3 补丁链的强依赖规则（必须遵守）

`0012` / `0140` / `0150` 把 `0003` 的**新增行当作上下文引用**。改动 `0003` 里任何一行，
必须同步改这些补丁的上下文，否则它们会**静默失效**（dxb 只警告不中断）。

工作流已加守卫：`Skipping patch .*patches/[0-9]{4}-` 命中即构建失败。

---

## 六、已踩过的坑与铁律

1. **santoni 的硬件常量漏进 WT86047 分支**（最高频错误，出现 5 次）。逐条核对过：
   - `mkadt.py` 帧缓冲基址 `0x90001000` → `0x83000000`
   - `0082`/`0098`/`0012` 早期标记与别名映射 `0x90000000/0x90200000` → `0x83000000/0x83200000`
   - `mkadt.py` CPU 拓扑 8 核 `0x100~0x103` → 4 核 `0x000~0x003`
   - `0012` relocate 偏移 `0x1000` → `0x1FA400 - 192`
   - `0003` fbcon 的 `stride < width*4` 校验（32bpp）→ 已改 `*3`（但该路径是死代码）
2. **改显示参数前，先确认那段代码真的在编译且被执行**。否则会连改三轮死代码。
3. **补丁改名要连 `.sh` 一起改**：`0019/0020/0026` 三个 stager 里有
   `[[ "$MACHINE_CONFIG" == SWEET || == SANTONI ]] || exit 0`，不改就静默不展开文件树，
   构建会以 `sweet_bearssl/inc/bearssl_hash.h: No such file` 之类失败。
4. **`tests/run_tests.sh` 已禁用**：224 处断言写死 santoni 的补丁名，必然失败。
5. **用照片反算几何时，色块左缘必须排除上一级残留画面**（例如 shim 的红底），
   否则边界会量偏 10 px。整数性检验有效：真实偏移落在 **8 列 = 64 px = 192 字节**，
   而错误读数给出 9.2 列 / 74 px。
6. **`fastboot boot` 后设备可能挂住不动**，需长按电源 10 秒强制关机再进 fastboot。

---

## 七、当前卡点：等 root device（阶段 2 的第一个任务）

### 7.1 现象

日志最后一行是 `SWEET BATTERY: static fallback, …`，之后**再无输出，也无 panic**。

### 7.2 代码层面的定位

`0142` 把 eMMC 探测插在 `IOFindBSDRoot()` 的**末尾**：

```
@@ -787,3 +787,34 @@     ← 插入点紧接在 "Now check if we are trying to root on a memory device" 之前
    { extern int wt86047_sdhci_probe(void); int emmc_error = wt86047_sdhci_probe(); … }
    if (strcmp(rdBootVar, "emmcroot0") == 0) { … }
```

而 `wt86047_sdhci_probe()` 一进去就会打印：

```
WT86047 SDHCI: core MCI_VERSION 0x…… GENERICS 0x……
WT86047 SDHCI: probe step 0: HC_MODE ……
```

**屏幕上一条都没有** ⇒ 根本没有执行到 `IOFindBSDRoot()` 的末尾。

结合 XNU 的 `IOFindBSDRoot()` 结构，唯一合理的解释是：

> 它在函数前段等待 `IOMediaBSDClient` / `IOMedia` 出现，而我们没有任何块设备驱动会发布 `IOMedia`，
> 于是永久阻塞；我们的 eMMC 驱动是**轮询式探测**，恰好排在等待之后 ⇒ **先有鸡还是先有蛋**。

### 7.3 阶段 2 建议路线（按优先级）

**任务 1（阻塞项）：让 `IOMedia` 出现**

三条候选，建议按顺序尝试：

1. **把 `wt86047_sdhci_probe()` 的调用移到 `IOFindBSDRoot()` 前段**（等待之前）。
   风险最低、改动最小；需要拿到 `IOFindBSDRoot()` 的源码做锚点。
2. **在平台层（`platform expert registered` 之后）就调用一次探测**，并让驱动在探测成功后
   **发布一个 `IOMedia` 对象**（`IOMedia::init` + `attach`/`registerService`）。
   工作量大一些，但符合 IOKit 模型，后续 root mount 也走得通。
3. **绕过：`rd=md0` 纯内存 root**。`0142` 已确保 eMMC 探测失败不致命，
   可用于**先把内核态跑通**（用户态后续再补），代价是没有持久化 rootfs。

**任务 2：root mount**

- 我们的 root 走 GPT 分区（`rdBootVar == "emmcroot0"` 分支已经写好）。
- 需要确认：分区表解析、HFS/APFS 选择（补丁集里有 `0026-sweet-builtin-hfs`）、
  `IOFindBSDRoot` 的 `major/minor` 与 `dks1` 一致。

**任务 3：驱动补全（不阻塞 root，但用户态会需要）**

| 驱动 | 现状 | 备注 |
|---|---|---|
| cpufreq | 编掉（MSM8916 无 cpufreq-hw 块） | `0xf523000` 在这颗 SoC 上未分配，映射即 fault |
| i2c / 电池 gauge | 编掉，静态兜底 | topaz 的 `0x4a84000` 在 MSM8916 未分配 |
| 触摸 | 已注册（`SWEET HID: touch registered`） | 待验证事件上报 |
| GPU | `IOAcceleratorES` 已发布空实现 | 软件合成可用 |
| 多核 | 无 PSCI，单核 | 若要 SMP，需要 APCS/MSS 的邮箱启动路径 |

---

## 八、关键文件与命令

```
D:\01_手机与刷机\红米2\ios-port\
├── ios-santoni/                     XNU 仓库（分支 wt86047）
│   ├── xnu/patches/                 236 个补丁 + 3 个 stager .sh
│   ├── tools/mkadt.py               生成 ADT（帧缓冲节点 + CPU 拓扑）
│   └── .github/workflows/xnu.yml    CI
├── shim/                            裸机加载器（console.c / loader.c / shim.c）
├── scripts/build-shim.sh            内核 → fastboot 镜像
├── build/wt86047/shim/out/shim-boot.img   产物
├── kernel-out*/                     各轮取回的内核
├── STATE.md                         逐轮实测记录（很详细，建议先读第九~十一节）
└── HANDOFF-阶段2.md                 本文件
```

常用命令：

```bash
# 校验单个补丁的 hunk 计数（改动补丁后必做）
python build/wt86047/tools/check_patch.py ios-santoni/xnu/patches/XXXX-*.patch
# 改动后重算 hunk 计数
python scripts/_recount_patch.py ios-santoni/xnu/patches/XXXX-*.patch
# 全量校验 236 个补丁
for f in ios-santoni/xnu/patches/*.patch; do python build/wt86047/tools/check_patch.py "$f"; done
# 设备体检（只读）
bash scripts/check-device.sh
```

---

## 九、风险与未验证项

1. **多核未启用**（无 PSCI）。单核能跑，但用户态对 SMP 有假设时需要补。
2. **屏幕只用了 320 行**（960~1279），`AppleCLCD` 发布的是 `720x320`。
   要让系统真正用满 720×1280，需要让 LK 交出完整的扫描输出或自己重配 DSI 时序 —— 属阶段 3。
3. **`frame stride 2880`** 是 `AppleCLCD` 内部按 32bpp 算的 surface 跨度，与面板 2160 不一致，
   目前只影响它自己的合成缓冲，不影响日志；做图形时需要处理。
4. **`0113-topaz-modem-pas-boot`** 把 topaz（SM7150）的 modem 驱动无条件编进来了，
   含 `0x90000000`。当前 ADT 没有对应节点所以不会实例化，属技术债。
5. **eMMC 驱动从未跑起来过**（`0142` 的探测一次都没执行），其寄存器时序、
   DLL 配置、UHS 模式选择全部**未经真机验证**。
6. **root 分区布局未确认**：设备已被用户完整备份，可以自由改动 system/data，
   但阶段 2 建议先只读挂载验证。

### 7.4 已确认的调用链与判据（2026-11-21 11:20 核对上游 xnu-8796.101.5）

```
bsd_init()                      bsd/kern/bsd_init.c:477
  └─ … pseudo_inits … 
     return IOKitBSDInit();      bsd_init.c:1099
        └─ setconf()             IOKitBSDInit.cpp:1136
           └─ IOFindBSDRoot()    IOKitBSDInit.cpp:655
              ├─ ① waitForMatchingService(IOResources + IOBSD) 30 s → 失败打 "!BSD"
              ├─ ② 解析 rd/rootdev、/chosen、ramdisk
              ├─ ③ 【我们 0142 的 eMMC 探测插在这里，一进去就打 MCI_VERSION】
              ├─ ④ startDeferredMatches()
              └─ ⑤ waitForService(root matching) → 打 "Still waiting for root device"
```

**判据**：屏幕上既没有 `!BSD`、也没有 `WT86047 SDHCI: …`，而这两条都出现在
`IOFindBSDRoot()` 的**前 30 秒内**。所以卡点**不在 root 等待**，而在
`IOFindBSDRoot()` 之前 —— 即 `bsd_init()` 内部或更早。

**正在验证**：新增补丁 `0237-wt86047-boot-checkpoints.patch` 打三条检查点
（`bsd_init` 入口 / `bsd_init` 末尾 / `IOFindBSDRoot` 入口），一次上机即可把范围二分。

> 注意 `IOFindBSDRoot()` 开头那个 30 秒等待是**无条件**的：即使没有文件系统驱动响应
> `IOBSD` 资源，30 秒后也会继续（打 `!BSD`）。因此"卡住"如果超过 30 秒还没有 `!BSD`，
> 就一定还没进这个函数。
