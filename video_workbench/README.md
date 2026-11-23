# 动态视频工作台

2026-11-24主线合并纳入视频学习及场景空间/共享几何实现；源码、历史运行记录和未完成
范围见[合并验证](docs/mainline-scene-merge-20261124.md)。本页后续旧日期的设计补充保留
当时语境，不覆盖新的[当前架构检查点](docs/current-architecture.md)。

video-service 管理总文稿、整体方案、项目统一音色、资源、动画工程、候选、采用和交付；创作由通用 Agent / Pi 完成。开发者效果库积累完整表达片段，通过显式发版生成分层 WikiSkill，正式制作按需读取固定版本，为新内容创作。

[当前架构与实施边界](docs/current-architecture.md)是当前实现入口，[文档导航](docs/README.md)区分操作指南、能力合同和历史验收。[统一设计](docs/agent-creation-effect-library-plan.md)记录完整目标；未完成任务以根仓库映射的 OpenSpec 项目为准。旧文档中的静态逐镜工作流、早期分支、任务计数和部署版本不覆盖当前状态。

## 工作台入口

| 使用者 | 入口 | 用途 |
| --- | --- | --- |
| 视频用户 | [动态视频工作台](https://video-service.panghuer.top/) | 文稿、整体方案、配音、制作、试听与交付 |
| 开发者 | [动画效果库](https://video-service.panghuer.top/dev/animations/) | 创作完整效果、比较、反馈和继续实验 |
| 开发者 | [知识库](https://video-service.panghuer.top/dev/animations/knowledge.html) | 手动发版、已生成 WikiSkill 的版本浏览与搜索 |
| 外部 Agent | [结构化 JSON API](docs/animation-lab-agent-api.md) | 提交实验、读取状态、反馈、取消及下载，无需模拟网页表单 |

知识库浏览与正式制作消费同一份发布产物，保留最近五个成功版本。发布机制已实现；最近一次真实整库业务编译超时，尚不能称为新整库成功发布或新视频已经采用，详见[真实结果](docs/wikiskill-reader-validation-20261116.md)。

## 技术关系

- **画面创作**：HTML/CSS/SVG、Canvas 2D、Three.js；Manim 由 Agent 编写 Python 后预制透明帧。
- **动作编排**：原生 JavaScript、GSAP、Anime.js，统一服从绝对时间。
- **渲染底座**：HyperFrames 项目与时钟；Playwright/Chromium 绘制实际帧，FFmpeg 编码与混音。实验室已有源码通过显式桥接消费，正式新流程使用整片共享工程。
- **公共服务**：agent-service / Pi 和用户 pi-cage、llm-service trusted 路由、独立 speech-service。视频服务组织业务及证据，不自行加载语音模型。

[通用协作](docs/general-creative-collaboration.md)将职责依赖、预算与调度和具体绘制能力分开。代码提供隔离、资源、时钟和检查约定，Agent 决定构图和动作；协作或技术通过不自动证明艺术效果更好。

## 使用流程

1. 创建项目，填写文稿、受众与目标，并选择项目音色。
2. 审阅并确认整体方案；正式批次固定方案及可用的 WikiSkill 版本。
3. 制作依赖资源，用真实旁白时长安排讲解段，再由 Agent 创作模块和整片工程。
4. 查看草稿、问题与制作历史；试听并核对实际时序，采用满意版本后交付。
5. 正片默认一轮自动打磨，用户最多追加两轮；之后按明确问题和有限预算修改。开发者效果库可持续优化，不受正片轮次限制。

项目默认不叠加逐句字幕，画面承担关键表达。实际开放能力取决于服务端配置与已准入执行组合；未就绪时明确报告，不借用旧角色或模拟结果。

## 本地运行与测试

基础项目页面可以在仓库根目录启动：

```sh
python -m pip install -r video_workbench/requirements.txt
python -m video_workbench --local
```

打开 `http://127.0.0.1:8089`。本地模式使用固定开发身份并只绑定回环地址；没有配置相关服务时，不代表可以生成动画或配音。效果库独立进程配置见[启动参考](docs/methods-only-lab-startup.md)，当前外部接口见[Agent API](docs/animation-lab-agent-api.md)。旧启动参考中的逐方法发布及角色版本须按现行整库方案与实际部署核对。

测试按改动范围运行，例如动作标签相关检查：

```sh
python -m pytest tests/video_workbench/test_animation_motion_evidence.py tests/video_workbench/test_animation_authoring_tools.py -q
node --test tests/video_workbench/animation-authoring-tools-ui.test.mjs
```

测试临时目录放在预留磁盘，不写入应用发布源码。夹具、单元测试、真实模型执行和人工视听验收分别留证；不要把各批重叠测试数相加成一次完整验收。

## 构建、部署与数据

服务器从已推送的干净 Git 提交同步完整源码，使用普通 Dockerfile 构建，不拼接已部署应用代码。应用镜像使用 `latest`，发布身份记录在 Git revision、镜像 labels、部署 annotations 与固定 digest 中，详见[源码构建](docs/source-build-and-release.md)。只构建本次实际需要更新的服务，未更改的共享 Worker 或 renderer 不因文档修改重建。

[部署参考](deploy/README.md)保留身份代理、内部网络、持久卷及启动参数说明；实际集群配置和发布收据优先于早期示例。业务数据库、媒体、冻结资源和仍被任务引用的技能版本属于持久数据，不能按临时文件清理。原始构建上下文、重复源码检出和测试缓存经确认不再使用后可回收；清理须记录边界与保留的证据位置。

当前已知缺口包括真实整库编译与正式采用、关键画面前置门控、正片打磨真实闭环、完整视听审阅和部分资源补做链路，见[当前架构](docs/current-architecture.md)。可信用量/费用与生产存储生命周期由共享框架统一承接。

## 2026-11-21 文档整理补充

> 本节来源：文档提交 `03369402067c5cc7fcd9571d3af7d9c81e4a39b2`（`codex/organize-video-design-docs`），其运行及交付事实对应视频分支 `faaa09affca3205ecfa46b2204f8d6aa2a017362` 的历史证据；不表示该分支新增实现已并入当前主线，也不表示当前部署状态。原主线正文按原记录日期保留。

已有 WikiSkill 第 2 版的实际发布与消费，以及《雨霁江渚》五分钟静音试片的技术交付：300 秒、1280×720、24 fps、7200 帧，完整解码和静音验证通过，见[交付说明](../docs/codex-work/operations/ink-long-video-20261120/formal/attempt8/DELIVERY.md)。原正文中关于较早整库失败或未有新发布的文字，保留为记录时点的历史结论。

试片的艺术质量尚未获认可，完整连续人工审美播放审查未完成；不得把本次技术交付扩大为所有学习、声音或创作目标通过，也不据此声明当前集群镜像状态。

[通用空间、分层、自动镜头及渐进学习设计](docs/scene-space-layer-camera-design.md)尚未实现或部署。新增归档及来源、版本、验证范围见[本仓库设计档案](../docs/codex-work/README.md)。
