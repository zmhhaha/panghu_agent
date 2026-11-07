# 动画反馈的独立开发执行器

执行器入口为`python -m video_workbench.animation_lab_iteration_runner`。它是开发工具，不装入动画工作台镜像；工作台仍不获得Agent或GitHub凭据。代码、任务、检查和PR都关联原任务身份，代码合入、候选发布和画面认可分别记录。

## 现有通用Agent如何复用

使用已有`workspace-task.v1`文本合同，把有限源码窗口和反馈发送给Agent，返回结构化源码替换提案。它不获得Git、shell或GitHub权限，也不负责执行测试。单次prompt不得超过16,000字符；超过时明确拒绝，开发者需要选择较小的源码窗口或拆分任务，不会自动截断意见。

已有video-workbench调用方仅允许`video.*`领域profiles，不能假定其凭据允许`workspace-task.v1`。真实调用必须为独立执行器配置获准访问该profile的专用调用方、固定profile revision和只读token文件；服务capabilities中的唯一匹配项必须configured/ready。此实现不扩大业务caller权限，不改通用Agent框架配置。

提案只能替换开发者显式提供的窗口。窗口绑定完整源文件SHA-256、行范围、内容和source_id；整份请求绑定反馈任务、模块、完整Git revision与开发分支。允许文件由runner中的模块映射限定，不允许反馈或提案添加shell命令、改变测试、runner、依赖、工作流或凭据文件。

源码窗口与SHA绑定受信提交中的Git blob字节，避免Windows干净CRLF工作区与独立LF checkout之间产生错误的版本冲突。

## 准备与离线检查

先从工作台导出单模块轻量反馈任务，保存为`task.json`。选择包含相同源码revision的干净本地仓库。以下为Linux示例，所有目录为开发者显式指定的路径：

```sh
python -m video_workbench.animation_lab_iteration_runner prepare \
  --repository /srv/development/panghu_agent \
  --task /srv/animation-tasks/task.json \
  --source video_workbench/animation_lab_canvas.py:100:140 \
  --base-branch codex/video-feedback-iteration \
  --workspace /srv/animation-runs/task-a/checkout \
  --run-dir /srv/animation-runs/task-a/receipts
```

行范围需按实际源码选择；也可传整个文件，仍受prompt总长度限制。新checkout必须不存在，源仓库和已有工作区不移动、不重置。runner创建`codex/animation-<module>-<task前缀>-<attempt前缀>`分支，每次显式prepare都有新的attempt_id、请求身份与分支。源仓库origin精确指向`zmhhaha/panghu_agent`时，独立clone保留该GitHub目标；其他origin不自动变成GitHub目标，后续publish会拒绝。

指定的本地base-branch必须等于任务source_revision，或仅在其之后追加文档提交：source_revision必须是该分支的祖先，二者文件差异只能为`.md`。这样允许镜像构建后再记录部署验收，而代码、测试、依赖变动、旧主线或分叉基准仍会被拒绝。独立checkout始终固定原source_revision；PR使用三点差异，不夹带基准分支已有的验收文档。示例分支以本次源码分支为准，代码基准变化后应重新导出匹配实际发布版本的任务。

`request.json`保存冻结请求，`synced-event.json`表示执行器已接收任务，需导入工作台才能推进其反馈游标；下载文件本身不推进游标，不表示问题已修复。

同任务显式重试使用新workspace和run-dir，并给prepare指定`--expected-state failed`或`rejected`；产生绑定新attempt的running事件。取消后重新接收可指定`--expected-state canceled`。它们不能替代发生网络歧义后的原运行查询，也不自动重试模型或发布。整个attempt的run_id始终为请求identity；真实Agent job_id单独保存在agent-job文件中，不在中途更换任务的run_id。

人工或其他开发Agent也可以按该请求产生离线提案：

```json
{
  "schema": "video.animation-lab-code-proposal.v1",
  "request_identity": "<request.json中的identity>",
  "task_identity": "<原反馈任务identity>",
  "edits": [
    {"source_id": "<某个源码窗口source_id>", "replacement": "该窗口完整的新文本\n"}
  ]
}
```

先运行`validate`只核验，再显式`apply`写入独立checkout：

```sh
python -m video_workbench.animation_lab_iteration_runner validate \
  --repository /srv/animation-runs/task-a/checkout \
  --request /srv/animation-runs/task-a/receipts/request.json \
  --proposal /srv/animation-runs/task-a/proposal.json \
  --run-dir /srv/animation-runs/task-a/receipts

python -m video_workbench.animation_lab_iteration_runner apply \
  --repository /srv/animation-runs/task-a/checkout \
  --request /srv/animation-runs/task-a/receipts/request.json \
  --proposal /srv/animation-runs/task-a/proposal.json \
  --run-dir /srv/animation-runs/task-a/receipts
```

工作区被用户改过、源码SHA不匹配、越界窗口、重复替换、无变化或过大提案均拒绝，不覆盖原仓库。`apply`不提交、不推送，也不表示通过验收。

## 可选的真实Agent调用

```sh
python -m video_workbench.animation_lab_iteration_runner agent-submit \
  --request /srv/animation-runs/task-a/receipts/request.json \
  --run-dir /srv/animation-runs/task-a/receipts \
  --base-url http://agent-service.agent-service.svc.cluster.local:8080 \
  --token-file /run/secrets/animation-developer-agent/token \
  --profile-revision '<已配置的revision>'
```

