# 音频先行的原生整片制作编排

`ProductionRunStore(..., film_creation=FilmCreationWorkflow(...))` 是新整片模式的显式接入点；未注入 facade 的历史路径保持原合同。新模式必须同时通过新 Agent profile、Skill adapter 与整片 renderer 的准入检查，不会把旧 motion 渲染器作为回退。

## 冻结与执行顺序

1. 新建 run 前验证项目归属、方案版本和工作状态，并在 SQLite 写事务外执行新能力 readiness。冻结 `renderer_mode=hyperframes-film` 及稳定 `configuration_sha256`；配置未准入时不创建任何配音或动画子任务。
2. 按已确认方案检查计划时长和固定停顿预算，超过当前 60 秒上限直接拒绝。计划预算是早期限制，不能替代录音实测。
3. 项目沿用统一音色，先依次取得所有旁白 WAV 候选，验证原字节、文本绑定和真实时长；不采用媒体头、不重写文稿。
4. 所有录音齐全后冻结一个 film，包含原录音版本、完整内容与 24fps 量化后的全片时钟。实测总量再次检查 60 秒限制，超限明确结束，不裁尾、不拉伸或切回每镜视频拼接。
5. 按影片顺序依次提交原生模块任务。模块目标和 namespace 来自 run 的固定目标清单；后镜只接收已完成前镜的精确版本及验证后的前任信息。记录模块包摘要，后续迁移和最终组装前再验证，不能用当前头替换前任。
6. 全部模块收集后，只提交一个整片 render 子任务；一个项目工程、一个时钟、一次整片编码，没有每镜 MP4。

每次新子任务准入前复核冻结配置。配置变化会停止后续制作，但已有任务仍按原稳定 request key 查询和取消，不因新准入关闭而遗失已付费的执行。

## 持久性与对外状态

run 的每个 intent 在调用子任务前写入 SQLite，只保存固定 revision、request key 和完整请求摘要。完整 film 只保存一次，模块源码、Skill 证据和整片 snapshot 分别由子任务存储，避免在最多 24 段的 run 文档中重复展开。响应丢失后先通过 request key 找回同一子任务；上游状态未知时持续跟踪，不另起任务。

取消会持久记录请求，调用原子任务的取消接口，并等待原任务证明已停止。`stop_proven` 不成立时，终态文字不能单独解除资源阻塞。只有相应候选或媒体通过本地合同验证，才能推进下一阶段；这些完成状态不代表用户认可、采用、试听通过或技能发布。

公开 run 增加 `renderer_mode`、`draft_render_source` 和每段的 `module_operation_id`、`module_version_id`、`module_status`、`module_engine`、`module_sha256`。新模块不冒充旧 `motion_version_id`。`draft_render_id` 指向整片 render，应用根据 `draft_render_source=hyperframes-film` 使用新 facade 的视频接口。已存在 run 不补写新模式；缺乏冻结输入的历史摘要只能显示 `current=false`，不能从当前项目补造完成身份。

## 验证范围

本地组合回归使用真实业务 SQLite、录音存储、模块验证与整片组装，注入明确的已停止 Agent 输出 fixture 和非媒体 render fixture。覆盖录音先行、精确前任、原 WAV 字节保留、只有一个整片 render、幂等响应恢复、配置变更、计划及实测时长限制、取消等待停止和不修改旧媒体头。它证明编排与持久边界，不构成真实模型、浏览器成片或用户认可证据。
