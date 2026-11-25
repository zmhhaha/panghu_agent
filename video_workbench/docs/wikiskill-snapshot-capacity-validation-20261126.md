# WikiSkill 整库快照容量修复与验证

2026-11-26，用户手动更新知识库时收到 `animation_wikiskill_snapshot_too_large`。整库已有 1678 条原始记录：快照 58,680,443 字节（55.962 MiB），索引 512,829 字节，完整输入 59,191,360 字节（56.449 MiB）。单条最大 462,002 字节，条数、单条及索引都未超限，触发的是原 30 MiB 快照和 32 MiB 总输入限制。拒绝发生在创建发布任务和上传之前。

修复代码为 `b9b4d6931d48d7313dbf440eb73a286322a2d73f`。保持 v4 编译器名称、资源包正文和输入/结果 schema，新增合同修订 2：63 MiB 快照、64 MiB 总输入，以及独立固定摘要的容量读取工具环境。旧合同修订 1、旧工具字节和历史绑定继续保留。新任务冻结 `compiler_revision: "2"`；缺标记的旧任务使用修订 1，不重新捕获或切换输入。

## 验证

- 七项新增测试覆盖超过旧上限的完整原文、文件摘要、反篡改、超限拒绝、原生读取器与结果收集、持久恢复、历史任务及拒绝时无新增任务；另有新增合同与旧目录并存测试。
- 原相关回归初轮 397 通过、1 跳过；24 个失败中，23 个是 Windows 临时路径过长，另一个是测试的最小 policy fixture 缺少新显式读取的 revision。更换短路径并更新 fixture 后，受影响检查 147 项通过。其他运行时与配置检查 151 通过、6 跳过，另有 13 个 subtest 通过；重复范围不累加为唯一总数。
- 小型固定证据原生探针实际完成 6 次模型调用，工具自检、输入与产物绑定、作者停止、过期 lease HTTP 401 和外部 Pod 停止证据均经独立准入验证。该探针不属于用户知识发布，没有导入其测试产物。
- 五镜像按其真实职责检查旧目录和新合同；三个 control 及 Pi 保留 104 条历史绑定和 104 份历史 Host 配置。
- 真实 1678 条记录在执行器捕获、规范化和校验后，通过只读临时输入交给实际 Pi 镜像。原生 profile 修订 2 和固定读取器均通过；14 页覆盖全部记录，最大回复 29,215 字节，小于 32 KiB。文件摘要逐项一致，测试副本已清理。该过程没有提交编译任务或调用模型。

真实读取核验按组件职责分开执行。执行器不包含 Node，故 JavaScript 读取器放在实际 Pi 镜像中验证；独立检查容器使用测试副本的实际所有者身份读取，不修改原件权限或添加执行器依赖。

## 上线身份与保留范围

为保留各组件原有修复，三个 control 和 Pi 分别从其运行基线仅合容量补丁。两处旧 publication 模块冲突保留了各自原 900 秒时钟；执行器继续原来已上线的 1200 秒外层预算及 250 秒收集预留。

| 角色 | 源码 revision | latest 镜像 digest |
| --- | --- | --- |
| API | `1c0b960eb16da3abc9772f7880f2159f145075c9` | `sha256:db5b49fcb8629a84e0ccc927319787e2c70662c50d9c4a2ca750c898f95c5cbb` |
| Dispatcher | `5e375c704dbfea34c5ee2bea118780bbe6f820ce` | `sha256:d614f9b597edf8f71ac6502e13640c4604d72c15a4688a3676e4cc0d72114754` |
| Model proxy | `a6e357aa54e4190d34bec2aab89cbf875e698dc6` | `sha256:9c02b1909c5e996189f46e6c343fc96b2ca38019174369b40c4f88e5524e2069` |
| Pi 编译器 | `284660075a03b40e2e0fb89b6c0acc456d7ae5ee` | `sha256:9d3f84e3856b757699562d236a4c81da3a8f95eaf27095a12686c58d5dc3194b` |
| Executor | `b9b4d6931d48d7313dbf440eb73a286322a2d73f` | `sha256:010bde208189a5b2997f0fdede734ca71b9bd2da6c278c30ae5fbbaf1ecd389a` |

实际 Pod 镜像及执行器 `prepare-private-root` 初始化镜像匹配；每次更新前重新核对共享任务空闲及无待处理发布。旧、新编译修订通过真实 Gateway 只读准入检查。104 个旧绑定的 pins 和 104 份历史 ConfigMap 的 UID、数据摘要不变，lab 的完整 Deployment spec 保持。

发布库五表指纹一致，原 10 次任务、2 个成功版本及当前第 2 版指针保留。本次没有新建用户知识发布任务，没有替换当前知识。整库模型编译与实际新版本发布需另一次用户明确发版；本次大输入验证不冒充知识质量、视频运动、声音或艺术验收。

证据位于 `D:/codex-work/operations/wikiskill-snapshot-size-fix-20261126/`：`real-library-verification.json`、`final-runtime-verification.json`、`publication-state-before.json`、`publication-state-after.json`、`test-validation.json` 和 `release/`。远端原始构建、真实准入、部署及保护回执位于 `/root/video-releases/wikiskill-capacity-20261126-r1/`。
