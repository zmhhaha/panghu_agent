# 执行器公开资料验收

最新状态（2026-10-22）：用户已通过真实 `website_development_before_manual_gate` 选择“先开发并测试网站（推荐）”，允许 PowerPoint 验收前开发测试，正式发布/邀请前的实际 PowerPoint 门禁仍保留。本机 SSH 开发站 `http://127.0.0.1:18505` 的 FastAPI/PostgreSQL CRUD、上传与受控材料解析已真实联通，网页提供解析状态和受限摘要；使用固定 local test identity，非正式 Casdoor。生成仍为 `POST /plans` 503 `execution_not_connected`，不能称 PPT 闭环可用。R8/V2 原 actor NEEDS_INPUT、独立 review 与非 accepted base 状态不变，人工 PowerPoint 验收仍待完成。下文旧实现状态、顺序决定与样稿镜像证据为历史记录，最新开发事实见末尾。

此前本地镜像：`ppt-service-worker:arm64-c350bc31-preview4`，image ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`；官方前台预览生命周期已无模型实测，142 项受限 ARM64 测试通过（9.503 秒、无 skip）。attempt 6 仍为 needs_input，无 PNG/PPTX、发布或 PowerPoint 人工验收；后续模型 retry 未启动。新控制器私有 working continuation 已通过 166 项受限测试（9.121 秒、无 skip）及免费 preflight，preview4 Worker 镜像未变；下一次收费授权尚未取得，详情见末尾补记。

样稿输入由固定提交 `c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7` 的 PPT Master 公开文档产生，不含私人会话或用户材料。

在 `ppt_service` 目录先运行 [prepare_sample.py](../scripts/prepare_sample.py)：

```bash
python scripts/prepare_sample.py --archive .vendors/ppt-master.tar --output .runtime/sample/inputs
```

这会校验固定 vendor archive 的 SHA-256，并准备文本、流程示意图和哈希，不调用模型、不生成 Office 文件。生成任务和图表验收基准保存在 inputs 的上一级，不能一起挂到 Worker 的 inputs。样稿目标为 6 页中文技术说明；第 4 页采用原生文档中的 Q1/Q2/Q3 示例，Cloud 为 12/15/19，Services 为 8/9/11。页面必须写明“文档示例数据”，来源没有单位，不补货币/公司/市场事实。

真实阶段由 [run_sample.py](../scripts/run_sample.py) 启动，每次命令只执行一个阶段，会产生真实模型请求。本轮授权主机使用根目录 `/root/ppt-service-sample/acceptance-v3`，输入位于其 `inputs` 子目录，初始任务为根目录内的 `sample-task.initial.json`。不要对已经准备好的目录重跑准备命令或覆盖输入。

以 root 运行时，根目录必须归 root 且权限为 `0700`；inputs 可以保持 UID 10000，但内容必须可由容器 UID 10000 读取。任务与输入不得含符号链接、硬链接或特殊文件。运行器使用本地 Docker socket，需要 ARM64 Linux 主机、本地已有镜像，以及安装本次临时防火墙规则所需的权限；缺少 `DOCKER-USER` 或 bridge netfilter 时停止，不继续调用模型。

先由运维通过既有安全配置途径准备根目录内的 `upstream.env`。该文件只配置 `PPT_UPSTREAM_API_KEY`，值由运维注入，不放进任务 JSON、inputs、命令行、聊天或仓库。文件必须归运行用户所有且权限为 `0600`，不能位于 inputs 或检查点目录内。已有文件直接复用，不用示例命令覆盖。运行器只检查该文件的元数据并将路径直接交给 Docker，不读取、复制或打印真实 key；只有代理容器接收该 env 文件，Worker 只接收每次新生成的 client token。

**本轮实际凭据来自平台既有 key，尚非独立供应商限权 key。** 容器内的凭据隔离不等于供应商侧限权、独立预算或生产接入已经完成。

从 `ppt_service` 目录运行初始阶段的示例：

```bash
python scripts/run_sample.py \
  --root /root/ppt-service-sample/acceptance-v3 \
  --image ppt-service-worker:arm64-c350bc31-usage \
  --task /root/ppt-service-sample/acceptance-v3/sample-task.initial.json \
  --upstream-env /root/ppt-service-sample/acceptance-v3/upstream.env \
  --upstream-ip 62.234.50.20
```

initial 使用该 tag 对应的本地 image ID 为 `sha256:d6f73e6d621a96f5f2559c4e92c27e05d537fb1b84c34d40897ef78cf3fed1e8`；后续修复和重建不得沿用该 ID 声称为最新版本，每次实际采用的 image ID 以 attempt 状态记录为准。运行器不隐式拉取镜像。上游固定为 `https://custom-llm-proxy.panghuer.top/v1`，以 `--upstream-ip` 将该域名绑定到授权 VPS 公网地址，避免主机 Fake-IP DNS；HTTPS 仍验证原域名和证书，不关闭 TLS 校验，不跟随 HTTP 重定向。

每次运行创建独立 `attempt-<uuid>` 目录，权限为 `0700`。Worker 只挂本次 internal 网络；代理另接出口 bridge，监听仅 internal IP。启动前安装仅匹配本次 Worker IP 和桥接口的规则，只允许 TCP 到代理 8080，并拒绝其余宿主/转发流量。Worker 的启动门禁要求无外部默认路由、外部 DNS 不可解析、代理健康可达，以及宿主网关、指定宿主 SSH 和 metadata TCP 不可连接；检查不通过就不加载模型执行器。

