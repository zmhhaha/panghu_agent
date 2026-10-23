# Parse-only 前端增量验证（2026-10-22）

本记录只对应前端本地测试与构建，不覆盖或替代 `RESULTS.md` 中上一轮真实浏览器历史证据。

## 本轮实现

- 接受真实 AssetDTO `parse_status`、`text_summary:string|null`、`parse_error:string|null`。等待、解析中、正在停止、完成、失败单独显示中文，不把材料完成当作演示稿完成。
- 摘要只在 ready（兼容旧 parsed）状态显示，使用 React 转义纯文本并截断到 2000 字符。null、来源移除、失败或退回等待时不显示旧摘要。
- 解析错误采用明确允许清单的中文建议，包含扫描 PDF、页数、文本/压缩/图片限制、不支持、损坏与超时等；未知 code 不显示原值、路径、堆栈、token 或 lease。
- 资料标签页对实际 queued/parsing/stopping 项目每 5 秒读取 GET project；请求不重叠。隐藏页面、切工作区、切项目、卸载会清理 timer 并 abort 请求；迟到响应不更新状态。回到可见页面读取真实快照。完成/失败/资源移除和权限错误后停止。
- 手动“刷新状态”保留；解析轮询不请求版本列表，也不触发任何规划、生成、修改或模型任务。解析修订更新不会清空未保存的演示要求。
- 开发通知明确为“制稿执行器尚未接通，不会调用模型”。只在真实文件状态出现 parsing/stopping/ready/failed 后显示隔离本地沙箱说明；旧 queued 状态不静态声称解析能力已接通。正式 SSO、PowerPoint 人工验收门和制稿 503 保护保持原样。

## 实际验证结果

在 web 目录使用捆绑 Node / pnpm 显式绝对路径，命令见上级 README。

- `pnpm test`：**4 files / 41 tests passed**，最终运行 3.79 秒。原 27 项保留，新增 14 项覆盖解析状态转换、真实失败/停止、轮询重载与隐藏清理、迟到响应、未知错误过滤、2000 字符上限、摘要转义与移除、旧 queued 不宣称已接、未保存要求保留。
- `pnpm run build`：**TypeScript + Vite passed**，最终 Vite 2.95 秒。
- 冻结输出：`dist/index.html` 引用 `assets/index-Cb8shtpo.js`（274.33 kB / gzip 86.03 kB）、`assets/index-C0RgSHL_.css`（15.78 kB / gzip 4.32 kB）。

仅修改 web 源码、测试和文档。本轮没有 SSH、站点更新、容器操作、模型调用或真实解析浏览器集成；没有新增 managed background job。等待父任务发布 migration 2 / parse runner 后再做端到端验证。旧刷新脚本固定上一轮构建 hash，不应直接用于本轮部署。
