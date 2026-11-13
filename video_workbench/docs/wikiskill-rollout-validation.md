# WikiSkill 接通与实际验证

本文记录 2026-11-13 至 2026-11-14 的新协议发布阶段。当前主设计见 [Agent 创作与效果库方案](agent-creation-effect-library-plan.md)。代码、镜像、技术准入、真实样片认可和业务发布是不同的验证层次。

## 已核对的源码与镜像

- `fb7d5d7707585bbdc33fe07690f2aeb4720f6241`：知识 Wiki、实验室标准 Skill、新正式创作输入与按需读取证据。
- `b05769cd91ef69491cc57405840215e1461822a5`：修复渲染器 Docker 上下文遗漏 `hyperframes_project.mjs`。
- `13399a5fe9723c930dc923965e54a24129edf8a2`：逐条精确认可界面、独立实验 v2／正式创作 v3 准入，以及部署打包修正。
- `0c9cf61baad3a6343c192ae72c49a615efe4f800`：根据首次真实 author 失败补齐动画源文件名、字段类型及确定性运行接口。
- `e7fc38171eba95a711dbf5a2e33054c50ea34212`：正式准入按无重复的精确工具集合核对；新迁移表单绑定确切原目标／新目标，移除参考输入并拒绝仅数量／时长变化。
- `a6098e58f25ffb33c7fd57a5e3a2ff3f61719b1f`：新 `animation-method-v4` 资源明确未持久策划草稿与确切持久父版本的谱系边界，保留 v3 原字节；增加只替换两个现有 Skill 路由、保留历史资源与调用者的离线升级准备函数，并将新资源纳入普通镜像构建。
- `93e7626a9a27200927210de724f85d1d5c02b78a`：仅提交并推送明确的 10 个文件，新增 `animation-method-v5` 完整知识字段指导，修正新 Wiki 页 revision 类型说明；v3、v4 资源与服务端 validator 保持原字节。

服务器集中回执根目录为 `/root/video-process-20261105/feedback-20261106/animation-capability-redesign/agent-creation-phase1/creation-product-r1/`。本地本轮工具、诊断及截图集中在既有 D 盘目录 `D:/github/armbianbegin/.research-temp/video-agent-development-20261112`；未在 C 盘下载模型或浏览器。

`skill-application-release-r1` 来自干净 Git 源码 `13399a5…`，使用普通 Dockerfile 构建 control、worker、executor、workbench、lab 五个镜像。渲染器独立来自 `b05769c…` 的 `skill-renderer-release-r2`。均使用 `latest` 并记录不可变 digest、源码标签与构建清单；没有将应用镜像拼接成新的源码版本。

## 精确发布界面

代表与迁移样片分别显示标准、媒体与版本，分别记录完整观看／必要音效试听。迁移另需确认内容、结构变化与脱离原参考后的适用结果。最终发布只引用已有的精确认可，不附加通用 `human_verification` 代签。

界面发送前拒绝过期方法、验证及错媒体。请求响应丢失后不自动重发；显式刷新成功后仅能使用原 request_id 和原 body 安全核对／重试。GET 失败不解除未确定状态。

Node 新模块 10 项及旧界面联合 60 项检查通过；真实双跳 HTTP fixture 验证 CSRF、媒体版本绑定、逐条认可、幂等回读和最终发布。fixture 确认只代表合成测试，不代表真实开发者观看或认可。

`skill-lab-rollout-r1/rollout-completed.json` 记录首轮 lab 镜像 `sha256:281a97ee6ff6cd5bbc6b9ce166a9f88cbeadc57ecd6ebf6df14f52c6167a4486` 的部署。后续 `skill-lab-ui-r2-rollout/rollout-completed.json` 记录从干净 `e7fc381…` 源码构建并部署的最新 lab 镜像 `sha256:9f4c5dd26a40e3e0013963cc1d6e2f1c759fcd0434496bf0c8c8f9183522752c`；Pod Ready，六个公开源码摘要实测匹配。登录浏览器已打开效果库和新建实验表单；独立禁提交的合成发布页也已核对。没有操作线上真实认可框或发布按钮。

## 独立渲染器技术验证

首次渲染器构建因 Docker ignore 漏文件失败，修复后新构建成功；原失败回执保留。首次原生验证操作因主机 Python 未装 Pydantic，在实际 fixture 启动前失败；随后明确复用现成受控 `deploy-python` 依赖，没有下载依赖。

