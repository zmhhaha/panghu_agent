# 视频学习失败修复与源码恢复

2026-11-25，对应映射 OpenSpec 变更 `repair-video-learning-execution-failures`。

## 修复范围

学习宿主的准备/导航窗口上限改为 180 秒，seek/截图上限为 120 秒，且每个阶段和编码都受真实剩余执行截止限制。Driver 在总额度中预留诊断和清理时间，保持现有 CPU/内存限制，不循环重试截图。新失败回执保存阶段、子阶段、帧、耗时、页面错误和崩溃信息；清理前读取身份匹配的容器退出/OOM 状态。

严格空间/JSON 校验继续生效。空间错误附带安全字段路径，指导明确精确复制来源摘要、证据闭包和变换后边界规则。创建输入附带严格 JSON 输出清单；Worker 收集失败可产生有界分类与行列诊断事件，事件入口和 Store 共用固定词表验证，不把原始源码或异常文本送入事件。

Worker 诊断生产者使用新 worker 镜像时仍需独立准入。不能把旧镜像的 accepted 状态挪给新镜像。创建输入的防错清单随 executor 发布即可供现有已准入 worker 读取；Worker 诊断生产者是否部署必须另按发布证据报告。

## 显式源码恢复

私有、owner-scoped `POST /api/video-lab/projects/<id>/recover-source` 接收：

- 原失败 experiment/operation 与精确 input 摘要。
- `origin={kind,sha256}`：既有 candidate、可重新收集校验的作者结果，或明确标注的 operator reconstruction。
- 完整严格有效的 `source`、`method`、修复原因和有限时间额度。

入口要求原任务已停止、仍为项目最新任务，且属于复现的 author/render 阶段；检查 owner、源操作、原摘要及现有能力约束。第一版不支持 transfer、资源/探索计划或非空可执行 scene plan 的恢复。

每次恢复是用户明确要求的新任务，冻结新 runtime，保持原目标、参考时间/字节、比较基线和知识快照。它记录单独恢复预算及原失败 lineage，严格校验源码/方法，直接进入渲染与独立评审。不会虚构作者 Agent 作业、改写原失败、追加作者生成、自动采用或发布知识。一次请求按 request ID 和完整 body 幂等；未知结果只核对同一编号。

保留的模型 write 提议不是已证明落盘的原 Worker 文件。由其修复的候选明确记录 `operator_reconstruction`，`origin_verified_by_host=false`，不能倒签原作业成功。

## 本次五项

逆光峰林、霓虹城市优先保持原源码；像素行星只修空间元数据的摘要、证据闭包和世界范围；水墨层次从保留写入提议修复严格 JSON 表达，保留解码后的画法；历史光环通过原源码实测定位到全局 `const top` 与浏览器 `Window.top` 冲突，仅重命名该局部三角顶点，原绝对 seek 保持不变。

本机单帧/正反 seek 验证仅证明局部可执行性，不能代替服务器资源准入或作品质量。最终恢复需要真实完整媒体、摘要/时长/帧率和独立评审；原失败、旧评分、采用状态和知识发布状态保留。
