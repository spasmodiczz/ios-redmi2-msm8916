# 阶段 3 报告：XNU 以 eMMC 上的 HFS+ 卷为 root（iPhone X 16.7.16 根树）

**日期**：2026-11-21
**设备**：Redmi 2 / wt86047（MSM8916，4×Cortex-A53，**4 KB 页**，无 PSCI）
**内核**：XNU 8796.101.5，246 个补丁，`WT86047` / `DEVELOPMENT`
**上机形态**：`fastboot flash boot` + 正常开机（**策略变更**，见第六节）
**本轮目标**：把 iOS 16.7.16 的根文件系统落到 `userdata`，让 XNU 以 eMMC 为 root 启动

---

## 一、结论

1. **eMMC 作为 root 打通。** HFS+ 卷挂载、`/sbin/launchd` exec、dyld 加载共享缓存全部实际发生，
   mockfs 的 `still alive (tick)` 不再出现。这是阶段 2 之后第一次让 root 离开内存盘。
2. **`userdata` 刷入成功**（14.1 GiB，47 个稀疏块）。
3. **当前卡点**：`vm_shared_region_enter()` 在 exec 阶段失败，任务没有共享区域，
   dyld 的 `shared_region_map_and_slide` 报 `no shared region`，launchd 因缺 `libSystem` 退出。
4. **两个曾经的判断被证伪**：iOS 16 用户态不是 arm64e（实测 arm64/ALL，且无 LSE/PAC 指令）；
   root DMG 没有 AES 加密（`bvxn` 是 LZFSE 的 LZVN 块魔数）。

---

## 二、里程碑时间线（2026-11-21 晚）

| 时间 | 里程碑 | 决定性证据 |
|---|---|---|
| 18:45 | IPSW 结构全部解出 | 91 条目 / 5.75 GiB；OS dmg 4.41 GiB；`blkx` 3634 块扇区总数 11,005,952 完全吻合 |
| 18:46 | 解出 5.25 GiB APFS 整盘 | 27 秒、199 MB/s；`NXSB`，block 4096，卷名 `SydneyUpdate20H392.D22D221OS` |
| 19:20 | WSL 内核带 hfsplus | `bzImage-hfsplus-6.6.87.2`，`/proc/filesystems` 出现 `hfsplus` |
| 19:43 | 建出 14.1 GiB HFS+ 根卷 | 179,359 文件；`fsck` **appears to be OK**；journal=off |
| 20:10 | `userdata` 刷入 | 47/47 稀疏块 Sending+Writing OKAY |
| 20:26 | **首次以 eMMC 为 root 启动** | dyld 实际映射 `dyld_shared_cache_arm64_37..42` |
| 20:35 | 定位 SIGILL 根因 | 文件内容合法代码 / CPU 取到非法编码 → 映射错位 |
| 21:55 | `0245` 修掉页移错位 | SIGILL 消失，`libignition` 走完全流程 |
| 22:10 | 卡点前移到共享区域 | `shared_region: ... map(): no shared region` |

---

## 三、镜像与设备实测数据（勿重测）

| 项 | 值 |
|---|---|
| IPSW | 6,171,035,648 B（5.75 GiB），`iPhone10,3,iPhone10,6_16.7.16_20H392_Restore.ipsw` |
| BuildManifest 角色 | `087-86698-044` = **OS**；`087-86593` = Cryptex1,SystemOS；`087-86715` = Cryptex1,AppOS |
| ApChipID | `0x8015` = A11 |
| OS dmg | 4,730,814,044 B，zip 内 **stored**（未 deflate） |
| 解出的整盘 | 5,635,047,424 B（5.25 GiB），GPT + 单 `Apple_APFS` 分区 |
| 三个卷 apparent | os 7,569 MiB / 179,359 文件；sysos 3,204 MiB / 1,457；appos 7 MiB / 884 |
| 共享缓存 | 42 个子缓存 + 主文件（主文件仅 384 KB，是总头+子缓存表） |
| 根卷内缓存文件 | 44 个、3.2 GB，`.01`…`.42.dyldlinkedit` 齐全，硬链接 |
| `userdata` | `0x37ADE7E00` = 14,946,303,488 B = **14,254 MiB**（100 MB system 包扩容后） |
| `system` | `0x6400000` = 100 MiB 整 |
| 产出镜像 | `E:/ios-rootfs/iOSRoot.img`，14,784,921,600 B（14,100 MiB），journal=**off** |
| sha256 | `f4b2a072e4ed72a964f6c69a5799fad29059da72b535d9cad788b51deb5221b9` |
| boot-manifest-hash | `7C1B7251BD1AD497634CC4E43A23770581AFBF9A43D3365BBF1057022D79DA2081E94564B03B021DBF9185B923808A6F` |
| `max-download-size` | `0x10000000`（256 MiB）→ 必须 `-S 256M` |

