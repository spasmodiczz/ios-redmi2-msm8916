# wt86047（Redmi 2 / MSM8916）XNU 移植 — 项目手册

> 面向接手者。记录**怎么用**、**为什么这样做**、**哪里会踩坑**。
> 最后更新：2026-11-27。当前 HEAD `84ca61a`，CI 绿。

---

## 一、目标与当前状态

把 Apple 开源的 XNU（`xnu-8796.101.5`，iOS 16 世代）移植到 Redmi 2（wt86047 / MSM8916 / 4×Cortex-A53），
并启动 iPhone X（`iPhone10,3`）iOS 16.7.16（20H392）的**原始用户态**。

| 阶段 | 状态 |
|---|---|
| 内核启动、framebuffer、触摸、RPM、SDHCI/eMMC | ✅ 已通 |
| mockfs 内存盘为 root | ✅ 已通（阶段 2 的 M3） |
| **eMMC 上的 HFS+ 卷为 root** | ✅ 已通（2026-11-21） |
| launchd 真实启动 + dyld 映射共享缓存 | ✅ 已发生 |
| **用户态 4 KB 地址空间** | ✅ 已通（`PS=12`、`MP=12`） |
| 执行到 `libcorecrypto` 的 AES 指令 | ✅ 已模拟（`0255`），FIPS POST 实测通过 |
| launchd 引导任务 `mount-phase-1` | ✅ 已通（可写根 `--rd-emmc`，`/` 的 update 成功） |
| launchd 引导任务 `fips`（`cc_fips_test`） | ✅ **FIPS POST result = true**（boot 15） |
| 早期引导全部完成、LaunchDaemons 启动 | ✅ `Early boot complete`，boot 16 约 200 个服务 `exec` |
| 合成器真的出帧（`SWEET SURF` 建面 + `FB: swap`） | ✅ boot 16 出帧 384，`display on`，无 panic |
| **屏幕上到底显示什么** | ❓ **当前未知**：`0257` 的 `sweetfbview=N` 一次启动即可判定 |

**当前状态（2026-11-28 00:30，boot 16 实测，已纠正 boot 15 的两处误判）**：密码学墙已越过 ——
FIPS POST 全项通过（含 `aes_gcm`，靠 `0255` 的 AES/SHA/PMULL 指令模拟 + 「写回异常上下文而非
live 寄存器」的修复）。系统**已经完整引导**：`Early boot complete`，约 200 个服务被 `exec`
（SpringBoard、backboardd、CommCenter、securityd、lsd、coreduetd…），`SWEET FB: display on`，
合成器实际出帧 **384 帧**，`SWEET SURF` 建面 720×1280 与 720×320 两种。

**boot 15 的两处误判（`0256` 去重后暴露）**：
1. `IOServiceMatchingfailed … AppleM2ScalerCSCDriver` 不是 12 689 行死循环，而是**同一条消息被
   `/dev/null` tee 重复捕获**；去重后只有 **3 条**，且 `IOAcceleratorES` 是**匹配到的**
   （`-> IOAcceleratorES`），不是缺失。
2. 「出帧停在第 16 帧」是假象：`present()` 里的日志闸门是硬编码 `sweet_fb_swaps_logged < 16`，
   之后的出帧不再打印。**日志安静并不能证明合成器停了** —— 已由 `0257` 改成「前 8 帧 + 每 300 帧」。

**尚未解决的，按证据强弱排序**：
- 屏幕到底显示什么**未知**（无相机）。`0257` 的 `sweetfbview=N` 把合成帧降采样成 60×22 字符图
  打进日志，一次启动即可判定「黑屏 / 苹果 logo / 转圈 / 花屏」。
- `kAKSWrapKey`（选择子 10）未实现 → `kIOReturnUnsupported`，`securityd` 在 t=139–157、
  t=270–298 两批各 32 次重试。其余选择子（0/7/14/17/26/102/103…）均返回 0。
- 可写根在 HFS+ 上被反复标记不一致：`hfs_mark_inconsistent` 23 次、`BlockMarkFreeInternal`
  试图释放未分配块 5 次（`--rd-emmc` 的已知代价）。
- `/private/var` 只是 HFS+ 根上的目录，没有 Data Protection（`MKB_INIT: mount point
  (/private/var/) does not support Data Protection`），系统据此走 `com.apple.datamigrator`
  + `migrationpluginwrapper` 的数据迁移分支。
- 崩溃循环（非致命）：`fairplayd.H2` 28 次、`bluetoothd` 26 次、`CommCenter` 6 次 `exec`。

AES 的历史（SIGILL 证据链、A 方案失败、C 方案实施）见第十一节。

---

## 二、目录地图

| 路径 | 作用 | 是否入库 |
|---|---|---|
| `D:/01_手机与刷机/红米2/ios-port/` | 移植工作区根（shim、脚本、报告、产物） | 部分 |
| `ios-port/ios-santoni/` | **补丁与工具的 git 仓库**（fork 自 console-ramoops/ios-santoni） | 是 |
| `ios-port/upstream/xnu-xnu-8796.101.5/` | 上游 XNU 源码（只读，补丁的基线） | 否 |
| `ios-port/build/wt86047/shim/` | shim 构建产物（`out/shim-boot.img`） | 否 |
| `ios-port/kernel-out<N>/` | 每次 CI 取回的内核（N 递增，用于回溯对比） | 否 |
| `ios-port/runlog/` | 真机日志样本（`.txt` 已解码、`.bin` 原始环） | 否（gitignore） |
| `ios-port/docs/archive/REPORT-阶段3-eMMC-Root.md` | 阶段 3 技术报告（结论、数据表、补丁索引） | 是 |
| `ios-port/DOC-console-log.md` | **持久日志通道操作手册** | 是 |
| `ios-port/PROJECT-GUIDE.md` | 本文件 | 是 |
| `ios-port/tmp/` | 一次性脚本（提取镜像、探针、对比工具） | 否 |
| `E:/ios-rootfs/` | 镜像产物与解出的卷（约 30 GB） | 否（不在工作区） |
| `tmp/`（工作区根） | 本次调用的临时脚本集合 | 否 |

---

## 三、核心文件与用法

### 3.1 打启动镜像：`ios-port/scripts/build-shim.sh`

```bash
bash scripts/build-shim.sh <kernel 绝对路径> [档位]
```

| 档位 | cmdline 差异 | 用途 |
|---|---|---|
| `--rd-emmc-ro` | `rd=emmcroot0`，**零写入** | 只读根，最快的验证组合 |
| `--rd-emmc-ro-log` | 追加 `sweetlog=1` | **只读根 + 写 system 分区日志**（推荐用于调试） |
| `--rd-emmc` | 追加 `emmcrw=1 rootrw=1` | 可写根（尚未稳定） |

产物固定为 `build/wt86047/shim/out/shim-boot.img`。

**两条烧写路径**：

```bash
fastboot boot  build/wt86047/shim/out/shim-boot.img   # 临时启动，不写分区（首选）
fastboot flash boot build/wt86047/shim/out/shim-boot.img   # 写入 boot（0.4 秒）
```

> 用户 2026-11-21 明确要求：常规验证用 `flash boot`；**临时试新内核用 `boot`**。
> 回到 Android：`fastboot flash boot boot.img`（工作区根，10,186,752 B）。

### 3.2 建根文件系统：`ios-port/scripts/mkiosroot.sh`

