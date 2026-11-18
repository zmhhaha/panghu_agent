# 图片学习：真实渲染与源码保全验证

2026-11-18。本记录只覆盖真实 PNG 渲染、源码与回执保全，以及未渲染作品的操作边界；上线、浏览器验收、独立视觉评审、迁移与技能发布由各自后续记录说明。

完整证据见 [JSON 回执](evidence/picture-learning-render-validation-20261118.json)。执行代码来自完整 Git 源码提交 `70aa9065e4904bbc4c16ec1fa11768cce842ab81`，使用构建后的执行器镜像 `video-animation-creation:latest@sha256:c533a680dc457ff9208e6700a173ef5258d297b86ee1f25af1dc21410a87eec2`。后续操作边界修复为 `7abec1d898d5b11431f89c11ec95df57563c5655`；两次提交之间，图片渲染器、图片宿主、共享绘制运行时与 HyperFrames 工程运行时文件均无差异。

## 真实 PNG 验证

在 Linux 服务器上运行真实隔离渲染器，调用真实 HyperFrames `renderSeek(0)`，生成 PNG；随后重复寻帧并重新加载，比较三次结果。没有启动模型任务，也没有修改生产执行数据。测试先复制现有准入账本和对应证据到独立验收目录，原目录只读挂载。

| 作品 | 尺寸与能力 | 实际结果 |
| --- | --- | --- |
| 历史水墨单帧源码 | 1800 × 906，Canvas 2D | PNG 与历史文件 SHA256 完全相同，逐像素相同，RGB 平均绝对差均为 0 |
| Canvas 技术样例 | 640 × 360，Canvas 2D | 生成 PNG；可见原生 2D 上下文、重复寻帧、重新加载与停止回执检查通过 |
| Three.js 技术样例 | 640 × 360，Three.js | 生成 PNG；真实 WebGL2 / SwiftShader，`context_lost=false`、`gl_error=0`，重复寻帧、重新加载与停止回执检查通过 |

三个样例共用固定渲染镜像 `video-motion-renderer:latest@sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b`，总执行时间 18.17 秒。水墨文件 SHA256 为 `46dc89107546748c60bd44410349b41756711a18088d74fde030df73b7d123dd`。这证明该份历史源码经过新图片渲染链路后没有产生画面回归，不代表已经完成新的水墨技法优化。

## 新作者源码的实际渲染

另将一个已停止作者任务导出的确切输入与结果交给同一图片渲染器。先校验输入、结果及源码身份，再执行一次渲染；没有改写作者源码，没有增加作者调用。实际生成 640 × 360 Canvas PNG，重复寻帧、重新加载、源码下载一致性和停止证明通过。

- 源码身份：`a57473412e49c3ecae950f57f9ca126c52e5900c89ea738b767bc1f8a418a580`。
- PNG 身份：`3f8dfde7ab3ac9394e39d6d10066f90e4237fd7c6954ccc62f41413a1bacd798`。
- 独立证据目录：服务器 `candidate-picture-author-r1/render-proof/`，包含 PNG、源码、渲染回执和绑定证明。

此项只证明该作者生成的源码可被真实渲染。参考图片是否实际到达模型，由对应 Agent 候选执行回执证明；画面复现程度与可迁移性需要实际对照评审，不能由 PNG 存在推断。

## 未渲染作品的操作边界

仅完成源码、随后渲染失败的候选可以下载源码和技术重试。服务端拒绝把它作为有图基线继续优化，返回 `409 picture_learning_baseline_not_rendered`；也拒绝采用为默认图片，返回 `409 picture_learning_candidate_not_rendered`。不再因缺少图像摘要发生内部异常。

针对该边界、正常采用和技术重试的三项测试通过，执行命令与结果写入 JSON 证据。测试确认无额外作者提交，失败作品和原状态得到保留。

## 保存位置与结论范围

实际文件保存在服务器 `/root/video-releases/picture-learning-20261118/render-acceptance-r1/output/`，以及本机 `D:/codex-work/archives/video-service/20261118/picture-learning-render-acceptance/`。新作者的 PNG、输入、结果、源码与回执另存于本机 `D:/codex-work/archives/video-service/20261118/picture-learning-author-candidate/`。

本次没有人工艺术认可，没有验证连续运动或声音，没有执行迁移验证，也没有发布 WikiSkill。渲染成功、作者看到参考、评审意见、开发者采用和知识发布是各自独立的事实。
