# 存储清单与有界回收

本轮工具独立于工作台和执行 worker，不增加后台清理，不改数据库 schema、业务行、
媒体 BLOB 或 Kubernetes RBAC。尚未在生产执行文件删除。

现有音频候选位于 `audio_versions.data`，成片位于 `video_renders.data`，
静态源码、项目、动态图解候选及采用记录位于业务 SQLite。它们全部保留。
executor 在独立 SQLite 记录 owner／operation／snapshot 绑定，文件位于
`executions/<64位operation key>/result/` 与 `scene-<index>/`。

最小回收范围只考虑有业务终态 render 对应、owner 摘要／operation ID／snapshot SHA
完全匹配且 executor 已终态的已知临时路径：`result/assets/`、`scene-*/input/`、
`scene-*/output.pending`。所有完成 MP4、证据、报告、未知文件和无法关联的疑似孤儿只统计，
不因“不是当前采用版本”而删除。该工具不能突破业务音频／render BLOB 容量或 executor 128 条记录上限。

默认 inventory／dry-run 只读。候选必须先输出包含根目录身份、数据库状态摘要、相对路径、
文件身份与 SHA-256 的 manifest；操作者审阅其候选预览和摘要后，才能明确 apply 该确切摘要。
apply 必须重新验证清单、无活跃任务、无 cleanup_pending、业务映射和每个文件身份。
符号链接、硬链接、路径逃逸、文件替换、UID 冲突、数据库不可读、未知 schema、
扫描／摘要预算耗尽或状态改变均拒绝 apply。

只读 SQLite 快照不能阻止另一个 worker 在检查后接收新任务。
安全 apply 因此需要保留两库的 `BEGIN IMMEDIATE` 写入预约锁，持锁期间仅 SELECT，
最后 ROLLBACK；不执行 DML／DDL／VACUUM，不改变数据库业务内容。
它会短暂阻塞新提交；若不能取得这两个锁，则只报告，不删除。
Linux apply 使用目录描述符和 `O_NOFOLLOW` 逐级验证；不支持该能力的平台只提供清单和预览。

## 操作方式

模块为 `video_workbench.storage_maintenance`，不启动 worker 或网络客户端。
默认命令没有删除能力：

```bash
python -m video_workbench.storage_maintenance \
  --business-db /mounted-business/workbench.db \
  --executor-db /mounted-executor/motion-render-jobs.db \
  --execution-root /mounted-executor/executions
```

报告包含两库与 WAL／SHM 的物理大小、各表行数及媒体 BLOB 字节数、
候选总数／当前采用数／未采用但保留数、执行文件总量、保留原因和阻断原因。
原文、媒体内容和 CSRF／上游凭据不写入报告。
遇到无法读取或未知 schema 等情形返回阻断结果，不把缺失数据库解释为空库。

生成外置审阅清单：

```bash
python -m video_workbench.storage_maintenance dry-run \
  --business-db /mounted-business/workbench.db \
  --executor-db /mounted-executor/motion-render-jobs.db \
  --execution-root /mounted-executor/executions \
  --manifest /operator-review/storage-plan.json
```

默认终态及文件均保留至少 86400 秒，清单有效期 1 小时；
默认最多扫描 20000 个条目、10000 行、每库 64MiB 元数据文本，
最多对 256 个候选／512MiB 文件计算摘要。
超出任一预算即阻断，不会拿不完整结果执行回收。
改动这些预算只能用于只读调查；apply 只接受默认预算。

操作者必须审阅 `candidates` 的具体相对路径、字节数、SHA-256、归属与保留边界，
再显式提供整个 manifest 文件的准确 SHA-256：

```bash
python -m video_workbench.storage_maintenance apply \
  --manifest /operator-review/storage-plan.json \
  --confirm-sha256 ACTUAL_REVIEWED_MANIFEST_FILE_SHA256 \
  --receipt /operator-review/storage-apply.jsonl \
  --quarantine-root /mounted-executor/operator-private
```

清单与 receipt 都必须是管理树外的新文件，不能覆盖数据库或其 WAL／SHM／journal。
两个数据库及执行目录必须同时可见；工具不复制生产数据库、不自动挂载 PVC、不读取 Kubernetes token。
apply 因预约 WAL writer slot 需要两库的 read/write 打开权限，但全部 SQL 限于读取／预约事务，
authorizer 拒绝数据和 schema 修改，结束时 rollback。锁不可得就拒绝，不杀进程或改队列。
Windows 不提供 apply；Linux 还必须支持 dir_fd、O_NOFOLLOW 与 renameat2(NOREPLACE)。
`operator-private` 必须是操作者已建立、归操作者 UID 所有的 0700 专用目录，
位于执行树外、同一 filesystem，不包含任一数据库。
其父目录也应由操作者控制并保持路径稳定；应用 UID 若可改名它的祖先目录，
私有描述符仍保护暂存数据，但 receipt 记录的绝对恢复路径可能失效。
apply 还要求操作者 UID 与执行树 owner UID 不同，通常是独立 root 操作者和应用 UID 10000。
不满足这个身份隔离时仅提供清单；不会用同 UID 的 0700 目录声称能隔离该应用写者。

## 执行与中断边界