---

## 四、本轮四个关键改动

### 4.1 `0244` —— 打印用户态非法指令的 pc/lr/sp/instr

`handle_uncategorized()` 本来就用 `COPYIN` 取了指令字，直接复用；限 8 条。
选此处而非 panic 路径的原因：已是正常进程上下文，`printf` 安全。

### 4.2 `0245` —— 共享区域子映射回到 4 KB 页移（**本轮关键修复**）

`vm_shared_region_create()` 对 64 位进程无条件 `vm_map_set_page_shift(sub_map, SIXTEENK_PAGE_SHIFT)`，
只有 `__ARM_MIXED_PAGE_SIZE__`（macOS）才改回 4K。本移植是 4 KB 页内核且未定义该宏，
于是子映射停在 16 KB，`vm_map` 把 dyld 传入的 4 KB 对齐文件偏移向下取整 —— **映射内容整体移位**。

证据链：`pc=0x2048d4900` 换算到子缓存 `.37` 的文件偏移 `0x3bb0900`，那里是**合法代码** `0xf81203a8`，
而 CPU 实际执行 `0x4e284840`（`llvm-mc` 判为非法编码）。修复后 SIGILL 完全消失。

### 4.3 `0246` —— 共享区域建立失败写进固定带

`vm_map_exec()` 里 `(void) vm_shared_region_enter(...)`，返回值被丢弃，错误行在开机早期滚走。
在 `done:` 出口把 `kr` 与 `VM_MAP_PAGE_SHIFT(map)` 写进 `sweet_early_fixed()` 的底部 32 px 带（永不滚动）。
未用 `snprintf`（本文件无 printf 族头），自带 16 进制格式化，避免编译期赌注。

### 4.4 构建链路

| 项 | 做法 |
|---|---|
| `apfs-fuse` | 源码编译；需 `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`；GCC 15 需 `-include cstdint -include cstring` |
| WSL 内核 | `linux-msft-wsl-6.6.87.2` + `/proc/config.gz` 基线 + `CONFIG_HFSPLUS_FS=y`；`arch/x86/Makefile` 加 `REALMODE_CFLAGS += -std=gnu11`（GCC 15 默认 gnu23，realmode/boot 全崩） |
| `.wslconfig` | `kernel=E:\\WSL\\bzImage-hfsplus-6.6.87.2`（原文件不存在，新建） |
| `mkiosroot.sh` | `MAX_MIB` → `${MAX_MIB:-14100}`；CRLF 需转 LF 副本；`install -o 272` → `chown 272:272` |
| 卷参数 | 必须 `NOJOURNAL=1`：带日志时 Linux 侧 `fsck` 恒报 `invalid primary VHB at 2 result 35` 且不收敛 |

---

## 五、当前卡点（下一步的起点）

```
libignition: 1: dyld_cache: opened shared cache directory: /System/Library/Caches/com.apple.dyld
libignition: 1: goodbye: ignition sequence complete
shared_region: 0xe1b4... [1((launchd)] map(0x...): no shared region
dyld cache '(null)' not loaded: syscall to map cache into shared region failed
Library not loaded: /usr/lib/libSystem.B.dylib
pid 1 exited -- exit reason namespace 6 subcode 0x1
```

