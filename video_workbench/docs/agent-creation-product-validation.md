# 动画实验室制作链发布与验收

本阶段将“Agent 初作 → 隔离渲染 → 独立审阅 → 必要时修改一次 → 技术复验”接入开发者实验室。作品由通用 Agent / Pi 编写，控制服务保存额度与运行身份，浏览器只读取隔离渲染后的媒体及 JSON 源码文件。普通视频制作流程不在本阶段切换范围内。

## 代码身份与离线检查

产品实现为开发分支 `codex/video-capability-experiment-redesign` 的提交 `3c187fc0843a3adcaa74804e416c429e8f5bb357`，包含此前 `b50f82264d8618e247d28ccae0fd74698254f3d8`。发布源码为 `e420aa043c661b20c807535782ed2d9e87a40b3d`，仅补充基础系统拆分出的 `docker-cli` 包。均已推送，未合主线。

定向测试覆盖制作持久账本、并发取消、隔离渲染证据、HTTP 两跳、前端刷新恢复、凭据隔离及 Kubernetes 资源生成。HTTP 相关 158 项、技术准入与执行器打包联合 90 项分别通过；两组范围不同，不与此前重复测试相加。模拟网关和本地测试不能替代真实模型与浏览器验收。

发布构建包仅从精确 Git 提交读取 112 个允许文件，逐文件字节核对通过。归档 SHA256 为 `147de76c4f67ee5144571424443b58d00ff14e9ebc77890b98f3f1b88a3bff3a`；不包含运行数据库、日志、凭据或 operator 文件。镜像使用 `latest` 标签，以实际摘要及源码标签识别。补充依赖后的部署生成检查 52 项通过；一次临时目录权限错误及一次 Windows 长路径错误在使用允许目录内的短路径后补验通过，没有修改业务逻辑绕过。

## 预算及边界

本阶段登记一个新的正式 HTTP 验收 run：初作 1 次、独立审阅 1 次、必要时创作者修订最多 1 次、隔离渲染最多 2 次。工具往返中的模型 HTTP 请求单独统计，不冒充创作轮数。结果未知只查原操作，不重发制作请求；失败停止核查，不自动换任务重跑。此前 p1/r2 已关闭预算不重开。

当前准入范围为 HTML/SVG、24fps、最多 10 秒。Canvas/Three.js 自由源码准入、任意参考视频分析、完整效果库和新整片导演流程分别实施。本阶段不自动发布效果或采用到用户业务项目。

历史 r2 审阅未识别预登记的 E 问题，修订片仍有固定说明不可见的现象；该事实保留在[前阶段验证](agent-creation-phase1-validation.md)。新流程技术通过不自动改变旧案例结论，也不代表审美评价或用户采用。

## 发布进度

精确源文件已上传并通过服务器准备检查。修复版两个镜像均已构建、推送：

`arm-cluster-master:5000/video-animation-lab:latest@sha256:cf65c85758864a6a830389cc8676ac625845dac28eb9ebb60e712eaa48ec3125`

`arm-cluster-master:5000/video-animation-creation:latest@sha256:0bc6396723d190a7af26beda191e83ee60340ec9fa544e93490ebfe208e574fc`

普通工作台 donor 有 125 层。独立打包将其文件层压为一层，完整运行配置逐字段相同；只创建未启动的临时容器和全新匿名卷，不挂载生产数据。最终 Dockerfile 仅将 `FROM` 指向固定摘要的浅层基座，应用文件来自上述精确 Git 归档，原 donor 身份另存于镜像标签和构建回执。原层数上限、匿名卷标签检查及缺少 Docker CLI 的失败均保留。

共享 Agent 已完成加法发布：三个控制 Deployment 固定新摘要，旧 62 个 Host、历史表数据、两条隔离记录和槽位保持；标准 `init-db` 仅新增空的图片输入表。create/review v2 的独立调用者与两个新 UID 范围已启用，旧 observe/review v1 仍停用。原 Service selector 已恢复，三个实际 Pod Ready、零重启。控制镜像为 `agent-service-animation-review-control:latest@sha256:e17b9578e508ac6699f3be66f52b8fba60baf8bc85bd445696c057076d91cc1a`；Worker 仍使用前阶段实测的 `38731ff5…` 组合。

实验室和私有执行器随后完成发布与只读连通验证。实验室固定上述 `cf65c857…` 摘要；私有执行器固定 `0bc63967…` 摘要，使用独立 namespace `video-animation-creation` 和数据根 `/var/lib/video-animation-creation`。两者使用不同于 Agent 调用凭据的 Lab 内部凭据，只有执行器挂载 Docker socket。原实验室 PVC、三个数据库和 3366 项历史媒体摘要保持，普通视频工作台 Deployment 未改。