apply 持有两库预约锁后重新读取 schema、任务状态、映射和业务文本引用，
再重建清单；任何变化都要求重新生成与审阅，不能只重试原 apply。
全部候选先通过安全目录描述符打开并核对身份／摘要，之后逐文件跨目录描述符原子暂存到上述私有目录，
再次核对暂存 inode／UID 与完整文件摘要，确认是已审阅文件后才 unlink。
如果在最后时刻出现外来替换文件，保留并尽量原子恢复它，不按旧路径身份删除它。
stage 不位于应用可写目录，应用 UID 不能在校验后替换它。
终态门禁和两库预约锁阻止应用正常开始新写入，暂存后重算摘要还可发现既存写 FD 的普通竞争；
这不是内核级文件冻结，不能保证隔离恶意持有写 FD 的进程或有特权的外部写者。

删除不是多文件事务。receipt 在每次暂存前 fsync 记录意图，随后记录已删文件和摘要。
中断或竞争会停止剩余操作；若存在 `.storage-maintenance-*` 暂存文件，
根据 receipt 人工核对恢复，不自动把它当作新孤儿删除。
目录本身保留，不执行递归删除；已完成 MP4、evidence、report、数据库行和 BLOB 均不在删除范围。

本轮没有在生产执行回收；本地清单和风险测试、Linux 临时夹具验证分别记账。
长期媒体删除、SQLite compact、executor 128 条记录上限回收及全服务保留策略仍未实现。

当前 Windows 本地风险回归为 **28 passed，1 skipped**（既有系统不允许创建 symlink）。
覆盖真实业务／executor schema、BLOB 与历史文件保留、任务活动、映射破坏、潜在引用、
年龄门槛、扫描／摘要预算、硬链接、数据库缺失、清单篡改／过期、WAL writer 锁及 DML 拒绝，
还有摘要期间文件变化和 Windows apply 拒绝。
Linux 九项隔离风险检查已真实通过（**9 passed**），脚本为
`tests/video_workbench/storage_maintenance_linux_smoke.py`，SHA-256
`33728c8ea2c5d2f9e6070d09016135baee2bc21368146f9bdbd749e0232b183c`。
运行模块 SHA-256 为 `e11072f3074b86654b4e98ee2361503fb0ee764c7b13b6870bf7c68f4cbae94f`。
容器无网络、根文件系统只读，仅挂载测试输入及临时 `/tmp`，没有生产数据库、业务 PVC 或媒体挂载。
脚本仅对合成执行树 chown 10001，覆盖：

1. 仅删除已审阅 scratch，保留业务逻辑数据、完整媒体 BLOB 和 final 文件。
2. 清单生成后的新任务活动拒绝删除。
3. 文件内容或身份变化拒绝删除。
4. 清单后出现 symlink 时保留外部目标。
5. 重新核验期间同时持有两个 WAL writer 预约锁。
6. rename 前出现外来文件时识别并恢复，保留原文件与替换文件。
7. 不同应用 UID 无法在 stat 与 unlink 之间替换私有 stage。
8. 既存可写 FD 在暂存后改动文件时拒绝删除并恢复。
9. 恢复原名已被另一文件占用时，保留所有文件、私有 stage 与 stopped receipt。

成功 stdout 位于服务器 `/root/video-recovery-20261103-r2/storage-linux-logical.stdout`，
对应 stderr 为空；本地后处理汇总命令另有 Python 引号 SyntaxError，与容器内九项结果分开记录。
没有据此声称已执行生产回收。

前两次失败均保留原始回执，没有覆盖成通过：首次隔离环境仅赋予 CHOWN／SETUID／SETGID，
缺少跨应用 UID 目录操作所需的 DAC_OVERRIDE／FOWNER，rename 与临时目录收尾出现 PermissionError。
之后只在无网络合成夹具容器补齐这两个 capability，没有修改生产权限。
第二次进入 apply 后，旧测试的数据库主文件逐字节相等断言失败。
旧 fixture 使用 `with sqlite3.connect(...)` 仅提交事务，没有显式关闭连接；
独立最小实验已复现延迟析构／最后连接关闭触发 WAL checkpoint 后主文件大小、摘要变化。
该物理断言失败本身不能证明发生业务 DML，也没有被直接改写为数据保持通过。

新版 fixture 和锁探针显式关闭每个 SQLite 连接。
每次 apply（包括预期拒绝或异常）均在 finally 中比较完整 sqlite_master、列定义、rowid、
全部 typed 字段值与每个完整 BLOB 的 SHA-256；TEXT／INTEGER／REAL／NULL 明确区分，
REAL 用 hex 表示。输出 `database_preservation` 逐库保存前后逻辑摘要和媒体 BLOB 全内容摘要，
另列 main／WAL／SHM 的完整物理 SHA、大小与一致性标记，不以物理相等代替逻辑证明。
同长度 BLOB 替换及 INTEGER→TEXT 反例探针均能检出变化，独立代码复审也已通过。

数据库状态摘要绑定 schema、引用／任务元数据和 BLOB 长度，不包含 BLOB 内容 SHA；
这里指运行工具的有界 manifest 指纹，与上述隔离测试的完整 BLOB 保持证据不同。
它不用于声称外部写者未修改同长度媒体。工具本身不写任何 BLOB，也不把媒体 BLOB 列入回收范围。

生产只读容量盘点见 [STORAGE_INVENTORY_PLAN.md](STORAGE_INVENTORY_PLAN.md)。
分来源采样报告不满足 apply 所需的同视图重新核验，不生成可执行回收清单。
