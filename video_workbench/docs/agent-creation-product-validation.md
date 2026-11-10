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

## 首次真实制作：失败，正在核查

已登录浏览器在群体筛选与统计实验的开发输入中实际点击一次“制作并打磨一轮”，请求编号 `d6bc962b-b1e8-42a7-8cec-5e4c9ccac05a`，产品 run 为 `867bf1be-46f0-431a-ae99-3d6fad4af44e`。实验定义身份为 `67a09e0378cbbb991bae4bfe1456d2bd43ff784dd13cda14823d698e5780c428`。

初作 operation `867bf1be-46f0-431a-ae99-3d6fad4af44e:initial_creation` 已获得真实 Agent job `3ad38f58-0033-446d-89e0-05978515d70e`，但终态为 failed，产品错误 `animation_creation_upstream_failed`。尚无作品、审阅或渲染。原失败保留，不重发制作、重置额度或把候选验证拼成此次成功。

随后只读核查得到 Agent 错误 `capsule_stopped_without_result`：唯一 attempt `7072eee5-daee-410a-a5f5-7fa608ac16b5`、fence 152，Pod UID `36702922-2b29-464d-9d7a-9ccce7a7a5d8`。该 attempt 的持久模型请求记录为 0；API 返回 `stop_proven=true`、`not_started=false`。这表示控制器证明容器已停止、未获得 Worker 执行结果，不能写成 Worker 从未启动；具体启动失败原因仍待核查。此时制作执行器 Pod 为 1/1 Running、零重启。

已用同一固定 Worker 镜像及线上实际 immutable Host ConfigMap 复现配置缺陷：原发布操作器将目标角色的单独目录覆盖为整个平台目录，加载了 21 个任务合同、66 个执行组合，其中 `video.process-plan.v1` 在该固定镜像中没有对应实现，导致 `_host_registry` 拒绝配置。把目录投影为当前角色引用的合同、能力包、工具环境、执行组合及路由各一项后，`host_policy` 和 `accepted_host` 静态检查均通过，组合摘要保持 `d57d40b7ff854183e89c8c5a66eabbb4bb3c02057cdde6e08d9d9960482c1db8`。这是实际配置校验的确定性缺陷；原 Pod 已按正常停止流程删除，未取得其启动日志，不补造原日志。

该预检只读取 ConfigMap，不加载 Secret，不启动 Worker／Agent 或模型；两个临时 Docker 容器均禁网、只读根文件系统、去掉 capabilities、无宿主机挂载。实际 Worker 以 root 控制进程启动，因此根因使用 root 静态检查的缺失 handler 结果；非 root 辅助检查的额外文件权限错误不作为生产根因。静态配置通过不证明实际 Pod 隔离或后续制作通过。原地证据为 `static-worker-config-check-1794314278555204542.json`。下一步仅修正两个新动画角色的 Host 配置闭包，补永久回归后再实际验收。

浏览器刷新后重新匹配同一请求与 run，显示原失败记录，未再提交制作。刷新恢复和失败展示已实际观察；作品播放、定位、源码下载和成功制作全链仍未完成。该预登记一个 run 的额度已使用，后续先只读诊断，不能自动重开旧预算。OpenSpec 1.4 继续未完成，旧案例 E 漏检的 1.3 也不因本次发布改变。

统一操作与原地回执位于忽略目录 `video_workbench/rendered/feedback-20261106/animation-lab/agent-creation-phase1/creation-product-r1/`；私有数据库、模型日志和凭据留在服务器，不提交 Git。
