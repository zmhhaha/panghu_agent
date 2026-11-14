# 正式整片模块的原版本修订输入

2026-11-14。本切片只补充 `FilmCreationStore.start_module` 的输入前提：允许同一正式模块作者收到确切原模块后再创作。没有接入独立整片审阅、正片 1+2 额度、用户追加或需求列表，没有生成实际动画或部署，不能因此勾选 OpenSpec 3.5。

模块请求可增加 `base_module_version_id`，值必须是已完成的正式模块版本 ID。原有请求继续省略这个字段；不会自动补 `null`、规范化旧请求或改变其指纹。提供字段与省略字段是不同的完整请求，同一幂等键不能切换来源。

Store 在插入新操作前，通过原有 owner／项目授权和不可变 package 校验读取原版本，然后核对：

- 相同 owner、项目、镜头和完整 film 身份，包括原声版本、摘要、实测时长、全片排程与语义。
- 相同当前镜头序号、module ID、namespace 和 transition target。
- 相同确切 runtime identity，原模块绘制引擎仍被当前运行准入允许。
- 原模块实际声明使用的每个资源仍存在，且资源摘要相同。

兼容后，完整 `package.result.module` 原样进入现有 `prepare_core_task(base_module=...)`：所有文件原文、文件摘要、入口、生命周期和 entry／exit 状态均保留。不会改源码、重命名、转换旧协议、提取部分文件、自动总结或截断。操作内部另外保存来源 version／package／input／result 摘要，来源请求与完整原模块都随子意图持久化。

现有冻结角色 `video.animation-film-module.v1` 已经明确支持 `base_module`，所以本次不修改角色、提示词资源、工具目录或 renderer。现有工作流直接透传请求字典，没有独立的 start-module HTTP DTO 需要放开；正式工作台也没有新增用户入口。原作者仍只使用 `read_inputs`、`skill_search`、`skill_read` 和 `write`。

`base_module` 是原场景模块，不是整份业务 package，也不包含该场景以前的 incoming transition 模块。当前前任仍由既有完整外观参考提供；作者按确切当前前任重新给出边界或转场。此切片不能证明转场保持、原版本修订效果或边界视觉连续。

原版本必须与此次请求使用相同 film 和模块目标。若后续业务新建 run 时重新分配了 module ID 或 namespace，不能静默改写旧模块来适配；正式修订协调器需要保留适用的原目标，或者显式按不同创作任务处理。此处也不会跨音频、跨工程或跨 runtime 自动迁移。

原模块的完整字节在 `start_module` 构造 core 时接受 v1 的 4 MiB 输入校验，同时在新操作预占前遵守现有 `SkillProductionAdapter` 更严格的 **2 MiB core_task 上限**。真实有效范围取两者约束，不能只看角色上限；JSON 转义、完整 film、前任参考和原模块都计入预算。超过上限直接 413，不创建新子意图，不冻结 Skill、不提交 Agent，也不削减原来源。测试分别使用有效原模块触发 4 MiB 角色边界和 2 MiB 适配器边界。加入确切 Skill 目录后的最终输入仍由 `prepare_module` 按原协议校验；在当前 2 MiB core 和 1 MiB 目录上限下，不能伪造一个“合法core＋合法目录却超过4MiB”的实际案例。

历史请求与恢复保持原语义：省略新字段的原请求按原指纹重放；已冻结的修订操作使用自己的完整 base 输入恢复，未知提交仍仅找回原任务，不重新读取原版本或再次提交。来源后来不可用，不会让已准入的原输入悄悄变化。

测试使用真实 Store、冻结 v1 校验器、工作流和 HTTP 客户端配合明确的合成 peer；验证完整输入传输、幂等、恢复、权限／兼容拒绝和真实字节预算。它们不调用外部模型、不执行动画、不构成审美或视听验收。

验证：`python -B -m pytest -q -p no:cacheprovider tests/video_workbench/test_film_creation_revision_input.py tests/video_workbench/test_film_creation_workflow.py tests/video_workbench/test_film_creation_concurrency.py tests/video_workbench/test_film_module_gateway.py tests/video_workbench/test_creative_film_module_contract.py --basetemp D:/fria2`，116 passed；仅有既存的 Starlette TestClient 弃用提示。
