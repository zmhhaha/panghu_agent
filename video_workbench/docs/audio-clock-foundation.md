# 实际配音时间基准：基础与受限实测

2026-11-05，分支 `codex/video-audio-clock`，基线为应用主线 `975c0997cd21c56b5f1eb18761cfb7ad38ad7040`。

本批交付独立字词合同、离线适配入口和同步评价工具。生产工作台尚未接入真实字词对齐；没有改动已有 motion/continuity 合同、编译器、快照或部署。后续任务由 OpenSpec `improve-video-audio-clock-and-expression` 跟踪。

## 可以做什么

- `word_alignment.py`：将真实后端字/词结果绑定到录音 SHA、实际时长、spoken_text 及完整 source/spoken 句段映射；区分 `provider_native` 和 `forced_alignment`。重复词以文本位置定位。
- `alignment_lab.py`：离线导入既有后端 JSON；或显式指定本地 Qwen 模型目录进行实验。懒加载重依赖，CPU 线程限制在 1–4；不自动下载模型、不调用 TTS/Agent、不修改业务数据库。
- `alignment_benchmark.py`：冻结至少十个不重叠关键词及预先确定的误差门槛，按独立人工参考比较实际起点。未填参考时间返回 `reference_pending`，不会判通过。
- `alignment_assets.py`：固定官方11文件闭包，逐文件验证大小、SHA和文件revision；完整清单摘要作为模型身份。
- `alignment_runner.py`：Windows Job在子进程执行第一条指令前施加2核等效CPU速率、4GiB提交内存和300秒限制，限制日志并清理完整进程树。未验证外部边界的Linux环境明确拒绝运行。
- 新结果始终保持人工待核准。模型、导入、单元测试和真人听审分别记录。

不会均分一个多字词的时长来制造字级精度。锚点切进后端单元内部或单元跨句无法投影时，明确拒绝。漏字、错字、无穷数、越界、错录音或文本映射变化也拒绝。

## 固定样本

`samples/audio-clock-baseline-v1.json` 绑定两条已有云健录音：

- 18 秒，24kHz/单声道/PCM16，864044 字节。
- WAV SHA-256：`3fa64b4a6800984b4b87719adca38a2eee533d19a5616d7a79305c08b566bce8`。
- 原提供者 `kokoro-rk3588`，音色 `zm_yunjian`，速度 1.0；原始生成回执摘要已冻结。
- 12 个独立关键词，包括会话日志、完整回答、失败、重试、取消、历史和尝试记录。人工参考时间全部留空。

第二条为32秒技术样本，24kHz/单声道/PCM16，SHA-256为 `e67c5a2e871aaf5928dc02e9569f51af31f3ae9aa3dd4bde401354da6bae3855`。以明确项目和候选ID只读核对了原始生成输入，保留 source/spoken 精确文本及三条斜杠术语替换。选定14个独立关键词，包含 Session、deriveMessages、失败/重试/取消和模型历史。

32秒样本满足技术片段时长及读音替换场景；数字和逐字母缩写覆盖仍需额外用例，独立人工时间参考尚未建立。既有字幕时间属于估算，不拿它作为人工真值。没有为了本批工作重新生成语音。

最初预算保留在baseline v1，不覆写历史。用户授权继续后，`samples/audio-clock-real-trial-v1.json` 冻结本轮32秒样本、输入摘要、完整模型身份及明确的Windows执行范围：单次、CPU 2线程、4GiB Job提交内存、300秒、最长60秒音频、无付费API/TTS/渲染调用，模型资产下载上限为已查明的1,840,073,172字节。运行器施加的是private commit限制，不是总RSS或映射文件物理内存上限；本机结果不能当作ARM/NPU性能。

首轮误差门槛为至少10个独立参考、中位绝对误差不超过150ms、最大误差不超过300ms，人工参考不确定性不超过50ms。门槛作为实验判据预先冻结，不是模型精度承诺；调整需建立新的基准版本并说明原因。

## 离线使用

模型与媒体不进入 Git。开发工作区统一在忽略的 `video_workbench/rendered/audio-clock/` 保存媒体和运行证据。先校验媒体摘要、准备精确朗读文本，以及已有后端的真实结果数组：

```json
[{"text":"请求","start_time":0.2,"end_time":0.7}]
```

这只是格式示例，不是真实样本时间。导入时填写真实来源：

```sh
python -m video_workbench.alignment_lab \
  --audio video_workbench/rendered/audio-clock/sample.wav \
  --spoken-text video_workbench/rendered/audio-clock/spoken.txt \
  --backend-json /absolute/path/to/actual-backend-result.json \
  --source-kind forced_alignment --provider actual-provider \
  --model actual-model --version actual-version \
  --output video_workbench/rendered/audio-clock/alignment-first.json
```

原生时间戳使用 `--source-kind provider_native`。有读音替换时同时提供 `--source-text`。输出父目录必须存在，输入、已有结果、符号链接等输出别名被拒绝。

可选Qwen入口使用 `--model-path /absolute/local/model --asset-manifest-sha256 FULL_64_CHARACTER_MANIFEST_DIGEST --threads 2`。必须先有完整资产清单，模型调用前后均重新核验；不再用单个权重revision冒充完整模型身份。入口只接受本地目录，禁用HuggingFace自动下载并使用 `local_files_only`；这不是操作系统级网络隔离。实际测试必须经有界运行器启动，不能只凭CLI中的线程参数宣称受限。