原实验室主数据库约 109MiB，超过原备份探针的 16MiB 上限；发布准备先停止。核实 Pod 内存上限为 1GiB、压缩结果约 1.8MiB 后，仅把原始备份上限调至 128MiB。重新准备获得一致备份并通过完整性检查，未放宽其他响应上限。发布计划及回执分别保存在 `activation-plan-r2/` 和 `product-deployment-plan-r2/`。

控制发布、健康检查和 capability GET 不能代表产品真实制作链通过。

## 首次真实制作失败与配置根因

已登录浏览器在群体筛选与统计实验的开发输入中实际点击一次“制作并打磨一轮”，请求编号 `d6bc962b-b1e8-42a7-8cec-5e4c9ccac05a`，产品 run 为 `867bf1be-46f0-431a-ae99-3d6fad4af44e`。实验定义身份为 `67a09e0378cbbb991bae4bfe1456d2bd43ff784dd13cda14823d698e5780c428`。

初作 operation `867bf1be-46f0-431a-ae99-3d6fad4af44e:initial_creation` 已获得真实 Agent job `3ad38f58-0033-446d-89e0-05978515d70e`，但终态为 failed，产品错误 `animation_creation_upstream_failed`。尚无作品、审阅或渲染。原失败保留，不重发制作、重置额度或把候选验证拼成此次成功。

随后只读核查得到 Agent 错误 `capsule_stopped_without_result`：唯一 attempt `7072eee5-daee-410a-a5f5-7fa608ac16b5`、fence 152，Pod UID `36702922-2b29-464d-9d7a-9ccce7a7a5d8`。该 attempt 的持久模型请求记录为 0；API 返回 `stop_proven=true`、`not_started=false`。这表示控制器证明容器已停止、未获得 Worker 执行结果，不能写成 Worker 从未启动；具体启动失败原因仍待核查。此时制作执行器 Pod 为 1/1 Running、零重启。

已用同一固定 Worker 镜像及线上实际 immutable Host ConfigMap 复现配置缺陷：原发布操作器将目标角色的单独目录覆盖为整个平台目录，加载了 21 个任务合同、66 个执行组合，其中 `video.process-plan.v1` 在该固定镜像中没有对应实现，导致 `_host_registry` 拒绝配置。把目录投影为当前角色引用的合同、能力包、工具环境、执行组合及路由各一项后，`host_policy` 和 `accepted_host` 静态检查均通过，组合摘要保持 `d57d40b7ff854183e89c8c5a66eabbb4bb3c02057cdde6e08d9d9960482c1db8`。这是实际配置校验的确定性缺陷；原 Pod 已按正常停止流程删除，未取得其启动日志，不补造原日志。

该预检只读取 ConfigMap，不加载 Secret，不启动 Worker／Agent 或模型；两个临时 Docker 容器均禁网、只读根文件系统、去掉 capabilities、无宿主机挂载。实际 Worker 以 root 控制进程启动，因此根因使用 root 静态检查的缺失 handler 结果；非 root 辅助检查的额外文件权限错误不作为生产根因。静态配置通过不证明实际 Pod 隔离或后续制作通过。原地证据为 `static-worker-config-check-1794314278555204542.json`。下一步仅修正两个新动画角色的 Host 配置闭包，补永久回归后再实际验收。

浏览器刷新后重新匹配同一请求与 run，显示原失败记录，未再提交制作。刷新恢复和失败展示已实际观察；作品播放、定位、源码下载和成功制作全链仍未完成。该预登记一个 run 的额度已使用，后续先只读诊断，不能自动重开旧预算。OpenSpec 1.4 继续未完成，旧案例 E 漏检的 1.3 也不因本次发布改变。

## 配置修复已发布

纯配置修复 `1073dcb519d01c25b784ccc042788a902dda632b` 已提交并推送原开发分支，31 项定向回归通过。`project_animation_worker_host()` 在解析目录前精确选出目标五项引用闭包；与目标无关的旧合同不会被导入，原模型、权限、配额、准入记录及目标组合摘要保持。发布模块直接从精确 Git 字节导出，SHA256 `f94433add81aff0b80e8c50ee9c4d41dd282050f8732d788dd190630e6410fe9`。

`host_catalog_fix_server_v1.py` 的 prepare、apply 已实际通过。仅新建两个目标 Host ConfigMap 与一个中央 ConfigMap，中央目录仅变更这两个池的 Host 名称；三个控制服务仅切换配置挂载，镜像及其余部署设置保持。新中央配置为 `agent-service-animation-hostfix-597a963876d95387`，配置 SHA256 `597a963876d953873ab1075156224cd5ec53928bfc2c7349c711d80def7765f0`。两个目标均在原固定 Worker 镜像内通过静态准入检查。