**已排除**：
- 缓存文件缺失（44 个、3.2 GB、硬链接计数 2，已挂根卷核对）
- 指令集不兼容（三个二进制 arm64/ALL，LSE/PAC 各 0 条）
- 缓存加密（无 AES 块类型）
- 页移错位（`0245` 后 SIGILL 消失）

**待确认**：`vm_shared_region_enter()` 返回什么错误码。`0246` 上机后看底部固定带：
`SRE=0x........` 即失败码，`SRP=0x........` 应为 12。带子为空则表示 enter 成功、问题在其后。

### 5.1 `0246` 实测结果（决定性）

底部固定带读出：

```
SRE=0x00000004     kern_return_t 4 = KERN_INVALID_ARGUMENT
SRP=0x0000000e     VM_MAP_PAGE_SHIFT(map) = 14 = 16 KiB
```

**⟹ 这个移植的真实配置是「4 KB 内核页 + 16 KB 用户地址空间」。**

- `pmap_create_options(..., PMAP_CREATE_64BIT)` 给出的用户 map 页移是 14，
  与内核 `PAGE_SHIFT=12` 不一致；而 `__ARM_MIXED_PAGE_SIZE__` / `ARM_PARAMETERIZED_PMAP`
  都没有开启，XNU 在这种混合配置下没有正确支持。
- 用 16 KiB 子映射（`0245` 之前）：页移与任务 map 一致 → 映射成功，但 `vm_map` 按 16 KiB
  算偏移、pmap 只能按 4 KiB 落 → **内容错位** → 执行到非法指令。
- 用 4 KiB 子映射（`0245`）：与任务 map 页移不一致 → `vm_map_enter_mem_object`
  直接返回 `KERN_INVALID_ARGUMENT` → 共享区域建不起来 → `no shared region`。

**⟹ `0245` 并非真正修复**，它只是让共享缓存在 16 KiB 子映射下的错误代码不再被执行；
正确的方向是让**整个用户地址空间与内核粒度一致**。

### 5.2 `0245` 已撤销，改为先测准 `PAGE_SHIFT`

缘起：`__ARM_16K_PG__ 1` 定义在 `pexpert/arm64/sweet.h`（SWEET 通用平台头，santoni 与本机共用），
于是 `0084` 里 `#if __ARM_16K_PG__ ... #else PAGE_SHIFT_CONST = ARM_PGSHIFT` 的 4 KB 分支**根本不生效**。
而 `PAGE_SHIFT` 在 XNU 里是**运行时变量**（`PAGE_SHIFT_CONST`，在 `arm_init.c` 依
`__ARM_16K_PG__` 与 memSize 选择），所以源码层面无法判断本机到底是 4 KB 还是 16 KB：

- `0180` 曾 `_Static_assert(PAGE_SHIFT == 12)`，能编过 ⇒ 当时是 4 KB
- `0182` 改成运行时判断，注释直言"PAGE_SHIFT/PAGE_SIZE can be runtime variables"
- `0246` 实测 `VM_MAP_PAGE_SHIFT(map) = 14` ⇒ 任务 map 是 16 KB

三条证据互相冲突，**在测准之前上 A 方案等于赌**。

处置（commit `06feeb2`）：

| 动作 | 内容 |
|---|---|
| 删除 | `0245-wt86047-shared-region-4k-submap.patch`（恢复 16 KiB 子映射，与 `SRP=14` 一致） |
| 新增 | `0247-wt86047-page-shift-probe.patch`：在 `vm_shared_region_enter()` 入口把 `PAGE_SHIFT` 打印到固定带第 0 行（`PS=0x..`），失败码仍在第 1 行 |

**判读**：`PS=0x0000000e`(14) ⇒ 系统统一 16 KB，SIGILL 另有他因，A 方案的前提要重写；
`PS=0x0000000c`(12) ⇒ 内核 4 KB、任务 map 16 KB 的矛盾成立，A 方案成立。

**实测结果（2026-11-21 23:06）**：`PS=0x0000000c`，且 `SWEET SIGILL #1 pc=0x1d54dc900
instr=4e284840` 原样复现（与撤 0245 前完全一致，可复现）。**⟹ A 方案前提成立。**

### 5.3 方案 A 已实施（commit `579b600`）

