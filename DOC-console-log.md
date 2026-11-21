# 系统分区持久日志通道 — 操作参考

> 面向"接手这个项目的人或模型"。只讲怎么用、怎么判读、哪里会踩坑。
> 实现见 `ios-santoni/xnu/patches/0253-wt86047-log-to-system-v2.patch`；
> 设计背景与实测见 `REPORT-阶段3-eMMC-Root.md` 第十节。

## 一句话

XNU 的每个控制台字节都会追加进一个 **16 MiB 环形缓冲**，由内核线程每秒把脏块写进
**`system` 分区开头 32769 个扇区**（1 个头 + 16 MiB 环）。`system` 分区 100 MiB、内容为空、
不承载任何文件系统，所以这条通道不碰 userdata 上的 HFS+ 根，也不需要 `emmcrw=1`。

## 快速路径

```bash
# 1) 出包：根只读 + 只写日志区（推荐的验证组合）
bash scripts/build-shim.sh /abs/path/kernel.development.wt86047 --rd-emmc-ro-log
fastboot flash boot build/wt86047/shim/out/shim-boot.img
fastboot reboot                      # 让它跑到失败，约 40~50 秒足够

# 2) 进 recovery（长按电源 10s 关机，然后 音量上+电源）后取回
sh ios-santoni/tools/sweet-log-pull.sh log.txt
```

```bash
# 3) 按启动分段查看
python3 ios-santoni/tools/sweet-log.py log.txt.bin --list
python3 ios-santoni/tools/sweet-log.py log.txt.bin boot2.txt --boot 2
python3 ios-santoni/tools/sweet-log.py log.txt.bin            # 全文到 stdout
```

`--rd-emmc-ro-log` 是 `build-shim.sh` 里的 cmdline 档位：`rd=emmcroot0 sweetlog=1`，
**没有** `emmcrw` / `rootrw`，所以 HFS+ 根只读挂载。若用 `--rd-emmc`（可写根），
需另加 `PERSISTLOG=1`；`--rd-emmc-ro` 与 `PERSISTLOG=1` 互斥，脚本会直接报错。

## 数据布局

| 位置 | 内容 |
|---|---|
| `system` 起点扇区 `se.sys_first_block`（实测 `0x70c60`） | 512 B 头（仅用前 60 B） |
| 其后 32768 扇区 | 16 MiB 环，流字节 `n` 位于偏移 `n % 16 MiB` |
| 分区剩余约 84 MiB | 未使用（预留给将来更大的环） |

写入函数 `wt86047_sdhci_write_log_blocks()` 自带栅栏，只接受落在
`[sys_first_block, +32769)` 的写，越界直接 `EPERM` 并打印拒绝原因。

## 头结构（v2，60 字节，packed，小端）

| 偏移 | 类型 | 字段 | 说明 |
|---|---|---|---|
| 0 | `char[12]` | magic | `"WT86047LOG2"` |
| 12 | `u32` | version | `2` |
| 16 | `u32` | size | 环大小（字节），必须等于 16 MiB |
| 20 | `u64` | total | **跨启动累计**的流字节数 |
| 28 | `u64` | seq | 本次启动内的刷盘序号 |
| 36 | `u32` | boots | 该区段被格式化成 v2 以来的启动次数 |
| 40 | `u32` | flags | bit0 = `COLD`（没读到有效 v2 头，`total` 从 0 重来） |
| 44 | `u64` | last_uptime_ms | 最后一次刷盘时的开机时长（ms） |
| 52 | `u32` | hdr_crc32 | 覆盖全结构、本字段置 0 后计算 |
| 56 | `u32` | pad | 保留 |

解码器结构：`struct.Struct('<12sIIQQIIQII')`。CRC 用的是 `0142` 里已有的标准 CRC-32，
与 `zlib.crc32` 等价。

## 环语义（四条规则）

1. **累计偏移**。启动时读扇区 0，magic/version/size/CRC32 全过才把 `header.total` 当作
   `resume`；否则 `COLD`，`resume = 0`。因此 `total` 只增不减，环是"一条连续流"。
2. **启动前暂存**。驱动挂上之前不知道 `resume`，这些字节**不进环**，先存进 256 KiB
   静态缓冲；挂上后回放到 `resume` 处。若超过 256 KiB，超出部分计数丢弃并在边界行写
   `lost N B`。
3. **启动边界行**。每次启动在回放之后追加一行：
   `=== WT86047LOG boot 3 continued, resume 0x1a2b3c, carried 40960 B, lost 0 B, ring 16384 KiB ===`
   解码器据此分段。**注意**：镜像里这个字面量被编译器拆成了 `=== WT86047LOG ` 与 `boot`
   两段，尾随空格不保证存在，所以匹配必须用正则 `^=== WT86047LOG boot\s*(\d+)`，
   不要用固定前缀 `startswith`。
4. **时间轴**。每次刷盘前若确有新数据，插入 `[WT86047 t=12.345s]`（1 Hz，取自
   `mach_absolute_time()` 换算的开机时长）。空闲系统不插标记、不写盘。

