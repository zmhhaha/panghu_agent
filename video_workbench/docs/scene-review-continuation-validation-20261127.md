# 6.1 一次有界续验的实际结果

2026-11-27，用户接受历史未知用量例外后，已完成一次新的独立评审。
部署和请求限额已实测，但此场景被质量门控拒绝，没有正式导出；OpenSpec
`improve-video-spatial-rendering-foundation` 的 **6.1 保持未完成**。

## 实际发布范围

从完整源码构建 `latest` 镜像，以 digest、源码与部署身份区分发行物。
API、模型代理及 Workbench 已实际滚动更新并核验 Ready；dispatcher、executor
保留原镜像。原 112 个准入绑定保留，当前共 113 个；原宿主、池、调用者及配置保留。

| 角色 | 源码提交 | 实际镜像 digest |
| --- | --- | --- |
| API / 模型代理 | `9c2cad6bd3ee9b05881871f697f94785ae5e93e3` | `sha256:b45be0e7336b3b64fb8f9cfac0c984f51aeaaee4f5e16bcf6c0d73e031522c57` |
| Workbench | `9f613e58a2fd97fe1392662719975da4dc4cac17` | `sha256:75060c55661e507e05f155e1e2a26d0babc920048d2957d7a305b569a13b4a6e` |
| CPU executor，保留原版 | 原有冻结发行物 | `sha256:99ba59c573ab4ebc2672d8f4b05a1b17227ca942790aa0103d43e9a4cba7fe0a` |

Workbench 刻意使用不含新 Three 宿主的独立源码版本，避免修改已有影片准入所绑定的
宿主字节。4.1 的 Three 适配、本地原生验证与本次 6.1 服务发布是分别记录的范围，
不能把前者写成已经生产准入。

模型代理仅新建 `agent_model_request_limits` 表，没有执行整库初始化。旧 19 表、
169069 个可见元组及结构在窄 DDL 前后保持一致。代理保留自己的原 ConfigMap，
没有误用 API 的资源目录配置；Workbench 原凭据保留。

## 唯一执行及实际消费

原 run `4ac2f4ca-22de-4b0a-b8ba-0abac3f13e36`、source-recovery parent
`6a935d07-cb98-4684-90f4-22c2917dfd15` 的失败、取消及账本均保留。
新 run 为 `a7b49db0-55ed-40e3-a216-6fc3cf69d10a`，唯一新 reviewer 为
`2d6d7b2f-e55d-43f7-b94f-6aa1ddd8ac9d`。复用原作者、固定 source/plan、seed
`631907` 及已有预览，没有新增作者或自动修画。

新增授权为一小时、一个评审任务、最多八次供应商请求、每次输出最多 8192 token、
600 秒新增 CPU 渲染、最多一次导出、零 repair。服务端实际返回的完整权威用量证据
绑定全部 attempt 和请求，每个实际请求均证明执行 `8 / 8192` 限额。

| 实际项目 | 结果 |
| --- | ---: |
| 新评审 job / attempt | 1 / 1 |
| 供应商请求 | 3 |
| 输入 / 输出 token | 59110 / 1044 |
| 新增总 token | 60154 |
| 新增作者 / repair | 0 / 0 |
| 新增渲染 / 正式导出 | 0 / 0 |
| 全谱系不同 Agent job | 6，上限 7 |

新增 token 用量完整；供应商没有报告货币价格，不能据此声称货币成本已知。
旧 reviewer 的未知用量也没有补零或覆盖，原场景 100 万 token 总上限仍无法核实。
`262144` 只是新账本的准入预留及已知用量停止阈值，不是含输入的硬 token 上限。

## 质量拒绝

新 reviewer 正常完成，得分 **83**，提出三项意见：同一 frame 0 的静图与短片样帧
形体疑似不同、尖峰与折面重复导致切纸感、云雾遮住共同山脚。现有门控同时要求
分数达到配置值且 `issues` 为空；本轮虽超过 80 分，仍因三项意见被拒绝。
`stop_recommended=false` 不等于通过，也不授权无额度的修画。
这是当前执行器对无严重度分级 `string[] issues` 的实际判定，不是规格要求所有
艺术建议都清零，也不表示评审中的疑似结构差异已经被程序证实。

