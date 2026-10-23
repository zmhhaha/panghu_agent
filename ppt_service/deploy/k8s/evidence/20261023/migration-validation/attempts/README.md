# P1 迁移演练 01–04 历史诊断

原始归档 SHA-256 为 `1bfa4213641195a6875a6e47839d55878f4f19d23464051da0ec7670afd03257`。它只含本次独立合成数据和专属验证目录。整理时先校验所有 282 个成员都是安全相对路径、普通文件/目录，拒绝链接、特殊项、重复或大小写别名，再解包至新的 `.research-temp/p1-migration/attempts-extracted/`。没有应用归档中的 UID、mode 等 POSIX 权限到 Windows 工作区。

每轮复制的原始日志、清单和 TOC 保持原字节。`index.json` 记录这些文件的 SHA-256、缺少的诊断文件及真实 cleanup receipt；各轮的 `source-hashes.json` 已与独立冻结的本地源码清单逐文件核对，原始 `source.tar` 的 digest 也与远端 receipt 相符。没有复制 service 配置、空 pgpass、Docker wrapper、重复源码树或数据库 dump。

| 轮次 | Linux unittest | 停止阶段 | 资源清理 |
|---|---|---|---|
| 01 | 19 项：15 pass、3 errors、1 skip；0.077s | fake-psql 放在默认 noexec 的私有 tmpfs，执行被拒绝；尚未启动 PostgreSQL | 专属容器/卷已清除 |
| 02 | 19 项：18 pass、1 skip；1.281s | PostgreSQL bootstrap、12 表 seed、文件备份、dump 及源比较通过；恢复因默认空 `public` 已存在而中止 | 专属容器/卷已清除 |
| 03 | 19 项：18 pass、1 skip；1.301s | 精确 TOC 修正后恢复通过；原始数据库比较发现差异并拒绝 | 专属容器/卷已清除 |
| 04 | 19 项：18 pass、1 skip；1.224s | 保存 target capture 后定位为唯一的 `public.jobs.uq_project_active_job` 类型转换反解差异，比较按原规则拒绝 | 专属容器/卷已清除 |

四轮的一个 skip 都是临时 tmpfs 不支持本用例设置 `user.*` xattr；没有把它计为通过。01 的三个 error 属于演练执行文件的挂载设置错误，02 起只为独立测试的 fake-psql 临时路径明确启用 exec；没有调整生产 worker 或站点安全清单。

03 的 `restore.log` 是原始空文件，表示恢复命令成功退出；该轮未保存 target 清单，无法从其历史证据独立定位比较差异。04 的 `restored-observed.json` 和 `restore-diff.log` 才包含定位证据。03 的原轮没有 `restore-toc.sha256`，整理时计算的每个 TOC digest 在 `index.json`；04 同时保留原始 TOC hash receipt。02 的零字节 `run.log`/空成功日志也原样保留，没有补写成成功报告。

## 冻结源码的演进

| 轮次 | source.tar SHA-256 | 本轮变化 |
|---|---|---|
| 01 | `ab8ab9b660be872c13af8deca11f1f677f284c6fb1f88f8fbbe85cbdf8d31be9` | 初次独立迁移演练 |
| 02 | `841b9188ab9640d527b04c435618be3663eec911f6629088c01ff0866358a46d` | runner 的私有测试 tmpfs 明确 exec；文档修正 Python 路径和 sequence 边界 |
| 03 | `2db73bfd4f8967ae22a35cbf2aada264e2e6c4693b68c7018bb691c64c002b9e` | runner/README 精确跳过唯一的既有空 `public` 创建 TOC 项，保留全部表和数据 |
| 04 | `f5e36d577210ab9b6fd6826f461f4fcf94453751b786bc1406b125639212966c` | runner 仅增加 target capture、失败 diff 和 TOC hash receipt；README 增加摘要保存命令 |

01–04 的 `db_inventory.py` 始终为 `78a4986451b7af5d6ed6b3ba3d9c4b1ef0d48527fad93a02d5958bc70fac0bb0`，查询从 01 起就固定 `search_path=pg_catalog`。四轮的 `snapshot.py` 始终为 `7c1153f2f530a7a03bfffb305bd825ee215a53eab062e164cd834c7ebae7c68f`，19 项测试源也保持不变。因此 03/04 的拒绝不能算作后续已知 DDL 等价规则的成功证据。

05 的源码包另行冻结为 `8ed8927eec88d98198341788d76a0747c6a721176796a624810883c27910583a`，只改 `db_inventory.py`、两项回归测试和 README。它保留全部原始定义，只在比较副本中识别两条完整已知 DDL；原始 SQL 的 hash 和规则使用记录都应随 05 的独立真实结果一并查阅。这里未补写 05 的运行结论。
