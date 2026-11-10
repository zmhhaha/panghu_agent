# 从 Git 构建动画制作服务

按用户确认，服务器从同一个 Git 分支同步完整源码，以普通 Dockerfile 构建；不再从已部署应用镜像抽取源码、拼接应用补丁或压平应用镜像。Docker 自身的依赖层缓存照常使用。开发分支为 `codex/video-capability-experiment-redesign`，此发布不合入主线。

源码目录保持为一个正常 Git checkout。发布前 `git pull --ff-only`，确认 HEAD、应用源码工作区干净，再以完整40位提交调用：

```bash
export PI_CAGE_IMAGE='固定的pi-cage镜像@sha256:实际摘要'
export PPT_DONOR_IMAGE='固定的Pi工具基础运行时镜像@sha256:实际摘要'
python3 -B video_workbench/deploy/build_creation_release.py \
  --revision '实际完整提交' \
  --output '/既有发布记录目录/本次构建回执'
```

两个固定基础运行时是标准 Pi Dockerfile 的依赖，不携带本次业务应用补丁。Control 使用 `agent_service/Dockerfile.control`，Pi 使用 `agent_service/Dockerfile.agent-pi`；标准构建清单包含全部当前动画协议资源。视频制作执行器使用 `video_workbench/Dockerfile.animation-creation-executor`，从固定 Python 基础镜像独立安装依赖并复制当前源码。其专用忽略清单排除运行数据库、密钥、媒体和临时结果。

所有应用镜像只使用 `latest` 标签。回执与镜像标签保存 Git revision、实际构建上下文摘要、普通 Dockerfile 摘要和推送 digest。构建只写镜像及回执，不切部署、不打开运行密钥、不发起模型或生成任务。部署使用实际 digest，完成真实审阅准入、切换及产品验证后，才能记为上线验收完成。

此前增量构建及失败的原地证据保留用于追溯；新发布不再依赖那些操作器。效果库的数据层属于后续接线工作，未接 HTTP、界面和正式制作前不能声称产品已完成。
