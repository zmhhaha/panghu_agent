# 正片打磨与真实技能采用验证记录

2026-11-16。本记录区分本轮已经实现的代码、定向测试、尚未发布的改动以及仍待实际观察的效果。它不新增 OpenSpec 勾选，不代表已经合入主线或通过完整视听验收。Git 提交、镜像 digest、部署版本和最终整体验证结果以之后的实际发布记录为准。

## 当前状态

| 内容 | 已确认事实 | 仍需完成 |
| --- | --- | --- |
| 正式整片打磨 | 持久协调器、真实制作驱动、私有审阅代理、HTTP／页面／后台工作流代码和定向测试已完成；干净 Git 普通构建的三个镜像已部署，rollout 均成功，正式页面已实际打开 | 尚未运行真实自动轮次、用户追加轮次与明确需求闭环 |
| 整库 WikiSkill | 发起过首次真实编译，保留原请求及失败证据 | 本次编译失败，尚无可供此次正式新项目采用的新发布版本；读取协议修复正在进行 |
| 新正式验收项目 | 已通过正常工作台创建，身份与只读采集准备已确认 | 尚未统筹、采用方案或启动正式制作；不能声称已经检索、读取或采用新 WikiSkill |
| 原山石题材优化 | 新的 Canvas 山石作品执行已完成，媒体已收集 | 审美、笔墨表现与完整连续运动尚未验收；不能因为技术完成就认定有效提升 |
| 参考水墨片段 | 已复看参考视频约 21–28 秒的水墨表现，存在可借鉴的制作方向 | 可行性分析不等于已经实现同等质量，也不将原视频作为运行依赖 |
| 整体测试 | root 在干净 index 副本中运行的全集为 128 项 Python 通过、91 项 Node 通过；Python 用时 267.61 秒，含一条既有 Starlette 弃用 warning | 真实模型、浏览器、连续视听与部署验收仍需分别完成 |

本轮目标镜像涉及工作台、动画实验室与创作执行器。镜像使用 `latest`，发布身份通过 labels、部署 annotations 和 digest 记录；下方只记录已取得的真实收据。

正片实现的 21 个文件已提交并推送，Git revision 为 `f9ee7ad9cc68213d111a905bfff35a00c948974b`；此前 native 函数表达式与水墨调研提交 `1a5f5a6` 同时推送。三个 Deployment 的部署 annotation 均已绑定此 revision，workbench 显式启用 `VIDEO_PRODUCTION_REFINEMENT_ENABLED=1`；其余运行配置保持，完整 before、CAS patch 和回退 patch 保存在服务器 `/root/video-releases/video-formal-refinement-20261116/f9ee7ad9cc68/`。

| 目标 | 实际 `latest` 镜像摘要 |
| --- | --- |
| 创作执行器 | `sha256:0d1de0eb59ed019ab943a76cc5b227a8b74172d52d8f3d3415493ab78ed0f9c1` |
| 动画实验室 | `sha256:c3d72eb56e01819a427b70ee1daff994709dbe42f2dd6b09ddbc9fc09641bdc6` |
| 正式工作台 | `sha256:4bd780894f27757a3b8d9324a85adfd8046f3fe1952c915abca993170b5872f0` |

部署 plan SHA 为 `1f0a1aa743c31a532ee84e610b8828d3e52eaf239385d39f2bcb9364c52f8f0e`。真实网页刷新后，8条代表样片分别为7条源码核实的原生JavaScript和1条实测Anime.js依赖；原生筛选7条、Anime.js筛选1条、GSAP筛选0条，清除筛选恢复8条。GSAP没有现成样片不能伪造其使用标签，合成测试的识别覆盖另计。当前账号正常读到15个项目，新正式验收项目仍为0镜头；新打磨面板已加载，尚不代表真实付费执行通过。

## 已实现的正式打磨链路

正式制作序列继续由既有 `ProductionSequenceStore` 管理。`ProductionRunStore` 已有真实整片来源登记基础，本轮没有重新建立另一套额度，也没有扩展旧的单动画 round-0 账本来冒充整片协调。

新增主要组件：

- `production_refinement_service.py`：持久任务、额度意图、逐子任务预算、CAS、恢复、取消与结果登记。
- `production_refinement_driver.py`：核对来源媒体与快照，调用原正式模块作者和 HyperFrames 整片渲染接口。
- `film_review_client.py`：使用工作台原有私有执行器连接，按 owner／项目／来源绑定审阅。
- `film_review_proxy.py`、`film_review_proxy_http.py`：执行器复用自身已经准入的 v4 审阅身份；工作台不复制该通用 Agent token，也不能通过代理查询任意上游 Agent job。
- 正式修订 HTTP、页面与后台工作流：处理用户追加、明确需求、原请求恢复及新成片的自动轮次；具体接线见当前源码，不以本文代替运行核验。

一个正式制作序列允许首次自动审阅并至多修订一轮，之后用户可主动要求追加至多两轮。再继续打磨必须提交明确的问题、预期和全片／时间范围需求，以及有限任务预算。失败、取消、来源变化或重启不会退款或重建 1+2。

