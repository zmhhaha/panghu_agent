# 2026-10-26 本地通用 Agent 回归验证

用户重新授权提交、推送、服务器构建部署和测试后，在 Windows 本地工作目录执行了本轮完整回归。本记录只证明离线代码行为；镜像、Linux 隔离、真实 Pi/Hermes、模型往返和 Kubernetes 生产验收需由服务器验证记录另外证明。

完整命令：

```powershell
python -m pytest tests/agent_service -q --tb=short --basetemp=.pytest-agent-service-20261026-final
```

结果：**596 passed、21 skipped、28 subtests passed**，运行 323.54 秒，无失败。另有一条已安装 Starlette 关于 TestClient/httpx 的弃用提示。跳过项是本机不能提供的 PostgreSQL、Linux 原生进程隔离或符号链接能力；SQLite 测试不证明 PostgreSQL 跨进程锁和真实容器隔离。

本轮新增 `test_file_data_plane.py` 的 10 项回归，覆盖二进制/零字节文件、检查点归属和 hash、跨引擎合同重放、PPT 相邻阶段图、能力包身份、检查点/Job/恢复记录的传递引用、重初始化不复活已释放引用、挂载子树与路径别名、Agent/preview 两列 UID 的永久交叉冲突。

新增 `test_deployment.py` 的 3 项验证实际渲染结果：配置 JSON 完整还原、内部服务与独立 init-db Job、普通只读 subPath 文件、K8s 精确 endpoint 策略和 PPT/科研精确入站、历史不可变 host 目录保留与绑定漂移拒绝、私密字段/无界控制端点在输出前拒绝。该组不调用 Docker、kubectl 或模型。

回归发现并修复一个实际可移植性问题：Windows 将 chmod(0444) 映射为 DOS 只读属性，阻止已确认可清理的暂存普通文件删除。清理器现在仅在 Windows 上对通过类型/链接校验的普通文件清除此属性；Linux 的生产停止、所有权及卸载约束不变。

同时更新了旧测试中与明确新合同冲突的断言：输入支持安全子目录和零字节；分配记录新增 aux_uid，每个 fence 消耗两个 UID；Worker 资源额度已提高；候选构建需要 PPT donor 和明确的中立依赖；启用池需要模型 grant 配置；host 环境必须携带相邻 preview UID；公开目录文件上限为 4 MiB。

以下代码的 `py_compile` 也已通过：`staging.py`、`storage.py`、`checkpoints.py`、`deploy/render.py`、`deploy/deploy.py`、`build_image.py`。

本机环境为 Python 3.13.5、pytest 9.1.1、FastAPI 0.138.2、Starlette 1.3.1、Pydantic 2.12.5、httpx 0.28.1、SQLAlchemy 2.0.51，部分版本与镜像锁定依赖不同。未修改系统依赖或锁文件；服务器的固定镜像仍需使用仓库 requirements 独立运行验收。此记录不登记 acceptance，不自动启用路由，也不证明 AppArmor bind/remount 或真实模型执行已经可用。