执行器阶段依次为 `plan_initial`、`plan_options`、`prepare`、`generate`；`revise` 从明确版本检查点恢复。前两阶段分别停在上游 Stage1、Stage2 两道确认门：

- `plan_initial` 只读取资料、澄清并给出 Stage1 待确认项。必须将实际结果交给真实用户，等待其确认或补充。
- 用户实际确认 Stage1 后，才可制作新的任务 JSON：保留 job/session/输入快照，递增 attempt，设置 `phase=plan_options`，将真实提交记录写入 `stage1_confirmation`。该阶段给出三套完整设计方向和逐页计划候选，停在 Stage2 门。
- 用户实际选择并确认最终方案和逐页计划后，才可将真实提交记录写入 `stage2_confirmation`，启动 `prepare`。正式规范、锁和 split 准备通过后，才进入 `generate`。

公开验收样稿同样需要真实用户确认。不得由执行 Agent 代选、预填空白确认对象、伪造确认文件或跨门生成页面；真实网站任务的确认应由网站提交。目前不能把 CLI 样稿验证表述为网站确认流程已经实现。

后续每个阶段都需显式传入 `--checkpoint /root/ppt-service-sample/acceptance-v3/attempt-<prior-uuid>` 和已获真实确认的新任务路径。checkpoint 参数指向包含 `home`、`project` 和运行状态证明的前次 attempt 根目录，不是其中某一个子目录。运行器要求前容器仍保留且 `.State.Running=false`，核对挂载、job 和输入快照后，只复制 home/project，保持容器内 `/work/home` 与 `/work/project` 路径不变。链接一律拒绝；唯一允许识别的镜像官方技能链接在复制时被省略，交给新容器的 `configure` 重建。不要手工重启作为检查点来源的容器，或在恢复期间修改其文件。

规划阶段默认期限为 600 秒，其他阶段默认 1800 秒；可显式传入 `--deadline-seconds`，规划最大 600 秒，其他阶段最大 3600 秒。命令通过 `docker wait` 等待，结束时停止 Worker 和代理，保留两者容器及数据；停写成功后删除本次精确防火墙规则、断开并移除 internal 网络。不要提前删除前次容器，否则后续无法核验停写证明。SIGKILL、断电或清理失败需按保留状态人工检查，不能假定 `finally` 已执行。

attempt 内的记录分工如下：

- `run-state.json`：调度结果、实际 image ID、容器名、停止状态、网络/防火墙清理状态；stdout 也输出不含密钥的状态 JSON。
- `worker-state.json`、`proxy-state.json`：仅 Docker `.State`，用于停写证据，不保存完整环境或全量容器 inspect。
- `result`：本阶段 execution、network preflight、候选 manifest 等结果；其中 Agent 自报信息不是独立验收。
- `audit/usage.jsonl`：仅由代理持久写入，以 job/attempt/phase 归属的上游用量观察；audit 不挂给 Worker。未发起请求时可能尚无账本文件，不能据此补造收费记录。
- `logs`：仅运维可读的 Docker/防火墙及容器私有日志；不得将完整日志、会话或环境直接贴到用户界面。

**本轮状态记录：** initial 已在授权 ARM64 Docker 主机执行结束；attempt ID 前缀为 `ec9dc1`，run ID 为 `run_e2e224092b54459186a4bf08fffca300`。该次 exit code 为 0，`worker_stopped=true`、`proxy_stopped=true`、`firewall_removed=true`、`internal_network_removed=true`。网络启动门禁已通过：无外部默认路由、外部 DNS 阻断、代理健康和宿主/metadata TCP 拒绝。

停写后独立 `validate_sample` 校验通过，核对 8 项 artifacts、合计 106138 bytes，`input_assets_verified=true`；同时 `quality_reports_verified=false`，不能把规划材料校验通过写成正式质量报告或 PPTX 已验收。规划质量字段的纯描述文字不作为报告文件证据；显式报告 `path` 仍严格验证。本轮另修复上游错误响应前需先结束账本记录的竞态，修复后 86 项回归测试通过，耗时 6.965 秒；代码修复不会补造 initial 已缺失的历史记录。

用户已通过真实确认交互选择“按上述参数继续（推荐）”，Stage1 方案确认完成。`plan_options` 随后从该停写检查点恢复并结束：attempt `4bd3593ce1b64ff1953fdeb7bfd5e6e7`，Worker exit code 0；Worker/代理停止、临时网络与精确防火墙规则均已清理。停写后独立校验核对 10 项 artifacts、合计 136658 bytes，输入哈希通过；正式质量门未验收，无 PPTX，未发布。该次实际 image ID 为 `sha256:cbae2610f600222cdb7d822415d92987eb7bc0bbddc8d82d359a52fce646b8a9`。

用户又通过真实确认交互选择 A「证据手册」，确认六页16:9计划、第4页原生分组柱状图、仅已有图片、无备注/旁白/动画、split/complete及逐页视觉复核，默认不开启规范锁定前额外审阅。同意将第3页误写的“网站提交Stage1”更正为聊天确认；CLI样稿不证明网站流程已实现。Stage2 记录写入新的 `sample-task.prepare.json`（attempt 3），绑定第二阶段 manifest SHA-256。

