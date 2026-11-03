# 恢复操作、存储维护与播放互斥验收

更新于 2026-11-03。本轮应用源码为
`f8db329e00a3da582a77f82d68177f68619897d0`，已在独立分支提交；
独立分支尚未合入主线。工作台发布、发布后保持检查与两轮故障夹具清理已通过；
生产只读容量盘点前两次 incomplete 保留；服务器新副本、同版本 Linux 验证、
最终四来源统计及安全证据导出／本地逐文件校验均已通过。用户浏览器和听审边界见末节。

## 本轮代码与离线验证

工作台对候选配音、逐段校时音频、批量配音、历史项目预览音频和 MP4 视频统一监听原生
`play`，播放一个时暂停其余四个。暂停还同步停止项目预览的控制状态／RAF 和旧试听段结束边界，
当前校时试听自身的结束边界保留。项目切换原有的暂停、关闭弹窗与清除来源保护保留。
同时补齐历史项目预览路径原先缺失的 `hasMotionScenes` 导入。

新增回归先在旧代码复现候选配音与校时音频同时播放，及批量配音与项目预览音频同时播放；
修复后覆盖五个媒体的全部 20 种有序组合、播放中切项目、项目留白／讲话期间的控制状态，
以及试听段结束与其他播放器接管。这里的 DOM 模拟不代表真实浏览器或真人听审通过。

新增 `storage_maintenance` 是仅用 Python 标准库的独立操作者工具，不启动应用 Store／Journal，
默认仅盘点。镜像源码打包的精确 allowlist 已包含该模块，共 57 个文件；不通过扩大目录匹配携带未知文件。

| 验证范围 | 实际结果 | 边界 |
| --- | --- | --- |
| 六个前端测试文件 | 93 passed | 含真实 app 入口组合；不是浏览器点击或声音播放 |
| 源码打包回归 | 30 passed | 检查精确文件清单；不是镜像发布成功 |
| 存储维护 Windows 回归 | 28 passed／1 skipped | 本机不能建立 symlink 的既有限制保留 |
| 存储维护 Linux 合成夹具 | 9 passed | 真实 apply 仅针对隔离临时树，没有生产库／PVC 挂载 |
| 容量 collector 最终兼容验证 | master SQLite 3.40.1：23 组 passed；Windows 11 组 passed | Linux 23 包含此前 22 项和新增 guard 验证，不相加为总数；真实统计另见下节 |

## 真实执行子进程与 API 中断

独立 run `render-faults-20261103-r2` 实际提交 3 个 Job，每项一次，没有自动重试。
它使用专用临时 journal、独立执行子进程和专用 loopback TLS 通道；没有重启生产 executor Deployment，
也没有改变全局网络、RBAC 或共享服务。

| 用例 | 真实证据 | 判定 |
| --- | --- | --- |
| 执行子进程强杀恢复 | PID 1685 被 SIGKILL，返回 -9；新 PID 1689 继续原 Job，UID 以 `28fa93ff-` 开头 | 39.066197 秒通过，原 Job 身份保持，精确清理完成 |
| API 连接中断后恢复清理 | 专用通道实际关闭 1 条既存 TLS 连接，拒绝 1 次新 CONNECT；原 Job UID 以 `5b28` 开头保持；原生 503 后持久 `cleanup_pending`，新 PID 1699 仅执行 cleanup | 20.137954 秒通过，没有提交第二个 Job |
| OOM 归因 | Kubernetes 返回 reason `Error`／exit 137，原生错误为一般 `motion_execution_failed`／502 | 专用归因未通过，整轮原始 report 仍为 failed |

原始证据中保存完整 UID、binding 与事件，表内缩写只用于阅读，不作为精确清理参数。
两项恢复通过不覆盖第三项失败，也不把独立执行子进程验证写成生产服务整体重启已验收。

## 两次 OOM 的证据与未通过部分

r2 的内核记录已把被杀进程关联到对应 Pod／container：当时 Pod 父 cgroup 的 256 MiB 限额先触发，
仅凭退出 137 不能要求容器运行时把它报告为 `OOMKilled`，原失败保持。

随后另行审阅一个新 run `render-oom-parent-20261103-r3`，最多只提交 1 个新 Job。
修正工具 SHA-256 为
`24b06974aed774a838966ee9345242ffeadb6db38791f50789cf47cb95db7254`。
单个普通 renderer 的 request／limit 为 256 MiB，普通 init 的 limit 提高到 512 MiB；
在该单 init／单普通容器、无额外 Pod 资源覆盖的构造下，Pod 有效内存限额为
`max(256 MiB, 512 MiB) = 512 MiB`。变更只作用于独立故障 Job，不修改生产 Deployment。