应用时暂时关闭 Agent Service 入口，并在共享准入锁内停止 API／dispatcher；无活动任务后切换配置。恢复前已核对全部 Agent 表摘要、原 Host、UID 账本、运行 Secret、槽位与隔离记录保持；三个实际控制 Pod Ready、零重启，实际挂载配置摘要匹配。原 Service selector 及真实 API endpoint 已恢复，create/review v2 的鉴权能力 GET 均 ready。本次修复没有创建任务、调用模型、重复分配 UID 或再次初始化数据库，原失败仍保留。计划及实际回执在 `host-catalog-fix-plan-r1/`。

这项配置发布成功仍不代表真实制作链或浏览器验收通过。后续内部 HTTP 功能验证使用另行冻结的一条新 run 预算，明确区别于已耗尽的原产品预算；浏览器工具因站点安全检查不可用而无法继续，不通过其他方式读取页面或登录会话来替代。

## 新内部 HTTP 验证：原作通过，独立审阅超时

原内部验收工具的准备检查因主机未安装 ffprobe／ffmpeg，在提交任何任务前停止。修正版复用固定摘要 `744b6241…` 的现有渲染镜像；检查工具版本及解码只在禁网、只读、移除 Linux capabilities、无密钥和 Docker socket 的受限临时容器中运行。唯一媒体挂载是操作器新保存的 MP4，只读映射；生成源码只作 JSON 解析及哈希核对，不执行。这些解码操作不是新的场景渲染。

`internal_product_validation_v2.py` 已在 `internal-http-validation-r2/` 登记独立预算：新 run 最多 1、初作 1、独立审阅 1、可选修订 1、场景渲染 2，参考观察和 TTS 为 0，不自动重试。原失败及原预算文件摘要保持。准备检查通过后只调用一次私有执行器 HTTP POST；请求为 `199bea60-51b9-4c33-88fe-c51f448cd695`，新 run 为 `3cfbf00d-a797-4a14-adac-86da222a0d18`。随后只做 GET 跟踪，终态为 `failed / review`，业务错误 `animation_creation_review_failed`。本次预算已结束，不自动续跑或重发原请求。

初作 job `c5497623-dfdb-440a-8e1b-5806263e5b49`、attempt `181aa63e-6c86-4b62-910b-2a736f3f07e9`、fence153 已完成，4 个持久模型请求全部 complete／HTTP200。原作隔离渲染通过：8秒、1280×720，MP4为115561字节，SHA256 `187214efe44d60b98a09f4285c680c02911b3054018fde3e3191826ba1792c42`；源码JSON为10479字节，文件SHA256 `9351e99de01565355bbbbd2cc1a00821ed3bec3e36ebc84fcd663cb4df9cb6e6`。实际产物HTTP MIME、字节哈希、ffprobe和完整ffmpeg解码均通过，未执行下载的生成源码。原地媒体与报告在 `internal-http-validation-r2/artifacts-1794318025634386640/`。

独立审阅 job `7a972378-4d40-440b-8636-b6c00f66469c`、attempt `da8fa921-ee54-4225-9a5a-5f18740b47ce`、fence154 为 `timed_out`。25个持久模型请求中24个complete／HTTP200，最后1个仅admitted、无HTTP终态，不计为成功。上传24帧、22个不同的预期图片哈希；成功模型请求记录了253个图片输入，覆盖20／22个预期哈希，`all_expected_frame_hashes_transported=false`。这不能证明全部帧送达、完整审阅或理解通过。跨回合图片重复传输是后续调查线索，尚不作为超时根因结论。

两次Agent均绑定真实停止证明。审阅未产出结果，修订与技术复验均未执行；不能把未提出修改意见当成“无需修改”。实际消耗为初作1、独立审阅1、场景渲染1、修订0、复验0。原作保留，但完整制作链验收仍未通过，OpenSpec 1.3／1.4继续未完成。

本次只验证私有执行器、真实 Agent 和产物，不能冒充已登录浏览器的新一轮点击、播放器或下载验收。后续核验使用按显式 run UUID 读取的 `inspect_completed_run_server_v3.py`。原只读检查器将停止资源误写为 `pod:UID` 的假设已纠正为真实 `k8s:namespace:UID`；原版和修正版分别保留，该旧 false 不代表服务停止证明失败。

终态已同步OpenSpec，revision `7f928b40fb5c114a5d6675d76c62c33a2ce76655`；CAS更新、精确回读和validate均通过，issues为空。只替换顶部产品进展说明，所有任务复选框保持，仍为5／29完成、24项待办，1.3／1.4均未勾选；本次同步不提交模型或渲染。回执为 `openspec-product-terminal-progress-*.json`。

统一操作与原地回执位于忽略目录 `video_workbench/rendered/feedback-20261106/animation-lab/agent-creation-phase1/creation-product-r1/`；私有数据库、模型日志和凭据留在服务器，不提交 Git。
