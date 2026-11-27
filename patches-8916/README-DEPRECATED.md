# 本目录已废弃，不要用它覆盖 `xnu/patches/`

**快照时间**：2026-11-20 21:27
**当前唯一真源**：`ios-santoni/xnu/patches/`（236 个 `.patch` + 3 个 `.sh`）
**生成方向**：`ios-santoni/xnu/patches/` → `patches-8916/`，由 `scripts/port-patches.py` 单向生成。

---

## 为什么标废弃

`docs/archive/STATE.md` 第 325 行与 `docs/archive/PLAN-msm8916-v3.md` 第 162 行原先写的是「把 `patches-8916/` 应用到 fork」
或「把 `patches-8916/` 覆盖到 `xnu/patches/`」。这两句表述是**错误的，且执行后果严重**：

1. 全项目**不存在**任何 `apply-patches` 步骤。CI（`.github/workflows/xnu.yml:115`）只做
   `cp xnu/patches/*.patch xnu/patches/*.sh dxb/patches/`，读的就是 `ios-santoni/xnu/patches/`。
2. `scripts/port-patches.py:26-28` 的 `SRC`/`DST` 明确了方向是 `xnu/patches` → `patches-8916`。
3. 因此本目录是**产物**，不是输入。
4. 这份快照停在 2026-11-20 21:27，之后阶段 1 的 12 小时修复全部只落在 `xnu/patches/`。实测差异：

| 补丁 | 本目录（旧） | `xnu/patches/`（现行） |
|---|---|---|
| `0098` | `early_fb = 0x90001000`、`EARLY_H 1280` | `0x831FA400`（含 `WT86047_FB_X_BIAS`）、`EARLY_H 320`、`EARLY_COLS 88` |
| `0012` | `slot[1] = 0x90200000` | `0x83200000` |
| `0065` | `panic("SWEET CPU: cpu=%u NOT alive...")` | `IOLog` + `KERN_FAILURE` |

**一旦按旧文档覆盖，`fastboot boot` 会回到「日志写进无人看的 RAM」的状态，阶段 1 全部成果归零。**

## 允许的使用方式

- 只读查阅：想知道某个补丁在移植前长什么样、`manifest.csv` 的动作分类。
- 对照审计：`grep` 某个常量在旧快照里的原值。
- 重新生成（**唯一**写入方式）：

  ```bash
  python3 scripts/port-patches.py     # 从 xnu/patches 重新生成本目录
  ```

  注意：重新生成会按 `SANTONI→WT86047` 等规则做文本替换，**不要把重新生成的结果再拷回 `xnu/patches/`**
  —— 现行补丁已经是手工修过的 8916 版本，再过一遍机械替换会破坏它们。

## 如果只是想核对补丁完整性

用真正的校验器，不用本目录：

```bash
cd ios-santoni
python3 tools/check_patch.py xnu/patches/0142-wt86047-sdhci-emmc-driver.patch
```
