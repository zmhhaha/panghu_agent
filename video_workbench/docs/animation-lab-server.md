# 动画开发者工作台的服务器部署

2026-11-07，分支 `codex/video-animation-lab`。用户已要求把本地动画实验与标注工具部署到服务器。本次部署正在实施；下列入口、资源和检查是实施范围，尚不能据此认定已经上线或通过验收。

## 访问和用途

计划入口为 `https://video-service.panghuer.top/dev/animations/`，沿用已有Casdoor登录。只允许明确配置的开发者身份进入，普通视频制作界面不增加实验入口。十五个固定样例、A/B同步播放、动作检查点、标注、JSONL导出和已保存评测继续使用原工作台能力。

这次增加独立服务器实例，不把固定效果直接推广到业务视频模板。技术验收、开发者偏好和生产采用分别记录；服务器部署不代表这些效果已由用户认可，也不完成旧生产全面视觉闭环任务。

## 运行结构

公共请求经过现有VPS转接和OAuth登录代理，由限定路径转发到集群内独立的动画工作台Service。其他路径继续指向原video-service业务服务。域名、Cloudflare和Casdoor回调沿用现有配置。

本次服务器部署显式配置固定HTTPS公共Origin、`/dev/animations/`前缀、监听地址和精确开发者allowlist。通用的显式代理模式也支持空前缀的根路径部署，但本次资源生成器和发布入口固定使用上述子路径；默认本地模式不会因此开放网络。应用只接受由受信登录入口清洗并设置的稳定用户标识；NetworkPolicy只允许指定代理连接。它不会从客户端自报的转发Host或协议推导信任。页面、媒体、历史与导出也检查开发者身份，不能只保护写操作。

写请求保留固定Origin、随机CSRF令牌、自定义标头、JSON类型和大小约束。缺失或重复身份头、错误Host/Origin和不允许的用户明确拒绝。健康探针仅返回不含实验数据的状态。本地默认模式继续使用回环监听，不能通过省略安全配置意外变成远程开放服务。

路径前缀适配发生在HTTP/UI边界，涵盖静态资源、API、预览、组合资源、报告、原帧、局部任务和导出。编译后的composition HTML和冻结样例身份保持原始字节，避免旧标注被错误迁移。

## 镜像、存储与迁移

工作台使用独立轻量镜像，只包含需要的Python依赖、工作台代码、已验摘要的GSAP/CJK字体/Three.js资源及许可证。构建白名单排除`rendered/`、数据库、临时证据、缓存和凭据。动画由访问者浏览器执行，服务器实例不安装或运行Agent、模型、TTS、编码器或采样浏览器。

镜像以`latest`发布，部署同时固定实际digest，并在labels/annotations保存源码和发布身份。采用单副本、`Recreate`更新、非root、只读根文件系统、drop ALL、禁止提权和禁用ServiceAccount令牌；设置资源限额及不调用模型的健康检查。

标注SQLite保存在独立可写持久卷目录，冻结评测资料以只读目录挂载。不会读取业务项目数据库，不向此实例配置业务Agent或模型凭据。现有本地SQLite迁移使用一致性backup API，核对摘要、表/记录数量和冻结身份；原本地数据库与历史不删除。冻结报告和资源按记录身份及文件摘要迁移，不能用新编译产物冒充旧评测。

当前已准备本地一致性快照，原数据保留。初始迁移清单记录20个冻结报告、1,796个文件；数据库120,418,304字节，SHA-256为`3b2352d7c26b9a640e16f090d6a21e430af8da9040cd99f9b78cabc3fe6954d7`。备份内有49条样例快照、9条标注、7条撤回、20条评测快照、3条分诊事件。这些是迁移前基线，尚不代表服务器已接收或核对成功；服务器技术验收新增记录会单独注明。

部署前保存受影响入口配置、原目标及原镜像digest的非敏感回退资料。验收失败时可撤回新路径或恢复入口原转发；实验工具的问题通过其独立资源处理。

