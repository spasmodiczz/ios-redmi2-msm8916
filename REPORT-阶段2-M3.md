# 阶段 2 报告：XNU 在 MSM8916 上进入用户态（M3）

**日期**：2026-11-21
**设备**：Redmi 2 / wt86047（MSM8916，4×Cortex-A53，无 PSCI）
**内核**：XNU 8796.101.5，243 个补丁，`WT86047` / `DEVELOPMENT`
**上机形态**：`fastboot boot`，纯内存运行，不写任何分区

---

## 一、结论

1. **M3 达成。** XNU 完整启动到用户态：`load_init_program` 成功加载 `/sbin/launchd`，
   PID 1 静态二进制运行并周期性输出心跳。全程无 panic、无复位。
2. **eMMC 读通路验证通过。** SDHCI 控制器完成 eMMC 枚举（CID/CSD/EXT_CSD 全部读出，
   容量 15028 MiB），GPT 按分区名解析成功，字符设备上的**字节偏移 `pread` 返回真实数据**。
3. **root 仍为 mockfs（内存盘）。** eMMC 上的 `userdata` 是 **ext4**，补丁集只有 HFS+
   支持，因此"eMMC 当 root"必须先写入一个 HFS+ 文件系统。该步骤尚未执行。

---

## 二、里程碑时间线（2026-11-21）

| 时间 | 里程碑 | 决定性证据 |
|---|---|---|
| 05:40 | 显示链路闭环 | 文字从屏幕左上角起笔、单行有序、长行不截断 |
| 11:07 | M2：platform expert + `AppleCLCD` | `SWEET: platform expert registered`、`AppleCLCD 720x320 … at 0x831fa400` |
| 12:05 | 定位 `ml_wait_max_cpus` 死等 | 三次上机二分：`kperf_init_early done` 之前的检查点缺失 |
| 16:15 | 定位 `socd_client_trace` 空线程崩 | 固定带回溯 → `debug_is_current_cpu_in_panic_state + 0xb` |
| 17:00 | 修掉 `ml_set_max_cpus` 早期取锁 | `lr = _ml_set_max_cpus + 0x17`、`far = 0x158` |
| 17:35 | **M3：用户态 PID 1** | `*** SANTONI userspace: init running as PID 1 ***` |
| 17:55 | eMMC 读通路验证 | `read eMMC LBA 2 (off 1024): c0 09 0b 00 e3 11 2c 00` |

---

## 三、本轮四个关键修复

### 3.1 `0242` —— 不再在 `arm_init()` 里取互斥锁

**现象**：内核在 `SWEET EARLY 3` 之后立即崩，寄存器转储 `pc` 落在异常向量，
`far = 0x158`，`esr = 0x96000005`（同 EL 数据中止，一级转换错误）。

**定位**：`lr = 0xfffffff007449f48` → `symbolize.py` 解为 `_ml_set_max_cpus + 0x17`。
即崩溃发生在我上一版为解除死等而加的 `ml_set_max_cpus(1)` 调用内部。

**根因**：`ml_set_max_cpus()` 首句是 `lck_mtx_lock(&max_cpus_lock)`，而 `arm_init()`
运行在互斥锁与线程机制建立之前，早期取锁必然缺页。

**修复**：撤销 `0010` 里的调用；改在 `ml_wait_max_cpus()` 内部处理 ——
`machine_info.max_cpus <= 1` 时直接返回，既不等也不碰锁。
`kperf_init_early()` 忽略返回值，`max_cpus_initialized` 没有其他读者。

### 3.2 `0241` —— socd trace 在无线程时不读 per-CPU

`socd_client_trace()` 每个 tracepoint 都会调
`debug_is_current_cpu_in_panic_state()` → `current_debugger_state()`（`PERCPU_GET`）
→ `current_percpu_base()` → `current_thread()->machine.pcpu_data_base`（偏移 `0x220`）。
启动早期 `TPIDR_EL1` 无值，`current_thread()` 为 NULL。

修复：入口处直接读 `TPIDR_EL1`（`mrs`，与上游 `current_thread_fast()` 编译结果一致），
为零则直接返回。**该修复在本轮才第一次被执行到并确认有效。**

### 3.3 `0243` —— 触摸日志刷屏

`bsd/dev/wt86047_sdhci.c` 里两处打印：`DIAG: rpm …` 每 2 秒一条、
`I2C NACK … (total 11xxx)` 的 `total` 无限增长。两者合计占满 18 行窗口，
所有内核与 init 输出都被挤掉。

