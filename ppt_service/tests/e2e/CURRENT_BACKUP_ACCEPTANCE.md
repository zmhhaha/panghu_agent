# 当前 schema 4 的合成数据库与文件恢复演练

`run_current_backup_linux.py` 使用 Git 提交冻结源码、专用无网络 PostgreSQL 容器和真正的 `pg_dump` / `pg_restore`，验证当前 15 张表、文件字节和 metadata、Agent outbox、两条 pending/revoke 审核历史、append-only trigger 与 runtime 最小权限。它不连接生产 PostgreSQL，不读取真实用户材料或凭据，不启动模型，也不改变历史迁移脚本。

## 单条服务器命令

先提交推送本轮代码，然后在服务器 Git 拉取到干净 checkout。镜像需要预先存在；沿用安全验收入口已确认的 runtime / PostgreSQL 完整摘要与 PostgreSQL UID/GID：

```bash
sudo python3 ppt_service/tests/e2e/run_current_backup_linux.py --source "$PWD" --output /tmp/ppt-backup-current-20261103-01 --runtime-image sha256:<服务器现有Python测试运行时完整64位摘要> --postgres-image postgres@sha256:<服务器现有PostgreSQL完整64位摘要> --postgres-uid 70 --postgres-gid 70 --postgres-data /var/lib/postgresql/data
```

`70:70` 仅为 Alpine 示例，必须使用实际镜像的 UID/GID；Debian 镜像常用 `999:999`。输出目录必须全新且在源 Git 仓库外。应用镜像继续以 `latest` 构建和发布，这里只固定已经解析的镜像摘要，不创建新版本标签。不在本地运行 PPT 测试。

宿主只需要 Linux、Python 3 标准库、Git 和 Docker；runtime 镜像需含当前 API 锁定依赖及 SQLAlchemy/psycopg。PostgreSQL 镜像需提供其相同 major 的 `psql`、`pg_dump`、`pg_restore`、`pg_isready`。不会 pull 镜像或安装依赖，缺依赖直接失败。复用的源 fixture/inventory/copy 工具都来自本次干净 Git checkout。

## 演练步骤与通过条件

1. 创建独立 Git checkout，校验 tracked regular file 对应真实 Git blob，记录 commit / SHA256，去掉 Git 元数据，再以只读方式挂载。
2. 创建带随机名称与精确 owner label 的 `--network none` PostgreSQL 容器；不发布端口，PG data 在 tmpfs，连接仅走专用 volume 的 `/pgsocket`。固定维护库为 `ppt_p1_maintenance`，源和目标库分别为 `ppt_p1_source_fixture` / `ppt_p1_restore_fixture`。
3. 创建无超级、建库、建角色、复制和继承权限的 migrator/runtime 角色。先生成已有离线源 fixture，再补当前 Agent outbox、一条 pending visual review 和一条 revoke。所有内容明确为 synthetic；不会写人工通过或发布授权。
4. 应用正式 runtime 授权；确认 schema revision 4、恰好 15 表、slot idle、无活动 Job/解析 lease，再捕获数据库全行/定义/trigger 清单和文件 SHA256/UID/GID/mode/mtime 清单。没有运行 API、parser、controller 或 worker，因此此 fixture 从建立后始终没有并发业务 writer。
5. 复制冻结文件树到备份目录，实际执行 custom `pg_dump`，独立保存 dump 与两个 manifest 的 SHA256。dump 后再次比较源数据库。
6. 创建全新目标库并验证 public schema 为空。检查 dump 摘要，从真实 TOC 中仅注释唯一的 `CREATE SCHEMA public` 项，然后使用 migrator 在单事务中恢复，保持 `--no-owner --no-privileges`，不使用 `--clean`。
7. 在任何重新迁移前比较恢复后的 15 表全部行与定义，并按原 metadata 恢复文件。按正式 grant 配置目标 runtime，验证恢复出来的原始审核 append-only trigger 实际拒绝 UPDATE/DELETE、pending/revoke/outbox 内容完全相等，runtime DDL 与 migration-state 写入被拒绝。
8. 在目标运行幂等 migrator、再次正式授权，实际验证 runtime CRUD 与 DDL 拒绝。最后重新比较源和目标数据库、源/备份/恢复文件树、当前历史摘要及 dump 摘要，确认源状态保持不变。
9. 清理时只删除精确 owner label 匹配的自有容器与 socket volume。任何阶段失败、无法核验或清理失败均使 `status.json` 的 `passed=false`，证据和文件树保留。

metadata 的 seed/copy 容器需要 root 与 CHOWN/FOWNER/DAC_OVERRIDE，以保留混合的 UID 10000 文件和 synthetic root-private controller 子树；仅新 evidence 路径可写、根文件系统只读、网络关闭、不挂 Docker socket。其余 DB probe 使用 UID/GID `10000:10000` 且 evidence 只读。宿主的受限 psql bridge 只把 inventory 的只读 SQL 送入自有 PG 容器，应用容器没有 Docker 权限。

## 证据与边界

`evidence/status.json` 给出最终状态；`stages.json`、各阶段 `*.json` / `*.log` 保留结果；`database.json`、`restored-database.json`、`files.json`、`database.dump`、`digests.json` 与 TOC 摘要保留比对依据；`source-data` / `backup-data` / `restored-data` 保留全部合成文件。`source.json` 和 `images.json` 记录精确来源，`cleanup.json` 单独记录自有资源回收。临时 PostgreSQL 在最终一致性检查之后回收，不留下持久测试数据库。

这项证据证明的是**当前代码、隔离静止合成数据的真实 PostgreSQL dump/restore 与文件恢复**。它不能证明生产 writer 已停止、并发业务快照、真实 PVC/CSI 恢复或异地灾备，也不证明 PPT 导出、人工视觉检查或 PowerPoint 编辑验收。生产备份与异地恢复仍须平台侧独立验收。
