# 生产只读容量盘点方案与执行结果

这是已实现 collector 的操作约束及本轮执行记录。前两次真实盘点 incomplete 保留，
最终四来源报告已 complete；最小兼容修复后 Windows 11 组及 master SQLite 3.40.1 合成 23 组通过。
输入是服务器自包含一致副本及运行中 Pod 文件元数据，输出不含业务文本。
数据库留在服务器；报告也先保存在 master 管理员证据目录。
不向业务目录放脚本或报告、不新建 Pod／PVC／挂载、不增加 RBAC，不启动应用 Store／Journal。

现有 `storage_maintenance inventory` 要求两库和执行目录同时可见。
当前 master 的备份与 executor Pod 内 `/data/executions` 不在同一视图，
所以本方案分别采样后汇总容量，不调用该 CLI，不生成它的 manifest，不提供 apply 入口。

## 来源与能回答的问题

| 来源 | 读取方式 | 输出 | 不能据此推断 |
| --- | --- | --- | --- |
| master 业务库一致备份 | `mode=ro&immutable=1` | 表行数、媒体 BLOB 总字节、采用／保留计数 | 当前在线 WAL 大小、全服务同一时刻状态 |
| master executor 一致备份 | `mode=ro&immutable=1` | operation／scene 总量、已知状态分布 | 盘点期间没有新任务或残留资源 |
| 工作台 Pod 数据库路径 | 仅 `lstat` | main／WAL／SHM／journal 的逻辑与分配字节 | 文件内最新业务内容 |
| executor Pod 数据库路径和执行树 | 数据库仅 `lstat`；执行树用目录 FD 扫描 | 文件／目录数、分类容量、未知／链接／边界数 | 孤儿归属、未引用、可删除、事务快照 |

备份文件大小只代表备份本身，不能替代线上数据库 main 加 sidecar 的磁盘占用。
SQLite `length(data)` 只用于统计容量；这里不声称验证媒体内容保持。
媒体内容保持的完整 SHA 证据来自隔离 apply 测试，见 `STORAGE_MAINTENANCE.md`。

## 1. 固定操作上下文与已有备份

所有操作在 master 运行，复用现有管理员 kubeconfig。使用已有备份的确切绝对路径，
另外记录原备份任务的开始／结束时间、文件身份及既有摘要；没有这些信息时标为 unknown，
不能把文件 mtime 冒充 SQLite 快照时间。
两库即使分别一致，也不是跨库同一事务，报告固定 `cross_source_consistency: false`。

备份必须来自已完成的 SQLite backup API，或已确认 checkpoint 后的自包含一致副本。
不能把普通在线 main 文件拷贝当作这种备份，也不能用 immutable 忽略它依赖的 WAL。
确认备份路径及祖先没有 symlink／junction，文件是普通单链接文件，
不位于任一在线 `/data` 业务树；备份目录不能存在相应 `-wal`／`-shm`／`-journal`。
存在依赖、来源不明、文件变化或无法读取时，该来源标 incomplete 并停止其统计。
不为满足检查而移走 sidecar、chmod、checkpoint、VACUUM 或重做生产备份。

本轮首次统计因备份 sidecar 拒绝。之后在管理员目录另行准备 server-only 自包含新副本，
完整 schema／typed 值／完整 BLOB SHA proof 通过，原 main／sidecar 的前后 identity 和完整 SHA 保持；
没有删掉原 sidecar 或把普通在线 main 拷贝冒充快照。
准备 receipt SHA-256 为
`32075fc645b8290543ff111c61bb0edbc19e58253d44c44d168f55d5fb5f3ea8`，
派生 postcheck SHA-256 为
`f46aff0bbf5fc3d4a94ef4fa9f21ce6c75daedf0280c7e6ebce2b4c25b80a5a0`。
所有数据库留服务器，没有下载。

报告输出使用管理员目录和 `umask 077`；该目录与业务 PVC、备份输入分开。
只把脚本通过 stdin 传给 Pod，不执行 `kubectl cp` 写入业务卷。

## 2. 在 master 读取冻结备份的统计

可直接复用本轮独立模块里的纯读取函数，不导入应用服务：

```python
connection = sqlite3.connect(backup.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=0)
connection.row_factory = sqlite3.Row
connection.execute('BEGIN')
# 仅在这条 immutable 连接上精确预热内建表值 PRAGMA，再安装原只读 authorizer。
connection.execute('SELECT name,type,pk FROM pragma_table_info(?)', ('sqlite_master',)).fetchall()
# 使用 storage_maintenance.capture(connection, BUSINESS_COLUMNS 或 EXEC_COLUMNS, DEFAULT_LIMITS)
# 只把返回值中的 tables/schema_sha256 及下述汇总计数放进报告。
# texts、业务 document、metadata.value、owner 和具体媒体内容均不输出。
```

