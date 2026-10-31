# 视频工作台：后续事项

更新：2026-10-31（Asia/Shanghai）。当前开发分支 `codex/video-audio-production` 已部署音频第一阶段，**尚未合主线**。当前结果见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，接口与功能边界见 [AUDIO_PRODUCTION.md](AUDIO_PRODUCTION.md)。

八镜头真实配音与 206 秒 MP4 的独立技术检查已通过，VPS 34m 上传、浏览器试听／采用／恢复和真实音频时钟跨镜播放已通过；工作台主动重启后的 8 音频／MP4 摘要、SQLite、原项目及进程权限检查也已通过。新 Pod 为 `video-workbench-6454f4845f-bj52n`，镜像摘要不变、异常重启 0，无需重新生成。原第三／六镜的失败及修复过程保留，见 [VALIDATION.md](VALIDATION.md)。

最终 19,122,821B 归档已下载并安全解包至 `D:\github\armbianbegin\.runtime\video-audio-production\evidence-final\`，SHA-256 `64cbe74652f62513504df98d4fa2ea483d5400fdfecd69fe02d2409b7269bec0`；成片为 `live-audio/dsh-narrated.mp4`。OpenSpec 已同步并验证通过，19 项完成 14 项，剩余 1.1 整稿听感、4.2 术语人工试听及 5.1–5.3 BGM。

1. 用户试听完整云健 `zm_yunjian` 技术稿，复核自然度、术语和停顿，人工检查 44 条 estimated 字幕。字幕时间／文本与 timeline 一致已经验证，听感和人工校对尚未获认可，不宣称强制对齐。
2. 后续实现 BGM／混音，补充全服务音频容量管理与删除流程。
3. 按需要评估动画渲染方案。当前 WeasyPrint 输出静态画面，不保留 CSS 动画。

独立分支尚未合主线，合主线仍需另行确认；后续不自动重复 TTS 或视觉 Agent 任务。

## 历史记录：2026-10-30 静态素材首版

以下待办保留当时语境；配音和 MP4 已进入上述音频开发阶段，不再属于“尚未开始”的方向。

更新：2026-10-30。当前事实见 [CURRENT_STATUS.md](CURRENT_STATUS.md)。用户已批准，功能分支 `codex/svg-video-workbench` 已快进合入 `panghu_agent` 的 `main`。本期实现与合并均已完成。

真实制作链路、浏览器控制、包含新 HTML 的最终备份、证据归档与验收环境清理均已完成。累计生产制作提交 5 次、成功 4 次、原 HTML 失败 1 次，另有独立取消任务 1 次；全部历史失败与人工修复记录保留。现有制作、发布和验收任务无需重复执行。

1. 后续优化播放器的画布缩放：完整 1280×720 HTML 可读，但固定画布在 1280×656 的播放器区域会裁掉底部 footer，自适应缩放尚未实现。上一／下一、10 秒配置下自动切镜和暂停后手动切镜已实测通过，不能写成所有视口均无裁切。
2. 后续按需评估配音、动画时间轴、MP4，以及成本下降后的图片生成与 ComfyUI；这些不属于本期已完成范围。

公开 Jobs 的 usage 为 null，没有可信可得的计费用量和单价，费用记为未知。运行超时和精确槽位释放时间未实测，保留为验证边界，不宣称通过。
