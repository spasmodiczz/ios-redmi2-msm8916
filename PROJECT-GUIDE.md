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
| 执行到 `libcorecrypto` 的 AES 指令 | ✅ 已在 `0255` 里模拟（`sweetemu=3`），真机已执行 |
| **launchd 引导任务 `mount-phase-1`** | ❌ **当前卡点**：`mount_hfs` 重复挂载 `/` → EBUSY/EROFS |

**当前根因（2026-11-27 21:20，boot 11 实测）**：内核在 `rd=emmcroot0` 阶段已把 HFS+ 根挂好且只读，
launchd 的 `mount` 引导任务又去 `open(/dev/emmcroot0)` 并 `mount_hfs`，得到
`GetMasterBlock: Error 16` → `mount_hfs: Read-only file system` → `mount: / failed with 1`，
`mount-phase-1` 被判失败后 panic。与加密指令无关（boot 10 用垃圾结果、boot 11 用真模拟，失败点与
返回码完全相同）。AES 的历史（SIGILL 证据链、A 方案失败、C 方案实施）见第十一节。

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
