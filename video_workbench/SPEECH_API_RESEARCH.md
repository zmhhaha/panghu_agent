# 自接语音 API 与开源服务选型

调研日期：2026-10-30。Star 来自当日 GitHub REST API；能力来自项目官方 README/文档，不是本服务器的质量或性能实测。

## 适合当前工作台的接法

已有云端语音 API 时，只需在后端配置服务地址、凭据、模型和音色，不需要再部署语音模型。
自建时把推理服务单独部署，工作台通过一个 SpeechProvider 适配接口调用；通用 Agent 继续负责分镜及 SVG/HTML，媒体处理负责时长、字幕及编码。
语音服务不要求对公网开放；集群内通信使用专用 Service 和明确网络许可。DNS 和 Casdoor 的变更仍由用户手工管理。

首个接口采用 Kokoro-FastAPI 等项目提供的 `/v1/audio/speech` JSON 请求及 WAV 返回约定。
其他提供者若使用自己的鉴权、音色或任务接口，在 SpeechProvider 内转换，不要求所有供应商原生兼容。

```json
{
  "model": "由你部署或购买的模型标识",
  "input": "这一镜头要读出的中文旁白。",
  "voice": "该服务支持的音色标识",
  "speed": 1.0,
  "response_format": "wav"
}
```

接口返回实际音频字节；状态码与错误由适配器处理。工作台必须解码并探测音频，不能仅凭文件扩展名或 Content-Type 宣称成功。
时间戳通过提供者的额外接口或独立对齐步骤取得，不能假定标准合成响应自带逐词时间戳。

## 开源项目

| 项目 | Star | 侧重点 | 与当前成本要求的关系 |
| --- | ---: | --- | --- |
| [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) | 5,619 | 轻量语音 API，CPU/ARM64、普通话、兼容合成接口与额外字幕时间戳接口 | 优先作为 CPU 原型候选；中文技术文稿仍须试听，实际时长尚未在用户服务器测量 |
| [CosyVoice](https://github.com/QwenAudio/CosyVoice) | 24,193 | 中文/多语言、发音控制和音色克隆，提供 FastAPI 服务示例 | 官方容器示例使用 NVIDIA；作为有 GPU 或可租用推理服务时的中文候选 |
| [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) | 14,104 | 0.6B/1.7B、多语言、固定音色、音色设计和克隆 | 官方主要推理示例使用 CUDA；需要单独选服务封装及测试资源开销 |
| [Fish Speech](https://github.com/fishaudio/fish-speech) | 33,490 | 表达与音色控制，提供 API Server | 不按 ARM CPU 轻量方案估算；代码/模型使用项目专有许可证，需按所选版本判断适用性 |
| [IndexTTS](https://github.com/index-tts/index-tts) | 24,822 | 中文、拼音/发音控制、情绪与克隆，当前主分支为 2.5 | 作为质量/控制候选；不能把路线图的精确时长控制当成已发布能力 |
| [LocalAI](https://github.com/mudler/LocalAI) | 50,075 | 多种模型和语音后端的统一 API 平台 | 已有通用 Agent 服务时，仅为 TTS 再增加平台可能增加运维工作；并非必须安装全部后端 |
| [Piper](https://github.com/OHF-Voice/piper1-gpl) | 6,089 | 本地快速合成 | CPU 方向备选，中文音色目录与实际技术术语读音仍应按具体模型核对 |
| [edge-tts](https://github.com/rany2/edge-tts) | 12,421 | 微软在线语音的 Python 客户端，可生成字幕 | 适合试验；它不提供可自行部署的语音模型，不把其在线连通性视为工作台的稳定服务保证 |

Kokoro 原模型仓库 [hexgrad/kokoro](https://github.com/hexgrad/kokoro) 为 9,790 Star，模型为 82M。
Star 衡量关注度，不等同于中文质量、稳定性或本机成本；未采用 README 的特定 GPU 性能作为用户机器的指标。

## 接入需要确定的参数

1. API base URL（例如集群内部 `http://speech.<namespace>.svc.cluster.local:8880/v1`）与是否需要鉴权。
2. 服务接受的 model 和中文 voice，以及实际支持的语速、发音修正方式。
3. WAV 输出、单次字数上限、请求超时、并发限制和计费规则。
4. 是否提供可靠字幕时间戳；否则另做实际音频对齐和人工校正。

凭据只进入后端 Secret/Vault 挂载，不发送到浏览器或通用 Agent 的提示词；请求地址由管理员配置。
先以一镜短文验证中文和 DSH/Cordis/SDK/ACP/PTY/LSP 等术语，再处理整片。
生成候选、试听和明确采用分别进行，修改某镜头仅使该镜头对应音频及派生字幕失效。

## 本轮实施边界

已经从应用最新 main 创建 `codex/video-audio-production`，先开发独立的 HTTP/WAV 适配器和离线验证。
提供者尚未确定，没有安装或启动语音模型，没有创建付费合成请求，没有修改线上工作台、DNS 或 Casdoor。
音频任务持久化、试听/采用界面、字幕对齐和 MP4 仍按 OpenSpec 后续任务实现，不能把本接口切片当成全部音频能力已上线。