collector 必须在 finally 中 rollback 并显式 close；`with connection` 本身不关闭连接。
以查询 authorizer 拒绝 DML／DDL／ATTACH，限定 SELECT／READ／必要内建函数及
读取列定义的 table_info；设置 instruction／30 秒查询 deadline 与进度中断，元数据处理另有 30 秒 deadline。
使用现有 schema／列／类型／主键 allowlist、每库 10000 行、64 MiB 文本预算。
不做完整媒体 SHA 或全库完整性扫描，不取 BLOB payload，媒体容量用 `length(data)`。
按 `capture` 的预算读取所需 JSON 后，额外对每份待解析 JSON 限长、限制结构；
坏 JSON、未知 schema、引用无法解析、预算耗尽均标 incomplete，不能返回零值冒充成功。
每库打开前后核对备份 device／inode／size／mtime／ctime，变化就放弃该来源。

本轮第二次统计在 SQLite 3.40.1 首次构造 `pragma_table_info` 时，因内部
`sqlite_master UPDATE` 授权事件被原 guard 拒绝而 incomplete，原结果保留。
最小修复只加入上面这一次、固定参数的 immutable 预热，不允许业务 UPDATE，也不扩大 authorizer。
Windows 新增回归确认真实 capture 完成，精确预热范围、两库十次 DML／DDL 拒绝和源文件家族保持；
同版本 master SQLite 3.40.1 合成 23 组最终通过，包含原 22 项和新增 guard；
初版两次 locale 干扰失败保留，真实最终统计结果见末节。

输出只保留：各表行数与媒体总字节；operation／scene 的已知状态计数；
当前项目 head 及 motion／explanation heads 的采用计数。
采用数按 distinct version ID 计算，同时单独记录 head 引用次数。
指向不存在版本的引用单列 `missing_references`；解析失败的保留统计为 unknown。
全部候选／历史版本仍保留，不把未采用数量称为垃圾、孤儿或可回收量。
不输出 SQL、原文、项目名、metadata 值、CSRF、凭据或完整行。

## 3. 固定 Pod 身份，仅采样在线文件元数据

依据当前部署固定 namespace／Pod name／container，不使用可能自动换目标的
`kubectl exec deployment/...`。默认命名空间为 `video-workbench` 和
`video-motion-executor`，executor 容器名为 `executor`；实际名称先读部署确认。
收集前后分别通过只读查询冻结 Deployment UID／generation／spec digest、ReplicaSet owner chain，
以及 Pod UID、目标 container ID、restartCount 和 imageID。
任一身份改变、exec 失败或容器重启时，丢弃完整性结论并标 incomplete。
报告仅保留所需身份，不输出完整 Deployment／Pod 对象或环境变量。

Pod 内命令形状如下；`READONLY_COLLECTOR` 为管理员目录中经过审阅的元数据脚本，
而非本轮 apply CLI，输出重定向发生在 master：

```bash
kubectl -n video-motion-executor exec -i "$EXACT_EXECUTOR_POD" -c executor -- \
  python -B - < "$READONLY_COLLECTOR" > "$SERVER_REPORT_DIR/executor-files.json"
```

工作台 Pod 只 `lstat` `/data/workbench.db` 及其 sidecar；executor Pod 只 `lstat`
`/data/motion-render-jobs.db` 及 sidecar，再扫描 `/data/executions`。
存在／不存在均记录；不可访问不等于零字节。
不对在线库打开 SQLite 连接：`mode=ro` 在部分 WAL 环境仍可能创建或更新 SHM，
所以本方案把在线数据库访问严格限定为文件元数据。

## 4. 执行树 collector 的具体约束

这是有界的目录元数据采样，不打开媒体文件，不读 renderer 凭据、不访问网络或 Kubernetes token。
collector 按以下顺序处理：

1. 以 `O_DIRECTORY|O_NOFOLLOW` 打开 `/data`，再经其 dir_fd 打开 `executions`，
   核对 root inode／device／UID。在任何子目录下降前读取 `/proc/self/mountinfo`，
   解码 mountpoint 八进制转义并建立执行根下的子挂载拒绝集。
   所有后续下降使用 dir_fd，不能只在路径 lstat 后再按路径 scandir。
2. 用 `os.scandir(directory_fd)` 枚举，每条用 `os.stat(name, dir_fd=..., follow_symlinks=False)`。
   每次下降前先对照上述子挂载拒绝集。
   子目录经 `O_DIRECTORY|O_NOFOLLOW` 打开，fstat 与刚读取的身份匹配后才下降。
   symlink、非普通文件、UID 冲突、跨 device 或子挂载只计边界／unknown，不跟随。