成因链条：`0085` 给 wt86047 建了独立平台头 `_PEXPERT_ARM64_WT86047_H`，**不含 `__ARM_16K_PG__`**，
所以内核被钉在 4 KB（`PS=12` 实测印证）；但 `PMAP_CREATE_64BIT` 单独就给出 **16 KB 的用户 pmap 几何**，
于是共享区域子映射 16 KB、其下 pmap 只有 4 KB —— dyld 按 4 KB 对齐的映射落到错误的文件偏移，
第一次经缓存的分支就执行到数据上。

| 补丁 | 内容 |
|---|---|
| `0248-wt86047-parameterized-pmap.patch` | `MACHINE_FLAGS_ARM64_WT86047` 追加 `-DARM_PARAMETERIZED_PMAP=1 -D__ARM_MIXED_PAGE_SIZE__=1` |
| `0249-wt86047-4k-task-pmap.patch` | `task_create` 里给 `pmap_flags` 加 `PMAP_CREATE_FORCE_4K_PAGES` |

`__ARM_KERNEL_PROTECT__` 全树未定义，故 `proc_reg.h` 里"与 MIXED 互斥"的 `#error` 不会触发。
`PMAP_CREATE_FORCE_4K_PAGES` 的实现（`p->pmap_pt_attr = &pmap_pt_attr_4k`）只在
`#if __ARM_MIXED_PAGE_SIZE__` 下存在，所以必须先开宏、再传标志，两者缺一不可。

**预期**：`SRP` 变 12、`SRE` 不再出现、共享缓存映射正确。**风险**：该板首次构建 parameterized pmap
配置，编译期可能出现连锁错误，需逐条修。

### 5.4 结果：A 方案被 XNU 的设计阻断（commit `305ba2e`，已撤回）

CI `44918681214` **失败**，`build.log` 里两条是设计性阻断，不是笔误：

```
pmap.c:11105: error: "pmap_insert_commpage_internal requires a 16KB default kernel
                      page size when __ARM_MIXED_PAGE_SIZE__ is enabled"
pmap.c:11203: error: "pmap_unmap_commpage requires a 16KB default kernel
                      page size when __ARM_MIXED_PAGE_SIZE__ is enabled"
pmap.c:4501:  error: call to undeclared function 'generate_rtlbi_param'
pmap.c:4503:  error: call to undeclared function 'flush_core_tlb_allrange_async'
```

**`__ARM_MIXED_PAGE_SIZE__` 的设计前提是"内核 16 KB + 用户态 4 KB"（macOS 场景）。**
XNU 没有"内核 4 KB + 用户态 4 KB"这个配置，而本机又不可能用 16 KB 页
（Cortex-A53 只有 4 KB / 64 KB 粒度）。因此"16 KB 用户地址空间跑在 4 KB 内核上"不是翻个开关的事，
必须在 pmap 里真正支持 16 KB 虚拟页落在 4 KB 粒度上——那是另一个量级的工作。

处置：删除 `0248`/`0249`，保留 `0244`/`0246`/`0247`（诊断），`0245` 维持已撤销状态。
代码回到"能构建、能启动到 launchd SIGILL"的已知状态。

**同时得到的第二条证据**：用可复现的 `pc=0x1d54dc900` 换算到子缓存 `.25` 的文件偏移 `0x4ba0900`，
文件该处是 `0xf9409e7a`（合法 `ldr`），`lr` 处是 `0xaa0103e3`（合法 `mov`），
而 CPU 实际执行 `0x4e284840`，该指令字在 ±1 MB 内**搜不到**。
说明不是"简单偏移"，而是**该 VA 映射到的物理页内容不对**——与 pmap/vm_map 粒度不一致的结论一致。

### 5.5 与原 santoni 仓库的逐补丁对比（结论：无法借用）

克隆 `https://github.com/console-ramoops/ios-santoni`（236 个补丁）与本仓库（246 个）做归一化比对
（把 `santoni`/`wt86047` 统一替换成 `PLAT` 后再比内容，因为 fork 做过全局改名）：