官方源码已核对 `List[ForcedAlignResult]` / `.items` / `text,start_time,end_time` 数据形状。官方本身会修正或插值部分模型时间，因此结果仍属于模型推断，不能代替独立人工参考。我们不再额外均分多字单元。注入测试明确标为 `injected-adapter-test`，只有真实SDK调用完成才记录 `sdk_runtime_verified=true`，仍不表示完整覆盖或人工精度通过。

提供 `--raw-output` 可在严格文本校验前保存SDK返回的items。这里的raw已经包含SDK内部时间修正，并非原始声学logits。即使最终合同拒绝结果，也可用这份证据分析，避免再次推理；raw不作为有效对齐。运行器只有在进程清理确认后才计算产物摘要，清理未确认时明确跳过。

`samples/audio-clock-model-candidate-v1.json` 保留初次查询记录。续作已下载并逐文件验证官方11个文件，合计 **1,840,073,172字节，约1.84GB/1.714GiB**，其中权重1,835,544,544字节。`samples/audio-clock-model-assets-v1.json` 保存完整固定清单，canonical SHA256为 `804a0b043891491014fe2fc410dc58904cea34c479845237e427273d1d710319`。体积不包含Python依赖和推理内存，float32仍可能超过当前预算。

模型及独立环境统一位于外层忽略目录 `.runtime/video-audio-production/alignment-experiment/`；真实测试日志和结果位于已有的 `video_workbench/rendered/audio-clock/real-trial/`。不进入Git，不修改全局Torch或生产语音节点。

## 2026-11-05 单次真实模型测试

结果记录于 `samples/audio-clock-real-trial-result-v1.json`，状态为 **memory_limit**，未取得可用对齐时间。

独立Python 3.13.5环境安装qwen-asr 0.0.6、Transformers 4.57.6和Accelerate 1.12.0，复用全局Torch 2.7.1+cu118，但实验指定CPU/float32/eager。29个SDK依赖档案合计149,192,007字节；镜像下载摘要逐项匹配PyPI官方元数据，安装使用固定URL和 `--require-hashes`，没有新增Torch/CUDA包。完整版本、安装清单和哈希回执保存在runtime目录；这些档案大小不是总网络传输量，前期慢速下载及中断没有精确计量。

初次沙箱导入检查因Windows DLL初始化失败，未加载模型。相同的独立有界导入检查在沙箱外通过：23.37秒，峰值提交内存1,090,105,344字节，进程清理确认。这个结果只证明SDK可以导入，不是模型推理通过。

随后仅对固定32秒云健录音进行一次模型测试，预算为2线程、4GiB Job提交内存、300秒。33.06秒时运行器收到Windows进程/Job内存限额通知，终止整棵进程树并确认清理。记录的峰值提交内存为2,878,787,584字节，未超过预算；新的较大分配申请可以在成功提交前因剩余额度不足被拒绝，因此峰值低于限额不矛盾。当前运行器未分别保存通知9和10，不能进一步区分进程限额与Job限额。

两份日志均为空，raw和正式结果文件均未生成，无法确认失败发生在权重加载、float32转换或forward阶段，不能声称完整推理已完成。静态safetensors头显示917,728,896个BF16张量元素，转成float32的张量体积为3,670,915,584字节；这只是静态体积估算，不是实测内存峰值，也不能与导入峰值简单相加当作精确运行需求。

模型闭包和WAV在测试后重新验证一致。未自动重试或扩大预算，没有TTS、Agent、渲染或付费API调用。人工参考仍为空，精度、声音自然度和ARM/NPU性能均未验收；OpenSpec 2.2b继续待完成。下一轮应先比较低精度/较小后端或独立计算位置，并在任何新模型尝试前建立新实验版本，不能直接把当前配置部署到语音Pod。

回归结果为 **347 passed、2 skipped**，JUnit位于 `rendered/audio-clock/real-trial/tests.xml`。覆盖新资产/运行边界及NaN诊断保存，保留motion和continuity历史编译黄金字节。独立审查确认内存失败的表述和证据边界；没有把自动结果或技术检查当成人工核准。

## 当前运行资源与验证边界

只读检查发现语音节点约86%内存占用，宿主可用约3.5GiB、无swap，现有语音Pod上限4GiB；控制节点可用约7.2GiB。未在已查的标准位置找到可复用的真实对齐模型或 Qwen 运行环境。因此本批没有把数GiB模型部署到语音节点。

单元测试使用显式合成的后端时间和注入runner，只验证合同和适配。报告区分 `mode=import`、`runner_injected` 与 `inference_executed`，没有将它们当真实模型/听感/性能证据。Windows 无符号链接创建权限的用例单独跳过并记录，不算通过。

回归覆盖新模块、motion句段逻辑、已发布motion及continuity编译器的黄金字节与重建准入；结果记录于OpenSpec检查点。独立审查发现的重叠参考点误报、悬空输出链接，以及非有限后端时间无法保存诊断的问题均已修复。

## 下一批

1. 按本轮实测结论取得可用的有界真实对齐结果，保持失败证据和资源记录。
2. 补齐技术样本和独立人工参考，比较估算与真实结果；不自动核准时间。
3. 在明确的新版本里接入存储/API与关键词动作、冻结快照和跨镜时间映射；旧编译器按原版本重建。
4. 按2026-11-05后续设计，受控二维路径传递和状态变化基础与真实对齐并行推进、分别验收，详见 [二维过程演示基础](process-motion-foundation.md)。生产词级同步仍需真实结果，配音自然度独立评估，保留项目统一音色、无字幕默认及引擎可替换方向。