`prepare` attempt `4fae7e47cd82499bb83c01c4188845d3` 已收尾：attempt 3、Worker exit code 0，Worker/代理停止，临时防火墙规则及 internal 网络已清理，网络 preflight passed。首次独立严格校验拒绝 Agent `quality` 字段中的 `passed` 等状态文字；可信控制器保留原件 `result/manifest.agent-original.json`，SHA-256 为 `117979624238ae3367a6950f86ddc0ab94d08b6d3b89eed6b107fb791fc8d5fc`，将原 quality 移入 `quality_notes`，quality 仅保留 4 个已声明报告的 path 与 SHA。规范化后 manifest SHA-256 为 `a2006705ee93dde88a485e2ba0040c390046ef432c18e1b414a5f32caadecb8e`；未改规范、锁或报告正文，未放宽校验要求。

重验核对 26 项 artifacts、合计 278281 bytes，`input_assets_verified=true`、`quality_reports_verified=false`，结果保存到运行根目录的 `prepare-validation.json`。可信控制器在独立无链接副本 `controller-check-53384c5fca3e4f268acd8953430eb759` 核对全部已确认页文案、配色、图片，并在禁网容器重跑官方 `attribution_guard` 和 `project_manager validate`，均 exit 0；唯一 empty SVG warning 属于尚未生成 SVG 的准备阶段正常提示。正式 spec SHA-256 为 `ebe5f57d179ad1978ec9cab6be0637518a11b41d5aac381aecdfaf81fd7e2cf3`，lock SHA-256 为 `e5d9bd1f82fa5d780942aa6333af28c0ceb29aa645d15035b8ec5fb99631a344`。以上为准备阶段证据，报告存在、哈希与官方检查通过不代表质量报告已全部独立验收。

`generate` attempt 4（`612bf88efea84f65bf066918e065bd4b`）从 prepare checkpoint 恢复后触及 1800 秒硬时限，execution 为 `deadline_exceeded`，run 为 `worker_failed`、exit 3；Gateway/Worker/代理均已停止，网络和防火墙规则已清理。manifest 在时限边界前已落盘，但任务未成功。保留的 `exports/acceptance-public-sample_20261021_064228_native_charts_tables.pptx` 为 6 页、42730 bytes，SHA-256 为 `95370af1dfb2947b7930273113c0bd61cedc52bd1762c4ba9164a3cf7e279d7b`。`controller-review-612bf88efea84f65bf066918e065bd4b` 是 no-link 诊断副本；本地 `.runtime/recovered-options/acceptance-sample-unaccepted.pptx` 仅供人工审阅。失败检查点不提升为恢复基线，失败草稿不成为 accepted base，未发布、未人工验收。

原 manifest 的 quality 再次混入说明字段，原件未改；本地诊断规范化仅保留报告 path/hash，原 metadata 留在 notes。诊断完整性核对 50 artifacts、1073582 bytes。独立 OOXML 与嵌入 workbook 校验确认 P4 为原生 column clustered 图表，类别 Q1/Q2/Q3，Cloud[12,15,19]、Services[8,9,11]；正文可编辑、图片哈希保留，无 notes、animations 或 transitions，记录保存为 `generate-independent-pptx-check.json`。禁网限权容器在诊断副本独立重跑归属 guard 与官方 final SVG checker，均 exit 0，6/6 passed、0 warning、0 error，日志为 `controller-review/independent-static-quality.log`。静态检查不等同 PowerPoint 渲染或人工视觉验收；当前会话模型不支持 image input，视觉检查未完成，不把 Agent 自报视觉 review 写成独立通过。所有 `quality_reports_verified` 仍为 false。

修复已在隔离目录 `/root/ppt-service-repair-20261021` 经 `build.sh` 构建为本地镜像 `ppt-service-worker:arm64-c350bc31-shutdown2`，image ID 为 `sha256:fd8ceaf194f4ec26def150ceb658494c3efe0631ef928749d368987b132f5d02`，固定 base/vendor 未变。修复包括：SIGTERM 关闭代理记录 `proxy_shutdown` / unknown，不补历史每阶段 2 条缺项；标准 printerSettings 与精确 root metadata 的兼容校验仍拒绝 macro/OLE 等；样稿新增显式 `--deadline-seconds`，规划/其他阶段默认 600/1800 秒不变，规划上限 600 秒，其他上限 3600 秒。

新镜像 UID 10000/aarch64 smoke 通过 Chromium 141.0.7390.37 与 Noto CJK。在禁网、只读根、drop ALL、no-new-privileges、限资源的容器中，只读挂载 tests/scripts，不挂凭据或 Docker socket，107 项测试全部通过（7.536 秒，含真实 Linux SIGTERM）。本地另有 8 项 checkpoint/deadline 测试通过。该构建阶段仅生成本地镜像，未 push、未部署；随后已按真实授权启动受控 retry，详见下段。旧镜像、各 attempt、之前 failed 草稿与原始记录均保留。

