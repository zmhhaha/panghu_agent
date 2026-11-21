# panghu_agent 的 codex-work 设计整理

日期：2026-11-21。本次将已整理文档集成到主线，集成基线为 `4c322af063f442036961f776461d7616f5659e5c`。来源文档提交为 `03369402067c5cc7fcd9571d3af7d9c81e4a39b2`（`codex/organize-video-design-docs`），来源视频实现基线为 `faaa09affca3205ecfa46b2204f8d6aa2a017362`。本次只合入文档，不合入视频分支新增运行实现，不改依赖、部署或效果/知识发布。

## 先读这些

- [通用场景空间、分层、全自动镜头与渐进学习设计](../../video_workbench/docs/scene-space-layer-camera-design.md)：已讨论确认的待实施设计。
- [图片学习](../../video_workbench/docs/picture-learning-library.md)、[视频学习](../../video_workbench/docs/video-learning-library.md)、[三库与效果库设计](../../video_workbench/docs/reference-learning-libraries-design.md)、[通用创作协作](../../video_workbench/docs/general-creative-collaboration.md)。
- [全部来源与主题目录](CATALOG.md) · [来源及摘要清单](manifest.json) · [原始资料引用](SOURCE-REFERENCES.md)。

## 主线集成与历史边界

- 共 59 条原来源：5 份正式设计、54 份历史副本、0 份假定主线已有的文档引用。原先仅映射的输入查看验收文档在主线尚不存在，现作为历史副本归档；来源路径与原哈希保留。
- 原主线的三个导航页与通用协作指南正文完整保留，只追加本次日期说明及新设计入口。四份此前不在主线的设计指南附来源版本说明；其中“当前”“已上线”和代码定位描述来源分支，不是本次主线能力声明。
- 来源视频分支已有 WikiSkill 第 2 版与五分钟静音影片的真实交付记录，见[交付说明](operations/ink-long-video-20261120/formal/attempt8/DELIVERY.md)。这不证明该运行实现已合入当前主线，也不表示当前集群状态。技术交付不表示水墨艺术质量获认可，也不表示已完成人工连续五分钟审美播放。
- 场景世界、对象级网格与分层、统一自动镜头及渐进式空间/镜头学习仍是待实施设计；复制文档不会使其成为已上线能力。
- 旧命令、旧镜像身份和旧准备状态只供历史追溯，不能直接执行。来源分支中的其他文档、API 合同与机器回执未递归导入，缺失引用以稳定锚点保存在原始资料引用表。
- 有效规格沿用主仓库映射的 OpenSpec 服务，项目 `ec663f5a-53d3-4a0e-9037-933eb788d183`，`https://openspec.panghuer.top/mcp`。本次不修改服务规格。
- 原文件、旧场景工作树的 Git 暂存状态和原科学分支的未提交 README 保留。`ink-landscape` 独立仓库资料未导入；没有复制视频、截图、数据库、凭据或原始模型输出。
