# 工作台服务器部署资源

这里提供离线 Kubernetes 资源生成器，供部署前审阅。运行它不会连接集群、创建凭据、调用模型或修改服务器，也不会配置公网域名、登录回调和 OAuth 会话存储。

生成内容包括工作台命名空间、持久卷申请、单副本 Deployment、内部 Service 和网络策略。程序使用 SQLite，固定为一个进程、一个副本，更新策略为 `Recreate`。数据目录挂载在 `/data`，包括数据库及 WAL 文件；请沿用现有存储备份方案。

## 生成内部资源

在仓库根目录运行，使用 Python 标准库即可。以下域名、代理标签、存储类和镜像仓库均为示例；先换成部署环境中的真实值。`VIDEO_IMAGE_DIGEST` 必须是本次推送后取得的实际 `sha256:` 摘要，`VIDEO_RELEASE_ID` 使用对应的 Git commit 或其他公开发布标识。

```sh
python -m video_workbench.deploy.render \
  --namespace video-workbench \
  --image registry.example.com/video-workbench:latest \
  --image-digest "$VIDEO_IMAGE_DIGEST" \
  --release-id "$VIDEO_RELEASE_ID" \
  --allowed-host studio.example.com \
  --proxy-namespace oauth \
  --proxy-label app=oauth2-proxy-video-workbench \
  --agent-namespace agent-service \
  --agent-service agent-service \
  --agent-label app.kubernetes.io/name=agent-execution-service \
  --agent-label component=api \
  --agent-port 8080 \
  --profile-revision 1 \
  --token-secret video-agent-service \
  --token-secret-key token \
  --storage-class ceph-rbd \
  --storage-size 1Gi \
  --output .runtime/video-workbench/deployment.json
```

输出是 Kubernetes `List` JSON，也可作为 YAML 输入。省略 `--output` 时输出到终端。完整参数可通过 `python -m video_workbench.deploy.render --help` 查看。多个允许的 Host 或代理标签分别重复 `--allowed-host`、`--proxy-label`；多个代理标签必须同时匹配。Host 不带协议、路径或端口；IPv6 使用 `[::1]` 形式。

工作台 Service 为 `ClusterIP`，端口 `8089`。上例对应的登录代理 upstream 为 `http://video-workbench.video-workbench.svc.cluster.local:8089`，代理需要保留原 Host、移除客户端提交的 `X-Forwarded-User` 并写入已验证的稳定用户标识。必须先确认这个代理的实际命名空间和 Pod 标签，再生成网络策略。

## 凭据和网络

默认引用已存在的业务 Secret，不输出其内容。凭据以只读目录挂载到 `/run/secrets/video-agent/token`，通过 `VIDEO_AGENT_TOKEN_FILE` 读取。Secret 文件只允许工作台 UID/GID `10001` 读取。不要把模型凭据或科研业务的 caller token 复制给工作台。

如果凭据已经写入 Vault，并且集群已经安装 External Secrets，可以在原命令追加以下参数，生成该业务侧的 `ExternalSecret` 投影：

```sh
  --external-secret-store vault-backend \
  --external-secret-store-kind ClusterSecretStore \
  --external-secret-remote-key secret/data/agent-service/runtime \
  --external-secret-property VIDEO_WORKBENCH_SERVICE_TOKEN
```

store、remote key、property 三项必须同时提供；这些参数仅表示凭据位置。生成器不会创建 Vault 记录，也不会给通用 Agent 添加 caller 权限。仍需通过通用服务自己的发布流程配置对应 caller、视觉任务、运行资源和验收。Secret 目录挂载支持 Kubernetes 更新文件；当前应用在启动时载入连接配置，轮换凭据后需按发布流程重启工作台。

网络策略仅允许指定身份代理访问工作台；工作台仅能访问集群 DNS 和指定 Agent API Pod，不开放公网出口。Agent namespace、API Service 和 API Pod 标签必须指向同一服务。

如果通用 Agent 现有策略尚未允许该工作台，可显式追加 `--include-agent-ingress`，额外生成 Agent 命名空间中的一条窄范围入站策略，供负责通用服务的人审阅。默认输出仅包含工作台命名空间资源。Kubernetes 网络策略按允许规则合并；现有的宽范围规则仍会生效，生成器不会删除或审计其他策略。

## 发布和校验

按仓库约定，构建和推送使用 `latest`。构建时记录公开发布标识，例如：

```sh
export IMAGE=registry.example.com/video-workbench:latest
export PLATFORM=linux/arm64
export SOURCE_REVISION="$VIDEO_RELEASE_ID"
python -m video_workbench.build_image
```

构建器使用精确文件白名单并核对视觉资源摘要；`SOURCE_REVISION` 为对应 Git commit 的小写十六进制值。只有验证实际推送摘要后才写入 `video_workbench/rendered/workbench-image.txt`。`--no-push` 可仅构建本地镜像，不会生成可部署回执。

取得实际推送摘要后再运行生成器。生成的容器引用形式为 `仓库:latest@sha256:摘要`，同时保留 `latest` 标签和精确镜像内容。Deployment 和 Pod annotations 保存发布标识、标签和摘要；Pod 标签保存发布标识，摘要变化会触发更新。

健康探针访问 `/healthz`，就绪探针访问 `/readyz`，均使用第一个允许的 Host。就绪表示应用配置和数据库可用，不表示视觉任务已发布、真实 Agent 调用已通过或模型画面质量已验证；探针不会调用模型。

上线前先审阅输出，确认 PVC 权限、业务 caller 和代理身份边界，再按现有发布流程做服务端校验。例如以下命令只检查集群是否接受这些资源：

```sh
kubectl apply --dry-run=server -f .runtime/video-workbench/deployment.json
```

新的命名空间、ExternalSecret CRD 或其他依赖不存在时，先根据校验结果处理部署顺序。此生成器不自动应用资源，也不保存或恢复线上 Deployment。更改服务器前应保留原资源和数据库备份，确认回退所需的历史镜像摘要。公网入口需要另行配置真实域名、TLS、Casdoor 回调和隔离的登录会话存储；现有 OAuth 脚本对未登记服务可能复用已经占用的 Redis DB，不能直接套用。

离线验证：

```sh
python -m pytest tests/video_workbench/test_deploy.py -q
```