从解出的 APFS 卷构建 HFS+ 根镜像。要点：

- `MAX_MIB` 默认 14100（分区 14,254 MiB，留 154 MiB 余量）
- **必须 `NOJOURNAL=1`**：带日志时 Linux `fsck` 恒报 `invalid primary VHB at 2 result 35` 且不收敛
- 脚本是 CRLF，需转 LF 副本运行
- 硬链接 sysos 的共享缓存到 `System/Library/Caches/com.apple.dyld/`
- 自检会核对文件数（179,359）、`fsck appears to be OK`、dyld cache 存在

### 3.3 补丁验证三件套（**每次改补丁必做**）

```bash
# 1) 结构自检：hunk 数量必须 exact
python3 build/wt86047/tools/check_patch.py <patch>

# 2) 干跑：只对"未被前面补丁改过"的文件有效
bash scripts/dryrun-patch.sh <abs path> [前置补丁...]

# 3) 真基线：用 git apply --include=<file> 逐个套前置补丁，再 check
```

**生成补丁的脚本模板**见 `tmp/make-*.sh`：复制 upstream → `git init` → 对目标文件套用所有改过它的前置补丁
→ 改文件 → `diff -u` → 恢复 → `git apply --check`。这是唯一可靠的方式，因为 `patch` 会因权限位整体拒收。

### 3.4 日志通道（**调试主力**，详见 `DOC-console-log.md`）

```bash
# 出包并启动（只读根 + 日志）
bash scripts/build-shim.sh <kernel> --rd-emmc-ro-log
fastboot boot build/wt86047/shim/out/shim-boot.img
# 等 50 秒 → 长按电源 10s → 音量下+电源进 fastboot
fastboot boot twrp-3.7.0_9-0-32位.img
# TWRP 起来后
sh ios-santoni/tools/sweet-log-pull.sh runlog/xxx.txt
python3 ios-santoni/tools/sweet-log.py runlog/xxx.txt.bin --list
python3 ios-santoni/tools/sweet-log.py runlog/xxx.txt.bin out.txt --boot N
```

- 环 16 MiB，在 `system` 分区开头 32769 扇区，跨启动累计，**不碰 HFS+ 根**
- 头 v2 60 字节，`total` 只增不减，边界行 `=== WT86047LOG boot N`
- 取回**必须进 recovery**（XNU 阶段无 adb）

### 3.5 探针补丁（打印到 18 行控制台或底部固定带）

| 补丁 | 输出 | 用途 |
|---|---|---|
| `0240` | `sweet_early_fixed(line, str)` | 底部 32 px **永不滚动**的 2 行带 |
| `0244` | `SWEET SIGILL #n pc= lr= i= pp= pp2=` | 用户态非法指令取证 |
| `0246` | `SRE=`/`SRP=` | `vm_shared_region_enter` 失败码与 map 页移 |
| `0247` | `B0=`/`B1=` | 共享区域实际基址（判断有无 slide） |
| `0252` | `SWEET CACHE FIRST @0x...: [...]` | 回读映射内容，验证映射忠实性 |

> 固定带只有 2 行，多个探针会互相覆盖；`sweet_early_mark` 的彩色块也可能盖住文字。

### 3.6 主机侧分析脚本（`ios-port/tmp/` 与工作区 `tmp/`）

| 脚本 | 作用 |
|---|---|
| `extract_apfs.py` | 从 IPSW 解 OS dmg → 裸 APFS 整盘（27 秒） |
| `build-rootfs.sh` | WSL 内构建 HFS+ 根（约 6 分钟） |
| `table25.py` / `slide-fix.py` | 解析共享缓存子缓存表，把 VA 换算成文件偏移（**必须扣 slide**） |
| `which-image.py` | 在 `imagesText` 表里定位某地址属于哪个 dylib |
| `scan-all.py` | 在全部子缓存里搜索 4 字节模式 |
| `pc-in-gap.py` | 判断某 VA 是否落在映射内（按真实文件大小） |

---

## 四、工具链与环境

| 组件 | 状态与要点 |
|---|---|
| WSL | Ubuntu 26.04；`/tmp` 是 **tmpfs**，VM 回收即清空 → 产物一律写 `/opt/work` |
| WSL 内核 | `linux-msft-wsl-6.6.87.2` + `CONFIG_HFSPLUS_FS=y`；`arch/x86/Makefile` 需加 `REALMODE_CFLAGS += -std=gnu11`（GCC 15 默认 gnu23） |
| `.wslconfig` | `kernel=E:\\WSL\\bzImage-hfsplus-6.6.87.2` |
| apfs-fuse | 源码编译；`-DCMAKE_POLICY_VERSION_MINIMUM=3.5`；GCC 15 需 `-include cstdint -include cstring` |
| llvm-mc | 判指令是否合法**必须带 `-mattr=+crypto`**，否则 `aese` 会被误判为非法 |
| adb / fastboot | Windows 侧，`adb 1.0.41`；recovery 下 `adb devices` 显示 `recovery` |
| 代理 | `http://127.0.0.1:7897`（Windows 侧）；WSL 内用 `172.30.224.1:7897` |
| GitHub | SSH 22 不通，走 `ssh.github.com:443`；`artifacts` 分支用 HTTPS+代理拉 |

---

## 五、镜像链路（实测数据，**勿重测**）

| 项 | 值 |
|---|---|
| IPSW | `iPhone10,3,iPhone10,6_16.7.16_20H392_Restore.ipsw`，6,171,035,648 B |
| BuildManifest 角色 | `087-86698` = OS；`087-86593` = Cryptex1,SystemOS；`087-86715` = Cryptex1,AppOS |
| OS dmg | 4,730,814,044 B（zip 内 **stored**，未 deflate） |
| 解出的 APFS 整盘 | 5,635,047,424 B，GPT + 单 `Apple_APFS` 分区 |
| 三个卷 | os 7,569 MiB / 179,359 文件；sysos 3,204 MiB；appos 7 MiB |
| 共享缓存 | 42 子缓存 + 主文件（主文件仅 384 KB = 总头 + 子缓存表） |
| `userdata` | `0x37ADE7E00` = 14,946,303,488 B = **14,254 MiB** |
| `system` | `0x6400000` = 100 MiB（**空分区，被日志通道占用前 16 MiB**） |
| 根镜像 | `E:/ios-rootfs/iOSRoot.img`，14,784,921,600 B，journal=**off** |
| sha256 | `f4b2a072…b5221b9` |
| 刷入 | `fastboot -S 256M flash userdata`（`max-download-size=0x10000000`），47 个稀疏块 |

---

## 六、补丁体系

- 位置：`ios-santoni/xnu/patches/NNNN-<slug>.patch`，四位编号，CI 按字典序套用
- **编号有重复**：`0082/0084/0085/0093/0094/0095/0097/0098` 各有两个（`topaz-*` 与 `wt86047-*`），
  目前目标文件不重叠、未出事，但建议重编号
- 守卫宏：`ARM64_BOARD_CONFIG_WT86047`（机器级 `-D`），新补丁一律用它包住
- 关键补丁分组：
  - `0004/0081/0085/0094`：机器配置、armv8.0 无 LSE/PAN、板级目标、`-mcpu=cortex-a53`
  - `0082/0084/0093/0097`：4 KB 入口、4 KB 用户 VA、39 位内核 VA、zone VA 尺寸
  - `0142/0253`：SDHCI eMMC + **持久日志通道**
  - `0144`：接受 16 KB 签名的共享缓存（**绕过**校验，非正解）
  - `0244/0246/0247/0252`：诊断探针
  - `0248/0249/0250/0251`：**用户态 4 KB 迁移**（照搬 UniXNU 的 native-4k 方案）