`skill-native-admission-r2` 对新渲染器 `sha256:a115fd513a34edc4fb53ce3400d47848a316f420b44272c5141b9b87e684f647` 实测 HTML/SVG、Canvas2D、Three.js：各 24 帧、19 个不同探测帧，独立捕获一致、反向／重复定位及空闲时钟检查通过，Three.js 验证软件 WebGL2 原生上下文。源码测量同时绑定 renderer revision 的 Git 对象、可读构建归档、镜像内文件、runtime SHA、labels 和 digest；允许控制面来自不同的源码 revision。

此项是三个原生引擎的技术夹具，不是 Agent 美术作品，不证明用户审美认可，也不证明 HyperFrames 已接管最终全片渲染。`hyperframes_takeover_proven=false` 保持明确。

实验室继续使用 `sha256:3fff8d076330a5becc876baa822de1fe9227bad19a3708848e736b3ceca19bc6` 渲染器。输入 v2 校验修正在 executor 侧；不将旧 Canvas／Three.js 证明改成新镜像的证明。新 executor 的宿主字节与旧 engine-admission 绑定的宿主不同，真实目录回验曾因此被拒绝；后续已通过独立技术重准入恢复，实际回执见下节。

部署复核要求原生 probe 的证据及父目录由 root 保管，原容器输出的 UID 10001 因此先被拒绝。`skill-native-custody-r1` 保存 218 个精确证据文件的原字节和元数据，核对容器无该目录挂载、无可写祖先挂载后，仅归还文件及 10 个目录的所有权；内容、权限、mtime 和原 receipt 均不变。保管收据 SHA 为 `92bad6df8882945c76d00cd0e07330f86849947877549fead833ad089b5e38c8`，没有重跑 fixture。只读的节点监控根目录挂载不具备写入证据的权限。

## 首次真实实验 author 失败

`candidate-method-author-s1` 使用 `13399a5…` worker，实际 4 次模型请求后停止，未交付 artifact，未启动后续渲染和 reviewer。原 worker／proxy 停止、租约失效、UID 退役及原上游响应证据保留。

只读重放已完成上游 write 参数复现 `animation_experiment_files_invalid`：模型输出 `source.files.index.html`，真实校验要求 `scene.html`。原新合同只写 files(path-to-text)，没有明确文件名与运行接口。Skill draft 自身可归一化，但不能因此将失败输出变成有效 artifact。

修复只补充新资源合同／创作说明与对应 manifest pin，不放宽校验，不手工改动画源码、不重命名失败产物。相关 profile/catalog/admission/build 检查 164 项通过、2 项平台跳过。新验证须使用独立 attempt、fresh UID、冻结预算及新镜像／bundle 身份；旧失败不能升级为成功准入。

## 修复后的实验角色与正式创作准入

`candidate-method-author-s2` 使用独立干净 Git 检出 `skill-method-source-r2` 及 `0c9cf61…` 的完整三镜像，实际 4 次模型请求完成原始产物。唯一一次原始源码渲染成功：4 秒、96 帧，反向定位、解码及容器移除通过。review-s2 只读取该次已有渲染的真实帧，实际 4 次模型请求完成；没有重复渲染。两个角色的严格独立准入及二次报告复核均通过，证据分别在 `skill-method-author-admission-s2` 和 `skill-method-review-admission-s2`。

渲染后的准入曾发现输入快照由渲染工具归给 UID 10001，而保管校验要求 root。先确认容器消失，在 `skill-method-custody-s2/original-bytes` 保存精确三文件的原字节副本和元数据，再仅归还 root 保管；0444、mtime 和全部内容 SHA 不变。原路径绑定不改变，未放宽验收。保管收据 SHA 为 `d7a3e3464b577fb7ae78638b2271cb6339ae22230b32d4e079f26015c7b98442`。正式汇总在 `skill-method-s2-final-reconciliation.json`，不将保管操作当第二次渲染。

`candidate-skill-r1` 使用 `13399a5…` worker，实际 6 次模型请求完成双产物和原生 Skill search/read 证据；唯一一次原始源码渲染为 13 秒、312 帧，绑定原录音，反向／空闲定位、解码和镜像身份通过。worker／proxy 均停止并精确清理，过期租约拒绝。其 Skill 明确是 operator-only fixture，不能称为业务已认可或已发布的 Skill。

正式准入首次误把工具名单的合法顺序差异判为缺失。独立校验器仅将这一处改为严格 list／字符串、无重复和精确工具集合比较；原源码、镜像、候选及捕获不改。替换前后函数 SHA 和校验器源码 SHA 独立封存，部署复核使用同一校验器。相关 production／film／Skill v3 定向回归 50 项通过，包括任意合法顺序及重复、额外、缺失、错误类型的拒绝。`skill-production-admission-r1` 已通过且仍为 disabled 技术准入；报告 SHA 为 `853a5534c6292a264c6dc35856471954acdc612a1a1c4e7ccdaeb25b6daa97b5`。