| 检查项 | 结果 |
|---|---|
| 仅原仓库有的补丁 | **0 个**（无遗漏） |
| 本地新增 | `0237`–`0244`、`0246`、`0247`（全部为本轮或近期 wt86047 专用） |
| 页大小核心补丁 | `0084-4k-user-va`、`0097-zone-va-size`、`0144-4k-unsigned-dyld-cache` 归一化后**完全一致（1.00）** |
| `0082-4k-entry`（相似度 0.70） | 差异**仅在 framebuffer 地址**：`0x90000000→0x83000000`、`0x90001000→0x831FA400`，属设备差异 |
| 原仓库文档 | 全文只有一处提到页大小（图形探针"reported 4 KiB page size"），**从未讨论共享缓存映射** |
| 原仓库用户态 | 是自研图形/渲染探针（`santonigpu_renderer`、graphics/raster/GLES），**从未尝试启动 iOS 根树或 launchd** |

**⟹ 原项目没有走过"iOS 根树 + 真 launchd"这条路，因此不存在可借用的共享缓存解决方案。**
本仓库是在这一步上首次推进，16 KB 用户地址空间与 4 KB 内核的矛盾是**新暴露的问题**，不是 fork 丢失的补丁。

**顺带发现的仓库卫生问题**：本地有 8 个补丁编号重复（`0082/0084/0085/0093/0094/0095/0097/0098`），
每组是 `topaz-*` 与 `wt86047-*` 两个不同补丁共用同一编号。当前按文件名字典序应用、目标文件不重叠，
尚未造成问题，但建议后续重编号（`topaz-*` 组整体后移）。

---

## 六、2026-11-22 凌晨：用户态 4 KB 改造及其后的 SIGILL 追踪

### 6.1 已完成的 4 KB 迁移（照搬 UniXNU 的 native-4k 方案）

参考 `UniXNU/UniXNU` 的 `patches/xnu/native-4k-user-abi.patch`，**不开** `__ARM_MIXED_PAGE_SIZE__`
（那条路要求 16 KB 内核，编译期即 `#error`），而是把四处硬编码 16 KB 改成 4 KB，同一守卫：

| 补丁 | 文件 | 作用 |
|---|---|---|
| `0248` | `bsd/kern/mach_loader.c` | `load_machfile`：64 位用户 map 用 `FOURK_PAGE_SHIFT` |
| `0249` | `osfmk/arm/commpage/commpage.c` | 向用户态公布 `USER_PAGE_SHIFT_64 = 4 KB` |
| `0250` | `osfmk/arm/arm_init.c` | `page_shift_user32 = ARM_PGSHIFT` |
| `0251` | `osfmk/vm/vm_shared_region.c` | 共享区域子映射 `FOURK_PAGE_SHIFT` |

**实测确认迁移成功**：固定带显示 `PS=0x0000000c`、`MP=0x0000000c`，
`vm_shared_region_enter()` 不再返回 `KERN_INVALID_ARGUMENT`，共享区域正常建立，
`B0=0x80000000 / B1=0x00000001` 说明基址就是 `0x180000000`（无 slide）。

### 6.2 SIGILL 仍在，且逐条排除了三个假设

| 假设 | 验证方式 | 结论 |
|---|---|---|
| 页移错位（本次） | `PS/MP` 都是 12 | **排除** |
| 映射内容错（文件偏移被折算） | 把 pc 换算成子缓存文件偏移，逐次比对 | 文件内容与 CPU 实际执行的字节不一致，但见下 |
| I-cache 陈旧 | `0252` 在 `vm_shared_region_map_file()` 成功后全量 `FlushPoC_Dcache()+InvalidatePoU_Icache()` | **无效，排除** |
| 多 VA 别名到同一物理页 | `0244` 增加 `pmap_find_phys(pc)` 与 `pmap_find_phys(pc+PAGE_SIZE)` | **排除**：`pp=0x9dcdb`、`pp2=0` |

### 6.3 现存事实（供下次接续）

四次启动的 SIGILL 数据：