## 判读清单

正常（一定会有）：

```
WT86047 SDHCI: "system" first block 0x70c60, 100 MiB (console log area available)
WT86047 LOG: SYSTEM console log v2 on block 0x70c60, boot 1, cold, resume 0x0, carried 11381 B
```

| 现象 | 含义 |
|---|---|
| 解码器报 `no console log header` | 该次启动没开 `sweetlog`，或分区从未被写过 |
| 报 `MISMATCH (half-written header?)` | 头被半写（掉电瞬间）；正文仍可读，`total` 可能偏小 |
| 边界行 `cold` 而非 `continued` | 上次的 v2 头无效（首次、或 CRC 坏），历史从 0 重来 |
| 边界行 `lost N B` 非 0 | 启动前输出超过 256 KiB 暂存上限 |
| 头部 `boots` 比预期小 | `system` 被擦过或格式化成 v1 后被覆盖 |
| 解码器报 `wrapped` | `total > 16 MiB`，更早的启动已被环覆盖（正常） |

## 已知缺陷

1. **接缝空洞**。两次启动的交界处会出现一段全零（实测 295 B）：约 29 B 来自
   `total` 先加、字节后写的断电窗口；其余来自刷盘线程可能把"已发布但尚未拷完"的区域
   当成已就绪提交。修法（未实施）：增加 `wt86047_log_published`，在字节拷贝结束后
   才发布，刷盘线程只提交到该值。
2. **panic 尾部丢失**。panic 时刷盘线程已停，最后 ≤1 s 的日志留在 RAM 未落盘。
   刻意不做 panic 同步写盘（风险高于收益）。
3. **取回需要 recovery**。XNU 阶段没有 adb，必须进 TWRP 读块设备。

## 不要做的事

- 不要 `fastboot erase system`（会清掉全部历史；虽然分区为空，但日志在这里）。
- 不要在 `--rd-emmc-ro`（零写入门）下期待有日志——那一档不追加 `sweetlog=1`。
- 不要把环大小改到超过 `system` 容量：`WT86047_LOG_SYSTEM_BLOCKS` 在
  `0142` 里，`WT86047_LOG_SIZE` 在 `0253` 里，两者必须同步（`blocks = 1 + size/512`）。
- 不要用固定前缀匹配边界行（见上）。
- 不要在 `_cnputs` 热路径里加锁或分配内存。

## 文件索引

| 路径 | 作用 |
|---|---|
| `ios-santoni/xnu/patches/0253-wt86047-log-to-system-v2.patch` | 内核侧实现（改 `bsd/dev/wt86047_sdhci.c` 两处） |
| `ios-santoni/xnu/patches/0142-wt86047-sdhci-emmc-driver.patch` | 扇区常量与写入栅栏 `wt86047_sdhci_write_log_blocks()` |
| `ios-santoni/tools/sweet-log-pull.sh` | recovery 取回：先读 512 B 头，再按头里的环大小读全环 |
| `ios-santoni/tools/sweet-log.py` | 解码：`--list` / `--boot N` / `--ring-size`，兼容 v1/SANTONI/SWEET |
| `ios-santoni/tests/test_wt86047_log_decode.py` | 解码器主机测试（合成 v2/v1/空区/CRC 错/wrap/16 MiB 回环） |
| `ios-santoni/tests/test_wt86047_log_ring.py` | 环形模型测试（跨启动连续、wrap、暂存溢出、resume 定位） |
| `ios-port` `scripts/build-shim.sh` | `--rd-emmc-ro-log` 档位、`PERSISTLOG=1` |
| `ios-port` `runlog/2026-11-22-bootlog-system-v2.*` | 真机两次启动的样本（`.txt` 已解码，`.bin.gz` 原始区段；`runlog/` 在 `.gitignore` 里，仅本机留存） |

两个测试已接入 `ios-santoni/tests/run_tests.sh`，`python3 tests/test_wt86047_log_*.py` 可单独跑。

## 实测记录（2026-11-22 03:39，本通道首次上机）

```
# WT86047LOG2: version 2, ring 16384 KiB, total 45577, flush #5, boot 2,
#               last flush at 48.956 s, header CRC ok
boot 1 cold,      resume 0x0,    carried 11381 B, lost 0 B
boot 2 continued, resume 0x5927, carried 11381 B, lost 0 B
```

两次启动的失败形态完全一致，可作为当前卡点的基线：

| 项 | boot 1 | boot 2 |
|---|---|---|
| 共享缓存子映射 | 43 个全部成功 | 43 个全部成功 |
| `no shared region` / `Library not loaded` | 0 次 | 0 次 |
| SIGILL | `pc=0x214bf0900 lr=0x214c2e784 i=4e284840 pp=9e8f3 pp2=0` | `pc=0x221a50900 lr=0x221a8e784 i=4e284840 pp=9e8f0 pp2=0` |
| pid 1 | `exit reason namespace 2 subcode 0x4`（SIGILL） | 同 |
| 发生时刻 | 开机后 < 39.9 s | 同 |