r3 仍返回 reason `Error`／exit 137 和一般失败 502。
但新的内核证据已精确匹配 Pod cgroup
`ba4bd8aa_e964_48ff_ab66_a8b6675324ef` 及以 `e43fe999` 开头的 container ID，
证明这次是容器自身达到 256 MiB 限额的真实 cgroup OOM。
**真实 OOM 已证实；Kubernetes／CRI 没有返回 `OOMKilled`，`motion_execution_oom` 专用归因仍未通过。**
该环境的 OOM 上报链路仍有缺口。不模拟该 reason、不修改原生错误、不改节点配置，
不把任一原 failed／once 记录覆盖成通过，也不追加自动重试。

两轮合计实际使用 **4 个 Job**，全部所属 Job／Pod／ConfigMap 已无残留。
本轮 0 Agent、0 TTS，没有业务数据库写入。

`finish_fault_workspaces` 已返回 cleaned：r2 的 3 个 Job／3 个 ConfigMap 与 r3 的
1 个 Job／1 个 ConfigMap 均 absent，全部所属 Pod 不存在，专用子进程均已退出。
只删除这两轮的确切 `/tmp` 夹具根；本次目录收尾没有调用 Kubernetes DELETE 或访问数据库。
核验并保留的两份原 report SHA-256 为：

- r2：`cc50ea0a60f153461a903480b07c3e0f741e391e43a2bdea104a6b653dacfa4c`。
- r3：`5a70931fcb29f6ce83b8e3d3e8ae5ec98399e62201ede0a79afdad7421ffcd1b`。

目录清理不改变原 failed；最终安全证据归档及逐文件校验见下节。

## 存储边界与保留的失败

存储维护 CLI 只允许清理已证明归属、终态且过龄的精确 executor scratch，
例如已知 assets、scene input 和 output.pending 文件。
数据库业务行、媒体 BLOB、最终 MP4、历史候选、证据和未知文件均不在删除范围。
apply 需要外置清单准确摘要、两库同时预约锁、重新核验和不同应用 UID 的私有 quarantine；
生产尚未准备该同视图维护环境，未执行生产 apply。

Linux 九项风险验证覆盖迟到活动、内容变化、链接、双 WAL writer 锁、原路径替换、
私有 stage 隔离、既存写 FD 改动及恢复原名冲突，详见
[STORAGE_MAINTENANCE.md](STORAGE_MAINTENANCE.md)。
最初缺 capability 导致的权限失败和旧数据库主文件物理相等断言失败均保留。
新版测试显式关闭连接，并在每次成功或异常 apply 后比较完整 schema、列、rowid、
typed 值与完整 BLOB SHA；main／WAL／SHM 的物理摘要另列，不把 checkpoint 物理变化当成业务写入，
也不以长度比较冒充 BLOB 内容保持。

生产容量 collector 使用服务器既有自包含冻结备份做 immutable 统计，
线上数据库仅取文件元数据，执行树仅通过有界目录 FD 扫描。
原版离线 22 项通过；真实盘点首轮因备份 sidecar 返回 incomplete，第二轮又因
SQLite 3.40.1 首次初始化 `pragma_table_info` 发出内部 `sqlite_master UPDATE` 授权事件、
被既有只读 authorizer 拒绝而 incomplete，两份原始结果均保留。
该授权事件不等于已经执行业务 DML，未把拒绝改成成功。

服务器已经另行准备自包含的 server-only 新副本，完整 schema／typed 值／完整 BLOB SHA
比较通过，原备份 main 与 sidecar 的前后 identity／完整 SHA 保持。
准备 receipt SHA-256 为
`32075fc645b8290543ff111c61bb0edbc19e58253d44c44d168f55d5fb5f3ea8`，
派生 postcheck SHA-256 为
`f46aff0bbf5fc3d4a94ef4fa9f21ce6c75daedf0280c7e6ebce2b4c25b80a5a0`；
数据库仍留服务器，没有下载，也没有通过删除原 sidecar 绕过检查。

collector 的最小修复只在 `mode=ro&immutable=1` 连接上，于安装原 authorizer 前精确执行一次
`SELECT name,type,pk FROM pragma_table_info(?)`，参数固定为 `sqlite_master`。
没有放宽 authorizer；新的 Windows 第 11 组验证真实 capture、精确预热范围，
并对两库的 UPDATE／INSERT／DELETE／DROP／CREATE 共十次操作确认原 guard 拒绝，源文件家族保持。
master 同 SQLite 3.40.1 的最终合成验证 **23 组 passed**，其中包含原 22 项及新增 guard 验证；
两次初版 locale 干扰失败保留，不把它们覆盖为通过。
它的报告是独立 `video.capacity-inventory.v1`，固定 `cross_source_consistency=false`、
`apply_allowed=false`、`candidates=[]`，不生成可执行清单，不推断孤儿。
目录读取可能改变 atime；没有显式业务路径写入，不宣称文件系统所有元数据绝对不变。
完整约束见 [STORAGE_INVENTORY_PLAN.md](STORAGE_INVENTORY_PLAN.md)。