用户先暂停后于 2026-10-21T15:54:44+08:00 回复“可以，继续”，授权从成功 prepare checkpoint 开始 45 分钟受控 retry，不构成 PowerPoint 人工验收。新任务 `/root/ppt-service-sample/acceptance-v3/sample-task.generate.retry-5.json` 为 attempt=5，SHA-256 为 `7e6ab2e75b94b7d52ca988fcaec820e78f645b7c158cd19bfeb81104c058acf2`；来源是已独立校验的 prepare attempt `4fae7e47cd82499bb83c01c4188845d3`，失败 generate attempt `612bf88efea84f65bf066918e065bd4b` 不提升为恢复基线。运行器 `/root/ppt-service-repair-20261021/scripts/run_sample.py` 显式传入 `--deadline-seconds 2700`，全局默认 600/1800 秒不变，实际使用上述 `shutdown2` 镜像及 image ID。任务仅增加 `quality` 应只列真实报告 path/SHA、说明移至 `quality_notes` 及简洁清单要求，六页方案与数据不变。

attempt `5a1702b416164cfaa68d7e9d27d77506` 的 network-preflight passed，现已停止：runner status 为 `candidate_execution_finished`、Worker exit 0；Hermes execution 为 `completed`，耗时 779.87 秒、`gateway_stopped=true`。Worker/代理均 exit 0 并停写，临时防火墙规则与 internal 网络清理均为 true。通用执行器状态不代表制稿成功。

停写后独立 `validate_sample` 因 `FileNotFoundError` 拒绝：`result/manifest.json` 不存在。实际只有 3 个 SVG（01「公开文档验收样稿」、02「资料忠实」、03「确认流程」），无 PPTX。Agent 最终用户可见回复说明工具执行授权等待超时而暂停；受阻命令未运行且未绕过，P4–6、preview、final、export、manifest 均未完成。本次并非 45 分钟 deadline 触发，应记录为阶段交付失败；源状态原件不改，部分 runtime 不提升为恢复基线或 accepted base，未发布，PowerPoint 人工验收仍未记录。

attempt 5 代理账本为 21 started / 21 finished，outcome 为 18 completed / 1 client_disconnected / 2 proxy_shutdown，无 unpaired 或 duplicate；SIGTERM shutdown 修复已获真实运行证据。`provider_cost_reported_count=0`，费用保持 unknown、待 provider 对账，不写 token 总值。控制器拒绝证据保存在 `/root/ppt-service-sample/acceptance-v3/retry5-controller-rejection.json`，本地副本为 `.runtime/recovered-options/retry5-controller-rejection.json`；spec/lock SHA 已实际比对，与 prepare 一致。内部审批规则定位与 Worker 候选交接合同修复已完成本地构建，详见下段。保留 failed attempt 4、attempt 5 部分产物及全部原件；仍等待真实逐页反馈及最终 PowerPoint 人工验收，完整生成闭环与逐页修改仍待后续证据。

准确诊断为 terminal 中纯算术 `python3 -c`（无 file/net/process 操作）触发通用 `script execution via -e/-c flag`，等待 333.10 秒后超时，命令没有运行。`smart` 模式默认 timeout 300 秒，API 保留 `ask`；`NON_INTERACTIVE=1` 不等于 no-wait。具体 smart guardian 未批准原因仍 unknown，不宣称一定是 guardian timeout；审批策略未关闭、白名单未扩大，blocked command 未执行。本次阶段失败属于工具审批与交接合同问题，不是源材料失真。

本地修复已在 `/root/ppt-service-handoff-20261021` 固定源码构建，新 tag 为 `ppt-service-worker:arm64-c350bc31-handoff3`，image ID 为 `sha256:a11c936ddeb94c8d1e2a217c410778f659ea277dcf22b946d029fe06f4b3247b`，base/vendor 固定不变。UID 10000/aarch64、Chromium 141.0.7390.37 与 Noto CJK smoke passed；新镜像在禁网、只读根、drop ALL、no-new-privileges、限资源的容器中，只读挂载 tests/scripts、无凭据或 docker.sock，125 项测试全部通过（7.566 秒）。这些是已记录的构建验证，本次文档同步不重跑测试。

`execute.py` 的候选交接新增 4 MiB 有界读取、普通非链接文件、strict JSON 与 phase/snapshot/status 检查；manifest 缺失或非法时 exit 4，状态为 `candidate_output_missing` / `candidate_output_rejected`，保留 `hermes_status`，`candidate_validated=false`，最终独立 validator 不变。初稿 prompt 优先简单算术与文件工具，不提前调用官方 calculator；blocked 时真实交接 `needs_input`，不伪造 candidate/pass，并修正 CLI 聊天确认与网站措辞。

修复构建完成，样稿阶段仍交付失败；无 push、无部署，未进行后续收费 attempt，prompt 调整尚未经新收费生成复验。未来 retry 仍需用户选择，从已成功且独立校验的 prepare 开始新 attempt，不恢复 failed runtime。旧原件及全部镜像保留，真实逐页反馈和最终 PowerPoint 人工验收仍待完成。

initial 的代理账本观察为 started 16、finished 14、completed 14；已观察 input tokens 722014、output tokens 5421，仍有 2 个请求缺少结束记录。这是可观察记录的合计，不能声称请求或用量已完整对账。供应商未提供 reported 费用值，费用保持 unknown，不记为 0，也不按 token 自行补算成实际收费。

验收必须区分：