3. 扫描结束复读 `/proc/self/mountinfo` 并与第 1 步比较。
   拒绝执行根以下的所有子挂载，包括同 device 的 bind mount；单靠 st_dev 不足以拒绝这些挂载。
   mountinfo 无法读取或变化时，标 incomplete，不为检查而修改 mount 或增加权限。
4. 每个目录 fstat 前后比较身份与 mtime／ctime；路径替换、权限失败或已观察到的变化均标
   `sample_changed`／incomplete。忽略 atime 的比较；目录读取本身可能更新 atime，
   本方案承诺无显式业务路径写入，不声称文件系统所有元数据绝对不变。
5. 实际默认最多 20000 条目、深度 32、30 秒扫描预算；目录也消耗条目预算。
   任一预算耗尽就输出 partial counts 加 blockers，不能当完整总量，不自动扩大预算重扫。
6. 分别统计 logical path bytes、去重 inode bytes 和 `st_blocks * 512` allocated bytes；
   `(device,inode)` 去重集合也受条目预算约束，防止硬链接重复计算磁盘用量。
   分类只按已知形状区分 final MP4、assets、scene input、output.pending、evidence／report、unknown；
   分类名称表示位置，绝不表示可删除。默认输出分类总数与总量，不输出逐文件名或内容。

即使所有检查通过，正在运行的服务仍可在两次观测之间改动文件。
本方案始终称为采样统计，不能声称一致文件系统快照，更不能据此放宽 apply。

## 5. 仅在 master 合并报告

合并器只接受以上来源的统计 JSON，检查必需字段、预算与来源状态。
来源缺失、未知或 incomplete 就传播到总体，不把缺失部分填零。
报告使用独立 schema，例如：

```json
{
  "schema": "video.capacity-inventory.v1",
  "sampling_only": true,
  "cross_source_consistency": false,
  "apply_allowed": false,
  "candidates": [],
  "sources": {
    "workbench_backup": {"status": "complete"},
    "executor_backup": {"status": "complete"},
    "workbench_live_metadata": {"status": "complete"},
    "executor_live_metadata": {"status": "complete"}
  }
}
```

实际报告还应附每个来源的采样开始／结束时间、备份来源与文件身份、Pod 身份、预算、
目录变化与链接／挂载／引用异常计数。例子中的 complete 只表示该来源采样完成，
不表示跨来源一致；不能直接照抄而不读取结果。
最终可向客户端展示容量摘要，原始数据库及业务文本始终不离开服务器。

这份报告只回答容量分布、候选／采用历史数量与需要进一步调查的来源。
当前两库和执行树的同视图、双库预约锁及私有 quarantine 尚未为生产维护环境准备，
生产 apply 不在该流程中；不能把合并报告改名或补摘要交给 apply。

## 6. 本轮最终回执

collector SHA-256 为
`66768f289b681c154782524a555da644cbc474992fc98302cca75ac0d76db78e`。
服务器 `storage-capacity-final.json` 四来源 complete、`blockers=[]`，
报告 SHA-256 为 `29bb66c4d8c33d9e4d3f50561370e1dee8e7cfe3120c45f3a48e94e63af09175`；
采样窗口为 2026-11-03 21:24:59.360–21:25:02.319（Asia/Shanghai），约 2.959 秒。
业务备份 15 表／11 项目，19 条音频候选共 14,832,836 B，7 条 MP4 记录共 5,094,193 B；
executor 2 表、8 operation、10 scene。线上两库 main 为 20,766,720／8,015,872 B，均无 sidecar。
执行树 106 文件／40 目录，logical 9,225,471 B、allocated 9,482,240 B，全部边界计数 0。
audio／static／motion／explanation 的总量、采用、保留分别为 19／11／8、19／18／1、3／3／0、1／1／0，
缺失引用均为 0。分类明细见 [RECOVERY_OPERATIONS_VALIDATION.md](RECOVERY_OPERATIONS_VALIDATION.md)。

这是容量统计完成，不是可回收量证明，也没有执行生产 apply 或新增业务行。
原始数据库及服务器副本未下载。安全证据归档共 54 精确源文件＋manifest，
55 成员，压缩 63,849 B／解包 276,707 B，SHA-256
`25436e2f13dd48bedd23c23bcfe39b1faf7a3daa2d256eb62fa2631a883c6755`，
已安全下载解包到 `D:/github/armbianbegin/.runtime/video-recovery-r2/evidence/verified/`。
`local-verification.json` 确认全部逐文件校验通过；
`safe-evidence-export.json` 另记录 `source_files_unchanged=true`。归档不含数据库或凭据。
