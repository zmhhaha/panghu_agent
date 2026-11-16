# 从 Git 构建动画制作服务

按用户确认，服务器从已推送的目标 Git 提交同步完整源码，以普通 Dockerfile 构建；不从已部署应用镜像抽取源码、拼接应用补丁或压平应用镜像。Docker 自身的依赖层缓存照常使用。发布绑定完整提交，不依赖某个永久开发分支；合并与实际部署身份分别留证。当前整体入口见[文档导航](README.md)。

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

上述入口构建 control、Pi worker 和创作执行器，不能当成任意改动都需重建三者的要求。`build_joint_plan_release.py` 则构建 control、Pi worker 和正式工作台；实验室使用自己的普通 Dockerfile。只更新实际受影响的应用，部署前核对该组合的源文件、运行依赖、准入和原配置。

此前增量构建及失败记录只用于历史追溯，新发布不再依赖旧操作器。效果库 HTTP、界面及正式消费已有接线；真实整库 WikiSkill 编译与新正式采用仍按[验证结果](wikiskill-reader-validation-20261116.md)留有缺口，不能将构建或上线写成产品全部验收完成。

发布输出位于应用源码以外，保留 Git revision、源文件清单、上下文摘要、镜像 digest、切换前后配置及验证收据。构建完成后可按清理记录删除重复构建上下文、未使用的干净检出和测试缓存；业务数据库、媒体、冻结输入及活跃任务路径不能作为构建垃圾回收。仍有恢复价值的原始证据需先归档并核验，文档中的旧临时路径作为来源记录保留。

审阅的真实模型验证已证明，要求模型在一次回复中并行读取24帧仍可能退化为24次单帧调用。新的 `video.animation-review.v4` 使用固定的通用 `read_inputs` 工具，一次返回只读输入目录中的清单文本和全部原始图片；该运行只开放 `read_inputs` 和 `write`。工具实现摘要、环境配置、镜像摘要共同参与执行身份，不会修改已有审阅身份或失败记录。真实准入仍核对模型代理记录中的全部预期图片字节，工具调用成功本身不代表模型理解、画面质量或用户认可。上线状态以本次实际候选、部署回执和浏览器制作结果为准。
