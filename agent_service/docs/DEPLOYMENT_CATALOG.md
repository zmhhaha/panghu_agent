# 从 Worker 镜像生成部署目录

`python -m agent_service.catalog` 在已构建的领域 Worker 镜像内读取固定能力包，生成公共
部署配置、每个绑定的宿主配置和组合 fingerprint。它不访问网络、模型、数据库、Vault
或 Kubernetes，也不生成密钥、验收记录。生成器需要 Linux root，以检查能力包确实为
镜像内 root 所有、无 group/other 写权限的普通文件；拒绝链接和多硬链接文件。

先按现有构建流程得到已推送的 Pi/Hermes Worker 镜像 digest。下例中的变量由操作者填入
真实的 `registry/repository@sha256:...`，不能使用 tag；不使用某个引擎时可省略对应参数。

```bash
PI_IMAGE='填写已推送的Pi领域Worker镜像@sha256摘要'
HERMES_IMAGE='填写已推送的Hermes领域Worker镜像@sha256摘要'
mkdir -p agent_service/rendered/catalog

docker run --rm --network none --read-only --user 0:0 \
  --mount type=bind,source="$PWD/agent_service/rendered/catalog",target=/catalog \
  --entrypoint /opt/hermes/.venv/bin/python \
  "$PI_IMAGE" -m agent_service.catalog \
  --pi-image "$PI_IMAGE" \
  --hermes-image "$HERMES_IMAGE" \
  --engine pi \
  --runtime-revision domain-runtime-v1 \
  --model llm-trusted \
  --provider task-model \
  --model-proxy-url http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1 \
  --pod-cidr 10.244.0.0/16 \
  --output /catalog
```

`--pod-cidr` 必须对应实际集群网络，可重复指定。只有 Hermes 时改用其镜像运行、传
`--hermes-image` 并设置 `--engine hermes`。每次生成必须使用新目录或空目录，避免覆盖
已有目录中的验收和历史绑定。生成器没有执行上述命令；本轮代码尚未实测。

输出：

- `config.public.json`：schema 2，五个 PPT 和两个科研任务合同，以及共享能力包、工具
  环境、每引擎的执行绑定和 K8s 池。初始新任务路由由 `--engine` 选择。
- `worker-configs/<host_config_map>/config.json`：每个固定绑定的 HostConfig；包含模型
  路由标识及私有任务代理地址，包含完整镜像摘要，不包含镜像仓库部署参数、Pod UID、
  模型 API Key 或其他密钥。目录名直接对应公共池的 `host_config_map`。
- `summary.json`：每个绑定的完整组合 fingerprint、资源测量范围、宿主配置映射及公共
  配置摘要。fingerprint 只标识组合，不代表该组合经过验收。

PPT 包固定为 `hugohe3/ppt-master` 的
`c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7`，先核对镜像 build-info 的 commit，再测量
固定技能入口、技能目录直属文件、完整 references 和 LICENSE；保留 MIT 许可与归属。
这些程序资源在五个阶段间共享。官方完整 scripts/templates/字体/浏览器等工具库存由
工具环境的镜像 digest 固定；目录没有把上万个模板文件误称为已逐个列出的技能资源。

科研两阶段共享 `local-procedures-v2`，核对固定 manifest 摘要及其三个声明文件，包含
原始本地程序的 MIT 许可。它不宣称安装 K-Dense 上游技能。选择两个引擎时，两镜像应
使用同一已审查能力包来源；宿主执行时会再次测量，任何资源摘要不同都会拒绝。

所有 `routes.enabled`、`kubernetes_workers[].enabled` 和宿主 `profile.enabled` 都为
`false`。不会凭文件存在声称模型、只读挂载、工具或输出质量已经就绪，也不会预留任务
UID。服务可先部署控制层，后续根据真实组合验收记录、AppArmor/只读挂载检查及平台
预留 UID 范围开放对应绑定。历史任务恢复仍需保留原绑定、原宿主目录及原验收记录；
此命令用于生成一组新候选，不能直接删去线上历史配置来切换引擎。

生成的公共配置可直接传给现有部署脚本：

```bash
bash agent_service/deploy.sh \
  --config agent_service/rendered/catalog/config.public.json \
  --host-config-dir agent_service/rendered/catalog/worker-configs \
  --init-db
```

部署脚本继续使用已有控制镜像记录；`--init-db` 用于首次初始化独立数据库。公用服务
配置遵循现有平台规范：Agent 服务身份和数据库等凭据来自
`secret/agent-service/runtime`，模型上游调用身份来自 llm-service 的权威记录
`secret/llm-service/callers` 的 `LLM_TOKEN_AGENT_EXECUTION`。科研与 PPT 只持有各自调用
Agent 服务的 token，生成器仅写对应的 `token_ref` 和 `receipt_key_ref`。

默认模型代理上游为 `http://llm-service.llm.svc.cluster.local/v1`；llm-service 的 Service
端口是 80，8000 是容器 targetPort。需要变更受控上游时显式使用 `--upstream-base-url`。