- ARM64 依赖、中文字体与 Chromium 运行成功；
- 隔离 Gateway 的 Sessions/Runs/停止契约经真实 HTTP 测试；
- 规范、锁、官方检查与原生图表导出通过；
- Worker 完全退出后，独立进程校验候选路径、哈希、OOXML 图表缓存和嵌入工作簿；图表期望只能使用可信调用方传入的 `expected_charts`，不得信任 manifest 自报的基准；
- 逐页 SVG PNG 视觉检查；
- 最终 PPTX 的独立渲染，以及维护者在 PowerPoint 中打开、编辑数据与中文排版验收。

SVG 浏览器预览不等同于 PowerPoint 验收。没有测过的检查项保持未完成，不能用 Agent 的 manifest 自报替代。代理 usage 不明时保持未知。运行器即使报告 `candidate_execution_finished`，也始终标记 `delivery_published=false`；Worker 的 `completed` 不等于已发布，当前仅保留未验收草稿，不声称已完成样稿交付或生产部署。

## 最新状态：attempt 6 已授权启动，待核验

用户通过真实 `ask_user_question`（id=`handoff3_retry_decision`）选择“按修复方案再试一次（推荐）”，授权界面为 `chat_question_selection`，仅 1 次、2700 秒。任务 `sample-task.generate.retry-6.json` 的 SHA-256 为 `fa8df7b7ac88071997a1ffe8a6aae3641761e09570d8e1377ea97194444f3dee`；来源为成功 prepare `4fae7e47cd82499bb83c01c4188845d3`，使用 `ppt-service-worker:arm64-c350bc31-handoff3`，image ID 为 `sha256:a11c936ddeb94c8d1e2a217c410778f659ea277dcf22b946d029fe06f4b3247b`。

新 attempt `0be96ee63ee145218cbbf47d9c1c47c3` 在启动后 103 秒的快照中为 running、network-preflight passed，尚无 SVG/PPTX/manifest；账本 7 started / 6 finished，仍有 active 请求，不据此判定缺账或完成。前述“未进行后续收费 attempt”等仅记录构建当时状态；当前此次 retry 已获授权并启动，待收尾和独立核验。attempt 4/5 均不作为恢复基线，所有原件保留；尚无新 PPTX 交付、发布或 PowerPoint 人工验收。

## 最新收尾：attempt 6 needs_input，live-preview 前提待补足

attempt `0be96ee63ee145218cbbf47d9c1c47c3` 已于 1280.34 秒结束，Worker/代理 exit 0，Gateway/容器停写为 true，防火墙与 internal 网络清理为 true；不是 2700 秒总 deadline 触发。manifest status=`needs_input`，有 2 个 questions。停写后独立严格 `validate_sample` 完整性校验通过：18 artifacts、184910 bytes，`input_assets_verified=true`、`quality_reports_verified=false`、`pptx=[]`。没有 PNG/PPTX、发布或人工验收；needs_input 不等于成功 candidate，不提升为 accepted/recovery base。

实际障碍不是审批：官方 `visual_review.py` exit 2，说明没有 running live-preview server。Agent 因顾虑 preview daemon/background terminal 限制而未启动服务；`approval_blocked=false`。这是控制器权限与服务生命周期前提需要补足，不是业务缺少事实。父代理正在实现 Worker supervisor 托管真实官方前台 server（不使用 `--daemon`），模型仅使用 ready loopback URL；禁止 Agent background/PTY 及审批策略继续保持。官方契约已只读确认 foreground、project/PID health 与实际 shutdown wait；空 SVG 状态可启动并动态发现新文件。该修复源码在此快照尚未构建，未发起新收费尝试。

控制器在 no-link 私有副本 `controller-check-retry6-0be96...` 独立禁网限权重跑 attribution_guard（exit 0）与 static final checker（6/6 passed、0 warning、0 error），spec/lock 实际哈希与已接受 prepare 一致。未渲染 PNG、未导出 PPTX、未升级 accepted base；静态检查不代表视觉验收。

账本已收尾为 37 starts / 37 finishes，无 unpaired/duplicate，35 completed / 2 proxy_shutdown；observed input/output tokens 为 4081593/34586，provider reported cost count=0，费用保持 unknown，不记 0、不估人民币，待 provider 对账。运行根目录已保存 `retry6-validation.json` 和 `retry6-usage-observation.json`；本地证据为 `.runtime/recovered-options/retry6-validation.json` 与 `.runtime/recovered-options/retry6-visual-render-blocker.json`。

attempt 4/5、全部镜像及所有原件保留。后续模型 retry 尚未授权；先做无费用生命周期测试与修复本地构建，完整生成、真实逐页反馈和最终 PowerPoint 人工验收仍待完成。本轮仅同步文档，不执行测试或模型任务；OpenSpec 额外 artifact 路径被服务拒绝，未提交历史 tasks/design/spec，不改勾选，不 apply/archive。

## preview4 构建与官方前台预览生命周期实测

