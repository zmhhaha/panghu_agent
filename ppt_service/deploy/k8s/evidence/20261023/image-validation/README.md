# P1 独立镜像验证 — 2026-10-23

验证对象为本次新建的 ARM64 镜像 `ppt-service-site:p1-20261023-r1`，完整本地 image ID 为 `sha256:374c669d4b556d744a8f1c4ee82b51b3867def29126ecefd6c0d43d0db616314`。这是本地 image ID，不是已推送的 registry manifest digest。

原有 `tests/server/test_site.py` 与 `test_auth.py` 共 **47 项全部通过**，无失败、错误或跳过，JUnit 时间 **15.276 秒**。两条 warning 分别来自已导入 anyio 的 pytest assertion rewrite 和 Starlette 依赖中已弃用的 BlockingPortal 别名。测试直接导入镜像内 `/opt/ppt-service/ppt_service/server/site.py`，没有挂载源码覆盖镜像核心。`PPT_SITE_TEST_DIST=/opt/ppt-service/web/dist` 指向实际打包的前端资产；认证测试使用合成签名 JWT/JWKS fixture 和 SQLite。

第二个独立容器使用镜像**真实默认 ENTRYPOINT**。无网络、只读根文件系统、UID/GID 10000、drop ALL、no-new-privileges、1 GiB/1 CPU/128 PID 限制与私有 tmpfs。只有合成的 `postgresql+psycopg://unused@127.0.0.1/ppt_p1_no_connection` URL；未挂载现有 DB、数据卷或凭据。容器内轮询确认 `/healthz` 为 `test` 且 `manual_acceptance=pending`，未登录 `/` 返回 401。读取实际 `/proc/net/tcp` 与 `tcp6`，确认唯一监听为 **127.0.0.1:8000**。

两个精确具名、带 owner 标签的专属验证容器均已删除。`cleanup.json` 记录 `owned_resources_removed=true` 和 `runner_exit=0`，模型请求为零。初次准备时给定远端父目录尚不存在，mkdir/上传失败后创建专属目录并继续；这次准备失败没有启动测试或容器。

证据：

- `summary.json`、`status.json`、`cleanup.json`：机器可读结果。
- `site-auth.xml`、`site-auth.log`：47 项原始 JUnit/日志及镜像内核心模块来源 hash。
- `entrypoint.json`、`entrypoint.log`、`entrypoint-probe.log`：默认入口的实际启动与监听结果。
- `image.txt`、`source-hashes.json`、`input-sha256.txt`：镜像身份和四个验证输入的来源；本地与远端输入 SHA-256 已核对一致。
- `evidence.tar.gz`：原始远端证据归档，SHA-256 为 `5ae865b1d1e0d35b6dc0a5dacd554a9e964c69e9fcdb2a8910863441f4cc9a09`。

可审查运行脚本位于 `deploy/k8s/tests/run_image_independent.sh` 与 `probe_entrypoint.py`，冻结输入归档位于 `deploy/k8s/tests/evidence/20261023-independent/input.tar`，SHA-256 为 `da39882a66486be1a99ebce08c1706d67a9e17783705846324025065790c7590`。远端证据位于 `/root/ppt-service-p1-image-20261023-r1/independent/output`。

本次证明镜像中的原有网站/认证边界回归和默认进程监听合同。没有验证真实 PostgreSQL readiness、Casdoor 登录、CNI、Ceph CSI、Kubernetes 部署、Pi 执行或 PowerPoint；这些结果不代表 deploy ready。独立 PostgreSQL 分权/恢复演练由另一组验证记录负责。
