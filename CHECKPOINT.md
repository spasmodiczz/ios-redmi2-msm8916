# 记忆检查点 — 2026-11-21 14:25

> 用途：上下文过重时，新会话从这里接着干。读完这一页 + `STATE.md` 第十~十二节即可恢复。

## 一句话

XNU 已在 Redmi 2 (MSM8916 / wt86047) 真机上跑过 platform expert、发出完整内核日志（M2 达成）；
现在卡在阶段 2 的第一个真实障碍：**一次同步异常，崩在 `current_percpu_base` 对 `current_thread()` 的空指针解引用**。
回溯打印刚改成 panic-safe 输出，**正在等 CI 验证**。

## 当前代码状态

| 项 | 值 |
|---|---|
| XNU 仓库 | `git@github.com:spasmodiczz/ios-redmi2-msm8916.git`（分支 main） |
| 最新提交 | `63f7e09`（修 0239 的插入位置） |
| 上一个可用提交 | `65e7fdd` / `8a5273a`（都能跑到 kperf 之后才崩） |
| CI | run `44875078989`，`queued`（2026-11-21T06:20:31Z 创建） |
| 本地工作副本 | `ios-port/ios-santoni`（分支 wt86047，remote `mine`） |
| 本地工程仓库 | `ios-port` 自身 git，HEAD `53060e2` |

## 正在解决的问题（关键）

### 现象

内核在 `kperf_init_early()` 之后（`ml_set_max_cpus(1)` 已修好死等）发生同步异常：

```
esr 0x96000005 → Data Abort(S1)，translation fault lvl1
指令 f9411000  → ldr x0, [x0, #0x220]
pc   0xfffffff00743804c = _current_percpu_base + 0xb
源码  vm_offset_t current_percpu_base(void) { return current_thread()->machine.pcpu_data_base; }
```

即 **`current_thread()` 为 NULL 时有人访问了 per-CPU 变量**。

### 为什么看不到回溯

`panic_with_thread_kernel_state("Synchronous exception taken while SP1 selected")` 只打原因 + 寄存器就停机，
XNU 自己的 `Backtrace` 不会执行。所以只能自己打印调用栈。

### 已试过并失败的两版（不要重复）

1. **走帧指针链**（`b5ff697`）：`fp` 可能指向未映射 VA，在异常处理里读它 → 双重故障 → 看门狗复位（半花屏后重启）。
2. **在异常处理里用 `printf`**（`f900701` 之前的 65e7fdd）：kprintf 要拿锁 / 读 per-CPU，当场二次异常，
   屏幕上只剩上一帧（只看到 `SWEET EARLY 2`），连 panic 原因都打不出来。

### 当前方案（`63f7e09`，待验证）

`0239-wt86047-synchronous-panic-backtrace.patch`：

- 只读当前栈 `sp .. sp+384`（该区域必然已映射），挑出落在内核代码段
  `0xfffffff007004000 ~ 0xfffffff007b7c000` 的字，当作候选返回地址
- 输出只用 `sweet_early_putc` + 本地 16 进制函数（**无锁、无 per-CPU、不分配**）
- `static int bt_busy` 重入保护，第二次异常不会再打印
- 每行两个地址，最多 12 个

预期屏幕：

```
WT86047 BT pc=fffffff00743804c sp=fffffff0085074..
fffffff007xxxxxx fffffff007xxxxxx
...
```

## 下一步（按顺序）

1. 等 CI `44875078989`；成功后取 `kernel.development.wt86047`，`build-shim.sh` 打包，`fastboot boot`。
2. 拍照 → 用 `python scripts/symbolize.py <地址...>` 把 BT 地址翻译成函数名（已验证：
   `0xfffffff00743804c -> _current_percpu_base + 0xb`）。
3. 按调用链定位「谁在无线程上下文的路径里访问 per-CPU 变量」，再决定修法
   （候选：该路径改为不用 percpu 宏、或在 `bsd_init` 前建立 thread 上下文、或延迟该初始化）。
4. 之后才是 root device（`0142` 的 eMMC 探测）+ 用户态。

## 必须遵守的硬规则（都是踩出来的）

1. **改补丁必须同步改引用它的补丁**：`0012` / `0140` / `0150` 把 `0003` 的新增行当上下文。
2. **改完补丁三步走**，缺一步就白跑一轮：
   - `python build/wt86047/tools/check_patch.py <patch>`（hunk 计数）
   - `bash scripts/dryrun-patch.sh <patch>`（能否套用）
   - **把补丁真正套到上游文件，读一遍改动区域**（`0239` 的语法错误就是漏了这步，CI 跑了 26 分钟才炸）