本地构建目录 `/root/ppt-service-preview-20261021`，最新 tag `ppt-service-worker:arm64-c350bc31-preview4`，image ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`；固定 base/vendor 不变。UID 10000/aarch64、Chromium 141 与 Noto smoke 通过。新镜像本身在禁网、只读根、drop ALL、no-new-privileges、限资源、无凭据或 docker.sock、tests/scripts 只读及 tmpfs 环境下，全部 142 项测试通过（9.503 秒、无 skip），包括真实官方前台 server 空项目 health/project/PID 绑定、shutdown 与进程组消失；不渲染 PNG、不发模型请求。本次文档同步仅记录既有结果，不重跑测试。

`preview.py` 由控制器固定本项目 `127.0.0.1:8765`，启动真实官方 `server.py` foreground，不使用 `--daemon`；health 核对 pid/project/service/live/status/svg_output，最小环境不含 model/API keys。admit 前 ready 失败不提交收费 run；finally 真实 HTTP JSON 关闭后 wait/TERM/KILL 进程组，cleanup 失败 exit 1。发现旧派生 preview 锁时 fail closed，不 kill 旧进程、不改原件。

COMMON 与 input_message 已修正为控制器托管 URL 及实际通道；禁止 Agent background/PTY 与审批策略保持不变。PPTX 不能由 SVG 替代，PNG 不能代表视觉门通过。此次仅构建及无模型生命周期验证，无 push/部署、后续模型 retry 未启动；attempt 6 仍 needs_input，无 PNG/PPTX、发布或 PowerPoint 人工验收。

合法 needs_input 私有 working continuation 已完成严格校验、166 项受限测试及免费 preflight，仅适用于私有 working 副本，不称 accepted base。控制器规范包为 `/root/ppt-service-continuation-20261021/ppt_service`；旧镜像、各 attempt 与全部原件保留。

## 私有 needs_input working continuation 与免费 preflight

`--continue-needs-input` 默认 false，须同时提供 task.continuation 严格五键对象：`kind=needs_input`、`source_manifest_sha256`、`approved_spec_sha256`、`approved_lock_sha256`、`resolution=worker_supervised_official_preview`。只允许 generate→generate，源必须合法 needs_input；核对两容器真实 exited 0、挂载与身份、新 attempt 递增、两道确认和 assets 相等、实际 inputs 哈希、完整 manifest artifact/quality refs 哈希、批准 spec/lock 哈希、无任何 PPTX 与全树无链接。不接受 failed/timeout/candidate/revise 或 base_version_id；默认转换不变。控制器脚本不进入 Worker 镜像，preview4 image ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc` 不变。

新控制器加 preview4 在真实 ARM64 禁网、只读根、drop ALL、no-new-privileges、tmpfs、无凭据或 docker.sock 环境下，完整 166 项测试通过（9.121 秒、无 skip），CLI help 确认新 flag。实际免费 probe `/root/ppt-service-sample/acceptance-v3/controller-continuation-probe-6` 只调用 continuation_inputs 与 checkpoint clone，没有 run/admit。实际 attempt 6 源 Stage1/2 非空，scope=`private_needs_input_continuation`；18 文件/184910 bytes 完整性通过，6 SVG 哈希逐一与源相同，源 manifest SHA-256 `e2da44ca17feea10232d7c27e60ba0fd23f763d3ee18af04134a986dc4b26b88` 未变。

clone 项目在 preview4 禁网、无 key 临时容器内启动真实官方前台服务，核对 health ok、live_preview、实际同项目、6 slides 与 PID=owned child，随后进程组真实 stop。未渲染 PNG、未导出 PPTX、零模型请求、未产生业务 attempt 7、未提升原 runtime 或 accepted base、未 push/部署。报告保存在 probe 目录 `probe-report.json` 及本地 `.runtime/recovered-options/continuation6-probe-report.json`。

下一次拟保留这 6 页，仅继续 pending Step 7 视觉检查/导出，仅实际视觉发现问题才修正；此快照尚未取得该次收费授权，未启动续跑。attempt 6 仍 needs_input，真实逐页反馈和 PowerPoint 人工验收尚未完成。本轮仅记录已提供的测试与免费 probe 证据，不重跑测试、probe 或模型任务。

## attempt 7 候选 V1 收尾与独立验证

