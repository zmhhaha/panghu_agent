# 科研接入 agent-service：2026-10-29 验证记录

本页接续 [暂停交接](SESSION_CLOSE_20261029.md)。用户已授权继续将科研接入通用
agent-service。本轮已用修复后的精确 Worker 镜像重新完成真实科研生成、固定证据
修订及独立隔离业务发布，旧镜像验收没有继承到新镜像。

科研生产 API 已切换为 `agent-service`，两个科研 capability 均为 ready，实际执行
引擎为用户的 pi-cage。正式 Jobs HTTP `r2` 的生成、修订、发布与回执已通过，且已
独立核对控制账本。下文分别记录候选、隔离业务 HTTP 和正式业务 API 的验证范围。

## 固定运行来源

- 业务仓库：`zmhhaha/panghu_agent`，分支 `codex/agent-execution-service`。
- Worker 功能提交：`6107b20fd23e099a0b354e1d82434fac86402df2`；该提交修复
  Kubernetes 只读投影挂载与初始空工作目录的启动检查。
- 实际 Worker 镜像：
  `arm-cluster-master:5000/agent-service-pi@sha256:c7208ced7b634686b3a8660da4506754538b1c1e493876d3a001fea17edd76ba`。
- 用户的 `zmhhaha/pi-cage` 仍固定为
  `e0262e65e43c6924164e995394f715dc5d19c210`；入口
  `/usr/local/lib/pi-cage/worker-cli.mjs`，Pi `0.99.2`，adapter 为
  `pi-cage-adapter-v1`。
- 候选配置：服务器
  `/root/agent-service-deploy-20261026/catalog-6107b20-scientific-startup/generated/`。
- 科研生成授权 `read,write,academic_search,academic_verify`；固定证据修订授权
  `read,write`。模型使用任务代理，不把供应商 API Key 放入 Worker。

此前无模型启动检查和 Linux 定向回归的范围见暂停交接；本次真实候选补充的是
上述精确镜像的领域执行与产物证据，不扩大为 PPT 或 Hermes 验收。

## 新镜像真实科研生成

| 项目 | 已确认结果 |
| --- | --- |
| 候选名称 | `candidate-pi-startup-gen-r1` |
| 永久消费 UID | `42000081..42000083` |
| Job | `681cd62e-8b8a-45ea-b385-99539b0c467d` |
| Attempt | `46310268-d8aa-44ad-886e-85df0c55a82b` |
| 模型账本 | 12 条请求全部 `complete/200` |
| 学术工具 | 一次真实搜索、一次标识符核验 |
| 报告大小 | 3375 bytes |
| 报告 SHA256 | `e497065d8c59901bb9ada12221f9b98bfad24468eb30a09906be456ddf2bde7d` |
| 独立验收 | 产物、引用及工具观测复核通过，隔离业务发布通过 |

服务器证据位于 `evidence/candidate-pi-startup-gen-r1/`，隔离业务数据位于
`research-business-startup-r1/`，独立生成发布记录位于其中的
`verified-generation/`。这里只证明有界检索下的生成与发布合同通过，不把结构校验
等同于科学结论正确。

## 新镜像固定证据修订

| 项目 | 已确认结果 |
| --- | --- |
| 候选名称 | `candidate-pi-startup-revise-r1` |
| 永久消费 UID | `42000084..42000086` |
| Job | `a5c4a296-24ef-4a12-b357-b15adc1a287c` |
| Attempt | `4e233e47-2d63-49c9-aa34-ba2b348112d5` |
| 模型账本 | 4 条请求全部 `complete/200` |
| 修订报告大小 | 3535 bytes |
| 修订报告 SHA256 | `2bbcb4ac0d911f65b69640097d4daf4a31cd27a2855d23d039f1a386a2d2b234` |
| 授权选区大小 | 569 → 729 bytes |
| 边界复核 | 选区外字节与固定证据保持不变 |
| 业务版本 | 独立隔离发布与比较通过，修订尚未采用 |

修订基于上述实际生成结果准备，未伪造父候选或扩大检索权限。证据位于
`evidence/candidate-pi-startup-revise-r1/`，独立修订发布记录位于
`research-business-startup-r1/revision-candidate-r1/verified-revision/`。
选区本次增长，不声称实现了缩短。

