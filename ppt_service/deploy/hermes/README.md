# 用 Hermes 接通 PPT 执行

本目录为现有正式 `ppt-service` 网站添加独立执行控制器。先用 Hermes 跑通 PPT；通用 Agent
接口层后续再做。网站、OAuth、白名单、PostgreSQL 和 Ceph 数据卷沿用现有部署。
这是部署资产和操作入口，离线测试通过不等于已完成线上生成验收。

## 组件和权限

| 组件 | 职责 | 访问范围 |
| --- | --- | --- |
| `ppt-hermes-controller` | 单并发领任务、准备目录、启动/停止任务、独立验收、发布产物 | runtime DB、完整 `ppt-data` PVC、namespace 内限定种类的 Kubernetes API |
| 每次任务的 Hermes Worker | 使用 PPT Master 生成候选结果 | 本任务的输入/工作/结果子目录；只能访问本任务的模型代理 8080 |
| 每次任务的模型代理 | 固定模型和 Responses 上游、记录用量 | 本任务用量目录；上游仅 `62.234.50.20:443` |
| 解析/验收沙箱 | 解析不可信材料、独立渲染和产物校验 | 本次输入及输出子目录；禁止网络 |

Controller 从已验证的小型网站镜像派生，只补当前 `control/server/worker` Python 源码。
它不引入 Hermes 底座。Worker overlay 沿用已缓存的 PPT Worker，保留 PPT Master、字体、
Chromium 和文档工具，同时覆盖本次受审源码；显式替换继承的 ENTRYPOINT。
构建上下文逐个收集 Python 源文件，不递归拷贝 HOME、`.venv`、`.env`、上传文件和 Git。

Controller 是可信组件，使用专用 SA 和 namespace Role，没有 ClusterRole、Docker socket、
hostPath 或特权容器。挂载完整 PVC 的目的为服务端准备/冻结/发布文件；与网站和任务一样使用
UID/GID `10000:10000`，`runAsNonRoot=true`，丢弃所有 capabilities，兼容 namespace 的
`restricted` Pod Security 策略。现有 `state` 目录须属于 `10000:10000` 且权限为 `0700`；
Controller 创建的文件与任务产出归同一用户，文件复制、权限冻结与恢复无需 root。任务间隔离
由各 Pod 仅挂载本次任务的 PVC subPath、独立网络策略和权限边界实现。任务 Pod 使用另一个
无 RBAC 的 `ppt-worker` SA 且不挂 token。Worker 和代理的任务 Secret/最终停止证明由后端
按 attempt 管理；只有所有容器停止后才移除 `ppt-service.panghuer.top/writers-stopped` finalizer。
历史任务的 authority journal 没有 `metadata_prefix` 字段时，控制器继续使用原有 metadata
前缀核验和收尾；新任务记录新前缀。两类任务均保留精确 API UID、镜像绑定和停止证明要求。
不要强删 Pod、移除 finalizer 或先删 namespace 来替代停止证明。

## 1. 投射现有上游凭据

按 [Vault 执行凭据说明](../../../../vault/inventory/PPT-EXECUTION.md) 应用 ExternalSecret，
精确读取已有 `secret/llm-service/providers.CUSTOM_API_KEY`，不复制新 Vault 记录。
只有任务的模型代理投射 `ppt-model-upstream.PPT_UPSTREAM_API_KEY`。
网站、Controller、Hermes Worker 和沙箱不把供应商 key 放进环境或文件。
这是复用共享供应商 key，不代表独立供应商配额或独立限权 key。

## 2. 在服务器构建并推送两个镜像

在 `panghu_agent/ppt_service` 目录操作。网站基底必须使用已经推送的 registry manifest digest；
Worker 基底可用服务器现有缓存的本地 ID，最终执行镜像必须是 overlay 推送后的新 digest。
以下变量是公开的镜像引用，不是 API key：

```bash
PPT_SITE_BASE=$(cat rendered/image.txt)
PPT_WORKER_BASE=sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a
PPT_BUILD_TAG=$(date -u +%Y%m%d%H%M%S)
bash deploy/hermes/build.sh \
  --site-base "$PPT_SITE_BASE" \
  --worker-base "$PPT_WORKER_BASE" \
  --controller-tag "arm-cluster-master:5000/ppt-hermes-controller:$PPT_BUILD_TAG" \
  --worker-tag "arm-cluster-master:5000/ppt-hermes-worker:$PPT_BUILD_TAG" \
  --push
```

默认只构建；`--push` 才发布。两个镜像全部成功后才记录 `deploy/hermes/rendered/build.json`，
推送后另有 `controller-image.txt` 和 `worker-image.txt`，内容为带仓库名的 manifest digest，
不能把本地 `sha256:...` image ID 当成部署引用。构建 import 检查不调用模型。

