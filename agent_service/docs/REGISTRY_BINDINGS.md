# 合同、能力包与执行绑定注册

> 历史注册实现快照：下文“唯一可执行合同”等描述对应早期注册提交。后续领域合同、
> 多绑定路由及目录生成器的最新实现见 [实现交接](IMPLEMENTATION_HANDOFF_20261026.md)
> 和 [部署目录](DEPLOYMENT_CATALOG.md)。新增代码尚未测试、构建或部署，
> 早期注册验证不覆盖它们。

2026-10-26。本轮实现了目标架构的注册与解析部分，尚未接管线上 PPT/科研。
[架构边界](CAPABILITY_ARCHITECTURE.md) 与 [业务覆盖矩阵](BUSINESS_CAPABILITY_COVERAGE.md)
继续描述领域能力及后续接线。这里说明已经实现的配置和拒绝行为。

## 配置结构

控制配置直接使用 `schema_version: 2`，不再读取版本1的合并式 `profiles`。Jobs API
仍为版本1，业务请求只选择 profile/revision；这是两个不同的版本边界。
配置示例见 [静态宿主](../config.example.json) 和 [按任务 K8s](../config.kubernetes.example.json)。

| 目录 | 固定的内容 |
| --- | --- |
| `task_contracts` | 任务/输出实现标识、工具与模型能力要求、时限 |
| `capability_bundles` | 资源名称/摘要、来源、许可、所需工具 |
| `tool_environments` | 工具接口、输出能力、runtime revision、镜像 digest |
| `execution_bindings` | 合同与包/环境的精确引用、引擎、适配器版本、模型策略、组合验收引用 |
| `routes` | 合同 revision 当前选用的 binding revision 和是否允许新任务 |

这是同一执行服务的五个逻辑目录。每个目录最多100项，拒绝重复身份、未知字段、
无效引用、动态模块/命令、模型密钥及任意 endpoint。调用服务的 profile 权限须引用
已登记合同。Secret 文件格式保持独立，模型密钥不放目录里。

当前唯一可执行合同是 `workspace-task.v1`：明确选择 `workspace-text.v1` 任务实现和
`text-output.v1` 输出实现，只接 `prompt`，仅 read/write，不支持运行输入文件。
PPT/科研名称和未实现 schema 会配置失败，不能用文本 validator/collector 冒充领域
实现。新增领域合同需先提交并验收真实 schema、工具环境和 collector，再扩展代码白名单。

## 解析、验收和新任务路由

配置加载先核对合同/包/环境引用、资源摘要格式、工具和模型能力，再生成不可变的
组合。模型策略明确使用 `openai-responses`，固定公开 provider/model 路由标识与策略
revision。它不包含供应商 API Key 或请求提供的模型 URL。

合同、包、环境、模型策略分别计算内容摘要；再绑定 engine、adapter/runtime/image
计算 `combination_sha256`。验收记录精确引用该组合及外部报告摘要：

```json
{
  "acceptance": {
    "combination_sha256": "<check 输出的精确组合摘要>",
    "suite": "workspace-contract.v1",
    "report_sha256": "<实际验收报告的 SHA256>"
  }
}
```

占位符不是合法配置。没有验收引用只能保留禁用路由；启用却缺少记录、报告摘要为零
或记录指向旧组合时配置失败。包、工具镜像、runtime、adapter、模型策略、合同发生
变化，都要重新验收。记录由可信部署者维护，配置检查不会运行验收，更不能凭自己
填写一个摘要声称真实模型或生产隔离已经通过。

只读检查仍使用原入口：

```bash
python -m agent_service --config /etc/agent-service/config.json \
  --secrets /run/secrets/agent-service.json check
```

输出 `catalog.combinations` 提供待验收组合摘要和是否存在批准记录；不访问数据库、
启动 Pod、调用模型或输出凭据。原生 Worker readiness 和独立宿主验收仍是后续门。

新 Job 保存完整 `execution_binding` pins，包括各资源身份/摘要、组合摘要与验收记录
摘要。它进入持久 Job、Worker 请求摘要、停止回执及 K8s 资源身份。已排队/执行的任务、
幂等重投和控制器重启均使用原组合；切路由只影响新的逻辑任务。

新增引擎路由时可以复用同一合同、能力包和环境引用，登记新的适配器及组合验收后
改 `routes.binding_id/binding_revision`。不必修改业务调用的模型/Agent 名称。当前
K8s adapter 按完整绑定选择同一 engine 下的不同 profile/pool。
运维须保留旧任务所需的镜像、宿主配置和执行资源，不能删除旧组合后期待新宿主接管。

## Worker 如何核对

受信 [Worker 配置](../worker.config.example.json) 的 `execution_catalog` 包含同一组
五目录。应从控制配置选出的精确组合派生，放入不可变 ConfigMap；不接受 Job 上传
catalog。宿主解析出的合同、完整绑定、engine/runtime/image、enable 和 timeout 必须
与控制端一致，任一摘要不同都不会进入原生 admission。

当前 Pi 镜像装配 `pi-cage-adapter-v1`，核对用户 pi-cage 仓库的固定入口、源码提交和
实际安装版本，详见 [pi-cage 接入](PI_CAGE_INTEGRATION.md)。宿主按合同实测资源内容、
manifest、工具及 provider/model；配置里的包描述不能改变镜像内的文件路径或脚本。
PPT/科研领域合同已有实现，真实完整验收仍需逐项通过；注册项本身不表示可用于生产。

K8s 注入 `AGENT_EXECUTION_BINDING_SHA256`，宿主 scope 与 `acceptance.json` 额外核对
`execution_binding_sha256`。宿主验收仍需要独立 evidence 文件和模型/隔离/任务代理
授权证明；控制端组合批准记录不替代这些条件。任务模型 grant 已按 attempt 签发和
注入；尚未通过真实领域验收的组合继续禁用。

旧无 catalog 宿主仅保留现有离线 capsule 测试构造，不能匹配新控制配置生成的有绑定
任务。生产新配置须使用带 catalog 的宿主。执行结束仍先可信确认所有 writer 及原 Pod
容器停止，再完成产物交接；本轮注册扩展没有改变停止、quarantine 或发布权限。

## 已验证和后续工作

本轮覆盖组合失配/缺依赖/未知合同拒绝、资源和模型策略变化使验收失效、不可变解析、
幂等重投/路由变更/重启固定原组合、控制到Worker/K8s的精确身份传递和宿主实际资源
核对。Linux 完整回归及真实 PostgreSQL 证据见 [验证记录](REGISTRY_VALIDATION_20261026.md)。

下一步是领域输入、多文件/二进制 collector 和真实 PPT/科研合同，然后完成模型授权、
两引擎生产宿主与业务连接器；本轮没有把这些缺口登记为完成。
