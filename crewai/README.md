# CrewAI 研究/分析助手

基于 CrewAI 的多Agent协作研究助手。三个 Agent 分工协作：

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

支持接入任何兼容 OpenAI API 格式的端点，例如：

- **本地模型**：ollama、vLLM、llama.cpp 等
- **国产大模型**：通义千问、文心一言、智谱等（兼容 OpenAI 格式的版本）
- **企业私有端点**：内部部署的模型服务

对应的 `.env` 配置：

```ini
PROVIDER=custom
CUSTOM_API_BASE=http://localhost:11434/v1
CUSTOM_API_KEY=your-api-key-here
CUSTOM_MODEL=qwen2.5:7b
```

## 项目结构

```
crewai/
├── main.py              # 入口文件（含引导配置）
├── crew.py              # Agent 和 Crew 定义（核心文件）
├── tools/
│   └── custom_tools.py  # 自定义工具（文件读写等）
├── .env.example         # 环境变量模板
├── requirements.txt     # Python 依赖
└── README.md
```

## Agent 说明

| Agent | 角色 | 职责 | 使用模型 |
|-------|------|------|----------|
| 研究员 | 高级研究分析师 | 搜集信息、整理数据 | PRIMARY_LLM |
| 分析师 | 数据与趋势分析师 | 深度分析、提炼洞察 | PRIMARY_LLM |
| 撰写者 | 技术报告撰写专家 | 输出结构化报告 | SECONDARY_LLM |

## 进阶玩法

### 启用网页搜索工具

```bash
pip install crewai-tools
```

在 `crew.py` 中取消注释：

```python
from crewai_tools import SerperDevTool, ScrapeWebsiteTool
# 并在 researcher 的 tools 参数中添加
```

### 切换执行模式

```python
# 顺序执行（默认）：研究 -> 分析 -> 撰写
process=Process.sequential

# 层级执行：由 manager 自动调度
process=Process.hierarchical
```

### 添加自定义工具

参考 `tools/custom_tools.py`，继承 `BaseTool` 即可。