---

## 七、运行与调试流程

```
改补丁 → check_patch + dryrun + git apply --check
      → commit + push mine HEAD:main
      → CI（约 12–15 分钟；判成功看 conclusion，不看"有产物"）
      → git fetch artifacts → kernel-out<N>
      → strings 校验探针字符串
      → build-shim.sh --rd-emmc-ro-log
      → fastboot boot / flash boot
      → 50 秒 → fastboot boot twrp → sweet-log-pull.sh
      → sweet-log.py --boot N → 分析
```

CI 产物回推是 `if: always()`，**失败也会发布**，所以必须看 `conclusion`。

---

## 八、硬件事实（写死，不要再猜）

| 项 | 值 |
|---|---|
| SoC / CPU | MSM8916，4×Cortex-A53，**仅 4 KB / 64 KB 页粒度，无 16 KB** |
| 加密扩展 | **无 ARMv8 Crypto**（AESE/AESD/PMULL/SHA 全缺）→ 见第十节 |
| 无 PSCI | 只有 cpu0 能跑；`ml_wait_max_cpus` 已改为单核直接返回（`0242`） |
| framebuffer | 可见区 `0x831FA400`，buffer 行 960–1279，720×320，stride 2160，**24bpp** |
| 控制台 | 18 行 × 16 px；底部 32 px 为固定带（2 行） |
| 看门狗 | LK 未开启 |

---

## 九、共享缓存结构备忘（**最容易搞错的地方**）

主文件 `dyld_shared_cache_arm64` 只有 384 KB，是"总头 + 子缓存表"：

| 偏移 | 内容 |
|---|---|
| `0xE0` | `sharedRegionStart` = `0x180000000` |
| `0xE8` | `sharedRegionSize` = `0xaade8000` |
| `0x188` | `subCacheArrayOffset` |
| `0x18C` | `subCacheArrayCount` = **42** |
| 表项 | **56 字节**：`uuid[16] + vmOffset[8] + 后缀名[16] + 保留[16]` |

**两个致命陷阱**：

1. **表项步长是 56，不是 32 也不是 48**。写错会解析出一堆垃圾。
2. **dyld 会给整个共享区域加 slide**（ASLR）。实测一次 slide = `0x21314000`，缓存头落在
   `0x1a1314000` 而不是 `0x180000000`。**换算 pc → 文件偏移时必须先扣 slide**：
   ```
   cache_offset = pc - (0x180000000 + slide)
   ```
   不扣 slide 会得出"每次落在不同子缓存"的错误结论——本手册作者曾为此浪费数小时。

其余工具：

- `imagesText` 表（头 `0x88` = 偏移，`+8` = 条目数，条目 32 字节）可把绝对地址映射到 dylib 路径
- `check_np` 失败（`vm_shared_region_start_address() failed`）是**正常**的：
  它在 `sr_first_mapping == -1`（尚未映射任何东西）时报错，dyld 本来就在映射前调用
- `not in trust cache` 是 `SHARED_REGION_TRACE_ERROR` 的跟踪输出，随后 `goto root_check` 回落，**不致命**

---

## 十、当前卡点的完整证据链

```
SWEET CACHE FIRST @0x1a1314000: [dyld_v1   arm64]      ← 映射忠实
SWEET SIGILL #1 pc=0x1f2e24900 lr=0x1f2e62784 i=4e284840 pp=9e8ef pp2=0
```

扣除 slide 后：

```
pc 0x1f2e24900 → 缓存内偏移 0x51b10900 → 子缓存 .25 文件偏移 0x11d4900
              → 文件该处 = 0x4e284840  ← 与 CPU 执行完全一致
              → 属于 image #851 /usr/lib/system/libcorecrypto.dylib，镜像内 +0x4900
```

`0x4e284840` 用 `llvm-mc -mattr=+crypto` 解码为 **`aese v0.16b, v2.16b`**。

**两条独立来源确认 MSM8916 无加密扩展**：sbc-bench 的 ARMv8 Crypto 文档把 Snapdragon 410 列为
"仅有的已知例外"；一份 MSM8916 实机 `/proc/cpuinfo` 的 `Features` 也没有 `aes`。

**内核却在对用户态撒谎**：

```c
/* osfmk/arm/commpage/commpage.c */
#if __ARM_V8_CRYPTO_EXTENSIONS__
    gARM_FEAT_PMULL = 1; gARM_FEAT_SHA1 = 1;
    gARM_FEAT_AES = 1;   gARM_FEAT_SHA256 = 1;
    bits |= kHasARMv8Crypto;
#endif
```

`sweet.h` 里定义了 `__ARM_V8_CRYPTO_EXTENSIONS__ 1`，于是无论硬件如何都上报"有 AES"。
`libcorecrypto` 据此选择 AES 指令实现 → UNDEFINED → SIGILL。

**已排除的假设**（均有实测证据）：页移错位（`PS=12`/`MP=12`）、
映射内容错误（回读到 `dyld_v1`）、I-cache 陈旧（全量刷新无效）、多 VA 别名（`pp2=0` 只是未缺页，不成立）、
`__ARM_MIXED_PAGE_SIZE__` 路线（要求 16 KB 内核，编译期 `#error`）。

---

## 十一、下一步方向（A 已落补丁，待 CI 与真机验证）

| 方案 | 做法 | 评估 |
|---|---|---|
| **A（推荐先试）** | 不给用户态宣称加密扩展：`__ARM_V8_CRYPTO_EXTENSIONS__` 置 0 或删掉，`gARM_FEAT_AES` 保持 0、commpage 不设 `kHasARMv8Crypto`；同时从 `-march` 去掉 `+crypto` | 一行级改动，可验证 Apple 用户态是否有软件回退路径 |
| B | 按真实硬件读 `ID_AA64ISAR0_EL1` 再决定上报，而不是按 board_config 硬编码 | 比 A 更正确，代码量略大 |
| C | 内核里模拟 `AESE/AESD/AESMC/AESIMC` | 治本但重：需在 UNDEF 处理里读写线程 NEON 寄存器并实现 AES 轮函数，性能差 |
| D | 替换/重建共享缓存里的 libcorecrypto | 不可行（无 dsc 重建工具链，且签名链断裂） |

**A 的前提假设**：Apple 的库在 `gARM_FEAT_AES == 0` 时会退回软件 AES 实现。
若 A 无效，说明 libcorecrypto 在该版本没有回退路径，则只能走 C。

**A 的实施状态（2026-11-27）**：`[实测]` 补丁
`ios-santoni/xnu/patches/0254-wt86047-stop-claiming-crypto.patch` 已提交（`55ace0b`）并推送，
CI run `45987055545` **conclusion=success**（build 17 分钟），build.log 第 317 行确认套用。
产物 `kernel-out39/kernel.development.wt86047`，sha256 `3eaf179b…ee7da413`；
出包 `shim-boot.img`（`--rd-emmc-ro-log`）sha256 `7ac7400a…4283573e`。
`[实测]` 静态核对：`kernel-out38` 里 `_commpage_populate` 有 4 条连续 `str w?, #1` 写
`gARM_FEAT_*`（`0x8b0/0x8b8/0x8bc/0x8c0`），`kernel-out39` 同页命中数 0 —— 能力位不再上报。