用户通过真实问题 `resume_verified_six_svg` 选择“复用六页继续（推荐）”；任务 `sample-task.generate.continue-7.json`，SHA-256 `d1d38cab5f4e48af8ef5e93794c46e8c130a3bf3cae42f14c2cbff78f859fb22`，scope=`private_needs_input_continuation`，源为 attempt 6 manifest `e2da44ca17feea10232d7c27e60ba0fd23f763d3ee18af04134a986dc4b26b88`。使用 `ppt-service-worker:arm64-c350bc31-preview4`，ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`。attempt `ed00fb6abfe2489b8f7834ce43f66d9b` execution completed、750.2 秒，Worker/代理 exit 0，Gateway+preview stopped=true，防火墙/internal 网络清理=true，`delivery_published=false`。

原 manifest 直接严格 `validate_sample` 通过，不需 normalize、不修改原件：status=candidate、33 artifacts、965257 bytes、inputs verified=true、6 slides/1 chart（slide 4、chart401）。可信 Q1/Q2/Q3、Cloud[12,15,19]、Services[8,9,11] 的 cache 与 embedded workbook 核验通过；`quality_reports_verified=false`、`visual_acceptance=false`、未发布。已产生 6 PNG 与 Agent 视觉报告，不视为父代理独立视觉或 PowerPoint 验收。

PPTX `exports/acceptance-public-sample_20261021_112554_native_charts_tables.pptx` 为 43360 bytes，SHA-256 `dcff0a90aa089ef4e46746d516f95fa148da0223cf67ff2daecec28108006068`，本地候选副本为 `.runtime/recovered-options/acceptance-sample-candidate-v1.pptx`，已提供用户手工审阅。独立 python-pptx/OOXML 实际核对 16:9、6 visible pages、每页可编辑文字（P2 连续长文本 234 字符）、P4 column clustered 原生源数据图表；只有 P3 一图，原 SHA 一致、4:1 全幅无 crop 且 bounds 在 slide 内；无备注/动画/转场，聊天确认措辞没有“已通过网站提交”。本地报告为 `retry7-independent-pptx-check.json`、`retry7-validation.json`。

no-link 私有 snapshot `controller-check-retry7-ed00fb6abfe2489b8f7834ce43f66d9b` 中重验 33 项 artifact 图及 native oracle，通过；spec/lock 批准 SHA 未变，旧 preview 锁 absent。独立 scratch 禁网限权 pinned image 重跑 attribution_guard exit 0、static final checker 6/6 passed、0 warning、0 error，证据为 snapshot 的 `controller-check.json`。这不构成 accepted base 或 publication，也不代表最终 PPTX 渲染已检查。

账本 26 starts / 26 finishes，无 unpaired/duplicate，24 completed / 2 proxy_shutdown，observed input/output tokens=3344823/18169，provider reported cost 0 条，费用保持 unknown，不记 0。attempt 4/5/6、所有原件和镜像保留。

样稿未最终验收，网站/API/DB 未部署。用户仍需报告 PowerPoint 版本，实际打开、编辑图表数据、保存后重开，并提交真实逐页反馈，随后至少完成一轮 revision。父代理视觉能力不可用，无法作独立视觉验收；PNG 与 Agent 视觉报告均不代替人工 PowerPoint 检查。候选后续角色/基线尚待用户明确选择，不提前称 accepted。本轮只同步本地文档，不执行测试、模型任务或 MCP 更新。

## R8 / V2 人工审阅版与独立控制器 review

用户于 2026-10-21 20:48 回复“可以，继续”，授权已提议 P3/P5 状态文案修订。revise8 task SHA-256 `987cf8b31ab0c8d0dbc08133c38c1a266462eacea3dc973f6555a2e56c805453`，base_version_id=`sample-review-v1-dcff0a90aa08`，review-version-maps 绑定原 V1 SHA；不构成 PowerPoint 实际验收。attempt `d6ddeff1bd5f4a8a8bb6e7e498894017` 采用 preview4（ID `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`），execution completed、1286.08 秒，Worker/代理 exit 0，Gateway/preview stopped=true，网络/防火墙清理=true。

原始 actor manifest.status=NEEDS_INPUT，仅 5 项声明 artifacts、图引用不完整；末尾额外只读包比对 `python3 -c` 工具授权等待超时，命令未运行，known rule=unknown。原 `result/manifest.json` 未改；不能表述成仅漏目录、原 manifest 通过或正式 candidate 成功。用户 21:50 再回复“继续”后，父代理仅独立只读检查、不重跑受阻命令、不启动模型；33 文件实际哈希 inventory 合计 970608 bytes，独立记录 `controller-review8-collection/manifest.controller-collected.json` 仍为 needs_input，原 manifest 另存且原哈希不变。

新 PPTX `exports/acceptance-public-sample_20261021_131243_native_charts_tables.pptx`，43274 bytes，SHA-256 `1819eb54fe25b1d760f4318da656425fb39a733432bba538b991bb1d58d770ed`，本地 `.runtime/recovered-options/acceptance-sample-review-v2.pptx` 已提供为“人工审阅版”。仅 SVG03/05、PNG03/05 改变；P1/2/4/6 SVG bytes 和 PPTX 对应 slide XML 完全相同。独立 `revision8-independent-pptx-check.json` 实际读两份 PPTX：6 页、16:9、仅 P3/P5 字变，P4 原生 column/clustered cache 与 embedded XLSX 仍为 Q1/Q2/Q3、Cloud[12,15,19]、Services[8,9,11]；P3 原图 SHA 一致、4:1 全幅无 crop/bounds 在页内，无 notes/animations/transitions。

远端 `final-review-check.json` 独立 inspect_pptx chart data verified=true，no-link snapshot 加 scratch 禁网限权重跑 attribution_guard exit 0、static final checker 6/6 passed、0 warning、0 error。控制器 review 不改变 raw actor 状态，不提升 runtime/accepted base、不发布；PowerPoint 人工验收与最终渲染检查不由这些静态证据替代。

账本 29 starts/29 finishes，无 unpaired/duplicate，26 completed/1 provider_failed/2 proxy_shutdown；observed input/output tokens=2960027/24312，provider cost 0 条，费用 unknown，不估为 0。attempt 4/5/6/7/8、全部镜像及原件保留。

实际 marker pair 为 `['.', 'revisions/round-08/base']`；preview.py 多 root 误判已做 direct-child-only 修复，同时保留全树安全扫描与 parent-marker 拒绝，15 focused Windows tests 为 12 passed/3 skipped。preview5 后续已完成本地构建及 Linux 验证，最终证据见下段；前述 Windows 测试结果保留为历史事实。COMMON 补齐所有未改页必须索引、官方检查不可跳；独立包比对应归 trusted publisher，审批规则不变。网站/API/DB 未部署，PowerPoint 版本与实际打开/编辑数据/保存重开、真实逐页反馈仍未提供。V2 仅供人工审阅，不是 accepted base。本轮不执行源码、测试、模型或 MCP 任务。

## preview5 最终构建、嵌套 marker 与完整索引核验

最新本地镜像 `ppt-service-worker:arm64-c350bc31-preview5`，image ID `sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a`，构建目录 `/root/ppt-service-preview5-20261021`，base/vendor 不变。UID 10000/aarch64、Chromium 141、Noto smoke 通过；真实镜像禁网、只读根、drop ALL、no-new-privileges、tmpfs、限资源，tests/scripts 只读且无凭据或 docker.sock，169 项测试全部通过（9.145 秒、无 skip）。新镜像不回填 R8 实际使用的 preview4。

preview.py 只选当前项目根 direct-child marker，拒绝工作区 parent marker 及多 sibling；nested 备份仍进行全树 bounds/links/hardlinks/special-file 安全扫描。实际 V2 只读副本含 `['.', 'revisions/round-08/base']`，pinned preview5 真实 foreground health 绑定当前 project/PID/6 slides 后实际停止进程组；model requests=0、pngs_rendered=false。报告为控制器运行根目录的 `controller-preview5-nested-health/probe-report.json`。R8 原项目与原 actor manifest 保持 needs_input，无 promote/push/deploy，也未进行模型复跑。

COMMON 补齐全部引用含未改页的完整哈希声明；包级独立比较归 trusted publisher，官方检查与审批停止边界不变，未绕过或关闭审批，无需额外 Python 临时比较作为清单前置。控制器运行根目录的 `controller-review8-collection/final-review-check.json` 补完整索引后独立核验通过：33 files、970608 bytes、status 仍 needs_input；独立 inspect_pptx 另列 6 页、1 个 P4 原生图表，cache/embedded workbook verified=true。静态 attribution_guard exit 0、final checker 6/6 passed、0 warning、0 error。原 actor 状态未改，V2 仍为 43274 bytes 人工审阅版，不是 accepted base。

21:50“继续”仅允许当前只读核验与整理，不是 PowerPoint 验收声明。网站开发测试是否提前到人工验收前尚未获用户明确选择，当前仍按人工验收后再展开完整业务的规则；正式发布/邀请前仍保留 PowerPoint 人工门禁。未先行更改规范或宣称用户同意业务实现。网站/API/DB 未部署，所有原件与镜像保留，本轮无源码、测试、MCP 或模型任务。

## 2026-10-22 网站开发测试与材料解析状态

真实选择 `website_development_before_manual_gate` 的“先开发并测试网站（推荐）”已授权网站开发测试提前，更新前节顺序未获授权的历史状态；正式发布/邀请用户前仍需实际 PowerPoint 验收。主代理记录 OpenSpec design/proposal revision `4268e63abfaa45f59da2bb87aefd574085e00fa3`、validate valid，未 apply/archive，tasks 暂未改（26/3/23）。本轮不访问或写入 MCP。

开发入口 `http://127.0.0.1:18505` 为本机 SSH 访问，固定 local test identity，不是正式 Casdoor。真实 HTTP 上传 201→queued→Docker parser→DB ready+summary→GET 已通过，项目 CRUD/上传也已联通；网页展示状态及受限摘要。部署瞬时证据为 5 projects/2 assets、材料均 ready，plans/jobs/versions/checkpoints/usage 为 0。之后实际浏览器 fixture 可以增加数据，不将该计数当成永久实时状态。