这一步会产生真实模型用量；离线prepare/validate/apply不会调用模型。提交前先持久保存claim，使用请求身份作为幂等键。响应丢失后使用相同参数的`agent-reconcile`进行只读lookup，不再次submit。已有claim不自动重放；未接收时需先核对上游状态再建立明确的新运行。

任务完成后，用相同连接参数运行`agent-collect`。执行器核对任务业务引用、profile/revision、原attempt停止证明、冻结manifest和下载字节摘要，再解开`response.json`的text提案。未完成、停止未证实、摘要不符或提案身份不符均拒绝；collect不会自动apply。

## 隔离测试与PR

真实生成代码只在独立容器中测试。开发者须先准备已安装且经过验收的测试镜像，包含Python、pytest及模块所需依赖，显式传入`:latest@sha256:...`。runner不会pull镜像、联网安装依赖，镜像缺失或容器不可用会产生失败回执，没有在持有凭据的宿主直接执行代码的fallback。

```sh
python -m video_workbench.animation_lab_iteration_runner check \
  --repository /srv/animation-runs/task-a/checkout \
  --request /srv/animation-runs/task-a/receipts/request.json \
  --run-dir /srv/animation-runs/task-a/receipts \
  --check-image '<已安装的测试镜像>:latest@sha256:<实际digest>'
```

固定容器命令禁用网络，使用UID/GID10001、只读根文件系统、只读checkout、可写临时目录、drop ALL、no-new-privileges及CPU/内存/PID限制。不挂载HOME、SSH目录、Docker socket、Agent token或GitHub凭据。模块测试由runner源码映射决定；修改process/continuity或diagram/process共享文件会合并相关模块检查，修改基础motion合同会扩大到全部相关动画模块。反馈与提案无法替换命令。已有renderer镜像是否有Python/pytest不能靠名称推断，未核对依赖前不能直接当作测试镜像。

`checks.json`绑定实际候选diff与固定测试命令。失败/超时不能publish；通过只代表本模块技术检查，不代表审美认可、旁白同步或正式采用。共享接口或时间轴的改动仍需补充相关集成验收。

检查通过且diff未再改变时，开发者可以显式运行`publish`：

```sh
python -m video_workbench.animation_lab_iteration_runner publish \
  --repository /srv/animation-runs/task-a/checkout \
  --request /srv/animation-runs/task-a/receipts/request.json \
  --checks /srv/animation-runs/task-a/receipts/checks.json \
  --run-dir /srv/animation-runs/task-a/receipts
```

执行器使用本机已配置的Git/gh身份，只向精确的`zmhhaha/panghu_agent` origin推送该开发分支并创建draft PR。PR正文通过UTF-8文件传递，带任务、源码与技术检查身份；不合并、不部署、不将开发者标注变成生产采用。发布前写入claim；网络结果不明时停止，读取`commit.json`及GitHub实际分支/PR人工核对，不能盲目重试创建PR。

`synced-event.json`、`running-event.json`、`pr-open-event.json`或失败事件可逐项导入工作台，事件使用精确expected_state和幂等键。候选镜像发布、对比链接及其验收仍需真实结果回传，runner不伪造candidate_ready/accepted事件。

## 绑定已有候选评测

真实候选已在独立分支编译、采样，形成可由现有`load_evaluation`完整校验的冻结报告后，可以运行：

```sh
python -m video_workbench.animation_lab_iteration_runner bind-candidate \
  --repository /srv/animation-runs/task-a/checkout \
  --request /srv/animation-runs/task-a/receipts/request.json \
  --publication-event /srv/animation-runs/task-a/receipts/pr-open-event.json \
  --evaluation-dir /srv/animation-evaluations/canvas-candidate-r1 \
  --run-dir /srv/animation-runs/task-a/receipts
```

该命令不渲染、不调用模型、不部署。它要求checkout保持干净，HEAD等于PR事件中的commit；冻结报告技术检查通过、模块和反馈的case_id匹配，原评测反馈已有semantic_input_sha256时必须相同。新增`iteration-candidate.json`将任务、源码、提交、冻结运行/报告、产物、runtime和语义摘要绑定，原run.json/technical.json/review-request.json保持原字节。该目录及sidecar随后需要按已有冻结资料发布流程放入服务器只读evaluations卷，工作台才能实际核验并开放候选验收。

已有评测格式没有独立源码构建证明。sidecar明确标记`developer_bound_frozen_evaluation_not_independent_build_attestation`，只记录开发者对提交和已验证冻结报告的关联，不证明这些画面一定由该提交构建。单次报告只覆盖其实际case，不扩展成整个模块或任务中全部样例已验收；后续构建来源证明应由隔离采样流程另外提供。

## 验证范围

此实现的回归使用真实本地Git仓库与小型明确标注的合成源码，测试窗口绑定、dirty拒绝、受限修改、差异身份、固定容器参数、失败拒绝、manifest/停止证明及draft PR命令。测试中的Agent和GitHub响应均为fake transport，Docker执行也以fixture专用执行器核对；不宣称已经运行真实付费模型、生产测试容器或创建真实PR。服务器工作台的任务导出/状态导入验收另行记录。