| 启动 | pc | 区域偏移 | 落点 | instr |
|---|---|---|---|---|
| 1 | `0x1d54dc900` | `0x554dc900` | 子缓存 `.25` | `4e284840` |
| 2 | `0x1f863c900` | `0x7863c900` | `.33` | `4e284840` |
| 3 | `0x21c138900` | `0x9c138900` | `.42.dyldlinkedit`（非代码） | `4e284840` |
| 4 | `0x2286bc900` | `0xa86bc900` | 末页，下一页未映射 | `4e284840` |

三条稳定特征：**① pc 页内偏移恒为 `0x900`；② `instr` 恒为 `0x4e284840`（ASCII `"@H(N"`）；
③ 区域偏移逐次增大，越来越逼近区域末端 `0xaade8000`。**

`pp2=0` 说明 pc 位于某个映射的**最后一页**，因此"执行流跑出映射边界"是最贴合的解释：
某个指针每次启动都往高地址推进，最终越过映射末尾落到非代码内容上。

### 6.4 下一步建议

1. **先补日志通道**。现在每次诊断只能靠一张照片读一行，已经成为瓶颈。
   `0143` 已有把日志写进 `system` 分区的能力（`PERSISTLOG=1`），配合刷一次 TWRP 即可取回全文；
   这比继续"一轮 CI + 一张照片"高效得多。**（已升级为 v2，见第十节）**
2. 技术上接着查：`pc = 映射末页 + 0x900` 的形态，指向 **dyld 的 stub / jump table / 末页边界处理**，
   以及 `__dyld_private`、chained fixups 的 target 计算是否把 16 KB 单位当成 4 KB 用。
3. 保留的探针：`0244`（SIGILL + pp/pp2）、`0246`（enter 失败码）、`0247`（基址 B0/B1）。
   `0252` 可保留也可移除（不影响行为，仅每次映射多一次全量刷新）。

---

## 六、策略变更与回退

- **此后一律 `fastboot flash boot`**，不再用 `fastboot boot`（用户明确要求）。
- 当前 `boot` 内容 = `build/wt86047/shim/out/shim-boot.img`（只读门，`rd=emmcroot0`，**零写入**）。
- 回到 Android：`fastboot flash boot boot.img`（工作区根目录的 `boot.img`，10,186,752 B）。
- `userdata` 已被覆盖，Android 数据不可恢复；分区表、`system`、`persist` 未动。

---

## 七、复现步骤

```bash
# 1) 解 IPSW 的 OS 卷到裸 APFS（27 秒）
python tmp/extract_apfs.py           # -> E:/ios-rootfs/os-apfs.img

# 2) 建 HFS+ 根卷（WSL，约 6 分钟；必须 NOJOURNAL=1）
wsl -d Ubuntu-26.04 -- bash tmp/build-rootfs.sh     # -> /opt/out/iOSRoot.img

# 3) 复制到 Windows 侧并刷入（约 22 分钟）
fastboot -S 256M flash userdata "E:/ios-rootfs/iOSRoot.img"

# 4) 打包只读门并刷 boot
wsl -d Ubuntu-26.04 -- bash scripts/build-shim.sh <kernel> --rd-emmc-ro
fastboot flash boot build/wt86047/shim/out/shim-boot.img
```

CI：`xnu.yml` 的 `push` 触发器覆盖 `branches: [main]` + `paths: xnu/patches/**`，
推补丁即自动构建，无需手动 dispatch。

---

## 八、补丁与提交索引

| 补丁 | 提交 | 作用 |
|---|---|---|
| `0244-wt86047-log-user-sigill.patch` | `b6fed6b` | 打印未知指令异常的 pc/lr/sp/instr |
| `0245-wt86047-shared-region-4k-submap.patch` | `7d40659` | 子映射强制 4 KB 页移 |
| `0246-wt86047-shared-region-enter-band.patch` | `e2fff32` | enter 失败码写进固定带 |

CI：`44905244813`（0244，success）、`44909348189`（0245，success）、0246 见最新运行。

---

## 九、工程坑清单（新增）

