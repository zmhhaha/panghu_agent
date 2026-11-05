# 项目统一配音音色

## 2026-11-01：四个女声已上线，保持项目统一音色

新增晓贝 `zf_xiaobei`、晓妮 `zf_xiaoni`、晓晓 `zf_xiaoxiao`、晓伊 `zf_xiaoyi`。连同原有四男声共八项，默认仍是云健；一个项目统一选择一个音色，所有镜头继承。更换音色后旧候选保留实际音色并标记需更新，不自动生成或采用。

- 语音源码 `e811a5f97df54efc6eb01878f5876d31c1fea1ff`，镜像 `arm-cluster-master:5000/speech-service:latest@sha256:5ff9a472792aaf0e931cc6c61be30a14246a4397687b0ef253546358a303c0bc`；Pod `speech-service-77fb679896-tmpsr`，Ready、0 重启。官方固定 revision `f3ff3571791e39611d31c381e3a41a3af07b4987` 四女声经 SHA、严格 ZIP/descriptor、finite float32 表和 worker 八资产重验，发布前再校验；未执行 pickle/Torch。
- 工作台镜像保持 `arm-cluster-master:5000/video-workbench:latest@sha256:e5e5aff8c1d94ad237bf035268dfd192db79bd8c39c216998dfb4df7eea5bb77`，只刷新启动目录；新 Pod `video-workbench-5c69474df6-9l6rv`，Ready、0 重启。语音只改镜像、发布身份与 `SPEECH_VOICES`，工作台只改目录刷新注解，其他 Deployment spec 比对一致。
- 真实登录浏览器确认八选项、项目保存与镜头继承、未保存禁生成和刷新持久化。独立项目「女声音色对比（项目统一音色验收）」`54ab2134-fdc7-4cd8-9006-e48084a23c2f`，revision 14，仅一个镜头；同稿 speed=1.0，四女声各一次成功，无自动重试，均未采用。新增视觉 Agent 调用 0、整片渲染 0。
- 语音单元测试 92 passed，测量工具测试 15 passed，前端 30 passed；发布脚本独立只读审查通过。复用单模型、串行 NPU 请求，保持原规划与禁止 CPU 波形静默回退；实际仍含 CPU 文本前端和混合计算。

| 音色 | 实测时长 | WAV SHA256 |
| --- | ---: | --- |
| zf_xiaobei | 7 秒 | `8a96b26ece1e0ea35e46c9090c39a998a4c30a6b8f03d526a307c793421135bb` |
| zf_xiaoni | 6 秒 | `27cc1c29338c9d0c62ab7913593f27748e342cfb7309c06eadacb1c711635605` |
| zf_xiaoxiao | 6 秒 | `7887b81878c19da43fbbe3ae5b31a437690ba073b094e9d6ebb45926127bc9d4` |
| zf_xiaoyi | 6 秒 | `78ae8ae68a1d559af697312c61ad4f80483999503d07649db6ad99c1ed6bf27e` |

四 WAV 均完整 PCM16、24kHz、单声道，候选与不可变请求音色绑定通过。原 DSH r27、八镜副本 r69 及八条采用录音非过期检查通过。证据在 `D:/github/armbianbegin/.runtime/video-audio-production/multi-female-voices/`，包括发布与浏览器回执、四 WAV 和取回回执；归档 SHA256 `2701e65148ee6bc3d90cd08388a75aea3030bd0ab58969b6bb0dc70cc73e8b16`。取回过程只读数据库，不合成或采用；不把未另行采集的逐段 NPU metadata 记作本轮外部证据。

增加音色不代表自然度已改善或获得用户认可，完整稿听感与术语确认仍待完成。保持无字幕默认、原项目和云健默认值；两开发分支独立保存，不合主线、不 apply/archive。以下此前记录为历史。

一个项目只选择一个音色，所有镜头的新配音继承它。云健仍为默认；其他选项来自语音服务实际启用并校验成功的目录，不在浏览器硬编码。

在项目的「项目配音」选择音色并保存，再到镜头生成、试听和明确采用。选择或保存不会自动生成。切换项目音色后，旧配音仍可试听下载，但不能作为当前音色采用或直接混入新成片；需要逐镜重新生成或上传对应录音。不同项目互不影响。上传录音单独标注，不推断其音色。

镜头仍可保存语速与读音修正，这些参数不覆盖项目音色。无字幕、保留旁白的默认呈现不变。增加音色选择并不意味着模型自然度或完整稿听感已通过。

## 接口与兼容性

- `GET /api/session` 的 `speech_voices` 为 `{id,name,language,gender,default}` 列表，`speech_voice` 是全局默认。
- `project.speech_settings.voice` 是项目的已保存音色。`PATCH /api/projects/{id}/speech` 接收 `expected_revision` 与 `voice`，沿用 owner、CSRF 与版本冲突检查。
- `scene.speech_settings.voice` 是继承后的只读结果；原镜头 PATCH 只允许语速与读音规则。
- 历史候选与运行任务冻结实际音色，项目改动不会把旧录音重标成新音色。迟到结果保留原身份并标记过期，不自动采用。
- 旧项目未保存项目音色时沿用默认值，旁白绑定文档的形状不变；扩展目录本身不会令已有云健录音过期。
- 已保存音色下线时保留其项目设置和录音历史，新生成返回 `speech_voice_unavailable`，不替换成默认值。

