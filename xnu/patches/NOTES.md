# patches/ 说明

- `*.patch` 由 dxb 按文件名排序依次套用，编号即顺序，**新增补丁取当前最大号 +1，不复用旧号**。
- `*.sh` 由 dxb 在套用补丁**之前**从 XNU 源码根目录运行（例如 `0026-stage-hfs.sh` 负责拉取
  `apple-oss-distributions/hfs` 的 `31d5ec2`）。
- 本目录下的其他文件（如本文件）不参与构建，可安全用于文档或触发一次重建。

## 重建与排错

`push` 只在 `xnu/patches/**` 与 `.github/workflows/xnu.yml` 变动时触发，**空提交不会触发**。

判读一次构建看两处：`编译错误: 0` 与 `构建完成标记: True`，二者同时成立才算成功。

若产物列表为空而编译错误为 0，且 `build.log.gz` 里只有下载进度条与
`curl: (56) Recv failure: Connection reset by peer`，说明失败发生在**拉取上游源码**阶段，
与补丁无关，重跑即可（本文件 2026-11-28 的一次提交即用于此）。
