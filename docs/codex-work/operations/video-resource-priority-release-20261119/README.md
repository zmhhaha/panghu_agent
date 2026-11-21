> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/video-resource-priority-release-20261119/README.md`；原文件 SHA-256：`aa4925b2bf9e5f5cec77ccf769965ca054cce426c43ae8a9a9cb859f2881269b`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 资源优先策略 executor 发布

最终 revision：`1b10d4ec126cb23969856e313b5b58a88bc724df`，由root审阅提交推送；root报告90项定向检查通过。完整Git detached checkout构建，使用普通Dockerfile及latest标签，按digest发布，不制作应用源码覆盖层。

独立路径：本目录与服务器 `/root/video-releases/video-resource-priority-release-20261119/`。操作不会读写或停止 `/root/video-releases/peak-original-extended-20261119/` 中的离线full任务。

## 资源限额

构建前两秒实测空闲1.278核，MemAvailable 7420669952字节；见 `pre-build-headroom.json`。legacy Docker builder的RUN限定1CPU、1GiB；smoke限定1CPU/512MiB，额外源码哈希核对限定1CPU/256MiB。COPY与push的daemon压缩不完全受RUN配额控制，未把CLI nice解释成整个dockerd限流。

## 已准备的操作

1. `launch_executor_fix.py --revision <revision> --base /root/video-releases/video-resource-priority-release-20261119`：fetch已推送分支，创建干净detached源目录，唯一启动build；构建并push latest，保存digest/来源清单/标签和离线smoke收据。
2. `read_build_status.py`：只读该build的状态及有界日志。
3. 服务器执行 `python3 -B <root>/verify_extra_image_bytes.py --revision <revision>`。
4. `python3 -B <root>/bridge_executor_rollout.py prepare --revision <revision> --output <root>/deployment-r1`，保存具体变更计划与其SHA。
5. 核对计划后，`python3 -B <root>/bridge_executor_rollout.py apply --revision <revision> --output <root>/deployment-r1 --plan-sha256 <确切SHA> --execute`。
6. `python3 -B <root>/audit_deployed.py --revision <revision> --output <root>/deployment-r1 --plan-sha256 <确切SHA>`，独立只读验证。

## 核对范围

- 全Git revision、干净源码、镜像revision标签、latest@digest与主/init实际运行digest一致。
- 镜像与运行源码按完整构建清单核对；受信host必须仍为 `df7253899839aef3df112e13ccc3a2c2ca09ceee96b037c84800021e1dd26d81`。
- 仅executor主容器/init容器镜像和来源注解变更；原参数、Secret、挂载、环境配置保持。
- 已准入Manim、public配置与既有WikiSkill发布内容前后保持一致；另外5个服务deployment spec保持。
- apply前检查本地业务任务已停止，使用r2修正的有界SQLite在线备份，逐库quick_check与SHA校验。离线full没有登记为业务任务，不受此检查或rollout管理。
- 不POST业务、不调用模型、不重跑实验、不修改历史记录、不自动发布WikiSkill。

14脚本语法通过，13项rollout不变量与6项备份transport检查通过。corrected backup helper与r2逐字节一致，保留transport非JSON噪声处理及失败约束。

## 实际结果

已完整执行build→prepare→apply→独立audit。运行镜像为 `arm-cluster-master:5000/video-animation-creation:latest@sha256:be66a34d1c1a9f354bf2436302f30a70c0d06b7f0cfbc1f4620dbc4d7976e10a`，主/init digest一致，新pod `video-animation-creation-669bd95d65-m2htx` Ready，health 200。

部署计划SHA为 `3ef5e26179fbf3b0116396c3e8fcc3ca6eea4e305d2b91e38474f0f0c21a55c0`。实际只替换主/init镜像与来源注解，58项运行源码字节核对通过、19库在线备份quick_check通过。生产host、原准入、public、既有WikiSkill及其他5个服务spec保持。12份远端收据已按原始字节下载并逐项核对SHA；见 `receipts/`、`independent-audit.json`、`deployed-summary.json`。未调用业务或操作离线full任务。
