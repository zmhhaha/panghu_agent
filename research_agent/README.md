# CrewAI 研究/分析助手 — Agent 引擎

基于 CrewAI 的多Agent协作研究助手。

```
研究员 -> 分析师 -> 撰写者
搜集信息   深度分析   输出报告
```

## 快速开始

### 1. 安装依赖

```bash
cd crewai
pip install -r requirements.txt
```

### 2. 配置 API

**方式一：直接运行，首次会自动引导配置**

```bash
python main.py
```

程序检测到没有 `.env` 文件时会提示你选择提供商并填写 API Key。

**方式二：手动创建 .env 文件**

```bash
cp .env.example .env
# 编辑 .env，填写你的 API Key
```

### 3. 运行

```bash
# 默认主题
python main.py

# 自定义主题
python main.py "大语言模型在医疗领域的应用"
```

## 支持的模型提供商

| 提供商 | PROVIDER 值 | 说明 |
|-------|------------|------|
| OpenAI | `openai` | GPT-4o / GPT-4o-mini |
| Anthropic | `anthropic` | Claude Sonnet / Haiku |
| DeepSeek | `deepseek` | DeepSeek Chat |
| 自定义 | `custom` | 任意兼容 OpenAI API 的端点 |

### 自定义 API（PROVIDER=custom）

支持接入任何兼容 OpenAI API 格式的端点：

```ini
PROVIDER=custom
CUSTOM_API_BASE=http://localhost:11434/v1
CUSTOM_API_KEY=your-api-key-here
CUSTOM_MODEL=qwen2.5:7b
```

## Agent 说明

| Agent | 角色 | 职责 | 使用模型 | 工具 |
|-------|------|------|----------|------|
| 研究员 | 高级研究分析师 | 搜集信息、交叉验证、整理数据 | PRIMARY_LLM | AcademicSearch, WebSearch, WebFetch, MultiFetch |
| 分析师 | 数据与趋势分析师 | 深度分析、提炼洞察 | PRIMARY_LLM | - |
| 撰写者 | 技术报告撰写专家 | 输出结构化报告 | SECONDARY_LLM | - |

## 自定义工具

内置免费工具（无需第三方 API Key）：

| 工具 | 功能 | 依赖 |
|------|------|------|
| `AcademicSearchTool` | 跨 OpenAlex、Crossref、Semantic Scholar、arXiv、PubMed 检索并去重排序 | `requests` |
| `WebSearchTool` | DuckDuckGo 网页搜索 | `ddgs` |
| `WebFetchTool` | 抓取网页内容转文本 | `requests` + `beautifulsoup4` |
| `MultiFetchTool` | 批量抓取 + 交叉验证 | 复用 `WebFetchTool` |
| `FileReadTool` | 读取本地文件 | 内置 |
| `FileWriteTool` | 写入本地文件 | 内置 |

学术检索实现位于 `tools/academic/`，CrewAI 包装位于 `tools/academic_tools.py`；网页工具位于 `tools/custom_tools.py`。

## 项目结构

```
crewai/
├── main.py              # CLI 入口（含引导配置）
├── crew.py              # Agent/Task/Crew 定义（核心）
├── tools/
│   └── custom_tools.py  # 自定义工具
├── .env.example         # 环境变量模板
├── requirements.txt     # Python 依赖
└── README.md
```

---

> 如需 API 在线服务 + 付费计费，请查看上级目录的 `api/` 模块。
