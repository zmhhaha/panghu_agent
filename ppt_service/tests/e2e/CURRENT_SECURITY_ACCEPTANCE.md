# 当前多用户与隔离契约验收（服务器执行）

入口是 `run_current_security_linux.py`。它从干净的 Git 提交创建独立 checkout，清除该 checkout 的 Git 元数据后只读挂载，不传源码压缩包、不安装依赖、不启动模型、不连接线上数据库。只允许 Linux 服务器执行；本地不运行 PPT 测试。

## 单条命令

先提交并推送本轮源码，在服务器通过 Git 拉取到干净 checkout。以下两个镜像必须已经存在于服务器 Docker；用本机 `docker image inspect` 确认实际 SHA256、PostgreSQL UID/GID 与数据目录后替换参数：

```bash
sudo python3 ppt_service/tests/e2e/run_current_security_linux.py --source "$PWD" --output /tmp/ppt-security-20261103-01 --runtime-image sha256:<已在服务器存在的测试运行时完整64位摘要> --postgres-image postgres@sha256:<已在服务器存在的PostgreSQL完整64位摘要> --postgres-uid 70 --postgres-gid 70 --postgres-data /var/lib/postgresql/data
```

`70:70` 是 Alpine PostgreSQL 示例，Debian 镜像常为 `999:999`，必须按所用镜像确认。入口不会 pull；应用镜像构建、推送继续使用 `latest`，本验收参数固定已经解析的镜像摘要，不创建额外版本标签。输出目录必须不存在且位于源码外。

Python 测试镜像必须具有 Python 3.11+，以及 `cryptography`、`fastapi`、`httpx`、`PyJWT`、`python-multipart`、`psycopg`、`pydantic`、`pytest`、`SQLAlchemy`、`requests`，版本与仓库锁定依赖兼容。生产镜像若未含 pytest，不满足此入口要求；应预先准备独立测试镜像。入口缺依赖直接失败，不隐式 pip install，也不跳过用例。

## 实际覆盖

- R3 现有 signed-JWT API：多用户/不同 issuer、Origin/CSRF、项目、上传、历史版本、下载、预览、SSE 续传、删除撤权、幂等重试、PostgreSQL 并发请求与全局执行槽。用量专项测试不在选择器中。
- R3 当前 Agent 审核：使用明确标记为 synthetic 的 portable review fixture；两个独立 cookie jar 与不同签名身份，审核意见/修改/发布/诊断/文件权限，双文档 CSRF 稳定、审核修订冲突与刷新恢复、取消去重、预览及 PPTX 篡改拒绝。所有人工验收保持 pending，不能据合成文件发布。
- R4 离线契约：绑定与真实仓库 bundle 字节、Kubernetes manifest 约束、运行时预检、模拟所有权、只读挂载失败闭合、停止后清理、AppArmor/NNP/seccomp 的观测解析。挂载与内核状态使用显式替身，不能据此声称线上隔离验收完成。
- R2/R5 离线上传/运维契约：容量探针拒绝行为、25 MiB 边界、分块与不合法上传、取消/断开清理，依赖本轮 `tests/ops` 和 `deploy/ops` 新源码已一起提交。

ASGI 请求由 FastAPI `TestClient` 在进程内执行，实际 JWT 签名校验、数据库事务和文件读取生效；没有监听真实 HTTP socket。部分 SSE 时序直接驱动 generator，双文档场景按请求顺序模拟。它们都不能替代真实浏览器、反向代理或断网验收。

## 隔离与证据

两个容器均无网络、无发布端口；PostgreSQL 仅在专用 volume 中提供 Unix socket，数据位于 tmpfs。测试容器 UID/GID 为 `10000:10000`、根文件系统只读、无 capabilities、开启 no-new-privileges；不挂主机凭证、生产数据、Kubeconfig 或 Docker socket。宿主 runner 只管理随机名称且精确 owner label 匹配的自有容器/volume，不改防火墙、不 prune，不删除源码或证据目录。

`evidence/status.json` 仅在每组有用例、全部通过、无 skip/xfail、源码 hash 未变及自有资源清理完成时通过。`*.xml` 和 `*.log` 保留逐项结果，`source.json` 记录 Git commit 与文件 SHA256，`images.json` 记录镜像身份，`cleanup.json` 单独记录清理。输出目录保留供复核，不自动删除；清理失败会导致整体失败。

R3 最后一层仍需在真实 SSO/Ingress 下核对两个账号与浏览器会话、下载和 SSE 断线重连。R4 最后一层仍需专用 Kubernetes 验收 namespace 的实际 Pod 网络、凭证/输入/输出挂载、UID、停止/OOM/回收证据。旧的 Docker 防火墙脚本与 2026-10-23 历史 E2E 结果不算当前 shared Agent 部署的隔离验收。