1. **WSL 的 `/tmp` 是 tmpfs**，VM 空闲回收即清空 —— 产物一律写 `/opt/work`。
2. **`wsl ... bash -c '...'` 内联引号会被提前展开**（`$i`、`${w:6:2}` 变空），带变量的逻辑必须落成 `.sh`。
3. **apfs-fuse 挂载随 VM 回收消失**，一次调用内要"挂载 → 干活"连做。
4. **`scripts/dryrun-patch.sh` 必须传绝对路径**（内部先 `cd` 再拼相对路径）。
5. **生成补丁的基线树要对**：`sleh.c` 被 8 个补丁改过，`vm_shared_region.c` 被 4 个改过；
   用 `git apply --include=<path>` 逐个应用，别用 `patch`（会因权限位整体拒收）。
6. **共享缓存子缓存表**：位于主文件（384 KB）内，`subCacheArrayOffset` 在头 `0x188`，
   项 **56 字节**（uuid16 + vmOffset8 + 后缀名16 + 保留16）。步长写错会解析出垃圾。
7. **`SHARED_REGION_TRACE_ERROR` 是开着的**，`not in trust cache` 那类是跟踪输出、**不致命**，
   后面 `goto root_check` 会回落到"必须 root 所有"。

---

## 十、2026-11-22 凌晨：system 分区持久控制台日志 v2（补丁 `0253`）

### 10.1 背景：0143 已在做这件事，但不好用

`0143` 早就把 `_cnputs` 的每个字节写进 `system` 分区开头的 4 MiB 环。真正的问题不是"能不能写"，
而是写完不好分析。三个实质缺陷：

1. **环小且每次启动清零。** `total` 每次启动从 0 开始，一次启动只保留最后 4 MiB；
   开机早期（正是 SIGILL 发生的位置）可能已被冲掉，下一次开机立刻覆盖上一次。
2. **没有时间轴，没有启动边界。** 解出来是一整块纯文本，无从判断哪段属于第几次启动、崩在第几秒。
3. **头没有 CRC。** 掉电或半写时解码器只能靠长度猜。

### 10.2 v2 的四个改动

| 项 | 改动 | 理由 |
|---|---|---|
| 容量 | 4 MiB → **16 MiB**（`8193` → `32769` 扇区） | `system` 实测 100 MiB 且无人使用，保留约 8 次启动历史 |
| 累计偏移 | 启动时读旧头（magic+size+CRC32 校验），`total` 接着上次末尾继续 | 环成为**跨启动连续流**，一次取回拿到全部历史 |
| 启动前暂存 | 驱动挂上之前的控制台输出先进 256 KiB 缓冲区，挂上后回放到 resume 点 | 否则这些字节会写在环偏移 0，**恰好覆盖上一次启动日志的开头**（即 SIGILL 那段）。这是模型测试发现的真实缺陷 |
| 时间轴 | 每次提交前插 `[WT86047 t=12.345s]`（1 Hz，仅在确有新数据时） | 回答"开机几秒崩的" |

头结构 v2（60 字节，`__attribute__((packed))`）：

```
magic[12]="WT86047LOG2" | u32 version | u32 size | u64 total | u64 seq
u32 boots | u32 flags | u64 last_uptime_ms | u32 hdr_crc32 | u32 pad
```

`flags` bit0 = `COLD`（没读到有效 v2 头，本段从 0 重新开始）；
`hdr_crc32` 覆盖全结构（本字段置 0 后计算），用 `0142` 已有的 `wt86047_crc32`。

### 10.3 取回与查看

```bash
# 1) 打包时显式开启（默认关，只读门 --rd-emmc-ro 与它互斥）
PERSISTLOG=1 bash scripts/build-shim.sh <kernel.macho> --rd-emmc

# 2) 刷 boot 并启动，让日志落盘，然后进 recovery
fastboot boot "twrp-3.7.0_9-0-32位.img"      # 已装 64 位 treble 版 TWRP
adb devices

# 3) 取回并解码（脚本先读 512B 头，再按头里的 ring size 读全环）
sh ios-santoni/tools/sweet-log-pull.sh log.txt      # -> log.txt + log.txt.bin

# 4) 分启动查看
python ios-santoni/tools/sweet-log.py log.txt.bin --list
python ios-santoni/tools/sweet-log.py log.txt.bin boot3.txt --boot 3
```

