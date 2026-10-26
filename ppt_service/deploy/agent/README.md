# PPT 通过通用 Agent 服务执行

PPT 网页及 API 保留现有的项目、资料、两次确认、任务、审核稿和下载接口。
新的 `agent-service` 控制器把业务任务提交给通用 Agent 服务，不传引擎名、
供应商密钥或 Hermes/Pi 的 HOME。执行引擎、模型、工具环境和能力包由
`agent_service` 的部署目录及审核过的执行绑定配置统一管理。

本目录为可选部署接线。默认部署和现网后端不会因为代码合入而切换。
此次代码整理尚未执行测试、真实模型调用、构建或部署。

## 任务与权限

专用 `ppt-service` 调用凭据只授权以下任务合同：

```text
ppt.plan_initial.v1
ppt.plan_options.v1
ppt.prepare.v1
ppt.generate.v1
ppt.revise.v1
```

每个任务的 profile revision 默认 `v1`。Pi 与 Hermes 必须分别经过对应能力包、
工具镜像、模型接口与工具组合的验收，配置路由才可启用。
generate/revise 的绑定需要图像输入与实际逐页图像检查能力。

服务连接默认为 `http://agent-service.agent-service.svc.cluster.local:8080`。
调用凭据由共享凭据管理流程写入 PPT 命名空间内的 `ppt-agent-service` Secret
的 `token` 字段，控制器只读挂载 `/etc/ppt-agent-service/token`。
OAuth、Cloudflare、Vault 和模型供应商密钥继续在各公共服务的现有目录管理；
本目录不复制它们，也不创建新的域名或登录应用。

## 数据与确认

`AgentOutbox` 与 PPT Job/ExecutionSlot 保存在同一 PostgreSQL 内，由 schema 3
迁移安装。请求意图、输入上传引用、远端任务 ID、幂等标识、执行绑定、事件游标、
检查点和业务接收状态均持久化；重启后继续同一任务。取消或不确定提交不会产生
第二次付费任务。终止证明不足或 quarantine 时保留业务执行槽。

两次确认来自 PPT 数据库内确切 Plan 记录，其确认内容必须匹配不可变输入快照。
Stage2 前必须有 Stage1 确认，prepare 前必须有选中方向的 Stage2 确认。
revise 只能使用明确选中的、同一所有者的 published Version 和精确检查点。
审核稿不能成为修订基线。模型输出不构成网站确认或发布许可。

原始资料位于 `inputs/<asset-id>.<extension>`；一个资料文件最多 16 MiB。
检查点文件恢复到 `base/project/<project-root>/...`，索引位于
`base/result/checkpoint.json`。业务工作区与输出为 `project/` 和
`result/manifest.json`。控制器停写后重新测量所有文件，并创建
`result/checkpoint.json`。最多 2048 个输出文件、每文件 64 MiB、合计 256 MiB。
原始资料与检查点挂载只读；检查点不含原生会话、凭据或 HOME。

此接线只读取中立的 `ppt-agent-artifacts-v1` 检查点。现有 Hermes/Pi 专用检查点
不自动转换；切换后需要从新的初始规划进入流程，旧项目与版本仍可查看下载。

## 构建与接线

构建沿用现有 source-only ARM64 overlay：只打包业务代码和显式列出的通用客户端/
合同/PPT profile 源码。独立校验器镜像仍使用现有完整 PPT Master 工具镜像，
其解释器属于该校验镜像自身，不成为业务任务合同的一部分。

```bash
bash ppt_service/deploy/agent/build.sh \
  --site-base '<site-registry-image@sha256:digest>' \
  --worker-base '<reviewed-ppt-tools-image@sha256:digest>' \
  --controller-tag '<private-registry>/ppt-agent-controller:<version>' \
  --worker-tag '<private-registry>/ppt-agent-validator:<version>' \
  --push
```

使用新 site/migrator 代码，先通过现有数据库迁移入口安装 schema 3。
renderer 只生成配置，加入 `--apply` 才会写入集群，`--enable-execution` 才允许提交：

```bash
bash ppt_service/deploy/agent/deploy.sh \
  --controller-image '<controller-registry-image@sha256:digest>' \
  --worker-image '<validator-registry-image@sha256:digest>' \
  --kube-api-cidr 192.168.137.101/32 \
  --kube-api-port 6443
```

确认服务端能力与网络隔离验收后，显式添加 `--enable-execution --apply --enable-site`。
renderer 原位替换已有 controller Deployment，保留其标识以避免同时运行两套调度器。
切换前应等当前执行槽释放；发现旧控制器任务尚在运行时，新接入层会拒绝抢占。
site 的常规后续部署会保留已经选定的 `agent-service` 后端。

控制器仅访问 PPT PostgreSQL、Kubernetes API、DNS 和 Agent Service API；
不接收上游模型 Secret，也不需要创建 Secret 的 RBAC 权限。
Agent Service 侧还必须允许来自此控制器标签的 8080 入站，遵循通用服务部署配置。
校验器 Pod 有独立 deny-all 网络策略和固定入口，其精确 Pod UID 的全部容器
实际终止后才能冻结验证证据。远端模型停写回执不会被当成校验器停写证明。

规划与 prepare 通过独立检查后可以推进业务阶段。generate/revise 完成真实
静态检查、重新渲染和原生包级检查后仅保存 review；视觉、完整后验交付及
PowerPoint 验收仍按既有发布门控制。业务数据库提交成功后，才向通用服务
发送 `candidate_received` 接收确认，它不会授予发布权限。