3. **panic 路径禁止用 `printf`/kprintf**，只能用 `sweet_early_putc`。
4. **禁止走帧指针链**，只读当前栈。
5. 产物回推是 `if: always()`，**构建失败也会发布产物** —— 判断成功与否必须看 CI 的 conclusion，不能看有没有 zip。
6. `git fetch mine artifacts` 在本机会 `early EOF` / `unpack-objects failed`（大包 + 网络），
   **不要挂后台轮询**，改用单次 API 查询 + 用户手动下载。
7. 写补丁时：某行如果是本补丁**新增**的，行首必须是 `+`，不能写成上下文（`0010` 踩过）。

## 关键常量（实测，不要重测）

| 项 | 值 |
|---|---|
| 面板 / 帧缓冲 | 720×1280，stride **2160** 字节，24bpp，字节序 **G/R/B** |
| 可见区 | 仅 buffer 行 **960~1279**，显示在屏幕顶部 320 行 |
| 可见区物理基址 | **`0x831FA400`** = `0x83000000 + 960×2160` |
| 控制台基址偏差 | `WT86047_FB_X_BIAS = 192` 字节（8 个字符格 = 64px） |
| 控制台列数 | `EARLY_COLS = 88`（704px） |
| 控制台行高 | `EARLY_CELL_H = 16`（18 行 + 底部 32px 标记带） |
| CPU | 4×A53，启动核 MPIDR `0x80000000`，**无 PSCI**（`0xFFFFFFFF`） |
| ADT CPU 节点 | **只声明 cpu0**（`cpu_phys_ids = [0x000]`） |
| 看门狗 | LK 未开启；内核 halt 后靠 WDT bite（约 11s）复位 |

## 常用命令

```bash
# 打包（必须绝对路径；脚本中途会 cd）
wsl -d Ubuntu-26.04 -- bash -lc "cd /mnt/d/01_手机与刷机/红米2/ios-port && \
  bash scripts/build-shim.sh /mnt/d/01_手机与刷机/红米2/ios-port/kernel-outN/kernel.development.wt86047"

# 上机（纯内存，不写任何分区）
fastboot boot build/wt86047/shim/out/shim-boot.img
# 进 fastboot：长按电源 10 秒 → 音量下 + 电源

# 符号化 panic 地址
python scripts/symbolize.py 0xfffffff00743804c

# 查 CI（API 未认证有 60 次/小时限制）
curl -s -x http://127.0.0.1:7897 "https://api.github.com/repos/spasmodiczz/ios-redmi2-msm8916/actions/runs?per_page=1"
```

## 设备状态

用户已完整备份 system/data，**允许改动系统分区**；但当前所有测试都用 `fastboot boot`，boot 分区未动。

---

## 更新 2026-11-21 14:50 — BT 曾经打印过，只是看不见

### 决定性证据

`strings kernel.development.wt86047 | grep -c "WT86047 BT"` → **1**。
代码编进去了、`#if defined(WT86047)` 成立、`sweet_early_putc` 路径也是通的
（寄存器转储完整显示、没有花屏、没有重启）。

**问题纯粹是可见性**：窗口 18 行，panic 标题 2 行 + 寄存器转储 15 行 = 17 行，
BT 行被顶出窗口。之前两轮在打印通道上找原因是方向错了。

### 新方案（提交 `32cf187`）

新增补丁 `0240`（独立，锚定 `sweet_early_disable` 尾部）导出函数 `sweet_early_fixed(int line, const char *s)`：

- 画在缓冲区**底部 32 px 的阶段标记带**（`y = 288..319`），分页滚动**永不触碰该区域**
- 8 px 行高（字形隔行取样），4 行，每行 90 列
- `0239` 用它输出：line 0 = `BT pc=… sp=…`，line 1..3 = 最多 12 个候选返回地址

预期画面（在寄存器转储**下方那条 32px 带子**里，而不是日志区）：

```
BT pc=fffffff00743804c sp=fffffff008507440
fffffff007xxxxxx fffffff007xxxxxx fffffff007xxxxxx fffffff007xxxxxx
...
```

### 新增验证工具

`scripts/apply-patch-check.py` —— 把补丁**真正套到上游文件**并读改动区域：

```bash
python scripts/apply-patch-check.py <patch> upstream/xnu-xnu-8796.101.5 --show 8
# --write  用于链式套用多个补丁
```

已用它链式验证 `0010 → 0012 → 0098 → 0140 → 0239`：`sweet_early.c` 的所有 hunk 全部命中
（`0012`/`0140` 在 `pe_serial.c` 等文件的失败是缺 `0003` 前置，与本改动无关）。
它支持新建文件（`0010` 创建 `sweet_early.c`/`.h`），这是 `dryrun-patch.sh` 做不到的。