最终 collector SHA-256 为
`66768f289b681c154782524a555da644cbc474992fc98302cca75ac0d76db78e`。
服务器 `storage-capacity-final.json` 已返回 complete，四个来源全部 complete、`blockers=[]`。
报告 SHA-256 为 `29bb66c4d8c33d9e4d3f50561370e1dee8e7cfe3120c45f3a48e94e63af09175`。
采样窗口为 **2026-11-03 21:24:59.360–21:25:02.319（Asia/Shanghai）**，约 2.959 秒。
这仍是分来源采样，不是跨库与文件系统的同一事务快照。

| 来源 | 最终统计 |
| --- | --- |
| 业务备份 | 15 表、11 项目；19 条音频候选共 14,832,836 B；7 条 MP4 记录共 5,094,193 B |
| executor 备份 | 2 表；8 条 operation（completed 6／failed 2）；10 条 scene（completed 9／failed 1） |
| 在线业务库文件元数据 | main 20,766,720 B，allocated 20,770,816 B，无 sidecar |
| 在线 executor 库文件元数据 | main 8,015,872 B，allocated 8,019,968 B，无 sidecar |
| executor 执行树 | 106 文件／40 目录；logical 9,225,471 B，allocated 9,482,240 B，全部边界计数 0 |

执行树分类为 assets 6 文件／492,495 B、scene input 70／5,003,117 B、
final MP4 15／3,690,594 B、report／evidence 15／39,265 B。
allocated 只统计普通文件分配块，不包含目录与文件系统额外开销。

采用统计均按 distinct version ID，缺失引用为 0：

| 类型 | 总版本 | 当前 head 引用采用 | 未采用但保留 |
| --- | --- | --- | --- |
| audio | 19 | 11 | 8 |
| static | 19 | 18 | 1 |
| motion | 3 | 3 | 0 |
| explanation | 1 | 1 | 0 |

这些数量不把未采用版本定义为垃圾、孤儿或可删除内容。采样没有回收文件或写入业务行，
不改变发布后零新增、全部旧 typed 值与媒体 BLOB 保持的结论。

全服务媒体删除／保留策略、SQLite compact、executor 128 条记录上限回收和长期容量管理仍未实现。
这两个独立工具不解决这些未完成项。

## 发布与数据库保持检查：已通过

新镜像构建先遇到旧镜像累积层数触发的 `max depth exceeded`；之后一次 Docker 29
`save` 输出 OCI 格式被旧兼容处理拒绝，两次失败都保留。
兼容工具已严格识别 Docker 29 OCI 输出并完成归一化，逐层 SHA、RootFS、runtime 与
history 保持；原 125 层不变，Labels 改动不新增层，没有 flatten 或 rootfs 变更。

实际工作台镜像为
`arm-cluster-master:5000/video-workbench:latest@sha256:8f9c91199307c3573651749a48462230397ea5db3d871e2762f80736cbc90439`，
对应本轮源码 `f8db329e00a3da582a77f82d68177f68619897d0`。
只更新工作台；executor 保持
`latest@sha256:7fc022866ad35f0c06d7078998a6fe58f7b91ca2fb59802d3a1e79b7a14efbc2`，
renderer 保持
`latest@sha256:9d4e1665aca99cd947b1f4d6de2b8883c87d8c26a67d40aa3c9af69e7508cbf8`，
既有 runtime ConfigMap 保持。

服务器 `release/postcheck.json` 已通过：工作台 15 表及 executor 2 表的全部 schema、
旧 typed 值与完整媒体 BLOB SHA 均保持，零新增、active 0、八音色保持；
health／ready 检查 200、匿名请求 401，七项依赖的 UID／spec 保持。
服务器一致备份分别为工作台 20,766,720 B、executor 8,015,872 B，仅留服务器，没有下载。

## 证据归档：已完成

安全导出包含 54 个精确源文件及 manifest，共 55 成员；压缩大小 63,849 B，
解包后总量 276,707 B。归档 SHA-256 为
`25436e2f13dd48bedd23c23bcfe39b1faf7a3daa2d256eb62fa2631a883c6755`。
已下载并安全解包到
`D:/github/armbianbegin/.runtime/video-recovery-r2/evidence/verified/`，
`local-verification.json` 确认全部成员逐文件校验通过。
`safe-evidence-export.json` 另记录 `source_files_unchanged=true`。
归档没有数据库或凭据，原数据库与服务器副本始终没有下载。
发布前两次构建失败、两轮 OOM 归因失败、两次 incomplete 盘点及 locale 干扰失败继续保留。

## 仍待完成的用户与环境验收

官方浏览器仍无可用实例／内核异常，真实点击、播放器切换和播放验收 pending。
人工旁白时序、声音自然度、整片节奏和表现力继续待用户复核；
不因恢复、媒体或离线控件测试通过而核准人工时序或宣称用户效果满意。
独立分支保持未合主线。