## 构建与资源生成入口

离线白名单打包器仅生成新的TAR，不下载、构建、推送或部署。以下命令从应用仓库根目录执行，输出路径必须尚不存在：

```sh
python -m video_workbench.deploy.build_animation_lab \
  --assets-dir video_workbench/rendered/feedback-20261106/animation-lab/assets \
  --output video_workbench/rendered/feedback-20261106/animation-lab/server-deploy/lab-context.tar
```

镜像定义为 `video_workbench/Dockerfile.animation-lab`，专用依赖为 `requirements-animation-lab.txt`。构建时必须传入公开的`SOURCE_REVISION`，使用`latest`标签；取得实际推送digest后才生成部署资源。发布验证不能用标签猜测运行的镜像内容。

资源生成器只输出JSON，不直接修改集群；下面使用已核对的发布标识、镜像digest和开发者稳定subject（不是显示名或自报邮箱）：

```sh
python -m video_workbench.deploy.animation_lab \
  --image arm-cluster-master:5000/video-animation-lab:latest \
  --image-digest "$LAB_IMAGE_DIGEST" \
  --release-id "$LAB_RELEASE_ID" \
  --developer-user "$LAB_DEVELOPER_USER" \
  --storage-class ceph-rbd \
  --output video_workbench/rendered/feedback-20261106/animation-lab/server-deploy/deployment.json
```

上述多行示例为Linux shell写法；PowerShell可写为一行并传入已核对的实际参数。新增输出文件采用排他写入，复测或重新发布应使用新文件名，不覆盖已有回执。

默认命名空间为`video-workbench`，Service和Deployment为`video-animation-lab`，内部端口`8766`。独立PVC为`video-animation-lab-data`和`video-animation-lab-evaluations`，默认各`2Gi`，分别挂载`/data`和只读`/evaluations`；渲染资产在镜像内的`/opt/animation-lab-assets`。默认入口身份来自`oauth`命名空间中标签`app=oauth2-proxy-video-workbench`的代理。实际应用前需核对生成资源与现有集群，迁移成功后再开放限定路径。

开发者准入由重复的`--developer-user`明确列出。修改允许身份应通过同一资源生成/发布流程，不能靠浏览器自报标头、共享业务账号或关闭身份检查临时放行。标注的新增可信身份记录在`client.forwarded_user`；旧`reviewer`和历史事件保持原格式，避免改写以前的来源。

## 验收清单

- 服务器配置、开发者allowlist、Host/Origin、CSRF、伪造/重复身份头及路径边界的针对性测试。
- 十五个固定样例和原冻结历史的精确身份保持，打包白名单与部署资源检查。
- 实际集群健康、未登录及非开发者拒绝、可信入口清洗身份头、网络策略与业务入口仍可达。
- 登录浏览器核对目录及代表性SVG、跨镜、Canvas、Three.js播放，执行同步定位和回退。
- 明确标为技术测试的标注完成保存、刷新保留、完整JSONL导出和撤回，不添加审美偏好。
- Pod重启后核对原记录、绑定身份和导出持续存在。

源码测试、实际集群和浏览器验收分别记录；合成测试不冒充真实登录或播放结果。真实失败保留原回执，不改写成成功。结果、镜像/源码身份、迁移清单和回退命令将在验收后补充。

## 记录位置

OpenSpec变更为 `deploy-video-animation-developer-lab`，初始10项待办。本次仅更新该变更，不自动apply/archive或合入主线；此前本地样例库已完成的记录保持其原状态。

所有非敏感部署与验收证据统一放到既有目录下的 `video_workbench/rendered/feedback-20261106/animation-lab/server-deploy/`。标注数据库、完整导出、迁移备份和冻结运行资料属于长期实验数据，按既有整体清理计划保留，不能作为临时截图随手删除。