**A 的实测结论：失败（2026-11-27 19:33，`runlog/2026-11-27-boot7-decoded.txt`）**：`[实测]`
用 `kernel-out39` 出的 `shim-boot.img` 由 `fastboot boot` 内存加载，运行 62.6 s 后 launchd 仍在
同一条指令上崩：`SWEET SIGILL #1 pc=0x21e28c900 lr=0x21e2ca784 i=4e284840`（`aese v0.16b,v2.16b`），
`pid 1 exited -- exit reason namespace 2 subcode 0x4`。即能力位清零对代码路径**无影响**，
该版本 libcorecrypto 在 arm64 上无条件绑定 `AccelerateCrypto` 实现（与 `apple/corecrypto` 源码一致）。
**不要重复尝试 A**；`0254` 保留在补丁序列里（它让上报与硬件一致，是正确的语义），但它不解决卡点。

`-march=armv8-a+crypto` **有意保留**：`osfmk/arm64/corecrypto/sha256_compress_arm64.s` 直接汇编
`sha256h` / `aese`，实测 `llvm-mc`（21.1.8）在 `-mattr=+v8a` 下报 `instruction requires: sha2`，
而对外宣称与否只由板级宏决定，与 `-march` 无关。

**A 的离线预证（2026-11-27）**：`[实测]` 从 `E:/ios-rootfs/iOSRoot.img` 挂载取出
`dyld_shared_cache_arm64.25` 故障点前后字节：`pc` 落在叶子函数 `0x11d48c0` 内 —— 入口
`cbnz x2` / `ldr w5,[x0,#0xf0]`，对 `[x0+0xf0]` 的三个取值 `0x60`/`0xc0`/`0xe0` 分派、其余 `mov x0,#-1; ret`，
分支体内才是 `aese`+`aesmc` 轮循环（`0x11d4900` 即故障指令）。`[推断]` 因此 AES 指令属于"硬件实现"
变体，是否进入由上层 `ccaes` 分派的能力位决定，而非无条件执行 —— A 命中的正是该分派输入。

**方案 A 的可行性判据（2026-11-27，两方证据相反，仍待真机定论）**：

- 反面（源码）：`apple/corecrypto` main 分支的 `ccaes/src/ccaes_ecb_encrypt_mode.c` /
  `ccaes/src/arm/ccaes_arm_ecb_encrypt_mode.c` 在 `__arm64__` 下把 `.ecb` 直接绑到
  `AccelerateCrypto_ecb_AES_encrypt`，`cc_runtime_config.h` 只对 `SHA512/SHA3/DIT/SB` 做运行时判定，
  **AES 无运行时判定**（只有 Intel 的 `CC_HAS_AESNI()`）。若 2023 年的 dylib 与之一致，A 不会生效。
- 正面（本机二进制）：同一 `libcorecrypto.dylib`（`__TEXT` 基址 `0x1d1b0c000`）内确实存在软件 AES 表 ——
  S-box 在镜像 `+0x73a24`、Te0 在 `+0x73cc8` 与 `+0x78c28`，`[实测]` 说明该版本编译进了软件实现，
  因此仍可能有运行时回退。
- 结论：只能靠一次真机启动判定；A 无效则转 C（内核 UNDEF 里模拟 AES/SHA/PMULL）。

**验证方法**：改完后用 `--rd-emmc-ro-log` 临时启动，取回日志看 SIGILL 是否消失、
以及 `libcorecrypto` 之后的新报错。

**下一步：方案 C 的落点（2026-11-27 拟定，`[未执行]`，未改代码）**

- 钩子：`osfmk/arm64/sleh.c` 同步异常路径里 `ESR_EL1.EC == 0x00`（Unknown reason）的分支，
  用 `copyin` 取 `ELR_EL1` 处 4 字节指令字，按掩码分类；无法识别的编码保持原 SIGILL 行为。
- 需模拟的指令族（libcorecrypto 实际会用到）：`aese/aesd/aesmc/aesimc`（16B 形态）、
  `sha1c/sha1p/sha1m/sha1h/sha1su0/sha1su1`、`sha256h/sha256h2/sha256su0/sha256su1`、
  `pmull/pmull2`（GHASH）。编码表用 `llvm-mc -mattr=+crypto -show-encoding` 生成，不靠记忆。
- 寄存器访问：内核 `CPACR_EL1` 已是 `CPACR_FPEN_ENABLE`（`osfmk/arm64/locore.s`），且内核自身
  `_AccelerateCrypto_SHA256_compress` 就用 V 寄存器 → 可用 inline asm 直接读写 `v0..v31`，
  不必临时改 CPACR。模拟核心用 GPR 保存 16 字节状态，避免在异常上下文再碰 SIMD。
- 语义实现放纯 C（`-mgeneral-regs-only` 友好），先做主机向量测试（NIST AES-128/256 CBC、
  SHA-1/256、GHASH）通过后再进 CI，减少真机往返。
- 探针：每个指令族一个计数器 + 前 N 次打印 `SWEET EMU: op=... pc=...`，一次启动即可判断覆盖是否足够。
- 影响面：纯新增补丁，不动现有 4K / 共享缓存路径；风险在异常上下文的寄存器一致性与性能
  （软件 AES 慢 1–2 个数量级，启动阶段可接受）。

**C 的实施状态（2026-11-27 20:55 起，补丁 `0255`）**

- `[实测]` 探针三档（`sweetemu=0/1/2/3`，boot-arg 名**不能带 `-`**：`PE_parse_boot_argn` 把
  dash 前缀一律当布尔并写 1，忽略 `=value`）。boot 9 即因写成 `-sweetemu=2` 退化成 mode 1。
- `[实测]` boot 10（`runlog/2026-11-27-boot10-decoded.txt`，mode 2 跳过）：整段启动只用到
  `aese` 与 `aesmc` 各 3 次，全部落在镜像 `+0x900/+0x904`；跳过之后 launchd 继续跑完 boot task，
  `fsck[2] rv=0`、`mount[3] rv=0x100`，随后 `mount-phase-1` 退出 1 → 内核 panic。
  **结论：AES 结果是被消费的，必须给真值。**
- `[实测]` mode 3 已实现 AES 四个指令的真语义（S-box 由 GF(2^8) 逆 + 仿射现算，不查表；
  `aese/aesd` = ARK+SB+SR，`aesmc/aesimc` = (Inv)MixColumns；V 寄存器用整存整取 q0..q31）。
  主机向量测试通过 FIPS-197 C.1（`69c4e0d8…c55a`）与 `aesmc/aesimc` 互逆检查。
- `[重要]` 轮次组合：`AESE(x,k)` = `SR(SB(x^k))`，密钥在**下一轮开头**吸收，正确序列是
  `AESE(rk0)` → 9×`AESMC` → 明文 `EOR rk10`；写成「先异或 rk0、每轮 AESE+MC、最后 AESE(rk10)」
  会多一轮 SubBytes/ShiftRows（本轮踩过，向量测试发现）。
- `[未验证]` `sweetemu=3` 的真机启动尚未做（等 CI `46003550737` → `kernel-out42`）。

