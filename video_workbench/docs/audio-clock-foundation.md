# 实际配音时间基准：第一批实现

2026-11-05，分支 `codex/video-audio-clock`，基线为应用主线 `975c0997cd21c56b5f1eb18761cfb7ad38ad7040`。

本批交付独立字词合同、离线适配入口和同步评价工具。生产工作台尚未接入真实字词对齐；没有改动已有 motion/continuity 合同、编译器、快照或部署。后续任务由 OpenSpec `improve-video-audio-clock-and-expression` 跟踪。

## 可以做什么

- `word_alignment.py`：将真实后端字/词结果绑定到录音 SHA、实际时长、spoken_text 及完整 source/spoken 句段映射；区分 `provider_native` 和 `forced_alignment`。重复词以文本位置定位。
- `alignment_lab.py`：离线导入既有后端 JSON；或显式指定本地 Qwen 模型目录进行实验。懒加载重依赖，CPU 线程限制在 1–4；不自动下载模型、不调用 TTS/Agent、不修改业务数据库。
- `alignment_benchmark.py`：冻结至少十个不重叠关键词及预先确定的误差门槛，按独立人工参考比较实际起点。未填参考时间返回 `reference_pending`，不会判通过。
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

模型运行前的实验范围已记录在样本中：单例一次、CPU 2 线程、内存上限4GiB、300秒截止、音频最长60秒、无付费API/TTS/渲染调用。CLI限制输入和线程；内存及墙钟限制仍需在实际执行时由独立容器或系统运行器强制施加，不能将预算记录当成已经执行。

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

可选 Qwen 实验入口为 `--model-path /absolute/local/model --model-revision FULL_40_CHARACTER_REVISION --threads 2`，替换导入参数。它只接受本地目录，禁用 HuggingFace 自动下载并使用 `local_files_only`；这不是操作系统级网络隔离。模型revision是操作员声明，真实SDK、模型文件身份、运行性能和目标硬件适配仍需单独验证；报告保持 `source-reviewed-runtime-unverified`，不会将源码核对标成已运行。

官方源码已核对 `List[ForcedAlignResult]` / `.items` / `text,start_time,end_time` 数据形状；尚未安装或运行SDK。官方本身会修正或插值部分模型时间，因此结果仍属于模型推断，不能代替独立人工参考。我们不再额外均分多字单元。

`samples/audio-clock-model-candidate-v1.json` 保存只读查得的官方ModelScope元数据：11个模型文件合计 **1,840,073,172字节，约1.84GB/1.714GiB**；其中权重为1,835,544,544字节。已记录官方文件SHA及该权重文件revision，不把它冒充完整HF仓库固定版本。体积不包含PyTorch/Transformers依赖及推理内存；float32加载可能超过当前4GiB实验预算。当前只查询，没有下载权重、安装SDK或扩大资源。

## 当前运行资源与验证边界

只读检查发现语音节点约86%内存占用，宿主可用约3.5GiB、无swap，现有语音Pod上限4GiB；控制节点可用约7.2GiB。未在已查的标准位置找到可复用的真实对齐模型或 Qwen 运行环境。因此本批没有把数GiB模型部署到语音节点。

单元测试使用显式合成的后端时间和注入runner，只验证合同和适配。报告区分 `mode=import`、`runner_injected` 与 `inference_executed`，没有将它们当真实模型/听感/性能证据。Windows 无符号链接创建权限的用例单独跳过并记录，不算通过。

最终回归覆盖新模块、motion句段逻辑、已发布motion及continuity编译器的黄金字节与重建准入；结果记录于OpenSpec检查点。独立审查发现的重叠参考点误报和悬空输出链接问题均已修复。

## 下一批

1. 核实候选模型固定版本、实际下载体积、SDK和可执行环境，取得有资源边界的真实对齐结果。
2. 补齐技术样本和独立人工参考，比较估算与真实结果；不自动核准时间。
3. 在明确的新版本里接入存储/API与关键词动作、冻结快照和跨镜时间映射；旧编译器按原版本重建。
4. 通过同步验证后，再扩充沿路径传递和状态变化。配音自然度独立评估，保留项目统一音色、无字幕默认及引擎可替换方向。