解码器同时兼容 `WT86047LOG1`（0143）、`SANTONILOG1` 与 `SWEETLOG` 三种旧布局；
v2 头 CRC 不符时会打印 `MISMATCH (half-written header?)` 但照常解码。

### 10.4 代价与约束

| 项 | 值 / 说明 |
|---|---|
| 内核 bss | +16 MiB 环 + 256 KiB 暂存（1 GB 机器，约 1.6%） |
| 每次启动额外丢盘 | 一次 `dd` 约 16 MiB，USB2 下十几秒 |
| eMMC 写入 | 只写脏块，1 Hz；空闲系统不写（无新数据直接跳过） |
| 未覆盖的失效场景 | panic 时刷盘线程已停，最后 ≤1 s 的日志留在 RAM 里未落盘 |
| 与只读门的关系 | `PERSISTLOG=1` 与 `--rd-emmc-ro` 互斥，脚本会直接报错 |

### 10.5 已做的验证（无真机部分）

| 检查 | 结果 |
|---|---|
| `git apply --check`（全部 251 个补丁应用后的树） | 通过，7 个 hunk |
| 真套用后与工作树逐字节比对 | 一致 |
| 抽出改动的函数体 + 桩头 `clang -Werror=implicit-function-declaration` 编译 | 无警告（除 harness 中 `log_start` 未被调用） |
| 解码器单测（合成 v2 / v1 / 空区 / CRC 错 / wrap / 16 MiB 回环） | 16 项全过 |
| 环形模型单测（3 次启动连续、20 次启动 wrap、暂存溢出计数、resume 定位） | 7 项全过 |

### 10.6 CI 与镜像级验证（2026-11-22 03:2x）

CI run **`44940036307`**（`e5e850f`）**conclusion = success**，9 分钟。逐项核对：

| 检查 | 结果 |
|---|---|
| `0253` 是否被应用 | `build.log:316  ⇒ Applying patch: .../dxb/patches/0253-wt86047-log-to-system-v2.patch`，**不在** `Skipping patch` 名单里 |
| `build.log` 中的 `error:` | 共 11 条，**全部**来自 blacktop 自带的 `iobuffermemd_monterey` / `skywalk_sonoma` / `syntax_checker_sonoma` / `TrustCache` 四个补丁的 skip 路径，与本补丁无关 |
| `wt86047_sdhci.c` 新增告警 | 仅 2 条 `-Wdeclaration-after-statement`（第 2902/2911 行）。该类告警在本文件**已有约 50 条**，XNU 用 `-Wno-error=declaration-after-statement` 降级，不是回归 |
| 符号进入镜像 | `llvm-nm` 可见 `_wt86047_log_append`、`_wt86047_log_early`、`_wt86047_log_early_lost`、`_wt86047_log_flushed`、`_wt86047_log_thread`、`_wt86047_log_total` |
| 字符串进入镜像 | 镜像内有 `WT86047LOG2`、`WT86047 LOG: v2 flusher`、`SYSTEM console log v2 on block`；`WT86047LOG1` 已为 0（v1 魔数被替换，符合预期） |
| bss 实际落点 | `_wt86047_log_ring = 0xfffffff0080dafd4`，`_wt86047_log_early = 0xfffffff00809afc8`，都在内核 bss 段内，无越界迹象 |

**一处需要留意的细节**：`=== WT86047LOG boot ` 这个字面量在镜像里**不连续**——反汇编显示它被拆成了
`=== WT86047LOG ` 与 `boot` 两段常量，尾随空格是否单独存放取决于链接器布局。所以解码器改为用正则
`^=== WT86047LOG boot\s*(\d+)` 匹配，不再依赖那个空格（提交 `74ccc7d`，纯工具改动，不触发内核 CI）。

**尚未验证**：真机上的一段完整启动日志。判读点：日志区出现
`WT86047 LOG: SYSTEM console log v2 on block 0x..., boot 1, cold, resume 0x0, carried N B`，
其后紧跟 `=== WT86047LOG boot 1 cold, ... ===` 边界行。
