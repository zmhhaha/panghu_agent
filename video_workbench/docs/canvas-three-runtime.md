# Canvas / Three.js 自由创作接入

本轮对应 OpenSpec `evolve-video-agent-creation-and-effect-library` 的 5.1、5.2。动画仍由通用 Agent / Pi 写完整源码；工程只提供可用资源、隔离渲染、时间接口和验证，不新增固定动画模板。渲染暂留 video-service，不启动公共服务拆分或多图层改造。

## 两条运行链

- 实验室：冻结实际能力与资源 → Agent 创作 → 独立 host 实拍 → 检查真实上下文、确定性与媒体 → 展示样片。Canvas / Three 使用 render request / receipt v2；历史 HTML/SVG v1 保持原有意义。
- 正式视频：单独冻结正式 renderer 能力 → production input/result v2 → creative-scene v3 → 实测旁白时钟驱动绝对 seek → H.264 导出。实验室与正式 host 身份不同，不能共用 host 摘要冒认通过。

共享 `motion_renderer/engine_runtime.mjs` 负责原生 Canvas2D / WebGL2 上下文测量与启动配置；Three 使用固定本地 0.170.0 模块及 MIT 许可，软件 SwiftShader 渲染，无外网依赖或 GPU 要求。源文件仍限 scene.html/js/css，可自主编排 HTML/SVG 与对应画布，不固定表现形式。

## 放开能力的条件

实验室默认只公布 HTML/SVG。离线验证可显式指定候选引擎，但不会在 HTTP 可用能力中出现。`--engine-admission-file` 必须指向匹配实际镜像、host、共享模块、资产的准入文件；每个新增引擎的记录必须能回查原渲染账本中通过的真实 v2 证据，之后才公布给创作者。

正式业务通过 `VIDEO_CREATIVE_RUNTIME_CAPABILITIES_FILE` 接收独立验收后生成的能力表，核对正式 runtime 的镜像、host、共享模块与资源摘要。不能把实验室能力表直接用于正式入口。已开始的委托和正式任务保留冻结的能力，不随扩容改变输入或重放身份。

## 验收范围

1. Canvas 真实 2D 上下文；Three 真实 WebGL2、SwiftShader、无 context loss / GL error。
2. 完整逐帧捕获、反向与中间位置 seek、重复 seek、静止稳定、重新加载后重复画面一致。
3. 原有无外网、无 worker/frame/自主媒体、容器只读与资源限制继续有效；错误引擎声明不能取得通过证据。
4. 实验室与正式视频各自预览和 MP4 全解码，正式音频 SHA 与实测时钟不变。
5. 新 Agent 包、镜像和工具组合重新做真实候选准入。技术通过不等于艺术效果已获认可，也不替用户发布方法。

## 实施状态

接口和代码已开始并行接入。先前 31 项 renderer 保管与历史回读检查通过；加入新引擎反标记、seek 篡改与运行模块绑定检查后 40 项通过。真实 Chromium 与最终镜像验收、部署结果将在本文件后续补充；当前不据此勾选 OpenSpec 5.1 / 5.2。