修复：DIAG 只在最初 6 秒打印，NACK 只打前 5 次；**轮询本身保留**，
将来触摸修好无需再打补丁。该版本已构建成功，**尚未上机**。

### 3.4 `0239` + `0240` —— panic 摘要写进固定带

日志窗口仅 18 行，panic 的寄存器转储占 15 行，任何普通打印都会被顶掉。
新增 `sweet_early_fixed(line, s)`，把 2 行 16 px 文字画进缓冲区底部 32 px
的阶段标记带（分页器不触碰该区域）。用 2 行真字形而非 4 行折叠字形 ——
折叠会削掉一半笔画，照片上无法辨认。

---

## 四、M3 的证据链

**屏幕输出（顺序）**

```
load_init_program: attempting to load /sbin/launchd
****************************
*** SANTONI userspace: init running as PID 1 ***
SANTONI userspace: read eMMC LBA 2 (off 1024): c0 09 0b 00 e3 11 2c 00
SANTONI init: still alive (tick)
```

**关键判据**：`still alive (tick)` 字符串**不在内核镜像中**
（对 17.5 MB 镜像做全字节搜索，`SANTONI` 出现 0 次）。它来自
`build/wt86047/tools/santoni-init.S`，由 mockfs 作为 `/sbin/launchd` 提供给 PID 1。

该文件的注释明确了这个测试的意图：跑起来即证明
**exec、用户/内核边界、devfs、控制台**四者可用。因此这不是内核自己的日志，
而是真实用户态进程的输出 —— M3 成立。

---

## 五、eMMC 通路事实

### 5.1 SDHCI 枚举（截图 2）

```
CMD1 ready: OCR bit31 set (card power-up complete)
CMD2 ALL_SEND_CID  → CID 0x13014e52…
CMD3 SET_RELATIVE_ADDR → R1 0x00000500 (state 2)
CMD9 SEND_CSD      → CSD 0xd07f0132
eMMC MID 0x13 CBX 0x01 OID 0x4e PNM "R1J96M" rev 1.0
CMD7 SELECT_CARD RCA 2 → card selected (TRAN)
CMD8 SEND_EXT_CSD  → sec_count 30777344 (15028 MiB)
switch GXC to 25 MHz
```

CID/CSD/EXT_CSD 均由设备真实回传，容量与 16 GB 相符，卡已进入 TRAN 态。
**这不是"探测失败后继续跑"，控制器与卡都工作正常。**

### 5.2 设备语义

`0142` 的注释：`One GPT partition is found by name (boot-arg emmcroot=, default "userdata")
and exposed as the BSD disk emmcroot0 (remmcroot0 character device)`。

因此 `/dev/remmcroot0` 是**按名字选中的 `userdata` 分区**，不是整块盘。
`pread(offset 1024)` 读到的是**分区内偏移 1024**。

### 5.3 字节解读

`c0 09 0b 00 e3 11 2c 00`（小端）：

| 字段 | 偏移 | 值 | 含义 |
|---|---|---|---|
| `s_inodes_count` | 1024+0 | 723,392 | ext4 inode 总数 |
| `s_blocks_count_lo` | 1024+4 | 2,888,163 | 4 KB 块数 ≈ 11.02 GiB |

数值量级互相印证，与十六 GB 机型 userdata 容量一致。

**由此确立**：GPT 解析正确、字节偏移寻址正确、返回的是真实磁盘数据。
`userdata` 上是 ext4。

---

## 六、当前系统能力清单

| 能力 | 状态 | 说明 |
|---|---|---|
| 显示 / 控制台 | ✅ | 720×320 可见（缓冲区 960–1279 行），88 列，24bpp，G/R/B |
| 中断 / 定时器 | ✅ | GIC 正常，100 Hz 周期中断 |
| MMU / pmap | ✅ | 内核虚拟地址空间正常 |
| platform expert | ✅ | 设备树平台层注册完成 |
| 用户态 exec | ✅ | PID 1 运行，`write`/`open`/`pread` 可用 |
| mockfs root | ✅ | 内存盘作根，`/sbin/launchd` 来自 init 镜像 |
| eMMC 读 | ✅ | SDHCI 枚举成功；GPT 解析；偏移 `pread` 正确 |
| eMMC 写 | ❓ | 未测试 |
| 文件系统挂载 | ❌ | 分区是 ext4，XNU 无驱动；HFS+ 未写入 |
| 触摸 | ❌ | I2C 地址 0x38 写寄存器 0xbb 持续 NACK |
| GPU | ⚠️ | `IOAcceleratorES` 空实现，软件合成可用 |
| SMP | ❌ | 无 PSCI，仅 cpu0；`max_cpus` 按 1 处理 |
| 电源/电池 | ⚠️ | 静态兜底值，未接真实 gauge |