## 新 acceptance 与正式交付准备

根据新镜像两组实际证据生成新的 acceptance 和 pins，输出位于服务器：

```text
/root/agent-service-deploy-20261026/scientific-promotion-startup-6107b20/accepted-research/
```

| 科研 profile | 新执行绑定 revision |
| --- | --- |
| `research.generate.v1` | `binding-6da5cd781915a625404e` |
| `research.revise.v1` | `binding-da3e7e7eb59cc85210cb` |

目录总计 16 个池，只开放上述两个科研 Pi 组合，全局保持单槽。两份旧已接受科研池
及 host 配置作为关闭的历史绑定保留，供首轮失败正式 Job 的来源与恢复记录使用；
没有删除失败身份、重置 fence 或复用 UID。PPT 与 Hermes 保持关闭。

历史正式 HTTP `r1` 的启动失败证据仍位于
`scientific-jobs-http-canary-r1/`，Job
`a1689dba-4241-4b6f-8372-eed2fb744f7d` 不会改写成成功。新正式验收使用独立
`scientific-agent-jobs-canary-r2` 和 `scientific-jobs-http-canary-r2/`，不覆盖 `r1`。

## 正式 Jobs HTTP 与生产切换

`scientific-jobs-http-canary-r2/` 使用真实 `ScientificAgentClient`、Agent HTTP 和科研
调用 token，但业务 SQLite 独立于生产数据库。生成与修订均下载实际成果、核对引用，
完成本地 publication ack 和远端 `candidate_received`。重连及两阶段幂等检查通过；
修订候选已比较，未采用，固定证据和选区外字节保持不变。

| 项目 | 生成 | 固定证据修订 |
| --- | --- | --- |
| 业务 task | `85143be2-8403-5373-813c-9cf84600c765` | `b770d273-8e56-5bd0-a46a-5d1fd6e6b404` |
| Agent Job | `03c6762f-51c1-4322-9fa7-8d465d088da7` | `78048ce1-070b-42b7-b293-efb933fd40f7` |
| Fence / Agent UID | `2 / 43000002` | `3 / 43000004` |
| 模型账本 | 12 条 `complete/200` | 3 条 `complete/200` |
| 报告大小 | 2108 bytes | 2216 bytes |
| 报告 SHA256 | `ea5cc0a6ed562f7674d2f12dcc3c3d3945b40e8364b70dc30003a8d85548ac25` | `b6b56bc54ac875fc50b3683a29ecb1130344ba664df1074d4eff132d39cc4536` |

独立控制数据库复核确认实际新镜像、acceptance/pins、单调 UID、完成状态、停止与退休
证据、模型调用和接收回执一致。两 Pod 均已清理，全局执行槽空闲。证据保存在
`independent-control-ledger.json`，与本次 canary summary 的 SHA256 绑定。

切换前确认 14 条历史 Pi 任务均终态，保存 API Deployment 与 NetworkPolicy。停止旧
API Pod 后应用现有 renderer 生成的补丁，再恢复单副本，避免新旧协调器重叠。切换脚本
带完整旧 Pod template 恢复路径；该异常恢复路径仅做离线模拟，没有宣称完成真实故障演练。

实际科研 API 镜像为
`arm-cluster-master:5000/scientific-api@sha256:3043932ba7481e753ed5689a2081eb69381c79336047bd6e157fa615942156e0`，
由 `db4e998` 构建。`/scientific-health` 确认 backend 为 `agent-service`，生成与修订分别
ready、revision `v1`、engine `pi`。历史 task 与 report 的正式 HTTP 读取通过，原 Pi
Worker、PVC、凭据及历史数据保留。未应用会额外隔离现有 API 出站的一条式 Egress 策略。

## 正式科研 HTTP API 验收

切换后通过实际科研 HTTP API 提交随机运维身份的任务，使用生产业务数据库，未重定向
SQLite、未直接调用 projection 导入候选。证据位于服务器
`scientific-production-http-e2e-r1/`，API Pod 内的下载、版本和比较证据均在重启前导出。