浏览器验收准备又发现旧迁移表单只提交结构变化，新 v2 同时要求内容变化。修复后由选定样片所属实验的原始目标和实际新目标形成 `content_changes`，不凭继承样片猜测来源；清空、禁用并从请求中移除原参考。仅数量或时长变化不足以迁移，界面在提交前给出说明。只含本次修改的拟提交源码和 Node 回归 52 项通过，包含预存编辑的工作树 54 项通过；原有编辑留在工作树，没有混入提交。

## 实际部署与目录回验（2026-11-14）

`skill-production-activation-r2/apply-complete.json` 记录正式 production v3、workbench 与 motion executor 的实际接线和入口恢复；`skill-method-activation-s4/apply-complete.json` 记录实验 author/review v2 的实际接线和入口恢复。新调用者分别限定对应的新角色，独立凭据与 UID 池已配置；既有调用者、宿主及业务数据的保全检查通过。方法部署后再次以 workbench 专用凭据读取 production capabilities，仍为 Ready，worker 身份保持一致；没有为此提交业务模型任务。

| 组件 | 已部署的 `latest` 镜像 digest |
| --- | --- |
| 正式 workbench／motion executor | `sha256:8440f2f15662604ea0c073e6a55101c522e556378cfd3317c1cd4354daa10804` |
| 正式 production worker | `sha256:3e58bbcdfbb4081c9140874d5e87dbed04a839197cb2b0574e54edff3608427e` |
| 正式 motion renderer | `sha256:a115fd513a34edc4fb53ce3400d47848a316f420b44272c5141b9b87e684f647` |
| 共享 control | `sha256:671fbde66662c3a1a501933791fab7938fdecd745caba03a6920b4eef479164a` |
| 实验 method worker | `sha256:8d1dcfabeceb277040f4c990703de19ca99c78366a4315b97c565607887b0b35` |
| 实验 creation executor | `sha256:52fbe1b291e5ce50669bc1c8e37e8db96089d6ecbee1c9e13de3adb8e35b634a` |
| 实验 method renderer | `sha256:3fff8d076330a5becc876baa822de1fe9227bad19a3708848e736b3ceca19bc6` |
| 效果库 lab 前端 | `sha256:9f4c5dd26a40e3e0013963cc1d6e2f1c759fcd0434496bf0c8c8f9183522752c` |

Pod Ready 和角色 capabilities 不代表业务目录可用。新建实验页的真实 catalog GET 曾返回 HTTP 409，原因是 `animation_method_engine_admission_changed`；拒绝期间界面禁提交，没有产生新的业务委托。旧 admission 的宿主摘要为 `64b9514787c3ec1caa8053fffb913aa96f55644b89b534d15f06136e89ba574e`，新宿主为 `842bbf66e44e9e786768ccd0a0857cc1a1a44cdd0b60f776812b9ea73f762e1a`；引擎运行时、资产和实验 renderer 镜像未变。可选 HyperFrames 桥接的宿主字节变化也须重新准入，不能因为未启用核心桥接就忽略身份差异。

`skill-method-native-readmission-r1/render-completed.json` 证明固定两次技术重渲染已通过：复用已核验来源的原 Canvas 与 Three.js Agent 源码，各 14 秒、336 帧，音效不变，仅重新冻结运行宿主身份；零模型调用、零构建，原始源码和历史账本均保全。两次新渲染均经严格回读后创建新 admission，摘要为 `2ce4cab2582318efed7dfffde6e146e7a47e12259d9cf3bfadae4b6ebf0d8b9a`。`skill-method-native-readmission-activation-r1/activation-completed.json` 证明通过 CAS 仅替换 executor 的准入文件参数，rollout 和实际 Pod 内 lookup 一致；旧作品、旧证明和历史账本保留，没有改写旧 proof。

随后使用真实 lab caller 读取目录，得到 `catalogue_verified`：owner 为 `27714443`，schema 为 `video.animation-method-lab.v1`，`enabled=true`，允许引擎为 `html-svg`、`canvas`、`three`。登录浏览器也已恢复三种技术及可提交表单。

## 首次真实业务委托的谱系失败

