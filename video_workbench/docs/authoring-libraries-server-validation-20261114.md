# Manim / Anime.js 服务器接入验证（2026-11-14）

## 能力与发布范围

动画效果库已开放 Anime.js 4.5.0 和 Manim Community 0.22.1。两者均由通用 Agent 自主创作，最后进入同一个 HyperFrames 时间线；平台代码提供时钟、隔离、资源和验证合同。

| 能力 | 作用 | 当前边界 |
| --- | --- | --- |
| Anime.js | 辅助 HTML/SVG、Canvas、Three.js 的运动编排 | 固定本地 ESM 和暂停适配器，HyperFrames 绝对定位；不新增绘制引擎或第二时钟 |
| Manim | Agent 原创 Python 经 Cairo 产生透明 PNG 帧，再由工程消费 | 独立 recipe 角色及离线资源执行；最多 60 帧、640×360 示例；无 TeX/MathTex |

效果库可用、技术通过、实际样片、开发者效果／迁移认可、Skill 发布和正式视频采用分别记录。此次接入不会发布候选 Skill。普通正式视频消费者仍排除需要 Manim producer 的方法，直到其资源再生链单独准入；不能将本次任务的 PNG 包作为通用方法发布。

## 实际部署

- 普通干净 Git 构建；镜像仅使用 `latest`，实际 Deployment 固定 digest，并以标签和注解记录源码。没有运行中覆盖源码。
- 基础镜像与 renderer 来自 `0b6913b53f8af155d4336ed64a5fdca1427bfb8a`；creation、workbench、motion 的兼容性接线来自 `9fab624b8a8029da722198736b727573566c6f37`。
- 四业务部署验证回执为 `authoring-apps-rollout-manim-r1/completed.json`，状态 `deployed_verified`。原项目、冻结记录、媒体、旧凭据与整片配置保持；新增独立 Manim 凭据挂载。
- Manim 角色使用 `36872c3ef70782546d043059e2f405a0ce861ffe` 的完整源码镜像；真实 source-only r3 取得双产物并完成停止与清理后激活，旧角色和数据库记录保留。
- 宿主机访问 Agent Service 受到原网络策略限制。部署预检和原任务查询改为从现有 creation Pod 内走实际业务路径，没有放开网络策略。

## LLM 协议阻断与修复

Manim r2 暴露上游 Responses 返回的未知 token-details 扩展对象被误拒。原失败请求保留为 uncertain，不声称免费或成功。修复继续严格校验主计数和已知数值明细，保留未知 JSON 扩展且不计入主汇总；没有修改模型路由或放宽主计数校验。

隔离分支完整源码 `152cdded8dd5ce2382976b6900f9adad844cf186` 的 155 项回归通过，正常构建并部署 `llm-service:latest@sha256:5ccd604ef305a0d040d6be8f8127b232ad5b6bf734ea98da0d1f1e5ef72f1f5d`。Manim r3 后续六次实际模型请求成功，证明原阻断已解除；不据此改变视频业务任务的费用统计归属。

## 技术与实际样片证据

基础技术准入已分别验证 Anime、HTML/SVG、Canvas、Three/WebGL2 的 96 帧探针，以及 Cairo 48 帧资源和 72 帧 HyperFrames 资源工程。检查包括正／反／乱序／重复定位、空闲稳定、重新加载、半开资源窗口和隔离资源清理。这些探针不冒充 Agent 原创作品或审美认可。

实际业务通过正常效果库 API 分别提交一次初始委托，保留唯一请求、原 Agent 源码、资源、原始 MP4、时钟和停止证据；不手改生成动画。

- Anime 委托：`6b719f65-6aca-4433-95ae-a350f84aa4d3`，目标为“冲击波与群体节奏”。
- Manim 委托：`a66253f0-2e2c-4da8-a367-e5c17166b67b`，目标为“剪切变形中的面积不变”。

Manim 委托已完成：真实 4 秒／96 帧 MP4 SHA256 为 `2bf7ce3ed024eeda3119c654b0c58275f2fd159b4da6d61e9ad71dbc430c4108`。独立核验原 Agent 源码、60 帧透明资源、v5 清单、HyperFrames 0.8.149 和最终解码全部一致；有限边界帧未发现窗口外残留，原任务均已停止清理。高度尺、细线和 b/h 标记偏暗、偏小，不能将技术出片当作目标效果已经获得认可。

Anime 首次委托被 `method_absolute_seek_mismatch` 正确拒收，没有 MP4 或候选。原源码每次重置 Anime 管理的 tween 对象，再执行 seek，破坏了同帧与历史无关的要求。纯状态诊断能复现，原桥接字节未改变；诊断内存里的修改不冒充 Agent 作品。原失败与原预算保留。另以新有限预算、`retry_of_commission_id` 关联同效果，启动修正委托 `5a5ffbd5-a9ae-47a8-b5c9-c53158c8c64b`；仅提供状态所有权反馈，让 Agent 自主重新创作并内化到 Skill 草稿，没有传入手改动画源码。修正结果以最终采集为准，不循环新增任务。

原声复用的 workbench／motion 已进一步升级到 `7633be283f3dd8158e1a60479b8fb54646a57f21`、镜像 `video-workbench:latest@sha256:65c3b69d7f1b34c6cc3a6139f94c4d76a12ebb7619d84f03648694eb8663c499`。creation／lab 保持原部署，原整片配置不变；通过真实 HTTP 字段和 readiness 检查。该升级不生成语音或媒体。

浏览器安全检查服务当前不可用，页面验收尚未完成；服务器 API／部署核验不替代页面验收。

## 原声复用与跨镜连续性

原项目 `6f2cbbdc-754a-4069-8c0f-5447cea35973` 的新对照 run 为 `06163975-8b2a-4122-b8ff-ed584c137e9f`，复用原 run `663b838d-091d-4378-8813-586d301f72c2` 的两段 WAV。新增恰好两个模块任务、两个模块版本和一个整片渲染；没有新增方案、TTS、录音版本或独立镜头 MP4 拼接，原项目和旧记录逐项保持。

新 MP4 SHA256 为 `0303bc49c6ebdce34d695316f763358c3b4e3870559d05cab76a0f38eb36de43`，单个 HyperFrames 工程输出 1280×720、24fps、16 秒、384 帧。独立完整视频／音频解码通过，原录音字节与 snapshot 一致；最终 AAC 的两段双声道相关度分别为 0.9999868、0.9999858，RMS 比为 0.99910、0.99916，共 768000 个样点。

第二个 Agent 的输入包含第一个 Agent 原样的完整模块文件、文件摘要及退出状态，最终 snapshot 也逐字保留两份原创源码。独立核验者与主 Agent 实际检查切镜前后第 168–215 帧的连续窗口，以及第 191、192、193 帧原尺寸图。12 个圆片的位置、大小、顺序及点／环／加号／斜线纹样连续，细描边、下方柔影、米白背景、通道底色及边线未跳变；随后开口逐渐扩大，圆片逐步松散，没有换物或黑帧。这支持本轮 5.3 的连续编排与真实边界评价完成，不能推广成任意内容都不会出现断裂。

技术证据为 `appearance-continuity-audio-reuse-business-r1/collection.json` 与独立 `validation.json`、`visual-sequence-review.json`，分别保留原记录和实际观察范围。本样片只有简单 2D 表面，未验收 PBR 材质、任意转场、完整声音理解或真人效果认可。整片独立视听审阅／一次修订及用户追加额度闭环仍未完成。