### 从此轮次起的判读方式改变

panic 时**先看底部 32px 带子**，再看日志区。日志区只有 18 行且会被 panic 占满。

### 为什么 `0240` 是独立补丁而不是追加到 `0098`

第一次我把这个函数追加到 `0098` 最后一个 hunk（`sweet_early_relocate` 的结尾），
结果 CI 报 `patch failed: osfmk/arm64/sweet_early.c:129`，守卫随即失败。

原因：本地链式验证时 `0012` 只**部分**应用（它依赖 `0003`，而 `0003` 没在验证链里），
于是 relocate 区域与 CI 真实状态不同，**验证在一棵不匹配的树上通过**。

改法：`0098` 恢复为原本可应用的内容，新函数放进 `0240`，锚点选 `sweet_early_disable`
的尾部 —— 这套补丁里没有任何一个改过这个函数，所以 `0012` / `0140` 怎么变都不影响它。

**新规则**：验证补丁链时，如果某个补丁在链里部分失败，就不能用它后面的状态去验证下一个补丁。
要么把前置补丁补齐，要么把锚点选在未被改动的位置。

### 补丁生成的两个坑（`0240` 连挂两次）

**坑 1：尾部假空行。** `open(...).read().split('\n')` 对以换行结尾的文件会多出一个空串。
把它当一行参与 `difflib`，补丁末尾就多一条「上下文空行」；`git apply` 因此要求
`#endif /* SWEET */` 之后还有空行，而真实文件没有 → 整个补丁被拒。
生成补丁时 **`while lines and lines[-1] == '': lines.pop()`**。

**坑 2：锚点选在被后续补丁改过的区域。** 第一次把函数追加到 `0098` 的最后一个 hunk
（`sweet_early_relocate` 尾部），而 `0012` 会改 relocate；本地验证时 `0012` 因缺 `0003`
只部分应用，于是**在一棵与 CI 不一致的树上验证通过**。

### 唯一可信的验证方式：`git apply --check`

本地 applier（`scripts/apply-patch-check.py`）比 CI 宽容 —— 允许 ±200 行搜索、
不校验行数、还自己发明过尾部空行，**它能通过而 CI 会失败**。

所以在真树上跑 `git apply`：

```bash
# 1) 复现 CI 的树：全部补丁按编号应用到上游源码
#    （脚本见下方，重点是排除待验证的那个补丁）
# 2) 严格检查
cd /d/wt-tmp/fullN && git init -q . && \
  git apply --check -p1 <patch>
```

`git apply` 的报错格式（`error: patch failed: file:line`）**与 CI 日志完全一致**，
这就是判断依据。`0240` 最终是在「全部 240 个补丁应用后」的树上生成的，
`git apply --check` 通过。

### 8px 折叠字形在照片上读不出来（`5033d65` 已改）

固定带方案**机制成功了** —— 底部 32px 带子里确实出现了 `BT pc=… sp=…` 和一行地址，
说明 `sweet_early_fixed()` + `sweet_early_putc` 这条路是通的、panic 输出确实盖不掉它。

但**字不可读**：把 16px 字形按 `glyph[gy*2]` 取样压成 8px，等于丢掉一半笔画，
相邻行还互相粘连。720×320 的面板拍成照片后，`7438` 会变成 `74.3.M`。

**改法**：带子只有 32px，就用 **2 行 × 16px 标准字形**，不压缩。
代价是地址从 12 个降到 5 个（一行 90 字符），但清晰比数量重要 ——
栈上最近的 5 个返回地址足够定位调用者。

**通用结论**：这块屏只有 320 行可见。任何「压缩字形换取行数」的做法都会牺牲可读性，
而可读性才是目的。宁可少打，不要糊。

---

## 更新 2026-11-21 16:15 — 阶段 2 第二个根因：socd trace 在无线程时崩

### 先说方法：这次是 BT 地址直接定位的

固定带里读出的四个地址（`5033d65` 的 2 行 16px 版本，字清晰可读）：

```
BT pc=fffffff00743804c sp=fffffff008587360
fffffff0072c236c fffffff00734d548 fffffff0070(8|0)67c fffffff007b17204
```

`python scripts/symbolize.py <addr...>` 结果：

| 地址 | 符号 | 意义 |
|---|---|---|
| `…72c236c` | `debug_is_current_cpu_in_panic_state + 0xb` | **最靠近 sp = 真正的调用者** |
| `…734d548` | `socd_client_trace + 0xc7` | 它的调用者 |
| `…708?67c` | `__doprnt.digits + 0x2xxxx` | 符号表在此区间无条目，忽略 |
| `…7b17204` | `machine_switch_context.cold.1 + 0x13` | 栈上残留，说明上下文切换过 |

