# PPT PostgreSQL 与文件迁移、保留和回滚

本文用于搬迁或恢复**已有数据**。首次安装和普通升级直接按 [部署说明](../README.md#服务器标准入口) 运行 `bash deploy.sh`，无需执行本文的停写、dump/restore 或迁移演练。恢复已有数据时，先准备新目标并完成恢复与一致性检查；只有目标满足部署合同（含 bootstrap 完成标记）后，才可交给部署脚本校验和发布，避免正常部署提前初始化目标表和 `state` 目录。

此目录提供可执行的离线工具及操作合同，默认不连接数据库、不控制容器、不删除原数据。`snapshot.py` 和 `db_inventory.py` 只使用 Python 标准库；数据库工具另外调用操作环境中已审查的 `psql`。应用镜像不因此自动包含 PostgreSQL 客户端。运行工具需要 Linux，Python 使用已批准 site/API 镜像里的 `/usr/local/bin/python`；在独立操作环境中也可显式选定其他可信 Python。

这些工具验证字节和数据库状态。它们不会把“没有查到活跃 Job”、一次文件扫描、`stopped` 字段或操作员自述当作操作系统停机证明。真实迁移必须先取得下面的停写证据。P1 包完成不等于已迁移线上，也不等于完成 P2 的 SSO 或 P5 的完整真实部署验收。

## 迁移边界和归属

1. 旧 PostgreSQL 数据库、旧镜像、旧配置、旧数据根和旧控制器状态保持原样保留；恢复到全新、专用 PostgreSQL 数据库和新 PVC，网站部署目标为 `ppt-service`。恢复工具必须指向经过核对的新目标，不能覆盖源库或原数据卷。
2. 备份整个 `DATA_ROOT`。除 `projects/` 里的原始材料、解析结果、attempts、checkpoints 和 versions，还包括 `material-locks/`、空目录及根级控制状态。没有“忽略旧任务”或清理规则。
3. 逐项核实所有数据/控制路径。若 UID ledger、控制器私有目录、Pi 状态等在 `DATA_ROOT` 外，分别按本流程 inventory/copy/verify，保留配置中的绑定。不能根据应用数据根推断已覆盖外部状态。Pi UID ledger 要求本地可靠文件系统和 root 私有归属，不能移动到未经验证的共享 PVC，也不能重置分配历史。
4. 文件默认选择 `--ownership preserve`，验证每个文件和目录的 SHA-256（文件）、mode、纳秒 mtime、UID/GID、类型和完整路径集合。inode、ctime 和 atime不属于恢复一致性声明。源文件的 hard link、symlink、FIFO、socket、设备、嵌套 mount/device、特殊权限位和 xattrs/ACL 全部拒绝；需要这些语义时必须采用另行审查的原生文件系统迁移。
5. `--ownership map --uid 10000 --gid 10000` 是显式改变整棵树归属的独立策略，只能用于已确认属于该身份的数据域。此策略仍保持 mode/mtime/hash，但绝不声明 UID/GID 与原始一致。它不适合含 root 私有控制状态/UID ledger 的混合树，也不等于已完成每个身份的实际读写验证。禁止使用递归 `chown` 抹平整个旧树。
6. 新 PVC 挂载点本身已存在，恢复目标应是尚不存在的子目录。本部署包挂载 `/var/lib/ppt-service`，`DATA_ROOT=/var/lib/ppt-service/state`。工具拒绝已存在的目标，包括空目录；不会合并、覆盖或盲删。Linux `renameat2(RENAME_NOREPLACE)` 在完整校验后原子发布目录；不支持时拒绝。失败会保留 `.ppt-migration-incomplete-*` 新临时目录，供检查，不能作为成功目标使用。
7. `fsGroup`、CSI 的 ownership 修改以及挂载选项可能在工具运行之后改变归属。应记录实际 UID/GID，并在最终挂载/安全上下文下复核。保留 mode 不代表新的容器身份已获得访问权限。

## 先冻结所有写入

必须记录时间、旧部署清单/镜像 digest、数据库及数据卷身份、控制器/子执行器身份和验证者。日志不记录 Secret 或含密码的 URL。

1. 将入口置于维护状态，阻止新上传、创建、删除、确认、生成和修改。使用现有受支持的取消/排空流程处理 queued/active Job 和 parsing/stopping 材料。等待控制器正常取得底层执行器的终止证明；不要修改数据库来伪造 `stopped`、清空 slot 或取消 lease。
2. 停止全部 API、材料解析协调器、dispatcher、任务模型代理和其他会写同一数据库/数据根的进程，包括旧开发 Docker 实例。K8s 工作负载应缩容至 0，并检查实际 Pod 消失；仅删除 Deployment 并不足以证明外部 Docker sibling、Pi/preview/parser/validator 子进程退出。
3. 通过对应 backend 的既有 stop gate，核实所有已知 task/attempt/UID scope 不再有存活写入者，保存真实进程/容器终止与挂载证据。若实际 scope 无法核对，停止迁移，保留租约/隔离，不得签发虚构 stop proof。
4. 源数据卷在独立迁移环境只读挂载；旧服务保持停止。数据库 `capture` 会检查所有非 terminal Job、未证明停止的附着 Job、项目 active ID、唯一执行 slot 以及 parser status/lease。需要它们通过，但这只是必要条件，不替代第 2、3 步。
5. 新环境的 API、parser、dispatcher、proxy 都保持关闭。新旧版本绝不能同时对同一资源写入。

网站在 `ppt-service`，由 `deploy.sh` 协调 PostgreSQL、一次性 schema 迁移、数据目录检查和网站 rollout。正常部署会自动启动工作负载；恢复操作必须单独保持新旧网站及全部 writer 停止，不能把模板的初始副本数或 Job 状态当作实际停写证明。trusted parser/dispatcher/Pi 运行器仍须按其实际 backend 独立停写并取得终止证据。下面的服务名是**显式输入示例**，不是当前线上资源探测结果。对现有 Docker 开发部署，须由操作员先确认精确容器 ID 和数据 bind mount，再执行对应 stop，不得使用全局 stop/prune 命令。

## 显式准备 PostgreSQL 客户端凭据

用专属迁移连接，分别连接旧库和新空库；只读 inventory 角色需能读取指定 schema 的全部表/定义，dump/restore 角色权限需独立审核。服务名称使用简单小写标识符。专用 schema 默认 `public`，要求恰好包含应用的 12 张表（包括 `api_schema_revision`）；存在额外表时拒绝不完整迁移。初次恢复保持同名 schema，迁入另一全新数据库。迁移不包括其他应用的 schema。

相邻 K8s 初始化合同中的应用数据库是 `ppt_service`，`POSTGRES_DB=postgres` 只用于维护/初始化；target service 必须明确连接 `ppt_service`，不能把应用表恢复到维护库。应用和 schema migrator 分别使用不同角色，应用 URL 使用 `postgresql+psycopg://`；这里 `psql`/`pg_dump` 使用的是 libpq service，并不接受 SQLAlchemy 的 `+psycopg` URL。

由部署流程提供 `/run/ppt-migration/pg_service.conf` 和 `/run/ppt-migration/pgpass` 两个 root 或当前运行 UID 所有、mode `0600`/`0400` 的普通单链接文件；不要从现网进程环境中提取，不要 `source` 应用 `.env`，不要在命令行传密码，不要开启 `set -x`。K8s Secret 投射通常是 symlink，工具会拒绝；需要在受信任初始化步骤复制指定文件到内存 `emptyDir`、设置 `0400` 并验证普通文件，或使用经验证的普通文件 mount。

`pg_service.conf` 的非敏感结构如下，实际 host/database/user/CA 路径由操作员填入私有文件；密码仅放在 `pgpass` 中。跨网络连接应启用受验证的 TLS；受限本地 Unix socket 可以使用相应已审查连接配置。

```ini
[ppt_migration_source]
host=<explicit-source-host-or-socket>
port=5432
dbname=<old-dedicated-database>
user=<reviewed-source-role>
sslmode=verify-full
sslrootcert=<trusted-ca-file>

[ppt_migration_target]
host=<explicit-new-host-or-socket>
port=5432
dbname=<new-empty-dedicated-database>
user=<reviewed-target-owner>
sslmode=verify-full
sslrootcert=<trusted-ca-file>
```

`db_inventory.py` 不打印服务文件、密码或 PostgreSQL stderr；它只向 `psql` 传 `service=<name>`，且子进程不继承 `PGPASSWORD`、`DATABASE_URL` 等应用环境。失败需在受保护的管理环境诊断，不能把凭据输出到任务日志。

## 离线备份

下面从只读 `/source-data` 创建独立 `/migration/run-001`。预先创建的 run 目录及其父目录必须是受信任、独占的存储，不能放在源树内。run ID 每次更换，禁止覆盖上次证据。使用实际工具绝对路径替换 `MIGRATION_TOOLS`；容器若未包含部署工具，可从固定来源 hash 的 readonly 挂载运行。

```bash
set -eu
umask 077
MIGRATION_PYTHON=/usr/local/bin/python
MIGRATION_TOOLS=/opt/panghu_agent/ppt_service/deploy/k8s/migration
mkdir /migration/run-001

"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/db_inventory.py" capture \
  --schema public --service ppt_migration_source \
  --service-file /run/ppt-migration/pg_service.conf \
  --passfile /run/ppt-migration/pgpass \
  --output /migration/run-001/database.json

"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/snapshot.py" inventory \
  --root /source-data --manifest /migration/run-001/files.json
```

将两条命令输出的 `manifest_sha256` 单独保存到可信变更记录，不能在后续未经核对地重新计算被篡改清单的 digest。下文用 `<DB_MANIFEST_SHA256>` / `<FILE_MANIFEST_SHA256>` 表示这两个值。manifest 原子创建且不替换既有文件。

```bash
"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/snapshot.py" copy \
  --source /source-data --destination /migration/run-001/data \
  --manifest /migration/run-001/files.json \
  --manifest-sha256 '<FILE_MANIFEST_SHA256>' --ownership preserve

# Shell noclobber 负责首次创建：pg_dump 输出到 stdout，绝不让 --file 覆盖旧备份。
set -C
env -i PATH="$PATH" \
  PGSERVICEFILE=/run/ppt-migration/pg_service.conf \
  PGPASSFILE=/run/ppt-migration/pgpass \
  PGCONNECT_TIMEOUT=10 PGAPPNAME=ppt-p1-offline-dump \
  pg_dump --no-password --dbname=service=ppt_migration_source \
  --schema=public --format=custom --no-owner --no-privileges \
  > /migration/run-001/database.dump
sha256sum /migration/run-001/database.dump

"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/db_inventory.py" compare \
  --schema public --service ppt_migration_source \
  --service-file /run/ppt-migration/pg_service.conf \
  --passfile /run/ppt-migration/pgpass \
  --expected /migration/run-001/database.json \
  --expected-sha256 '<DB_MANIFEST_SHA256>'

"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/snapshot.py" verify \
  --root /source-data --manifest /migration/run-001/files.json \
  --manifest-sha256 '<FILE_MANIFEST_SHA256>' --ownership preserve
```

独立保存 dump SHA-256。命令失败留下的 dump 可能不完整，不能因文件存在就认为备份完成；保持停写，改用新的 run 目录重做。以上流程是停写备份，不能声称支持并发生产备份。文件扫描前后发生内容/路径/metadata 漂移都会被拒绝；攻击性 root 并发替换不属于这些离线工具的保护边界。

## 恢复到新数据库和新 PVC

管理员先创建全新、专用 PostgreSQL 数据库，确认目标连接不可能指向旧库；记录数据库/卷身份。使用同一 PostgreSQL server major 和兼容的 `pg_dump`/`pg_restore` 客户端进行首次演练。严禁对原数据库执行 restore、`--clean`、drop 或破坏性 schema downgrade。

普通部署只有在角色密码及全部 bootstrap 授权完成后，才写入数据库 comment `ppt-service-bootstrap-v1`，并要求既有目标具有该标记。本文的 schema dump/restore 不建立这个完成标记；恢复比较通过也不等于可自动发布。目标缺标记或存在部分初始化时，保持停写并保留数据供管理员核实，不手工补注释绕过部署检查，也不让脚本重置旧密码或覆盖恢复结果。

`check-empty` 检查指定 schema 没有任何表，不会创建数据库。**它不能证明 target 和 source 是不同数据库**；该身份对照是操作员的必要检查。dump 和 manifest 的独立可信 digest 必须先验证。下例 `printf` 只校验无敏感信息的文件摘要，不接收任何 API/数据库凭据。

```bash
"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/db_inventory.py" check-empty \
  --schema public --service ppt_migration_target \
  --service-file /run/ppt-migration/pg_service.conf \
  --passfile /run/ppt-migration/pgpass

printf '%s  %s\n' '<DUMP_SHA256>' '/migration/run-001/database.dump' | sha256sum -c -
pg_restore --list /migration/run-001/database.dump > /migration/run-001/archive.list
"$MIGRATION_PYTHON" - /migration/run-001/archive.list /migration/run-001/restore.list <<'PYTHON'
import re, sys
from pathlib import Path
lines = Path(sys.argv[1]).read_text().splitlines(keepends=True)
pattern = re.compile(r'^\d+;\s+\d+\s+\d+\s+SCHEMA\s+-\s+public\s+\S+\s*$')
matches = [i for i, line in enumerate(lines) if pattern.fullmatch(line.rstrip('\n'))]
if len(matches) != 1:
    raise RuntimeError('expected exactly one CREATE SCHEMA public archive entry')
lines[matches[0]] = '; P1 existing verified-empty schema: ' + lines[matches[0]]
with open(sys.argv[2], 'x') as output:
    output.writelines(lines)
PYTHON
sha256sum /migration/run-001/archive.list /migration/run-001/restore.list
env -i PATH="$PATH" \
  PGSERVICEFILE=/run/ppt-migration/pg_service.conf \
  PGPASSFILE=/run/ppt-migration/pgpass \
  PGCONNECT_TIMEOUT=10 PGAPPNAME=ppt-p1-offline-restore \
  pg_restore --no-password --dbname=service=ppt_migration_target \
  --use-list=/migration/run-001/restore.list \
  --exit-on-error --single-transaction --no-owner --no-privileges \
  /migration/run-001/database.dump

"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/db_inventory.py" compare \
  --schema public --service ppt_migration_target \
  --service-file /run/ppt-migration/pg_service.conf \
  --passfile /run/ppt-migration/pgpass \
  --expected /migration/run-001/database.json \
  --expected-sha256 '<DB_MANIFEST_SHA256>' \
  --output /migration/run-001/restored-database.json

"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/snapshot.py" copy \
  --source /migration/run-001/data --destination /var/lib/ppt-service/state \
  --manifest /migration/run-001/files.json \
  --manifest-sha256 '<FILE_MANIFEST_SHA256>' --ownership preserve

"$MIGRATION_PYTHON" "$MIGRATION_TOOLS/snapshot.py" verify \
  --root /var/lib/ppt-service/state --manifest /migration/run-001/files.json \
  --manifest-sha256 '<FILE_MANIFEST_SHA256>' --ownership preserve
```

PostgreSQL 16 的 custom archive 含 `CREATE SCHEMA public`，全新数据库也默认已有空 `public`。上面的 TOC 只注释唯一的 `SCHEMA - public` 创建项，保留其余全部内容，并用 `--use-list` 恢复，避免因 namespace 已存在而失败。运行前必须确认 target 的空 `public` 已由 bootstrap 归属/授权给目标 migrator；不得靠 `--clean` 或删除旧 schema 绕过。TOC 项数量不是预期的恰好一项时拒绝，需审核实际 archive，不能扩大过滤条件。后续完整数据库比较负责验证表、行、约束及 trigger/function 全部保留。

数据库比较采用单个 read-only repeatable-read 事务，流式计算每表完整行集合的行数和 SHA-256，并比较列、约束、索引、trigger/function 定义及 `pg_sequences` 可见摘要。摘要不包含每个 sequence 的 `is_called`，`last_value` 也可能在未调用/权限不足时为 NULL，因此不是完整 sequence 恢复证明；若需要验证 sequence 的下一次取值，需单独检查 `last_value`/`is_called` 及授权，不能仅依赖此摘要。比较覆盖 projects/assets/checkpoints/plans/jobs/events/versions、usage_records、user_quotas 和 idempotency_keys 的完整持久数据；不只比较项目数量。数据库对象归属与 ACL 不属于 `--no-owner --no-privileges` 恢复的一致性声明：新对象由明确 target role 所有，授权需在新库单独审核，禁止继承无关旧角色。新库测试成功不等于线上用户数据已完成迁移。

PG16 实测会将 `public.jobs` 的唯一部分索引 `uq_project_active_job` 中 `varchar[]` 整体转 `text[]` 的表达式，反解为每个 `varchar` 元素分别转 `text`。`capture` 和 `compare --output` 始终保存原始 `indexdef`。`compare` 先验证 expected 清单的独立 SHA-256，再在副本中应用唯一规则 `ppt-jobs-active-index-cast-v1`：只接受代码中记录的两条完整 DDL，严格绑定表/索引名、唯一性、`btree(project_id)`、status 列、六种状态的顺序及全部类型转换；成功回执的 `comparison_rules` 记录是否使用。改状态、唯一性、列、类型、附加条件或其他未知 DDL 仍拒绝；其他清单字段完整比较。原始 SQL、清单 digest 和已用规则应一并留作审计证据。

先完成**恢复前后的数据库完整比较（含上述已知 DDL 规则）**，再运行所选新镜像的一次性 `python -m ppt_service.server.migrate` schema Job。当前工具精确覆盖 schema revision 3 的 14 张表，包含两张人工审核表；旧 revision 2 的 12 表源应使用与该版本匹配的历史 inventory 工具先完成恢复比较，再对新 target 运行正式升级。若源 schema 与所选工具不同（少列、缺表或多表）会拒绝；不能让工具猜测或修改原库。迁移 Job 只能拿到新 target 的连接。schema 安装幂等不等于版本任意向前/向后兼容；不得在生产 API 启动时隐式迁移。

在新环境保持模型能力关闭，分别验证真实挂载下 API UID 的材料上传/下载/删除、跨用户权限、旧项目/计划/版本历史、SSE 和幂等重放、quota/usage、材料恢复状态。新写测试使用新的合成项目，写入时间和项目 ID要记录。验证通过后才按部署流程逐步开放入口与 parser；dispatcher/付费执行/Pi 切换仍需相应主线验收，P1 不启用模型请求。

## 回滚

- 新环境**尚无新写入**时：保持入口维护，停止新 API/parser/dispatcher/proxy，取得真实停止证明，确认新旧写入者都停；把部署精确切回记录的旧镜像、旧配置、旧数据库和旧数据根，再启动旧系统并复核数据/权限。原库原卷从未被覆盖，因此无需 schema downgrade。
- 新环境**已有新写入**时：不能直接把流量切回旧库丢弃这些写入。继续维护，冻结并为新库/新数据建立另一份独立备份；评估有审计的前向修复或显式数据对账/迁回。得到一致性决策前不恢复旧服务写入，不在这两个独立状态间自动同步或合并。
- 恢复失败：新库新卷保持停止和可检查状态，旧库旧卷继续保留。禁止从部分恢复树启动 API，禁止删除旧 PVC、旧数据库、旧镜像或 UID 分配历史。只有单独授权的数据保留流程才能处理旧副本；此工具不承担该操作。

## 独立验证命令

```bash
/usr/local/bin/python -m unittest discover \
  -s ppt_service/deploy/k8s/migration/tests -v
```

测试只使用临时目录和假 psql transport 来验证拒绝行为、凭据隔离及元数据保护；真实 PostgreSQL dump/restore 和 Linux PVC/挂载权限需要独立演练。真实演练报告应记录合成数据、源 hash、client/image digest、备份/恢复 fingerprints、旧数据保持不变、所有 writer 停止方式，以及未完成的 K8s/SSO/CSI 验收边界，不能把 mock transport 计为真实数据库验证。