**boot 11（`sweetemu=3`，`runlog/2026-11-27-boot11-decoded.txt`）**：`[实测]` AES 模拟在真机上执行
（`aese/emul`、`aesmc/emul` 各 3 次，mode=3），launchd 走到 boot task 后**仍**在 `mount-phase-1` 失败，
返回码与 boot 10（垃圾结果）完全一致 → 该失败与加密无关，根因见第一节：
`open(/dev/emmcroot0)` EBUSY + `mount_hfs` EROFS。加密这条路在本阶段已不再是瓶颈；
后续若出现 `sha256h`/`pmull` 会由探针打印出来，再按需扩展模拟范围。

**下一步候选（`[未执行]`，等确认）**：①让内核不预挂根、交给 launchd 的 `mount`；②让
`mount-phase-1` 看到 `/` 已挂载而直接成功；③把该 boot task 标记为非必需。三者都要改引导/镜像侧，
不再涉及 CPU 指令模拟。

**AES 实现选型（2026-11-27，依据资料库《AES实现选型短报告》）**

- 报告推荐 Gladman AES 作为**内核侧**软件后端（API 与 `libkern/crypto/aes.h` 同源）。`[实测]` 该推荐
  不能直接用于指令模拟：`AESE` = ARK+SB+SR、`AESMC` = MC，而 Gladman 的 T-table 把 SR 与 MC 融在
  一次运算里，正好跨过我们两条指令的边界；整块库也没有「单条指令」入口。
- 可迁移的是「查表 + 用已验证数据」：S-box 仍按 GF(2⁸) 现算，但主机测试逐字节对照 Gladman
  `sb_data`（256/256 一致）；正向 MixColumns 改为 xtime 形式（每列 4 次 xtime，取代 4 次 GF 乘法），
  逆向路径保持简单直白（启动路径尚未用到 `aesd/aesimc`）。
- `[未验证]` 若后续内核自身需要 AES 后端（参考移植的 TCP/keybag 路径），再按报告把 Gladman 的
  `aescrypt/aeskey/aestab/aes_modes` 裁进内核，届时另开补丁，不混进 `0255`。

**SHA-1 / SHA-256 指令模拟（2026-11-27 21:40 起，同一补丁 `0255`）**

- 触发：boot 12 的 `fips` 任务 `FIPSPOST … FAILED: digest: sha1`（SIGABRT）。日志里出现的族正好是
  `sha1su0/c/h/su1/p/m` 与 `sha256su0/h/h2/su1`，没有新指令。
- 语义来源：QEMU `target/arm/tcg/crypto_helper.c`（Arm 伪码转写），要点：`sha1c/p/m` 一次四轮、
  状态保持 b-rotated；`sha1su0` 用两个 64 位半字；`sha1h` 只留 `ror32(w0,2)`；`sha256h` 消息走 n、
  `sha256h2` 消息走 d；字在寄存器内是小端，读写按字节拼装而非强制转换。
- 验证：十个指令 × 4 组随机向量 = 40 例，与**按 FIPS-180 定义重写**的 Python oracle 全等；
  AES 侧 FIPS-197 C.1 仍通过。`[未验证]` 真机 FIPS POST 结果（等 CI `46010061025` 出包后 boot 13）。

**关键坑：模拟结果必须写进异常上下文，不能写 live 寄存器（2026-11-27 22:10，boot 13）**

- `[实测]` boot 13 里 `aes/sha` 全部进了 `/emul` 分支，但 FIPS 三个 KAT 同时失败
  （`digest: sha1`、`digest: sha256`、`aes_ecb_encrypt cmp`）——即所有模拟都是**空操作**。
- 根因：`arm_context_t = { arm_saved_state ss; arm_neon_saved_state ns; }`，异常入口把 `q0..q31`
  存进 `context->ns`，返回时再从那里恢复；而 `sleh_synchronous` 只把 `&context->ss` 传进
  `handle_uncategorized`。写 live 的 V 寄存器在返回时被覆盖。
- 修法：`handle_uncategorized` 改收 `arm_context_t *`（内部保留 `state = &context->ss`，其余不动），
  探针通过 `neon_state64(&context->ns)->v.q[]` 读写。**顺带不再需要任何 EL1 FP 访问**，
  原来那套 `stp/ldp q0..q31` 全部删除。
- 主机测试新增断言：mode 3 必须把结果写进保存的 NEON 区（守护这次的回归）。

**boot 15 里程碑（2026-11-27 23:30，`runlog/2026-11-27-boot15-decoded.txt`）**：`[实测]`
FIPS POST 全项 PASSED，`Returned from calling the FIPS_POST function: result = true`；
`Early boot complete. Continuing system boot.`；482 次 `exec`（SpringBoard、backboardd、
CommCenter、searchd、accountsd 等）。日志 1.32 MB / 363 次 flush / 456 s。
新卡点：`backboardd` 反复 `IOServiceMatching failed for: AppleM2ScalerCSCDriver`（12 689 次）。
后续工作从「CPU 指令模拟」转到「IOKit 显示栈补齐」，与加密无关。

**boot 16 与两处纠正（2026-11-28 00:20，`runlog/2026-11-27-boot16-decoded.txt`）**：`[实测]`
`0256` 的去重把日志从 976 KB 压到 143 KB（2173 行），于是看到真相：
- `IOServiceMatchingfailed` 只有 **3 条**（不是 12 689）；`IOAcceleratorES` 是**找到了**的。
- 系统真的引导完成：`Early boot complete`、约 200 个服务 `exec`、`SWEET FB: display on`、
  `SWEET SURF` 建了 720×1280 与 720×320 两种面、`SWEET EMU` 16 个 opcode **全是 `/emul`**（无 `/skip`）。
- 但「只出了 16 帧」是日志闸门 `sweet_fb_swaps_logged < 16` 造成的假象 → `0257` 改为前 8 + 每 300 帧。

`0257`（帧缓冲文本视图）：`sweetfbview=N` 把即将呈现的那一帧降采样到 60×22 网格，每格取**最亮**
像素（细笔画降采样后不丢），映射成明暗字符，每行一条 `SWEET FBVIEW|…|`。同签名帧跳过，
所以 N 计的是「不同的屏」。主机测试覆盖默认关闭、上限、签名去重、行宽、黑白两端、短 stride 拒绝。

**boot 17（2026-11-28 00:39，`runlog/2026-11-27-boot17-decoded.txt`，`sweetfbview=6`）**：`[实测]`
`SWEET FBVIEW #1 720x320 stride 2880` 之后**再无第二次 dump**，22 行全为空格 —— 即整段 320 s 里
**合成帧是纯黑且签名从未变化**。同时 `display on` 正常、`SWEET SURF` 建了 720×1280 与 720×320
两套面、`FB: swap` 到 69 帧、无 `FBSDisplayMode` 报错。所以：
- 显示模式被 iOS 接受，像素路径没有花屏/错色问题；
- **没有任何人往呈现面上画东西**，不是格式或拷贝通道的错。

**与参考手记的对照（关键）**：`ref/NOTES-sweet.md` 明确记着「backboardd 调
`DMPerformMigrationIfNeeded`，并**把 SpringBoard 挂在 `checkInAndWaitForDataMigration` 里**直到
迁移返回」。我们的日志签名完全吻合：`exec SpringBoard` 一次 → `AKS sel 0/7` → 此后再无任何
SpringBoard 活动、也无崩溃。参考平台在迁移期间由 backboardd 画进度界面（1080×2400 双面，
5 分钟 13 000+ swaps）；我们只有 69 swaps 且全黑，说明**进度界面也没画出来**。