### 根因

```c
osfmk/kern/socd_client.c:127
	if (!debug_is_current_cpu_in_panic_state()) {
		KDBG(debugid, arg1, arg2, arg3, arg4);
	}

osfmk/kern/debug.c:714
debug_is_current_cpu_in_panic_state(void)
{
	return current_debugger_state()->db_entry_count > 0;
}

osfmk/kern/debug.c:190
static struct debugger_state PERCPU_DATA(debugger_state);
static inline struct debugger_state *current_debugger_state(void)
{
	return PERCPU_GET(debugger_state);      /* → current_percpu_base() */
}
```

`PERCPU_GET` → `current_percpu_base()` → `current_thread()->machine.pcpu_data_base`。
启动早期 TPIDR_EL1 还没有线程 → `current_thread()` 为 NULL → 读 `+0x220` 缺页。
**而这次缺页发生在异常处理内部**，所以双重故障、看门狗复位。

`0x220` 就是 `machine.pcpu_data_base` 在 thread 结构里的偏移，与 `far = 0x220` 完全吻合。

### 修复（`853d056`，补丁 `0241`）

在 `socd_client_trace()` 入口判 `current_thread() == NULL` 直接 return。
不修 `debug_is_current_cpu_in_panic_state()` 是因为：它返回 FALSE 后紧接着的 `KDBG()`
同样依赖 per-CPU 状态，只挡一半等于把崩溃点后移。整条 trace 路径都没有意义 ——
第一个线程还不存在，没有可追踪的东西。

### 诊断方法沉淀

1. panic 摘要写进**底部 32px 固定带**（`0240`），2 行 × 16px 正常字形。
2. 拍照 → 直接读 `BT pc=… sp=…` 和地址行。
3. `python scripts/symbolize.py <addr...>` → 函数名（返回地址按 symbol-1 解析）。
4. 拿最靠近 sp 的那个函数去源码里查，通常就是元凶。

`symbolize.py` 的已知缺陷：`__TEXT_EXEC` 里有些区间没有独立符号，会落到
`__doprnt.digits + 大偏移` 这种无意义结果。忽略这类行，不要被误导。

---

## 更新 2026-11-21 17:05 — 上一个修复本身把内核崩了（已回退）

### 事实

崩溃地址符号化后指向我自己的补丁：

```
lr = 0xfffffff007449f48 -> _ml_set_max_cpus + 0x17
pc = el1_sp0_synchronous_vector_long + 0x33     （二次故障，发生在向量入口）
esr = 0x96000005   far = 0x158                  （NULL + 0x158 缺页）
```

`ml_set_max_cpus()` 的第一句是 `lck_mtx_lock(&max_cpus_lock)`，而 `arm_init()` 在
互斥锁/线程机制建立之前运行 —— 早期拿锁必然缺页。

### 现在的做法（提交 `6aef2b3`，补丁 `0242`）

1. `0010` 删掉 `ml_set_max_cpus(1)` 调用（连带清掉一处多余 `#endif`）。
2. 新增 `0242`：`ml_wait_max_cpus()` 在 `machine_info.max_cpus <= 1` 时直接
   `return machine_info.max_cpus` —— 既不等，也不碰锁。
   `kperf_init_early()` 忽略返回值，`max_cpus_initialized` 无其他读者。

### 验证流程的新坑：本地补丁是 CRLF

`git apply` 在本地对所有补丁都失败（`patch does not apply`），因为工作区里的补丁文件是
**CRLF**（Windows 检出），而目标源码是 **LF** —— 每个上下文行都多一个 CR。CI 上是 LF，
所以只在本地假失败。**验证前先把补丁复制成 LF 再 `git apply`**（`\r\n` → `\n` 后存到
`D:/wt-tmp/p3/`）。

### 仍未验证

`0241`（`socd_client_trace` 在无线程时直接返回）**尚未在真机上生效过** —— 这次崩溃点更早，
根本没走到它。下一轮才能确认。

### 已知小毛病

底部 32px 固定带只有 2 行，第 1 行（地址行）会被 `sweet_early_mark` 的彩色块覆盖，
所以照片上只看到 `BT pc=… sp=…` 一行。修法是让 mark 只画带子左半，或把 BT 行上移到
日志区最后两行；等真需要读地址时再改。

### 必读文档（每次开工先读）

- `.workbuddy/memory/MEMORY.md` —— 长期要点与硬规则
- `.workbuddy/memory/2026-11-21.md` —— 当天踩坑记录
- 本文件 —— 接续入口；`STATE.md` / `HANDOFF-阶段2.md` —— 详细历史与交接
