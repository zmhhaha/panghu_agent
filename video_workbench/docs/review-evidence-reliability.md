# 无损关键帧与分级评审

对应独立 OpenSpec `improve-video-review-evidence-reliability`，项目
`ec663f5a-53d3-4a0e-9037-933eb788d183`。本轮先实现两个通用合同，并在新
SceneSpace 生产路径适配。原图片/视频学习、效果评审及已冻结影片没有静默升级。

## 无损采样

实际 renderer capability 必须声明准确的 `video.scene-space-sample-capture.v1`。
新 clip 的 `document.constraints.sample_capture` 冻结该策略、原 source/plan、范围、
宿主及执行身份，最多在首、中、尾三个去重绝对帧另截 PNG。视频帧继续 JPEG88，
H264 编码路径保持独立；不是把 JPEG 改个 PNG 后缀。

每张 PNG 最多 4 MiB、合计 12 MiB，纳入原 512 MiB 文件预算；额外截图和确定性
检查继续消耗原 CPU/时限。捕获记录固定 schema、绝对帧、相对帧、格式、null 质量、
字节长度及 SHA。HTTP frames v2 对照冻结 request、完成结果及 renderer 原文件，
检查原始 PNG、时钟和完整选帧集合；GET 不重画。无策略的请求仍走原 JPEG/v1 协议。
旧恢复从保存的 request/host/receipt/原文件读取，不用今日 HOST 或默认能力解释旧数据。

新生产端保留原 PNG 字节，重建评审时再次校验 capture 的完整字段、范围、相对帧及
摘要。冻结 `review_evidence` 关联 render ID、source/plan、绝对帧、捕获/保留/实际
送评摘要及 identity conversion。超过原模型运输 2 MiB/张或总量限制时，在提交模型前
停止并保留图像；新路径没有静默 JPEG 回退或缩小图片。

## 评审分级及门控

新 profile 为 `video.picture-learning-review.v2`，profile revision 仍为 `1`。
结果为 `video.picture-review-result.v2`，具有独立 `picture-review-v2` bundle、
运行环境、binding、caller 及准入身份。输入复用原文件运输外壳，必须包含服务器
冻结的 `constraints.review_policy`；旧 v1 bundle、validator 和原图片工具字节保持。

每个 required criterion 必须恰好评估一次，状态为 satisfied、violation 或 unresolved。
finding 必须引用已有 criterion 和实际送评 frame ID，记录 blocking/advisory、
observed/uncertain、观察与建议。必需违例对应 blocking/observed，必需未决对应
blocking/uncertain；optional 才能记 advisory。漏评、矛盾降级和虚构图片引用均拒绝。

默认必需项包括明确目标及风格、可见结构/比例/遮挡、声明光向的一致解释。
艺术风格属于明确目标时仍是必需项；可选项只表达已经满足目标后的细化建议。
程序原有的身份、source/plan、覆盖、时序、可信用量等检查独立保留，不由模型评分替代。

新 gate 由服务端重算：分数达到冻结阈值、全部 required satisfied、无 blocking，且
stop_recommended=false。仅有 advisory 可以进入下一阶段并保留建议，不单独触发
自动修画；低分、必需违例、未决问题或 stop 仍阻断。有界修正继续受原次数及预算约束。
实际图像运输回执仍必须覆盖所有提交图像摘要；模型不能自行给出权威 passed。

进入正式导出时，再次重建原评审输入并验证结果、门控与图片运输。导出请求绑定
profile、policy、source/plan、评审输入/结果/决策、原图来源及运输回执的摘要；建议及
结论不会只停留在可丢失的临时意见里。预览请求不提前携带尚不存在的评审批准。

## 实际选择与兼容

新配置必须同时满足 renderer 的准确 PNG capability 与已准入 v2 review 的真实
preflight。仅有新 renderer 而缺 v2 服务时停止，不降回 v1 假装完成新合同。
恢复按原配置中的 profile/revision、gate schema、policy 和 capture 策略分派；
不会根据模型返回的 schema 或今日 capability 猜测版本。

原 v1 仍按“score 达标且 issues 为空”判定，原 stop flag 含义不变。
旧失败评审不重分类，旧一次性 continuation 不重跑；该旧例外接口不扩展到新 v2。
旧未知用量、原预算及取消记录保持。新代码不能把旧 83 分评审补签成成功。

v2 使用独立 caller。Workbench 可配置以下参数；仅配置变量不构成运行准入：

- `VIDEO_SCENE_SPACE_REVIEW_V2_URL`
- `VIDEO_SCENE_SPACE_REVIEW_V2_TOKEN_FILE`（或已有 token 环境变量方式）
- `VIDEO_SCENE_SPACE_REVIEW_V2_PROFILE_REVISION`，当前值 `1`

原作者和 v1 reviewer 的调用方、凭据及历史轮询保持，v2 不借用旧 caller 的权限。
候选 catalog 新增项默认 route/pool/host disabled，无 acceptance、无 UID 分配，
保留既有 catalog 的完整前缀。必须独立验证新 worker/image、工具、原图运输及验停，
不能复制 v1 的接受报告来宣布 ready。

独立冷准入路径也安装原图片工具，并为新 v2 在真实 candidate job 中持久化
`max_requests=8 / max_output_tokens=8192`。连通性模型请求占这八次中的一次；
重启不能恢复额度，新增未知用量阻止后续 body。旧 v1 候选的配置与 wire 不改。
候选回执从原 SQL 请求/限额行及供应商 JSON/SSE 响应原字节重算消费，保留
实际图片运输；独立 v2 verifier 再检查运行身份和外部停止证据。
这不是伪造生产控制面 all-attempts/retire 证明；货币成本没有价格时仍为 unknown。
候选技术接受输出仍 disabled，不自动启用调用路由。

## 本地验证与限制

合同与集成测试覆盖 required 漏评/误降级、未决阻断、建议不修画、分数和 stop、
实际图片引用、证据篡改、PNG 超限前拒绝、导出重新绑定及旧 v1 恢复。
独立审查发现的 capture 恢复字段缺口与导出缺少 review 摘要均已补齐并回归。

真实 CPU 浏览器验证复用原固定 source `6dd43af0…`、完整 plan `0045b4ac…`、seed
`631907`，没有重新生成山体。48 帧 clip 的绝对帧 0/24/47 PNG 与对应静图的
RGB 像素和 PNG 原字节均一致；同一今日宿主下，与未选新采样策略的对照相比，
48 张 JPEG 视频帧及 H264 MP4 逐字节保持一致，完整解码通过。

该固定静镜头的三个 PNG 各 839162 字节，合计额外 2517486 字节；本次捕获墙钟
12.669 秒，对照 8.720 秒，差 3.949 秒。五个原生 case 共 40.733 秒；采样的进程族
CPU 下界 48.219 秒可能漏计已经退出的进程，不是完整 CPU 计费。另有实际浏览器
非零绝对帧范围的捕获测试，避免只验证 frame 0。

上述结果仅证明本地证据可靠性与兼容；不是模型已看完视频、作品变好、实际容器
准入或正式 6.1 成功。新 host 原字节与 Git 归一化后的字节相同；新 bundle 的
manifest 与资源 pin 也不受换行规范化影响。Python/测试脚本的换行差异另保留来源说明。

本轮没有使用旧 grant，没有发布 WikiSkill、合 main、换内容或恢复五分钟影片。
后续正式准入、CPU 端到端交付和具体效果认可分别记录，详见配套证据索引。