## 3. 渲染并检查清单

当前实际控制平面端点为 `192.168.137.101:6443`。Controller 显式使用
`PPT_KUBE_API_SERVER=https://192.168.137.101:6443`，保留集群 CA/TLS 验证，
NetworkPolicy 精确放行该 `/32` 的 TCP 6443，避免 Service DNAT 地址的歧义。
若迁移集群，先重新查询实际端点并同时调整地址和对应策略：

```bash
kubectl -n default get svc kubernetes -o wide
kubectl -n default get endpoints kubernetes -o wide
```

将实际地址填入命令。每个参数必须为单一主机 `/32` 或 `/128`，不能填整个网段：

```bash
bash deploy/hermes/deploy.sh \
  --controller-image "$(cat deploy/hermes/rendered/controller-image.txt)" \
  --worker-image "$(cat deploy/hermes/rendered/worker-image.txt)" \
  --kube-api-cidr 192.168.137.101/32 \
  --kube-api-server https://192.168.137.101:6443
```

此命令只生成 `deploy/hermes/rendered/runtime.json`。
检查 SA/RBAC、两个镜像 digest、PVC、上游 Secret 引用和 NetworkPolicy。
默认 Controller 的执行准入为 disabled，不会新增付费任务；重复上述命令加 `--apply` 才应用。
部署 helper 校验正式 namespace、已有网站、Bound RWX PVC、runtime/upstream Secret 的必要键，
执行 server dry-run 并等待 controller rollout。它不创建/迁移数据库、不替换网站和 owner。

现有 `dns-egress` 原本匹配所有 Pod，NetworkPolicy 规则会叠加，因此清单将它收窄到网站、
迁移、PostgreSQL 和 Controller。任务 Pod 没有 DNS egress：Worker 通过本次代理 PodIP
的固定 host alias 访问代理，代理通过固定上游 IP 对应 TLS 域名。任务网络策略由后端按
attempt 精确创建。原网站 baseline 的同名 DNS 策略也已同步收窄，普通网站升级会保留此边界。

## 4. 验证网络并接通网站准入

先验证 Controller 可以连接 runtime DB 和 Kubernetes API；模型密钥只存在于 proxy Pod。
用实际 CNI 验证允许及拒绝流量：Worker 只到本次代理 8080、代理只到固定上游 TCP 443、
Worker 无互联网/数据库/API/DNS访问，沙箱无网络，Controller 无任意互联网。
`--enable-execution` 表示这些实际策略已被操作者确认，清单本身不能证明网络已隔离。

验证后重复相同部署命令，加 `--enable-execution --apply`。网站使用同版本的新镜像后，
可再显式加 `--enable-site`：它只在 `--apply` 和 Controller 已开启时有效，等待 Controller
rollout 后仅更新网站 ConfigMap 的 `PPT_EXECUTION_ENABLED=true`、
`PPT_EXECUTION_BACKEND=kubernetes` 两项并重启/等待网站 rollout。默认不改网站准入，
不会修改 OAuth、白名单或数据库连接；disabled 状态不会先入队再自动执行。

MaterialParser 和 dispatcher 在同一个 Controller 进程循环中运行，共用 Kubernetes
backend、素材解析 scope 和任务单槽，不另建常驻 parser Deployment。

图表任务需要独立审核的数据 oracle。默认 `expected_charts=[]`，真实图表与空清单不匹配时
仍拒绝发布；当前不声明通用自动图表可用。管理员可将按精确 input snapshot hash 建索引的
审核 JSON 放入 ConfigMap 的 `oracles.json` 键，再传 `--chart-oracle-configmap NAME`。
该文件以只读 `subPath` 挂载，供 `PPT_CHART_ORACLE_FILE` 的 no-follow 读取器校验。
不能拿未经审核的 Agent manifest 充当 oracle。修改 ConfigMap 后需要重启 Controller
更新 subPath 文件，不会在运行任务中热换审核依据。

最后在网页走完整流程：上传材料、第一轮方案确认、第二轮方案确认、准备、生成、修改、
下载；另外验证取消和重启恢复。生成、独立校验及取消的线上回执齐全后，才能记为验收完成。
当前不把离线清单测试或镜像 import 检查记为真实 PPT 生成成功。

## 停止与升级

暂停新请求时，先关闭网站执行准入；让已运行任务停止并完成控制器回收，再升级执行镜像。
不要用 `kubectl scale ... --replicas=0` 当作停止 Worker 的保证：任务是独立 Pod。
控制器崩溃后，替代实例按数据库租约和 fencing token 对旧执行做停止/失败回收，不自动重试
可能已计费的模型阶段。单实例 `Recreate` 配合数据库全局单槽保证初始并发为 1。
保存数据库、PVC 和用量回执；本扩展不包含删除数据操作。