实际终态为 `failed / scene_space_preview_quality_not_met`；场景已停止，
`draining=false`。没有通过取消产物、修改分数阈值或清空意见来授权导出。
剩余请求次数或未到期的窗口不能再次创建评审、grant 或重试本轮业务。
该评审仅收到关键图片，没有连续观看视频或音轨，也没有取得用户艺术认可。

同帧意见已另做零模型核查：实际送评静图为 838118 字节、SHA `42942632…`；
实际 clip 样帧为 547661 字节、SHA `3b134caa…`，与完成运输回执准确匹配。
后者未包含在临时工作区 TAR 中，已从同一实际 Workbench Pod 的持久 artifact
只读取回，另存本地并核验完整 SHA/长度；没有把它冒充旧归档成员。

两路的 source、完整 SceneSpace 侧车、计划和 runtime identity 一致。实际宿主静图
截 PNG，clip 截 quality 88 JPEG，`renderer.frames` 返回原 JPEG 后再转 RGB PNG
送评，因而文件摘要不同。实际送评两图 RGB 平均绝对差为 **1.361 / 255**，PSNR
42.40 dB；主峰与小松旁台阶两处的平滑强边距离第 99 百分位均为 0 像素。
这些结果及原图查看未建立所称大幅形变的证据，差异符合有损编码路径。
这里没有保存 clip 编码前的原始 framebuffer，不能宣称测得未压缩像素完全相等；
像素诊断也不证明艺术质量。原 review、三条意见和失败门控均保持原样。

## 终态收集与保留

独立只读核验了三个原 renderer 的准确 container ID，实际 Docker GET 均返回 404，
并匹配 queue、claim、public outcome 和冻结文件摘要；原作者、旧 reviewer、新
reviewer 的准确 worker Pod UID 均已物理不存在。这些证据独立于无导出状态及归档
成功，不以 journal 的 `stop_proven` 字段单独代替物理验停。

首版观察器因打印参数重名发生 TypeError 并退出 1；原错误、代码和日志完整保留。
观察器故障没有重新提交业务。修复后的只读观察器仅跟踪同一 run、同一期限，
退出 0、stderr 为空，并收齐真实终态；自身没有模型或业务 POST。

归档包含 406 个临时工作区普通文件、40 个控制日志文件。远端归档前、TAR 与归档后
inventory 一致，本地下载逐文件长度及 SHA-256 核验通过。旧独立 TAR 中 355 个
文件逐字保留，原六条业务记录未变。本 run 没有正式导出 MP4；归档保留历史队列
和预览短片。此范围不能扩大成所有 `/data` 持久产物原字节均已封存；实际 clip
样帧 PNG 的另外取证与原工作区 TAR 分别记录，不回写旧归档。

- 工作区 TAR：`d40f0dd6c4c9c7e6036bafaa64c0f9ccc8dfc9cdd9819deafe81f21d3932f01b`
- 控制日志 TAR：`8b2f4d29d36d3687889739dca36345dfcb32e936f20842ee4e5aae024d758844`
- 原 355 文件 TAR：`04bd7772c9d696fbc87d3f99104053ed4e7a777c11e4435cb68719699c997a6e`

另以原先固定的 before 做严格历史比较，没有重新采集或覆盖基线。58 个数据库
仍为 58 个，9210 条旧记录全部保留，恰好新增 4 条由真实提交及主键独立绑定的
授权记录；原严格 comparator 与业务序列检查均通过，零失败、零序列变化及例外。
before SHA 为 `5388f4efd02e5928ea5ed9222aaa154c919e9ab334099363cf996a8d80e7d8b5`，
after SHA 为 `7993b516a6733de1e74a7e97afc91421b2beb53415cf8b1212b2ef2c2941bb01`。
这项历史检查绑定旧字段/原始行指纹与路径集合，`media_files_read=0`，不代表重新
逐字节读取了全库历史媒体。预览和送评图片的字节核验由前述独立证据提供。

完整操作证据位于 `D:/codex-work/operations/video-next-work-20261127/`，
源码仓保留配套摘要 [scene-review-continuation-20261127.json](evidence/scene-review-continuation-20261127.json)。
原预览短片仅为两秒、48 帧；它不充当目标三秒、72 帧的正式导出证据。

本轮没有合 main、发布 WikiSkill、开展换内容验证或恢复暂停的五分钟影片。
后续若要再次执行模型、修画或正式导出，需要另行明确目标与可用预算；本次
一次性 continuation 已使用，不能递归续期或换身份重置。