**这套 1+2 规则只约束正式视频，不约束开发者效果库。** 效果库仍可由开发者持续优化，每次实验保留自己的有限预算和停止条件。

协调器在提交前持久化子任务身份；只有 CAS 胜者可以提交。未知响应只找回原 key，不换身份重发。停止尚未证明时不进入下一项付费工作，也不把记录标为已经安全取消。

自动轮次按实际审阅问题确定最早受影响镜头；明确需求只按用户列出的范围确定修订起点，额外审阅发现不会擅自扩大修改要求。该镜头以前的模块复用，之后依次由 Agent 基于原模块及当前前任边界重新创作，最后仍由 HyperFrames 进行整片编码。用户笼统追加默认处理整片。这是保守的下游依赖处理，并非已经证明所有未指定画面完全保持不变。

原声、实际音频时钟、模块目标和冻结 WikiSkill 继续保留。宿主不手写动画冒充 Agent 创作；结果保持草稿，不自动采用、核准旁白时序或代表用户满意。

## v4 审阅的真实覆盖

当前复用 `video.animation-review.v4`，从真实整片时长中取至多 24 张有序抽帧，并核验模型网关收到的原始图片字节及对应控制面回执。

这能检查取样位置上的构图、清晰度与部分关系变化，**不是连续看完全部帧，更没有听取音频**。结果保留 `continuous_playback_reviewed=false`、`audio_reviewed=false` 及具体限制。模型列出帧名、返回零问题、任务完成、技术编码通过、播放或下载，均不能替代真实连续运动观察、音画一致性、语音自然度或人工审美验收。

一轮修订后执行现有技术渲染验证，不再自动开展第二轮 Agent 审阅。未来如需更强的连续视听观察能力，应单独实施并保留实际证据，不能修改文案把抽帧能力描述成已经具备全帧／音频审阅。

## 本轮独立审查修复

### 点问题的镜头归属

审查发现协调器能把 `start_seconds == end_seconds` 的点问题定位到镜头，但驱动传递问题时使用严格区间交集，导致作者收不到同一问题。

现在两处使用共同的时间交集规则：0 秒归首镜，镜头边界归后镜，整片最后终点归末镜；明确需求对审阅问题的筛选同样保留符合范围的点问题。新增回归覆盖自动轮次、明确需求、0 秒、镜头边界及片尾，核对实际传给作者的 `observed_issues`。

### 用户意见合计超出作者输入预算

公开字段分别允许较长指令、problem 与 expected，但加入 JSON 和固定说明后可能超过原作者 16,000 字符上限。若只在生成阶段拒绝，会先消耗轮次与审阅任务。

现在 `service.start` 在冻结真实上下文之后、建立任务或预占额度之前调用确定性 `driver.validate_request`。逐镜头编译完整用户修改输入，连同固定结构最多 12,000 字符，给既有作者输入中的后续观察信息预留空间。超限返回 `production_refinement_user_input_too_large`；不截断用户原文、不扩大 Agent 合同，也不为了预检执行审阅。

新增回归验证：单独合法的 16,000 字符指令、8,000 字符 problem 加 8,000 字符 expected 在合计超限时，均为零任务、零额度和零模型执行；合适长度的意见逐字保存。未来模型审阅结果本身异常过大时仍可能明确失败，不能靠删除用户意见硬塞进输入。

## 首次真实 WikiSkill 编译失败

首次真实编译操作为 `ca5cba98-470d-436a-9116-1f5adb252067`，上游 job 为 `b5e91172-73c0-4198-b564-ede85ac4538d`。当前真实结果为 `animation_wikiskill_agent_failed`／`worker_output_invalid`，没有由本次操作产生可用的 `release_id`。

已确认的阻碍是约 144 KB 的单行输入超过内置 `read` 工具约 50 KB 的单次读取上限，使编译 Agent 无法按原入口完整取得输入。读取／交付协议的修复正在进行，选择新增 compiler profile v2 与 bundle v2，不修改已经冻结的旧 v1 身份。本文件不把正在修复写成已经发布，也不把重试当成原任务恢复。是否重新发起编译及使用哪个发布版本，由 root 根据实际修复和记录推进。

原意图、状态与请求保存在本轮 D 盘工作目录的 `wikiskill-real-publication-intent.json`、`wikiskill-real-publication-status.json`。这些是本次尝试的证据，不是正式技能已经应用的证明。

## 正式新项目与后续验收准备

已创建项目：`WikiSkill 正式采用验收｜快递分拣与重复计数｜2026-11-16`。

- 项目 ID：`6224e72c-38d3-47fb-a65c-0b14cdea2c66`。
- 所有者：`27714443`。
- 准备时只读核对：revision 1、0 场景、0 统筹操作、0 正式制作 run。
- 尚未统筹或采用方案，也没有用本项目触发模型、TTS 或制作。

