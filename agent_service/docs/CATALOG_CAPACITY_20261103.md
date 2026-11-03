# 共享目录的有限扩容与发布预检

公开目录保留全部不可变历史，不删除绑定、不改写旧 Worker host，不触碰永久 UID 分配账本。能力包、工具环境、执行绑定和 Kubernetes pool 的读取上限从 100 提升到 256；合同、route、caller 的上限仍为 100。历史目录的读取字节上限保持 4 MiB。

这次实现有限扩容，没有实现长期归档或历史裁剪。目录达到新的数量或字节预算后仍会拒绝继续发布，不能通过重置 UID 或删除历史配置回收容量。

## 消费端兼容门槛

默认有效上限仍是 100。发布超过 100 的候选前，管理员必须分别验证实际控制 API、Dispatcher、模型代理与候选 Worker 的不可变镜像和报告，提供以下四项记录：

```json
{
  "schema": "agent-catalog-consumer-compatibility.v1",
  "consumers": [
    {
      "role": "control-api",
      "image": "registry.example/agent-control:latest@sha256:<64位实际摘要>",
      "history_record_limit": 256,
      "evidence_sha256": "<64位实际报告摘要>"
    }
  ]
}
```

示例只展示一条记录的格式，不能用于放行。完整记录必须包含 `control-api`、`control-dispatcher`、`model-proxy`、`candidate-worker`，不可重复。遗漏任意角色或某角色仅支持 100 时，105 条记录的候选仍被阻止。零报告摘要、非固定镜像或额外字段也不接受。普通部署渲染器核对三个控制角色的镜像必须与本次 `--control-image` 完全一致；新增 host 使用的镜像必须与候选 Worker 记录一致。

这些是管理员核实后的输入，工具不会独立证明线上 Pod readiness，也不会根据提供了镜像名就宣称运行验收通过。组合 acceptance、节点隔离和正式发布授权继续走原有流程。

当前线上共享控制镜像可能含有较新的其他服务实现。升级前应从实际固定 donor 摘要核对源码和依赖，只叠加经过审定的容量差异，保留其余文件原始哈希，不能直接用较旧业务分支的完整 Agent 源码覆盖控制面。

仓库内的 `agent_service/deploy/build_capacity_overlay.py` 接受管理员取得的 donor 检查报告（实际镜像摘要、image ID、Python SHA manifest 和 `config.py` / `registry.py` 原文）。它从精确 donor 构建，只替换两个文件中明确识别的计数/读取门槛并添加 `catalog_limits.py`，保留其他控制逻辑。构建上下文只有这三个文件和 Dockerfile，没有 `RUN`、`ENV`、`ARG` 或依赖安装。

```bash
python agent_service/deploy/build_capacity_overlay.py \
  --inspection /path/to/control-capacity-inspection.json \
  --image arm-cluster-master:5000/agent-service-control:latest \
  --source-revision <本次干净Git检出的40位提交> \
  --output-dir /path/to/new-capacity-overlay-evidence \
  --push
```

构建器不会启动 donor/candidate 容器：通过 `docker create` / `docker export` 分别读取完整镜像 rootfs，计算所有常规文件的 SHA 并保留目录、链接、权限、UID/GID 和非时间戳 pax 元数据。前后差异必须恰好是审定的三个文件，非 Python 文件也参与检查；结果保存两份完整 manifest 和其摘要。运行时 Env、入口、命令、用户、工作目录、卷及其他配置保持 donor 原值，未知环境键、疑似凭据值和 `OnBuild` 被拒绝。构建必须来自包含该工具和固定预算模块的干净 Git 来源，输出目录必须为新目录。

这证明镜像文件保留边界，没有证明新镜像已通过实际读取验收。`runtime_catalog_reader_verified`、`publication_authorized` 均为 false。管理员仍需在服务器中对精确新镜像验证 95/100/105、所有旧合同与绑定，以及三类控制进程的启动/健康，再登记四类消费端兼容证明。推送镜像不会修改任何线上工作负载。

## 精确镜像的只读 reader 验证

从当前服务只读取得 public 配置和全部 95 个不可变 host 的 `config.json`，按原有 `worker-hosts/<ConfigMap 名>/config.json` 结构保存。不要读取或复制任何 Secret。随后在干净 Git 检出中运行：

```bash
python agent_service/deploy/verify_capacity_reader.py \
  --image <新control仓库:latest@sha256:实际摘要> \
  --legacy-image <原100条上限的control仓库:latest@sha256:实际摘要> \
  --public /path/to/current.public.json \
  --host-config-dir /path/to/worker-hosts \
  --output-dir /path/to/new-reader-evidence
```