| 项目 | 正式生成 | 正式固定证据修订 |
| --- | --- | --- |
| 业务 task | `8481090c-00f9-53a0-9e91-eca2cbb4d1a1` | `5dd2e5b4-330c-5ea1-8806-a8fed2871942` |
| Agent Job | `74b40d06-af8b-47b0-8f8a-f3df90f2bab7` | `0c6d9a97-6f88-44f7-be43-c8eb788ddbfb` |
| Fence / Agent UID | `4 / 43000006` | `5 / 43000008` |
| 模型账本 | 12 条 `complete/200` | 3 条 `complete/200` |
| 报告大小 | 2096 bytes / 263 words | 2302 bytes / 286 words |
| 报告 SHA256 | `9003b9252ba83aabf49bffd44ca837025336eae57865771d21cf5c0e1d9aeb28` | `b50c45cc679559dbcaf195353ef6b3382bb1cad545c2184a8e538ee838207a9b` |

本次正式业务检查通过的范围：

- 生成实际执行一次 Crossref 搜索（limit 1）和一次 DOI 核验，报告及引用从正式 API
  读取、下载，所有产物的大小与 SHA256 匹配，生产业务数据库读回一致。
- 使用正式 sections、revision preview/start、版本下载与 comparison 接口完成选区
  修订；固定 papers/evidence/references 和选区外字节不变，原报告与当前版本未改变。
  候选版本为 `83236844-20a8-5dfe-8ef1-798a0faf25ce`，未采用。
- 两业务任务均记录本地发布 ack 与远端接收 ack；异用户读取被拒。未请求邮件、未新增
  通知，也未替真实用户操作已有任务。
- 独立复核生产 Job/attempt/pool 的精确新镜像与 pins、模型账本、单调 UID、停止与退休
  状态；两执行 Pod 已清理，全局槽空闲。该复核保存在本目录的
  `independent-control-ledger.json`，不是依赖业务脚本自报模型验收成功。

## 取消、恢复与最终状态

正式 API 另建排队取消任务 `9ddeaefb-5f67-5dd7-a55e-00a79b36f2e4`，对应 Agent Job
`8837ba1b-d50b-4324-92ee-fad8856a75d3`。暂停 dispatcher 后验证相同幂等键只产生一个
任务、异用户不可读取、排队取消成功，随后恢复 dispatcher 并重启科研 API。
重启后任务仍为 cancelled，两个科研能力均 ready。独立数据库实查该 Job 的 attempt
和关联模型请求均为 0，全局槽空闲；没有把测试代码中的固定数字当作模型账本结果。
记录位于 `scientific-jobs-http-canary-r2/actual-api-cancel-smoke.json`。

最终核对保存为 `scientific-production-http-e2e-r1/final-state.json`：

- 两条科研路由、两个新 Pi 池开放；PPT/Hermes 均关闭，全局仍只允许一个执行任务。
- 四个正式 Agent Job completed、一个排队取消、一个历史启动失败；失败记录未改写。
  正式模型账本累计 30 条，全部 complete/200，不把候选侧的调用混入此统计。
- 五份历史/当前分配均退休，UID `43000000..43000009` 永久消费。Worker Pod 为 0，
  全局执行槽空闲；用户等待和查看报告不占用任务 Pod。
- 三个 Agent 控制 Deployment、科研 API/UI 和保留的旧科研 Worker 均为 1/1 Ready。
- 实际公开配置 SHA256 为
  `e3f4d9ffcb0dc0e7f5608f135dff03dcffdebf938a3b51bb08f317ca456e5296`。

本次未更改科研 UI，也未做新的浏览器人工交互验收。真实模型和工具观测仅覆盖上述有限
科研样例；没有安装 K-Dense、开放 PPT/Hermes，或声称结构校验证明科学结论正确。

OpenSpec 项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的
`add-pi-first-agent-execution-service` 已同步本轮 tasks/design，4.3 完成，整体为
11/24；revision `069c7809e9d9f381585f4b040c7896c3bbcc7e9c`，校验 valid=true、
issues 为空。其他业务、完整引擎组合及故障回滚演练继续保持未完成，未 apply/archive。

正式切换使用现有 `Dockerfile.scientific-pi-api` 与固定 API 镜像摘要，再通过
`k8s/scientific-pi/render.py --execution-backend agent-service` 生成 API 补丁。
不使用会重新覆盖为专用 Pi 的旧部署入口。具体操作与回退说明见
[科研接入指引](../../scientific_agent/AGENT_SERVICE.md)。