**离线读根镜像（`E:/ios-rootfs/iOSRoot.img`，只读挂载）**：`[实测]`
缺 `/private/var/mobile/Library/Preferences/com.apple.migration.plist`、缺 `Preferences/ByHost`、
缺 `com.apple.purplebuddy.plist`、缺 `/private/var/containers`；`/private/var/datamigrator` 存在但为空
（无 `migrate` 哨兵）。即参考手记 v12/v14 里用来「跳过迁移与 Setup」的那批标记，本镜像一个都没有。

**待判定的分叉（`0258`）**：`sweetfbsurf=1` 会打印每个活跃 IOSurface 的非黑像素数。
- 全为 `lit 0` → 确实没人画，下一步要用户栈（采样器在本树无输出，需改 `printf`→`IOLog`）；
- `id 2`（720×1280）有内容而 `id 3`（720×320，即 layer 0 呈现面）为空 → 是呈现选错了面。

**boot 18 判定（2026-11-28 01:22，`sweetfbsurf=1`）—— 黑屏根因确认**：`[实测]`
```
id 1 720x320   lit 0 of 14400      panel buffer，空
id 2 720x1280  lit 3739 of 57600   UI 画布，有内容（6.5%）
id 3 720x320   lit 0 of 14400      layer 0 呈现面，空
id 4 720x1280  lit 3740 of 57600   第二块画布，有内容
id 5 720x320   lit 0 of 14400      空
```
同时 `FB: swap 6600` —— 合成器实际出了 **6600 帧**。所以「只有 69 帧、画面静止」是前 8 帧日志闸门
造成的假象，backboardd 一直在正常合成与动画（6.5% 的覆盖率正好是黑底上的 logo + 进度条）。

**根因：显示几何自相矛盾**（同一份 `build-shim.sh` 里硬编码的两个值互相冲突）：
| 来源 | 值 | 由谁决定 |
|---|---|---|
| ADT `/arm-io/disp0` 的 `canvas-*` | 720×1280 | `mkadt.py --native` |
| IOMFB 上报用户态的尺寸 | 720×320 | `sweetfb_native=1` → `fLogicalH = fHeight` |

iOS 于是按 720×1280 渲染、再往 720×320 的呈现面**缩放**；缩放在 GPU 上，而 `IOAcceleratorES`
是本项目的空桩（`0138`）—— 所以呈现面永远是空的，面板自然全黑。

**修法（已改 `build-shim.sh`，不需要重编内核）**：新增 `SWEET_FB_NATIVE`（默认 1），
一个变量同时决定 cmdline 的 `sweetfb_native=` 与 mkadt 的 `--native`，两者不会再漂移。
取 `SWEET_FB_NATIVE=0` 时是参考移植原本的「舷窗」设计：iOS 认为屏幕是 1125×2436，
面板取**中心** 720×320 窗口（`fOffX/fOffY`），全程 1:1 无缩放，CPU 直接拷贝。
**boot 19 判定（2026-11-28 01:27）—— 面板首次出现 iOS 内容**：`[实测]`
```
id 1 720x320   lit 746 of 14400     ← 面板缓冲，第一次非空（5.2%）
id 2 1125x2436 lit 11018 of 171738  ← UI 画布
id 3 1125x2436 lit 11018 of 171738  ← layer 0 呈现面（现在是画布本身，1:1）
FB: swap 4800 layer 0 surface 2 1125x2436 ... b 0,0 1125x2436
```
`SWEET FBVIEW` 截到了实际画面（居中窗口里的斜向白色图形 = 苹果 logo 的一部分）：
```
|                                   +%@@@     |
|                                 -@@@@@@     |
|                                %@@@@@@@     |
|                               #@@@@@@@+     |
|                              .@@@@@@@@      |
|                 .#@@@@@@@@#:   .*%@@@@@@@@%=|
```
即：**几何自洽后链路全通** —— iOS 在 1125×2436 画布上绘制，backboardd 以 1:1 呈现，
内核按 `fOffX/fOffY` 取中心 720×320 窗口，CPU 直接拷贝到面板，全程不经过 GPU。

**当前可见范围**：1125×2436 的中央 720×320（物理落在面板 960~1279 行，即下方四分之一）。
要看到完整画面有两条路：
1. 让面板真正扫描 720×1280 并把 canvas 设成 720×1280（1:1 全屏，需显示驱动侧工作）；
2. 在拷贝路径上做一次 CPU 缩放，把 1125×2436 压进 720×320（能看全貌，但底部一条、
   非等比），或在 canvas 层面直接选一个与面板同比例的小尺寸。

**boot 20（2026-11-28 01:47，`sweetfb_fit=1`）**：`[实测]` 摄像头确认整幅画布被垂直压缩后
显示在面板**顶部** 320 行（其余区域仍是 LK 的 fastboot 残留画面）。两点结论：
- `0259` 的缩放生效，整屏内容确实都在正常渲染；
- **320 行窗口落在物理屏幕顶部**，不是此前假设的底部 —— 换成整屏 720×1280 后若出现上下翻转，
  需要在 `blitPanel()` 里补一次行序反转。

**迁移进度实测（2026-11-28 01:57–03:06，同一 boot）**：`[实测]` 用摄像头 + `tmp/bar.sh`
量化进度条长度（左端固定、右端增长）：
```
01:57 260  02:09 304  02:21 353  02:33 373  02:46 384  02:58 384
02:01 261  02:13 321  02:25 362  02:38 379  02:50 385  03:02 386
```
前 45 分钟稳定推进（+126 px），随后在 **384–386 px 抖动近 20 分钟不动** → 判定**迁移卡在末尾**。
参考手记里同阶段的失败模式是 `installcoordinationd` 报
`Failed to create data directory EACCES for /private/var/containers/...`；本镜像确实**没有**
`/private/var/containers`。下一步要给根镜像补 `containers/{Bundle,Data/System,Shared/SystemGroup,Temp}`
以及迁移/Setup 标记，而不是继续等。

**判定纪律（2026-11-28 03:20，两次误判后补充，务必遵守）**：
1. **解码出的某次启动，先看它的第一个 `[WT86047 t=...]` 时间戳。** boot 21 的日志从
   **t=74.67 s** 开始（前 74 秒不在保留段里），据此断言「没有 `SWEET FB` / `AppleCLCD`」
   是错的——`IOCoreSurfaceRoot registered` 在 boot 20 里正好是 t=76.6 s。凡首时间戳 > 10 s，
   该启动的早期事实一律视为**未知**，不能当「不存在」。
2. **摄像头只能做粗判**（屏幕是否有内容、大致位置、是否全屏覆盖）。分辨率不足以读文字，
   把模糊像素当字读会直接产生幻觉。任何文字结论必须来自内核日志。
3. 结论要写成「实测」时必须能指到日志行；否则写「推断」。

**日志读取的两个真实故障（2026-11-28 03:50，已修）**：`[实测]`
1. `sweet-log-pull.sh` 用单次 `adb dd` 读 16 MiB 环，会**短读**（曾只返回 9,527,xxx / 16,777,728 字节）
   却继续往下解码 —— 最新的那次启动被截断，于是「`AppleCLCD` 不存在」这种结论完全是假的。
   现改为 2 MiB 分块 + 短块重试 + 失败即停。`tmp/auto-log.sh` 也会在短读时直接中止。