默认 image-owned 包根为 `/app/agent_service`、解释器为 `/usr/local/bin/python`，可通过 `--package-root` / `--python-executable` 指定受信镜像中实际路径；旧 donor 路径不同时使用两个对应 `--legacy-*` 选项。默认期望 95 条实际 binding/pool，`--expected-binding-count` 可显式适应其他尚未超过 100 的现状。

每个镜像只运行一个无网络、只读、非 root、清空 capabilities 的验证容器。不挂载 `/src`、仓库、数据库、HOME 或秘密，配置仅由 stdin 进入。自包含 `python -I -B -c` 程序先固定 image-owned 包路径，再验证三个实际 reader 模块都来自该包；记录实际源文件 SHA。检查当前全部 host/pins，追加无 acceptance、停用且不保留 UID 预留的虚拟 binding/pool 到 105/256，原有 95 条和 route 保持原样，验证完整扩展 host 和原 pins。257、未知 public/binding/host 字段必须被拒绝；旧 donor 必须仍能读取当前全部 host/pins且拒绝 105。验证不访问永久 UID 账本，也不将虚拟记录写回配置。

成功输出 `reader-verification.json` 及其精确字节 SHA 引用的 `compatibility.json`。默认只证明共享镜像中三个 control reader 的计数兼容，不冒充进程健康或正式业务验收；`runtime_process_health_verified` / `publication_authorized` 仍为 false。候选 Worker 应对其自身精确新/旧镜像及 image-owned Python/包路径单独运行 `--consumer candidate-worker`，只生成该角色的证明，再由管理员合并四角色兼容文件。任一镜像检查失败不会产生兼容文件。

## 精确字节预算

| 内容 | 新发布上限 |
| --- | ---: |
| 五组目录数组的 canonical UTF-8 JSON | 768 KiB |
| 完整 public canonical UTF-8 JSON | 896 KiB |
| 每个实际 ConfigMap 的 canonical JSON，包括 API wrapper、metadata、data 键和值及转义 | 1 MiB |

host ConfigMap 的计算包括 `config.json`、实际存在的 `acceptance.json` 和 `acceptance-evidence.json`。不会只计算内层目录并忽略 JSON 字符串转义膨胀。报告记录实际字节数、余量、内容摘要和固定失败原因；输出没有凭据或业务资料。已有不可变 host 可以保留自己的历史目录，新增 host 必须提供完整新目录并匹配所选 binding pins、镜像、输入支持和 timeout。

## 离线预检

在服务器上通过 Git 获取代码后运行。命令不连接数据库、模型或集群，不生成验收，不分配 UID，也不写入配置或输出目录：

```bash
python -m agent_service.deploy.catalog_capacity \
  --current current.public.json \
  --candidate prepared.public.json \
  --host-config-dir worker-hosts \
  --compatibility verified-consumers.json
```

仅需提供候选中新引用 host 的目录及文件，原有 host 引用保持原值。退出码 0 代表容量预检就绪，2 代表被阻止。`publication_authorized` 始终为 false。缺少兼容文件时可省略 `--compatibility`，有效上限会保守地保持 100。

正常部署的 `render.py` / `deploy.py` 增加 `--catalog-compatibility verified-consumers.json`。渲染器在创建输出目录前执行同一门槛，失败不写入 manifests；通过时保存 `catalog-capacity.json`。旧配置保留原始 host/pins，PPT route 可指向新绑定，旧 PPT pool 只允许由启用变为停用，其余历史记录和已有 caller 授权保持不变。

`count_only_ppt_releases_remaining` 按每次五阶段绑定、pool、工具环境各增加 5 和能力包增加 1，计算数量余量。它没有预测未来包资源或验收证据的字节增长；每次正式发布仍必须重新计算实际完整字节。

## 验证边界

`tests/agent_service/test_catalog_capacity.py` 覆盖 95 → 100 → 105、完整与缺失/混合消费端证明、256/257 边界、历史保留、旧 host 恢复、精确 wrapper 字节、历史读取预算和普通渲染器的默认阻止。每个实际携带超过 100 条历史记录的 host，必须由其引用镜像的 256 兼容证明覆盖，不能借用另一个禁用历史池的镜像证明；保留的旧 100 条以内 host 不要求升级。`test_capacity_overlay.py` 覆盖最小 donor 补丁、完整非 Python 文件和链接保留、环境/OnBuild 拒绝。测试应在服务器执行；文档和测试代码本身不构成线上已验证的证据。
