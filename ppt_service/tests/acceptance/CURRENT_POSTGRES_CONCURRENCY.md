# 当前 PostgreSQL 并发、幂等与 fencing 验收（R3）

仅在实际 Linux 服务器运行；代码先提交、推送，经服务器 Git fetch 创建独立干净
worktree。不得在本地运行测试、构建或下载依赖。本项不连接生产数据库或挂载生产
PVC，不读取 Secret、用户身份或项目资料，不启动模型，也不生成/验收 PPT。

```bash
python3 ppt_service/tests/acceptance/current_postgres_concurrency.py \
  --source "$PWD" --output /root/ppt-current-pg-<commit>-r1 \
  --test-image sha256:075abcca8db33c3c4ad5d4560391b938255948a077808538193e7fca02d54208 \
  --postgres-image postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea
```

复用 `tests/ops/run_storage_lifecycle_linux.py` 的 stdlib 命令、断言和独占收据
写入器，以及现有安全验收入口的 Git 快照校验。新快照只读挂载，移除 `.git`，
不把 Git 凭据带入测试容器。应用模块来自显式记录的当前 Git，镜像提供依赖；
不声称应用模块来自生产 site 镜像。

PostgreSQL 使用已缓存不可变 PG16 镜像，UID/GID70、只读根、768MiB 临时内存
数据目录；数据库名是随机 `ppt_p5_*`，只共享本轮所有者标签绑定的 `/pgsocket`
Unix socket。PG 和测试容器均 `--network=none`、drop ALL、no-new-privileges，
有内存、CPU、PID 和临时空间预算，镜像禁止自动拉取。测试容器 UID/GID10000，
只能写独立证据和临时目录。收据记录 PostgreSQL 版本、随机数据库名、空的
`listen_addresses` 与精确 socket 路径，没有任意数据库 URL 参数。

17 个精确用例全部通过且不得 skip：4 项并发检查真实独立 PG 连接与行/建议锁，
同请求只建一个 job/event、同 revision 竞争只有一个赢家、每用户队列上限、4 个
独立 backend PID 不能重复认领全局执行槽；5 项缓存重放仍授权并且不重复副作用；
7 项当前 Agent 审核资源、诊断、预览、下载、重放和取消的合成身份 ACL；1 项旧
fencing token 不能更新或释放下一任务。JUnit 必须匹配全部17个方法名。

HTTP 为进程内 ASGI，JWT 使用临时 RSA 密钥和合成 issuer/subject；真实 JWT
验证和 repository/当前 schema PostgreSQL 执行，但 JWKS 来源、Worker/Sandbox
为明确测试替身。它不证明真实 Casdoor 双账号切换、浏览器标签页、Pi 进程停止，
也不将合成计划/Version 夹具当作真实制稿或人工验收。

保存源码完整清单、镜像ID、PG身份、JUnit、stdout/private stderr 和收据。清理
先验证本轮标签和精确 container ID，实际 stop/wait 后确认 `Pid=0`，再删除并
验证缺失；所有容器停止删除后才删除唯一 socket volume，并查两类标签清单为空。
Docker daemon/API 错误不能冒充资源缺失。不操作其他容器、卷或目录，证据保留。