---

## 七、遗留问题与风险

1. **触摸 I2C 无效**：0x38 地址写 0xbb 寄存器持续 NACK。可能原因：I2C 总线/时钟未使能、
   上拉缺失、或该机型触摸地址不同。与 root 无关，但影响可用性。
2. **日志窗口极小**：可见 320 行、文本 18 行。任何高频打印都会吃掉历史。
   `0243` 已压掉主要噪音，后续新增驱动必须遵守"有界打印"。
3. **固定带第二行被覆盖**：`sweet_early_mark` 的彩色块覆盖底部 32 px 整条，
   导致 `BT` 的地址行看不见（只有 `pc/sp` 一行可读）。需要时再改 mark 的绘制宽度。
4. **`0243` 未上机**：仅构建通过。
5. **init 能力有限**：自写汇编，只有 open/pread/write/getpid/select。
   后续需要更完整的用户态程序才能验证 dyld、frameworks。
6. **HFS+ 未验证**：`0026-sweet-builtin-hfs` 存在但从未在真机挂载过。

---

## 八、下一步候选（均未执行）

| 路线 | 内容 | 风险 | 产出 |
|---|---|---|---|
| A | 把 init 探测偏移改为 1080，读 `53 ef`（ext4 魔数） | 只读，零风险 | 把 ext4 结论从 95% 提到确定 |
| B | 生成 HFS+ 镜像 → 写入某分区 → `rd=emmcroot0` 启动 | **破坏性**，覆盖该分区 | 真正的持久化 root |
| C | 转驱动：触摸 I2C、GPU、电源 | 低 | 用户态更完整，但 root 仍为内存盘 |

选择 B 时需要先确定目标分区（`userdata` 与驱动默认值一致，但会抹掉 Android 数据；
`cache` 等小分区破坏面小，需同时传 `emmcroot=<分区名>`）。建议先导出一份
`by-name` 分区表（名称/偏移/大小）再定。

---

## 九、复现步骤

```bash
# 1) 取内核（HTTPS + 代理，SSH 在本机不稳）
cd D:/01_手机与刷机/红米2/ios-port/ios-santoni
HTTPS_PROXY=http://127.0.0.1:7897 git fetch -f \
  https://github.com/spasmodiczz/ios-redmi2-msm8916.git artifacts
(cd . && git show FETCH_HEAD:kernel.development.wt86047) > ../kernel-out27/kernel.development.wt86047

# 2) 打包（内核作为 payload，注意绝对路径）
cd ..
wsl -d Ubuntu-26.04 -- bash -lc \
  "cd /mnt/d/01_手机与刷机/红米2/ios-port && \
   bash scripts/build-shim.sh /mnt/d/01_手机与刷机/红米2/ios-port/kernel-out27/kernel.development.wt86047"

# 3) 上机（需先长按电源 10 秒 → 音量下 + 电源 进入 fastboot）
fastboot boot build/wt86047/shim/out/shim-boot.img
```

**判读**：屏幕底部 32 px 彩色带上方为日志区；panic 时摘要写在彩色带内。
符号化用 `python scripts/symbolize.py --image <内核> <地址…>`。

---

## 十、索引

| 文件 | 内容 |
|---|---|
| `CHECKPOINT.md` | 接续入口（当前状态、硬规则、必读文档） |
| `STATE.md` | 逐轮实测记录（第九~十二节最关键） |
| `HANDOFF-阶段2.md` | 阶段 2 完整交接（硬件事实、构建链路、补丁结构） |
| `.workbuddy/memory/MEMORY.md` | 长期要点与硬规则 |
| `.workbuddy/memory/2026-11-21.md` | 当天踩坑记录 |

**补丁**：243 个，`ios-santoni/xnu/patches/`；权威版本在
`git@github.com:spasmodiczz/ios-redmi2-msm8916.git`。
关键提交：`6aef2b3`（`0242`）、`16287bc`（`0243`）、`853d056`（`0241`）、
`32cf187`（`0240`）、`841859f`（`0240` 修正尾部假空行）。