## 运行配置

工作台设置 `VIDEO_TTS_VOICE_DISCOVERY=true`（清单生成器参数 `--speech-voice-discovery`），启动时用现有凭据请求语音服务 `GET /v1/voices`。只接受匹配模型、唯一且有效的音色目录，配置默认值必须包含其中；请求有大小、时限限制，不重试、不跟随重定向。发现失败则拒绝启动，避免展示未验证列表。通用语音 API 未实现目录时可保留默认关闭，只使用已配置的默认音色。

服务侧显式启用 `SPEECH_VOICES`，在单模型和串行 NPU 请求下选择经固定官方资产校验的 style。CPU/NPU 混合路径、原有规划限制和禁止 CPU 波形静默回退不变。

## 本轮验证

2026-11-01 工作台 Python 测试 458 passed / 1 skipped，前端 Node 30 passed。覆盖统一音色请求、全项目失效、其他项目不变、历史及迟到结果身份、owner/CSRF/revision、音色下线、非法目录与前端草稿。最初运行因默认临时目录权限报错；一次目录超大样本因参数 ID 过长触发 Windows 路径问题，已改为短 ID，最终回归通过。跳过项是原 Windows Secret 符号链接权限场景。

部署和真实短句结果见下方；不把技术检查记作用户听感验收。

## 2026-11-01 上线与真实验证

四个中文男声已上线：云健 `zm_yunjian`（默认）、云希 `zm_yunxi`、云夏 `zm_yunxia`、云扬 `zm_yunyang`。用户明确要求一个项目统一一个音色，界面没有逐镜音色选择。

- 工作台源码 `cbca8323458dda3f00fe9143cb38f26460c31f77`；镜像 `arm-cluster-master:5000/video-workbench:latest@sha256:e5e5aff8c1d94ad237bf035268dfd192db79bd8c39c216998dfb4df7eea5bb77`；Pod `video-workbench-5fff98c4d8-wlv86`，Ready、0 重启。
- 语音源码 `a0f43e04eab5b77a88ddfcf4e08fa1287f2b47e0`；镜像 `arm-cluster-master:5000/speech-service:latest@sha256:96b4bc880e18c9b3570b263da3b34d67574a0a1a700f252deadba658870287b7`；Pod `speech-service-d97d54fb6-8vbb2`，Ready、0 重启。四音色目录及 PID1 capabilities 全零、NoNewPrivs=1 已检查。
- 发布只改变两个 Deployment 的镜像、身份注解及分别一个开关 `VIDEO_TTS_VOICE_DISCOVERY=true` / `SPEECH_VOICES`。server dry-run 与发布后对比证明其他 spec 不变。
- 真实登录浏览器确认四选项、默认云健、未保存时禁止新配音、保存不自动生成或采用、切换时旧候选过期但仍保留实际音色。刷新后项目设置与三候选持久保存。
- 独立项目「男声音色对比（项目统一音色验收）」`9d025676-7138-4a45-83fb-7987b6d15872`，revision 11，一个镜头。旁白为「先看清这张图，再跟着旁白，一步一步理解它的工作方式。」，语速 1.0；仅云希、云夏、云扬各一次，全部成功，音频依次 6/6/5 秒。没有新增视觉 Agent 调用、没有重渲染整片，三候选均未采用。
- 三份 PCM WAV 24kHz、单声道、16bit，完整帧、摘要、实际请求与候选音色绑定均通过。三个 SHA 不同。历史原项目保持 r27，八镜验收副本保持 r69，8 条采用音频仍非 stale；没有重生成这些旧项目。
- 证据 `D:/github/armbianbegin/.runtime/video-audio-production/multi-male-voices/`：发布回执、`browser-comparison-evidence/transfer-receipt.json`、三份 WAV。归档 SHA256 `9ffe8d26ed2ca2d63aab0eba0c68e3b9cd2c61ad5dea0211403cb1ba88942ba7`。采集只读数据库，未导出数据库、owner 或凭据；collector 的 `synthesis_requests=0` 表示采集本身不推理，本轮浏览器实际共 3 次推理。

语音适配器在成功返回前强制验证整段和各分段 NPU 路由、fallback=false 与请求音色；本轮采集证明为持久化请求/候选绑定和 WAV 完整性，没有把未持久化的逐段 NPU metadata 当成额外采集证据。CPU 文本前端及混合计算仍存在。切换音色未证明机械感已解决，完整稿听感仍待用户评价。开发分支已独立提交推送，不合主线、不apply/archive OpenSpec。