首次有界真实业务实验“筛选发生时统计一起变化”已通过页面提交，委托 ID 为 `e73879f3-ad27-4dd8-abc6-f47424edff7c`，预算 12 秒／1 轮／15 分钟，最终状态为 failed，没有渲染或样片。策划完成，未提出 `knowledge_proposals`；创作任务 `8bb17c02-b7da-4f0f-a0e5-330180d46a8d` 实际进行 3 次模型请求，均为 HTTP 200 complete。原始 model transport 输出的只读重放确定拒绝原因为 `animation_skill_method_lineage_mismatch`：策划输入含两份 version 1 Skill 包，尚无持久 `version_id`；创作输出沿用相同 Skill ID，改为 version 2，却未提供 `parent_version_id`，不满足确切谱系合同，拒绝是正确的。

对应三个动画新角色实际使用 `llm-trusted`、模型 `gpt-6.1-sol`；已核对 Guard 为 trusted spotlight=false、canary=false、detection=log，没有改写 prompt 或拦截这次输出，不能将此次合同失败归因于 Guard。原始证据保留在 `skill-business-A-failure-r1`；本地只读提取为 `skill-business-A-agent-jobs.json` 和 `skill-business-A-lineage-detail.json`。

此次未执行后续优化 B 或迁移 C，未自动重试，也未把失败委托改写为成功。后续合同修复与独立技术验证见下节；没有据此证明 Wiki CAS、跨委托消费、样片认可或业务发布。

## v4 谱系合同修复与新镜像（2026-11-14）

`a6098e5…` 新增独立的 `animation-method-v4`，没有原地改写已运行 v3 的合同或清单。新合同明确：没有 `version_id` 的输入只是尚未持久的策划草稿，首次 create 仍保持 version 1 且省略谱系字段；只有输入提供确切持久 `version_id`，才允许引用对应 `method_id`／`parent_version_id` 并递增包版本。新独立 Skill 仍从 version 1 开始，迁移包保持只读。服务端校验没有放宽，也没有修补失败 A 的原始输出。

升级准备函数仅替换原顺序的两个现有 author/review v2 路由；旧合同、包、环境、绑定、认可、池、宿主引用、UID 和 caller 均保留，新池保持禁用、未分配 UID。其独立测试及既有两组目录回归 27 项通过。这是离线升级准备，不是新镜像已准入或已部署。

`skill_method_build_v2-poll.json` 中的三份 `skill-method-release-r3/*-build.json` 回执证明：服务器从干净完整 Git 检出 `skill-method-source-r3` 的 `a6098e5…`，使用普通 Dockerfile 完成并推送以下 `latest` 镜像，源码清单、revision 标签和 digest 已核对。回执均为 `deployments=0`、`model_requests=0`；原线上镜像表不因此改变。

| 已构建、待独立准入／部署的组件 | 镜像 digest |
| --- | --- |
| control | `sha256:71795708d1b51f2918bb0321871bbd669efca016cac256074508fa87a9e888de` |
| method Pi worker | `sha256:b93d19732e41ff204c30ed9051c2d1fb9d9d2e82702d7b14a70c082a1151033e` |
| creation executor | `sha256:8d739191bc3243f367a3e00a2b48ee58cbe1ef24d77698c294b3ae7e1c19a0e3` |

新独立技术候选 `candidate-method-author-s3` 在执行前被阻断，模型调用为零：启动封装提前写入 `candidate-launch-once.json`，runner 的 fresh 目录验证在赋值证据目录前拒绝，未进入模型任务。`skill-author-s3-zero-execution-evidence.txt` 只读核对候选 UID `307000015`／s3 没有保留记录，`agent-service` 与 `agent-workers` 两命名空间没有该候选 Pod，也没有 `candidate-output`。不能将它记作模型已运行或费用未知；这是启动封装失败，不是新合同的模型结果。

后续 s4 仅修正启动封装，应用代码与镜像不变，实际完成 4 次模型请求，均 HTTP 200 complete。原始输出的两份 Skill 均保持 version 1，未带持久父版本，谱系修复已生效；但任务最终为 `worker_output_invalid`。只读重放确定新拒绝原因 `invalid_animation_knowledge`：`knowledge_proposals[0].expected_revision` 为 `null`，新 Wiki 页合同要求整数 `0`。`skill-method-author-s4-validator-diagnosis.json` 与 `skill-method-author-s4-knowledge-proposals.json` 保留提取诊断，服务器原始证据为 `skill-method-author-s4-failure-evidence.json`。没有渲染、reviewer 任务或独立准入，v4 的三个 r3 镜像未因此上线。

