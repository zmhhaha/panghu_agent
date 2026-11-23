# 图片学习 Agent 准入与控制服务上线验证

本次使用干净源码 `70aa9065e4904bbc4c16ec1fa11768cce842ab81` 完整构建的新镜像，实际完成图片作者、图片评审两个新身份的独立技术准入并启用。没有改变历史动画身份或其验收。可核对的脱敏证据见 [picture-learning-agent-validation-20261118.json](evidence/picture-learning-agent-validation-20261118.json)。

## 真实执行与观察边界

作者和评审分别执行一次，各产生 **4 个实际模型请求，共 8 个**，包括连通性请求；每次具有 8 请求的硬上限，不自动重试。两个候选均保留原始请求、工具结果、源码、控制器证据、原始 worker/proxy 的零重启退出现场，以及实际租约过期后拒绝访问的证据。

作者直接接收用户水墨参考图的原始 PNG：355,077 字节，SHA-256 `34874e709c5ae3540dd14e1ba6d40a7265a4a440c851676221f5e072aa955260`。作者产生原创 Canvas 源码，源码标识为 `a57473412e49c3ecae950f57f9ca126c52e5900c89ea738b767bc1f8a418a580`。该源码未经修改，经实际隔离渲染得到 640×360 PNG，SHA-256 `3f8dfde7ab3ac9394e39d6d10066f90e4237fd7c6954ccc62f41413a1bacd798`。实际 HyperFrames 绝对时间调用、重复捕获和重新载入的 PNG 一致，原渲染容器已停止并确认不存在。操作者随后接管 8 个原由隔离 UID 10001 持有的渲染文件，记录原始元数据并验证全部文件字节未变；该步骤没有重新渲染。

独立评审直接收到参考图与该实际 PNG 的原始像素。模型代理账本分别记录作者 2 次、评审 4 次已完成请求中的图片传输条目；重复条目来自同一实际图片随请求传输，不表示新增实验。图片传输证明对应字节进入完成的模型请求，不能证明模型理解。

评审给出 **66/100** 和 5 项具体问题：山峰偏规则尖锥、皴擦过于均匀、雾带过直等。这说明实际对照链路可以指出当前复现的不足，不能据此宣称已达到参考画质、人类审美认可或迁移验证。此次证据只覆盖静态画面；没有动态、音频或正式视频制作的验证，也没有发布 WikiSkill。

## 已上线身份与完整镜像

| 项目 | 实际值 |
| --- | --- |
| 作者身份 | `video.picture-learning-author.v1` |
| 评审身份 | `video.picture-learning-review.v1` |
| 两个身份的模型能力 | `text`、`tool_calls`、`vision` |
| 控制镜像 | `arm-cluster-master:5000/agent-service-control:latest@sha256:718c9b465effff817c61b8df2b67e190b89945bde34ddf5c9405827b7a40bb06` |
| Worker 镜像 | `arm-cluster-master:5000/agent-service-pi:latest@sha256:b9b9d363c769549be514a758dd086f515c39f801f877b16659e5363d4f0a9372` |
| 实际渲染镜像 | `arm-cluster-master:5000/video-motion-renderer:latest@sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b` |
| 专用调用身份 | `video-picture-learning`，只允许上述两个 profile |
| 私有 executor Secret | `video-animation-creation/picture-learning-agent-20261118`，键 `token` |

两个候选分别永久预留 UID `312606736` 起 3 个、`312606739` 起 3 个。正式新作者池使用 `312672256` 起 65,536 个 UID；新评审池使用 `312737792` 起 65,536 个 UID，均写入既有永久预留账本，不得复用。

只增加两个已准入的 route/pool 和专用 caller；逐项比对确认所有既有 task contract、bundle、工具环境、binding、route、pool、caller 记录保持原值。运行时新 Secret 在内存中保留全部旧 token 值和其他原有字段，只增加专用 token；脱敏证据仅记录比对结论，不保存密钥值。

API、dispatcher、model proxy 三个控制 Deployment 均已 Ready。随后从真实部署的 executor 内，使用新挂载的专用 token 调用控制服务 `/v1/capabilities`，得到 HTTP 200；调用身份只能看到两个图片 profile，两者 `configured=true`、`ready=true`、要求 `vision`，且均 `publication_authorized=false`。该检查没有模型请求。

## 可追溯产物

服务器操作根目录为 `/root/video-releases/picture-learning-20261118/`：

- `candidate-picture-author-r1/`、`candidate-picture-review-r1/`：原始执行与停止证据。
- `author-technical-admission/`、`review-technical-admission/`：独立报告及仍与组合绑定的 host 验收。
- `control-activation-r1/`：发版计划、永久 UID 回执、上线回执与实际 executor 发起的能力查询。

本地主机上的小型回执已存放在 `D:/codex-work/operations/picture-learning-20261118/`，PNG 与评审结果存放在 `D:/codex-work/archives/video-service/20261118/picture-learning-technical/`。这份记录覆盖 Agent 准入和控制层上线，业务页面完整实验循环的验收另行记录。
