# ios-port —— 红米2 (wt86047 / MSM8916) 移植 iOS 工作区

> 本文件是 2026-11-21 的旧入口，部分红线与补丁说明已过期。行为规范见 [AGENTS.md](AGENTS.md)，当前状态、命令与踩坑见 [PROJECT-GUIDE.md](PROJECT-GUIDE.md)。

主方案（历史归档）：**[docs/archive/PLAN-msm8916-v3.md](docs/archive/PLAN-msm8916-v3.md)**
硬件实测：**[board/hw-facts.md](board/hw-facts.md)**

## 一句话现状

ios-santoni（redmi 4X / MSM8937）已经把该踩的坑踩完了，红米2 和它的同构度比原方案估计的高：**GICv2 地址逐字节相同、eMMC 控制器地址逐字节相同、触控同为 FT5x06、屏幕同为 720×1280**。真正的两个新增问题是：**没有 PSCI（多核起不来，先单核）** 和 **帧缓冲基址不同（0x83000000）**。

## 三条命令

```bash
bash  scripts/check-device.sh          # 设备体检（只读，不写 flash）
bash  scripts/fetch-santoni.sh         # 拉上游源码
python3 scripts/port-patches.py        # 生成 patches-8916/（236 个补丁改名）
```

然后再：

```bash
bash scripts/build-shim.sh             # 只编 shim，先验证串口/屏幕
fastboot boot build/wt86047/shim/out/shim-boot.img
```

## 红线

- **只 `fastboot boot`，不 `flash`、不 `erase`。** 断电重启就回到 MIUI。
- 阶段 2 写 `userdata` 放 iOS rootfs 会清空 Android 用户数据，那是唯一一步真动数据的，动手前单独确认。
- `0143` 持久化日志补丁会写 `system` 分区开头 4MB，**默认禁用**。
- `emmcrw=1` 是唯一允许写 userfs 的开关，阶段 1 不带。

## 目录

```
board/           WT86047.h（XNU 板级头）、hw-facts.md（DTB 实测证据）
patches-8916/    236 个已移植补丁 + manifest.csv + MANUAL-ADJUST.md
ios-santoni/     上游源码，只读参考，别在里面改
scripts/         dtb_probe / port-patches / check-device / fetch-santoni / build-shim
build/           编译产物（build-shim.sh 生成）
```

## 环境要求

| 干什么 | 需要什么 |
|---|---|
| 读 DTB、改补丁、设备体检 | 任意系统 + Python 3（本机 Windows 就行） |
| 编 shim | clang + llvm-objcopy/ld.lld（Linux/WSL2/macOS） |
| 编 XNU | **macOS + Xcode 16.0**，没 Mac 就用仓库自带的 GitHub Actions |

---

## 本地仓库（2026-11-21 建立）

`ios-port/` 现在是一个独立的 git 仓库，用来管理**移植工程本身**（脚本、板级参数、文档、补丁导入副本）。

### 纳入版本管理

| 路径 | 内容 |
|---|---|
| `scripts/` | 打包、校验、dry-run 等工具 |
| `board/` | 板级头文件、DTB、硬件事实记录 |
| `patches-8916/` | 239 个补丁的**导入副本**（原始来源） |
| `docs/archive/STATE.md` | 逐轮实测记录（已归档，仅作历史查证） |
| `docs/archive/HANDOFF-阶段2.md` | 阶段交接报告（已归档） |
| `docs/archive/PLAN-msm8916-v3.md` | 总体计划（已归档） |
| `ios-santoni.rev` | 固定 XNU 仓库的 remote / branch / commit |

### 不入库（可由命令重建，或体积过大）

`build/`、`kernel-out*/`、`runlog/`、`upstream/`、`ios-santoni/`、`*.img`、`*.zip`

### 关于两个补丁副本

- `patches-8916/`：原始导入副本，作参考与回溯用。
- `ios-santoni/xnu/patches/`：**构建实际使用的**副本，权威版本在
  `git@github.com:spasmodiczz/ios-redmi2-msm8916.git`。

改补丁时以 `ios-santoni/xnu/patches/` 为准；如果两边出现差异，以 `ios-santoni` 为准，
并同步回 `patches-8916/`。

### 每次改动补丁后的固定动作

```bash
# 1) 结构校验（hunk 计数）
python build/wt86047/tools/check_patch.py ios-santoni/xnu/patches/XXXX-*.patch
# 2) 本地 dry-run（不要拿 CI 当编译器）
bash scripts/dryrun-patch.sh ios-santoni/xnu/patches/XXXX-*.patch
# 3) 更新 pin
cd ios-santoni && printf 'repo: %s\nbranch: main\ncommit: %s\npatches: %s\n' \
  "git@github.com:spasmodiczz/ios-redmi2-msm8916.git" \
  "$(git rev-parse HEAD)" "$(git ls-files 'xnu/patches/*.patch' | wc -l)" > ../ios-santoni.rev
```