2. `sweet-log.py --boot N` 的**分段不严格按时间**（boot 20 的解码末尾竟是早期 SDHCI 枚举），
   按段做「有无某行」的判断不可靠。改用 `tmp/ring-peek.py`：直接在原始环里搜关键串，
   并用最近的 `WT86047 LOG ... boot N` 标记归属，同时打印最新启动的尾部。

**显示几何的三个旋钮（改 `build-shim.sh` 环境变量即可，不需要重编内核）**：
| 变量 | 作用 |
|---|---|
| `SWEET_FB_NATIVE` | 0/1，同时决定 cmdline 的 `sweetfb_native=` 与 mkadt 的 `--native`，两者必须一致 |
| `SWEET_FB_PANEL` | `WxH` 或 `WxH@ROW`，决定 `sweet-fbcon` 的窗口大小与起始行 |
| `SWEET_FB_HZ` | `blitPanel()` 的扫描频率，整屏时转换开销是 320 行的 4 倍 |

`720x320@960` 精确复现旧默认（`0x831fa400`），用作偏移算式的自检。

**整屏尝试的现状**：把 `sweet-fbcon` 设为 `0x83000000` / 720×1280 后，系统仍能 `Early boot complete`、
`exec backboardd`，但**整次启动没有任何显示日志**（`AppleCLCD`、`IOCoreSurfaceRoot`、`FBSURF` 全无），
面板缓冲也不会被覆盖（摄像头可见 LK 的旧画面完好）。ADT 本身经核对含 `sweet,display`、
`display-subsystem`、`canvas-*`，`sweet-fbcon` 的 reg/height 也确为整屏值。
因此不是 ADT 问题，需要内核埋点定位 —— `0260`（`AppleCLCD::start` 入口 + PE video 实测值）就是为此加的。

**整屏几何已解决（2026-11-28 04:57，`0260` + `0261`）**：`[实测]`
- `0260` 埋点定性：`AppleCLCD::start enter provider=disp0` —— **驱动匹配正常**；
  但在 ADT 已含整屏 `sweet-fbcon` 的镜像上，`boot video phys` 仍报 `0x831fa400 720x320`。
  **结论：PE_Video 由引导器（LK）填入，ADT 的 `sweet-fbcon` 只能喂控制台**，所以
  此前所有靠改 ADT 的「整屏」尝试都是死路（这也解释了它们为何静默退回 320 行）。
- `0261` 在驱动里覆盖：`panel override rows 320 row 960: base 0x831fa400 -> 0x83000000,
  height 320 -> 1280`，随后 `AppleCLCD 720x1280 panel stride 2160 24-bpp at 0x83000000,
  frame stride 2880, presented as 720x1280`。几何**完全正确**。

**新的卡点：backboardd 不再使用显示（boots 21–25）**：`[实测]`
`present()` 未被调用（`SWEET FB: swap` = 0），`IOMobileFramebuffer` 查找 = 0，而 `exec backboardd`
有、`IOCoreSurfaceRoot registered` 有、`Early boot complete` 有。boot 24 跑到 t=265 s，不是时间不够。
`blitPanel()` 只在 `present()` 里调用，所以面板缓冲永远不会被刷 —— 屏幕上仍是 LK 的旧画面。

**已验证可用的最后配置（boot 20）**：`native=0` + `sweetfb_fit=1`，出帧 **196,200** 次、
`SWEET FBSURF id 1 720x320 ... lit 929 of 14400`（内容确实到过面板）。恢复命令：
`SWEET_FB_NATIVE=0 SWEET_CMDLINE_EXTRA='... sweetfb_fit=1'`（zài 默认 320 行 ADT 上）。

**下一步要查的**：为什么 `native=1` / 整屏 override 之后 backboardd 跳过显示初始化。
建议在 `IOMobileFramebufferUserClient::initWithTask` 与 `AppleCLCD::addClient` 加埋点，
并对比 boot 20 与 boot 24 的 backboardd IOK `sweetiolog` 序列。

**【用户纠正 · 前提事实，优先级高于本节任何推断】**
1. 本项目**从未实现全屏**：shim 从最开始到现在**都没有突破上半屏**。所以「320 行窗口在屏幕
   底部/顶部」「整屏几何已解决」这类表述都只是**内核模型**层面的变化，**不代表面板真的全屏显示**。
   `0261` 让驱动以为自己驱动 720×1280，但硬件扫描仍是 LK 设定的那一部分，屏幕上「毫无变化」
   与此完全一致——不是回归，也不是几何算错。
2. 因此后续任何与「全屏」有关的结论，必须**同时**有面板实测（可见区域扩大到多少）支撑，
   不能只凭 `AppleCLCD ... 720x1280` 这行日志。日志只证明内核侧的模型，不证明硬件扫描。
3. 措辞统一为**内存临时启动**（`fastboot boot`，不写任何分区），不要用「刷入/刷机」。
   判断是否真的完成传输，必须看到 `Sending 'boot.img' (…KB) OKAY` 这一行；只有 `Booting` 说明
   镜像未传输，设备会继续正常启动（现象：出现 MI 标志而不是 iOS 画面）。

**【用户补充的现场判定标准，优先采用】**
- 是否在 fastboot：**以 `fastboot` 命令为准**，不看屏幕。
- FB 模式下 **MI 标志与米兔都可能出现**，两者都属正常，不能据此推断状态。
- 镜像是否真的启动：看**屏幕上半部分是否出现白条状小字**（内核控制台输出）。

**boot 20 之后显示彻底消失的真正原因（2026-11-28 05:00，按启动统计，`[实测]`）**：
| boot | containermanager 崩溃 | `IOMobileFramebuffer` 查找 | `SWEET FB: swap` |
|---|---|---|---|
| 18 | 0 | 1 | 有 |
| 19 | 0 | 1 | 有 |
| 20 | 0 | 1 | 有（196,200 次） |
| **21** | **126** | **0** | **0** |
| 22 | 78 | 0 | 0 |
| 23–25 | 57–72 | 0 | 0 |

分界线精确落在 **boot 20 之后**，而 boot 20 正是那次持续 79 分钟、大量写入可写根的数据迁移。
推断（`[推断]`，证据强）：**迁移把设备上的 userdata 写坏 → containermanagerd 崩溃循环 →
backboardd 拿不到容器 → 不再初始化显示**。这与「进不到 iOS 界面」完全一致，
且与显示驱动、几何参数**无关**。

注意：`E:/ios-rootfs/iOSRoot.img` 是 **PC 上的源镜像**，设备写入的是**手机 userdata 分区**，
所以本地检查只反映基线（基线本身就没有 `/private/var/containers`，而 boots 18–20 崩溃为 0）。
要看设备侧的真实状态，必须在 TWRP 里挂载 userdata 检查。

**下一步（优先于任何显示工作）**：
1. TWRP 内只读检查设备 userdata 的 `/private/var`（containers 是否被写成半成品、权限是否错乱）；
2. 用已知良好的 `iOSRoot.img` 重新制 `.simg` 并回写 userdata，把损坏复位；
3. 回写后**先抑制迁移**（参考手记 v12 做法：`LastSystemVersion` + `containers` 目录齐备），
   再启动，避免再次被同一个迁移写坏。