前端已有 27 项测试与 11 项 actual browser 记录；parse UI 已通过 41 项测试并构建 `Cb8shtpo`，新增 parse browser 正在运行，未声称通过。此前后端 20 项（3 项真实 PostgreSQL）、控制层 23 项（8 case 真实 PostgreSQL）、parser 3 项真实容器测试已记录；本轮 37 项为 17 项材料真实 PostgreSQL 测试（包含实际 Docker 解析→PostgreSQL）加 20 项 API 回归。本轮文档整理不重跑这些测试。

schema 2 增量迁移保留原 4 projects/1 asset 的 mapping、hash 和 bytes，备份位于 `/root/ppt-service-webdev-20261021/backups/pre-materials-20261022`。API image=`ppt-service-api:parse-dev-20261022`、ID=`8148cd5182e7`，API container=`b516ac8a15c7`、IP=`172.18.0.3`；parse coordinator=`694a11cfe224`、IP=`172.18.0.4`、name=`ppt-service-materials-dev-20261022`。无 upstream env、paid=false、model disabled。仅 trusted outer 有 Docker socket/DB/root DAC_OVERRIDE；实际 parser 子容器 network none/non-root/read-only/drop ALL，无 DB/socket；DATA_ROOT/code 采用相同路径 bind。

`POST /plans` 仍 503 `execution_not_connected`，正式 SSO、可信数值基准、完整 Worker 网络与发布质量门、PowerPoint 人工验收尚未完成，不宣称 PPT 生成闭环可用。R8/V2 仍为原 actor needs_input、控制器独立 review、非 accepted base；全部旧镜像测试及原件保留。用户允许开发测试不代表最终验收、目标完成或生产上线。本轮仅更新两份本地 README，无源码、部署、测试、模型或 MCP 操作。
