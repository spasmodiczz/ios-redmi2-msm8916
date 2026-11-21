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

`0098` 新增导出函数 `sweet_early_fixed(int line, const char *s)`：

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
