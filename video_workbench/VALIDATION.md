# 分支验证记录

日期：2026-10-29。分支：`codex/svg-video-workbench`。

本轮验证覆盖工作台、通用服务接入和服务器容器运行。没有提交真实模型任务，没有发布生产验收记录，也没有正式部署工作台。

## Python 与前端

| 检查 | 结果 |
| --- | --- |
| 完整业务与通用服务回归，1435 项 | 1378 通过，49 条件跳过，发现 8 项候选驱动兼容失败 |
| 修复后的完整候选驱动回归，含新增的两个视频网络检查 | 37 通过 |
| 原 8 项失败用例独立复测 | 8 通过；失败缓存为空 |
| 前端状态与并发测试 | 10 通过 |

8 项失败分别涉及视频重复加载技能，以及旧任务的仅渲染入口被新增约束影响。视频和科研现在使用嵌入提示词的已校验技能；PPT 保留单独技能加载。视频候选必须提供明确输入场景，原有科研/PPT 渲染行为保留，真实执行仍校验输入。

最终测试集合为 1437 项，完整回归与修复后定向复测合计覆盖 1388 个通过用例、49 个条件跳过用例。这是组合验证结果，并非宣称修复后又执行了一次完整回归。跳过项包含 Windows 无法提供的 Linux/原生运行条件，不代表真实引擎和模型已经验收。

复现命令如下，临时目录名可以调整：

```sh
python -m pytest tests/video_workbench tests/agent_service -q --basetemp=.pytest-video-release-final2-20261029
python -m pytest tests/agent_service/test_candidate_driver.py -q --basetemp=.pytest-video-driver-final-20261029
python -m pytest tests/video_workbench tests/agent_service --lf -q --basetemp=.pytest-video-release-recheck-20261029
node --test tests/video_workbench/ui.test.mjs
```

初版另已用显式离线 fixture 在浏览器验证创建、拆镜、生成、采用、修订后保留旧预览、ZIP 下载和独立审阅播放器切镜。页面中的示例明确标记 OFFLINE FIXTURE。

## ARM 服务器

在现有 ARM64 服务器上使用白名单上下文和 `--native --no-push` 成功构建 `arm-cluster-master:5000/video-workbench:latest`，依赖安装与 `pip check` 通过。

| 身份 | 实际值 |
| --- | --- |
| 构建上下文文件数 | 26 |
| Context SHA-256 | `c639220668926d8c7a798280b7dea883916063247fb116b20b6859dd422c5672` |
| Source SHA-256 | `ed45dcfeef13669addc08830c7d3f812f9259ef332f40beea1c183b974017267` |
| 本地 Image ID | `sha256:7d4359e7b483f2232070a9dff831745ea284f02baf15b32a801b3d36af8b1552` |

该 Image ID 是本地镜像内容身份，不能代替推送后的仓库 manifest digest。本轮没有推送镜像，也没有生成发布回执。

使用合成凭据、禁网容器、只读根文件系统和独立数据卷，验证了 UID/GID `10001`、健康与就绪探针、Host 检查、身份与 CSRF、项目及镜头保存、重启后数据和 CSRF 保留。测试容器和测试卷由脚本清理。脚本不发布端口、不拉取镜像、不读取部署凭据、不提交模型任务：

```sh
python tests/video_workbench/container_smoke.py --image arm-cluster-master:5000/video-workbench:latest
```

最终源码重新生成的上下文摘要与服务器构建时相同。

## 尚待真实环境确认

视觉 Agent 的候选组合验收、正式路由启用、专用调用凭据与登录代理、持久卷备份，以及真实文稿试做仍待完成。画面质量、模型执行时间和费用尚无真实数据。远端上传和产物的保留/清理策略也需要在长期运行前确定。

本版输出 SVG/HTML 素材和顺序审阅包；配音、动画时间轴、MP4 编码和图片生成属于后续工作。