仅在内存 deepcopy 中把这一处 `null` 改为整数 `0`，完整 `validate_result` 通过；`skill-method-author-s4-knowledge-hypothesis.json` 明确记录这是定位假设，不改变原始字节，不形成可采用产物或认可，零新增模型调用／渲染。原始 s4 产物继续保留为失败，不修补后用于准入；新候选与失败业务 A 分开，不重新打开 A 的预算，也不以诊断通过替代真实 Wiki CAS 成功。

真实路由只读证据 `skill-llm-trusted-live-policy.json` 确认：`llm-trusted` 经 Responses 线路解析到 `custom` 上游，配置模型 `gpt-6.1-sol`、tier=`trusted`，路由 revision 为 `f93cc97a5fb1bebf8b24905b`；允许工具，token cap 为 8192。Guard 的 spotlight、canary 为 false，detection 与 canary action 均为 log。本轮没有为修复谱系合同而修改这条模型路由或 Guard 配置。

## v5 知识字段合同与完整源码构建（2026-11-14）

`93e7626a9a27200927210de724f85d1d5c02b78a` 已提交并推送独立 v5 资源及接线，manifest SHA256 为 `3e17c8b9766568021b9fbca2ace6cc31f588c966266e169bf229cdbafb69cce0`。新资源补齐完整知识候选字段说明，新页 `expected_revision` 必须是整数 `0`；没有改写 v3／v4 原资源、服务端 validator 或 s4 原产物。profile／lineage／knowledge／build 检查为 174 passed、2 skipped，主 Agent 的 catalog／upgrade／acceptance 独立检查为 15 passed；离线通过不代表新候选已准入。

服务器已通过 `skill_method_build_v3.py` 完成 `skill-method-source-r4`／`skill-method-release-r4` 的干净完整 Git 源码构建，并推送三个 `latest` 镜像。源码清单 SHA256 为 `d513471058edb07edefb9fce0b8287a9c06e7bacafbef25686ad090361579c8e`；本地 `skill_method_build_v3-poll.json` 和服务器 `skill-method-release-r4/*-build.json` 记录进程已结束、无 build-failed、`model_requests=0`、`deployments=0`。r3 摘要仅保留为 v4 历史，不用于 v5。

| v5 已构建、待新独立候选／部署的组件 | `latest` 镜像 digest |
| --- | --- |
| control | `sha256:aa3730c7ba0e4ac3cfcd2cbf4b8fda3d8959677563a2137756a2bfd7a3c51a9a` |
| method Pi worker | `sha256:3d6b7a778f0fbb220272b211dc81f2a0b9c94ebf29d4904d5c1733764d178718` |
| creation executor | `sha256:0a3f8c5e30d08e0e2bfbfe3b23cdba0c94943bc4eaf93649f16ddbb64a0fb6db` |

未启动 v5 新模型候选，未部署 v5；本轮实际失败后不追加付费尝试。下一阶段是 v5 author／reviewer 新独立技术验证及保留历史身份和凭据的确切升级，之后才继续真实 Wiki CAS／后续消费与发布采用闭环。已上传的 `skill_method_upgrade_v1.py`／`skill_method_upgrade_v2.py`、`prepare_upgrade_v2` 与 s4 执行器固定 r3／`a6098e5…`／v4 及 UID 21／24，不能直接用于 r4／v5；续作必须制作绑定新 release 的新脚本，使用新 candidate 目录与未保留 UID。失败的 s4 不充当成功准入证据，本次不新增或运行这些续作脚本。

## 仍须独立完成的验收

- 修正知识字段合同后 author／reviewer 的原始产物、渲染与独立准入，随后才实施现有 Skill 两角色的确切升级并做真实目录回验；构建或内存诊断不代表准入或上线。
- 有界真实委托产生经验与有证据的 Wiki CAS 回写，后续普通委托实际消费前序冻结经验／知识；去参考迁移另行验证，不以其证明 Wiki 消费。
- 开发者实际认可新代表片段与内容、结构均改变的迁移片段，再发布不可变 Skill。
- 正式新内容实际读取已发布 Skill，保留原生 search/read 记录，并分别核对可见效果与自报采用。
- 全片 HyperFrames 工程及实验室统一宿主仍按主设计独立推进；安装 core、运行 native fixture 或角色准入不自动完成这些目标。

本文件继续随真实回执更新，不以单元测试、夹具或技术准入代替真实发布及主观认可。

OpenSpec 同步已完成：项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的本变更更新至 revision `987a15a80f81a74f6e11bece0751ae36b68f2610`，三份文档精确回读一致，规格校验通过。仅替换最新进展、同步主设计并澄清历史阶段边界；43 项任务保持 16 完成、27 待办，没有将 v5 构建或诊断通过勾成真实验收完成。凭据和 Guard 配置未变。