**第 1 步结果：设备侧卷确实已损坏（2026-11-28 05:15，`[实测]`）**
导出 userdata 整块 14,253 MiB（分块 dd + 尺寸校验，14 分钟）后，在 PC 上 `fsck.hfsplus -n`：
```
Overlapped extent allocation (id = 339883, /private/var/mobile/Library/Spotlight/...)
Overlapped extent allocation (id = 339917, /private/var/db/systemstats/current_build)
… 共二十余处，全部位于 /private/var 下
** Volume bitmap needs minor repair for under-allocation
** Volume header needs minor repair
** The volume iOSRoot was found corrupt and needs to be repaired.
```
卷签名 `HX` = **HFSX（区分大小写、无日志）**。基线镜像 `E:/ios-rootfs/iOSRoot.img` 同时做同样的
`fsck -n` 为 `appears to be OK` —— 说明损坏是设备侧写入造成的，源镜像干净。

**损坏机制（本次真正的教训）**：卷**无日志**，而数据迁移在 `/private/var` 下持续写入近 79 分钟，
其间多次强制断电 → 区段重叠分配 + 位图下分配 → 目录结构与位图不一致。
containermanagerd 读到这些损坏项后崩溃循环，backboardd 因此拿不到容器、不初始化显示。
**所以「看不到 iOS 界面」的直接原因是卷损坏，不是显示驱动。**

**已执行的修复（第 2/3 步）**：在基线副本上补 `containers/{Bundle,Data/System,Shared/SystemGroup,Temp}`
（root:wheel 0755）、`Preferences/ByHost`、`com.apple.migration.plist`（`LastSystemVersion=20H392`，
501:501 0600），复查 `fsck -n` 通过，转 sparse：
`D:/ios-rootfs-work/fixed-1128-0454.simg`，11,583 MiB，sha256 `176390b1b9c68d10166850f820b03027…`。
用 `fastboot flash userdata` 回写（基线镜像保留，可随时重建）。

**回写后验证：崩溃链已切断（2026-11-28 08:05，boot 28，`[实测]`）**
| boot | containermanager 崩溃 | IOMFB 查找 | swap | 迁移标记 |
|---|---|---|---|---|
| 24–27（修复前） | 57 / 72 / 54 / **123** | 0 | 0 | 5–6 |
| **28（修复后）** | **0** | **1** | 有 | 4 |

并且面板缓冲恢复收到内容：`SWEET FBSURF id 1 720x320 ... lit 72 of 14400`，
`SWEET FBVIEW` 出现非空行（正中一块白色图形）：
```
|                          @@@@@@@@                          |
|                          +@@@@@@@                          |
```
即「卷损坏 → containermanagerd 崩溃循环 → backboardd 不初始化显示」这条链**已被切断**。
用户实机确认屏幕上出现了苹果标志（比例被 `fit=1` 压扁，属预期）。

**更正：迁移自 boot 21 起就没有再运行**（2026-11-28 08:10，按启动统计，`[实测]`）
| boot | `exec datamigrator`（查询服务） | `exec migrationpluginwrapper`（真正执行迁移） |
|---|---|---|
| 24–27 | 1–2 | **0** |
| 28 | 4 | **0** |

`datamigrator` 只是「是否需要迁移」的判定服务，被 launchd 反复拉起属于正常；
真正写盘的是 `migrationpluginwrapper`，而它自 boot 21 起**一次都没运行**。
所以此前「迁移仍未被抑制」是**把查询当成了执行**，结论作废。
因果链据此修正为：**损坏由 boot 20 那次 79 分钟迁移造成**；其后各次启动并未再迁移，
只是坏卷拖垮 containermanagerd，直到 boot 28 回写后恢复。
既然迁移不再运行，**再次损坏的概率很低**，`fixed-1128-0454.simg` 应视为恢复备份而非耗材。

**观察画面时应避开 `fit=1`**：它把 2436 行压进 320 行，垂直压缩约 7.6 倍，进度条几乎无法判读。
用默认（`fit=0`）的「舷窗」裁切——取画布中央 720×320、1:1 无缩放——logo 与进度条正好落在其中，
形态真实，适合目视判断。`fit=1` 只适合「确认整幅画面存在」，不适合读进度。

**设备操作纠正（2026-11-27，实测）**：`fastboot reboot recovery` 返回 OKAY 但不进 recovery；
`fastboot oem reboot-recovery` 报 `unknown command`；`fastboot boot <img>` **可用但要求纯 ASCII 路径**
（中文文件名会导致失败），把 TWRP 复制成 `tmp/twrp.img` 即可；`fastboot boot` 传输中断后
fastboot 协议层会卡死（连 `getvar` 都 5 s 超时），**拔插 USB** 即恢复。

---

## 十二、工程坑清单（全部，按踩过的次数排序）

1. **WSL 的 `/tmp` 是 tmpfs**，VM 空闲回收即清空 —— 产物写 `/opt/work`。
2. **`wsl ... bash -c '...'` 内联引号会被提前展开**（`$i`、`$K`、`${w:6:2}` 变空），
   带变量的逻辑必须落成 `.sh` 文件再执行。**本手册作者在此犯错 4 次。**
3. **apfs-fuse 挂载随 WSL VM 回收消失**，一次调用内要"挂载 → 干活"连做。
4. **`dryrun-patch.sh` 必须传绝对路径**（内部先 `cd` 再拼相对路径）。
5. **生成补丁要按文件筛前置补丁**：`grep -l "a/<file> b/<file>"` 只能匹配 git 格式，
   自家 `diff -u` 生成的补丁没有 `diff --git` 行，需另行列出。
6. **补丁文件是 CRLF，源码是 LF**，`git apply` 前先 `sed 's/\r$//'`。
7. **子缓存表步长 56**（见第九节）。
8. **换算共享缓存地址必须扣 slide**（见第九节）。
9. **`llvm-mc` 判指令要带 `-mattr=+crypto`**，否则 AES 指令被误判非法。
10. **`pp2=0` 不代表未映射**：`pmap_find_phys` 对未缺页的页返回 0。
11. **固定带只有 2 行**，多探针互相覆盖；`sweet_early_mark` 也会盖字。
12. **panic 路径只能用 `sweet_early_putc`**，禁止 printf；禁止走帧指针链。
13. **CI 失败也会发布产物**（`if: always()`），判成功看 `conclusion`。
14. **artifacts 分支拉取不稳**，用 HTTPS + 代理，失败就重试一次（ssh push 也偶发认证失败，重试即好）。
15. **不要 `fastboot erase system`**：持久日志就存在那里。
16. **`--rd-emmc-ro` 档位没有 `sweetlog=1`**，那一档不会产生日志。

---

## 十三、快速排障索引

| 现象 | 先查 |
|---|---|
| 屏幕无输出 | 是否用了 `--rd-emmc-*` 档位；`sweetfb_native=1` 是否在 cmdline |
| `no shared region` | `0246` 的 `SRE=`；多半是 map/子映射页移不一致 |
| `Library not loaded: /usr/lib/libSystem.B.dylib` | 共享缓存没映射成功（看日志里 43 条 `map()`） |
| `SWEET SIGILL` | `0244` 行；扣 slide 后用 `slide-fix.py` + `which-image.py` 定位 |
| 取不到日志 | 先进 recovery；`adb devices` 应显示 `recovery` |
| CI 失败 | 拉 `artifacts` 分支的 `build.log.gz`，`grep "error:"` |
| 补丁套不上 | 用 `git apply --include=<file>` 按序重建基线，别用 `patch` |