操作准备位于：

```text
D:/github/armbianbegin/.research-temp/video-agent-development-20261112/effects-seven-optimize-20261116/
  prepare_wikiskill_formal_acceptance.py
  wikiskill-formal-acceptance-requests.json
  wikiskill-formal-acceptance-evidence.json
```

脚本只执行 owner／项目／标题限定的 SQLite `mode=ro`、`query_only` 检查。它不发送 POST、不声明代理身份、不读取浏览器 cookie，也不取服务 token。真实统筹、采用与制作由已登录工作台正常控件执行。

有效发布后，可运行 `prepare --release-id <实际版本>` 刷新结构化请求参考；各阶段之后运行 `collect` 收集实际证据。请求计划有固定参考 key；若正常页面生成自己的稳定 key，必须以实际记录恢复，不能在页面已提交后再发送计划 key。提交结果不明时只读取原操作。

采集器记录规划和制作的 `library_binding`、原生 `skill_search`／`skill_read` 回执、apply／adapt 声明及 `segment_ids`，并在正式模块中重新核对 package 合同与源码锚点，最终核对实际成片字节摘要。只有绑定同一版本不算采用；有读取和采用声明，也不能直接推导可见效果改善。真实视频仍需连续播放和试听。

## 山石与参考片段

本轮新的山石作品执行 `e11245e0-4a53-4461-8af1-72319443d2f4` 已完成，候选为 `efc39c43-5df7-4bb1-8038-192991945727`，Canvas、24 秒。收集媒体摘要为 `c675e1fbd68e26fc10b61d15c0075f17f833f7798c1fc46061e134499912d818`。

本轮 `mountain-rework-result.json` 是已完成结果；`mountain-rework-latest.json` 曾保存运行中的中间快照，不应单凭它判断作品仍未结束。技术完成与媒体存在尚不能证明山石题材已经有效优化，审美和完整运动验收继续保留。

复看参考视频约 21–28 秒的水墨片段，有助于明确可借鉴的笔触、层次和展开方向，但当前仍属于参考观察与可行性判断。不能将它写成已经达到同等质量，更不能将另一题材的新作品算作原山石的有效优化。不同内容或实现技术的作品仍应保持独立样例身份。

## 尚未覆盖的资源工作

目前正式修订复用原模块可继承的 Manim 等预制资产，保持原来源、原声和时钟；没有因为新建修订模块就自动重做资源本体。

需要修改 Manim 本体、重新制作资源、传播失效、补做资源或在预算内替换生产方案时，仍需继续完成 U.x 资源依赖与补做链路。不能把模块重新生成或资产继承成功算作这些事项已经完成。

## 已执行的定向验证

以下为本轮实际运行范围；存在重复覆盖，不能将各次数字相加称为全集总数。

| 范围 | 实际记录 |
| --- | --- |
| `test_production_refinement_service.py` | 初轮 16 项通过；其后明确需求范围／点问题／时长／并发的定向 4 项通过，包含后来新增的范围约束案例 |
| `test_production_refinement_driver.py` | 真实正式 Store、原版本输入、原声／时钟／目标保留及实际渲染结果合同的 6 项通过；合成模型与媒体不构成表现力验收 |
| 两项 P2 修复的新增／影响范围 | service 与 driver 的筛选测试合计 6 项通过，覆盖点问题归属和占额度前输入预算检查 |
| `test_film_review_client.py` 与 `test_film_review_client_integration.py` | 合计 10 项通过；后者 2 项再次核对真实 Driver 取消路径，不先推进 queued 任务去付费提交 |
| 私有审阅 facade | 伙伴报告 `test_film_review_proxy.py` 26 项通过；与客户端 8 项联合运行 34 项通过，包含双实例 SQLite CAS 仅一次付费 POST、owner／来源隔离和真实 v4 网关的离线 HTTP 组合 |
| `test_production_refinement_http.py` | 伙伴报告 5 项通过，含活动审阅阻止项目 trash |
| `test_production_refinement_workflow.py` | 伙伴报告 2 项通过 |
| `refinements-ui.test.mjs`、`production-runs-ui.test.mjs` | 伙伴报告合计 28 项通过；`node --check video_workbench/web/app.mjs` 与 diff whitespace 检查通过 |
| 干净 index 副本全集 | root 报告 128 项 Python 通过，耗时 267.61 秒，含一条既有 Starlette 弃用 warning；91 项 Node 通过。该结果与上述定向测试存在重叠，不合计为另一总数 |

测试临时目录均在 D 盘，Python 测试使用 `-B`、`-p no:cacheprovider`。代码、协议、持久化、隔离和合成 peer 测试通过，仍不能替代真实模型、浏览器、连续播放、声音或部署验收。

干净 index 全集测试已通过，代码已推送，服务器正在从该干净 revision 构建。之后应补录实际命令与构建部署收据，再通过有效 WikiSkill 发布和新正式项目的真实链路完成验证。本记录截至此检查点没有勾选这些未完成事项。
