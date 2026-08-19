# Literature Downloader 实施计划

## 1. 目标

在 `panghu_agent` 下实现一个独立的文献下载工具，完成以下流程：

1. 文献检索：查询本地文献库和外部学术 API，生成待下载清单。
2. 文献收集：按来源优先级逐篇下载 PDF，支持多轮重试，并为每轮生成收集报告。
3. 文献检验：逐篇检查 PDF 文件大小和文本可读性，生成校验报告。
4. 最终提供 PDF 下载按钮和 Markdown 报告下载入口。

本模块第一版不改变现有 `scientific_agent` 的综述流程，而是作为独立的下载服务实现。

## 2. 参考实现

### `scientific_agent`

- 使用 CrewAI 组织 Agent 和任务。
- 使用 FastAPI 提供异步任务接口。
- 使用 Gradio 提供前端页面。
- 通过后台线程执行长时间任务，前端轮询任务状态。

参考目录：`panghu_agent/scientific_agent`、`panghu_agent/app/api/scientific_agent.py`、`panghu_agent/app/ui/scientific_agent.py`。

### `EvidenceGate-new`

- `workflow/pipeline_runner.py`：阶段编排、用户确认和多轮收集循环。
- `workflow/pdf_downloader.py`：arXiv、DOI、Unpaywall、Semantic Scholar 下载策略。
- `workflow/litdb.py`：SQLite 文献库和 PDF 状态管理。
- `workflow/reports.py`：检索、收集和校验报告格式。
- `server/app.py`、`web/index.html`：状态 API 和报告查看界面。

生产实现不直接依赖上一级目录的 Python 包，而是将需要的能力适配到本模块，避免部署时依赖外部路径。

## 3. 目录和组件规划

```text
literature_downloader/
├── PLAN.md                    # 本计划
├── __init__.py
├── config.py                  # 路径、超时、重试次数、API 配置
├── models.py                  # 文献、任务、下载尝试、校验结果模型
├── db.py                      # SQLite 初始化、查询和状态更新
├── searcher.py                # 本地库 + 外部 API 检索
├── collector.py               # PDF 下载和多轮重试
├── verifier.py                # PDF 文件和文本校验
├── reports.py                 # EvidenceGate-new 风格报告生成
├── pipeline.py                # 三阶段流水线和状态机
├── api.py                     # FastAPI 接口
├── ../app/ui/literature_downloader.py  # 通用 UI 镜像注入的 Gradio 页面
└── tests/
    ├── test_searcher.py
    ├── test_collector.py
    ├── test_verifier.py
    ├── test_reports.py
    └── test_api.py
```

## 4. 数据层设计

使用 SQLite 保存任务和文献元数据，PDF 文件保存在独立目录。

文献记录至少包含：

- 标题、作者、年份、期刊或会议、摘要
- DOI、arXiv ID、PMID、原始 URL
- 检索来源和本地 PDF 路径
- PDF 状态：`none`、`pending_download`、`downloading`、`downloaded`、`verified`、`failed`
- 最近一次错误、更新时间和校验时间

下载尝试记录至少包含：

- 任务 ID、文献 ID、轮次、下载来源
- 请求地址、耗时、文件大小
- 成功标志和错误原因

去重优先级为 DOI、arXiv ID、标准化标题加作者。数据库操作需要幂等，重复提交同一任务不能产生重复文献记录。

## 5. 阶段一：文献检索员

### 输入

- 研究主题
- 可选检索数量上限
- 可选年份范围和 API 配置

### 流程

1. 对主题做关键词提取、同义词扩展和查询变体生成。
2. 先查本地 SQLite 文献库。
3. 查询 OpenAlex、Crossref、arXiv。
4. 使用 Semantic Scholar 作为补充来源。
5. 统一外部 API 返回的字段格式。
6. 合并结果并去重。
7. 标记本地已验证文献和需要下载 PDF 的文献。

### 输出

- 检索统计：总数、本地命中数、各 API 命中数、待下载数。
- 检索报告：记录主题、查询变体、各来源结果和错误。
- `need_to_download` 清单：标题、作者、DOI、arXiv ID、来源和 URL。

检索完成后进入 `waiting:search_approval` 状态，等待用户确认清单，确认后才开始下载。

## 6. 阶段二：文献收集专家

每篇文献按照以下顺序尝试获取 PDF：

1. arXiv 直接 PDF。
2. DOI 跳转或 Unpaywall 开放获取地址。
3. Semantic Scholar Open Access 地址。
4. 文献元数据中已有的 PDF URL。

每种策略失败后记录原因并继续尝试下一种策略。下载时先写入临时文件，确认响应为 PDF 且文件完整后再移动到正式目录。

### 多轮重试

- 默认最多 3 轮，可通过配置调整。
- 下一轮只处理上一轮下载失败或校验未通过的文献。
- 每轮结束后生成独立目录，例如：

```text
data/reports/<task_id>/collection_round_1/
├── collection_report.md
└── verification_report.md
```

- 每轮完成后进入 `waiting:collect_approval` 状态。
- 用户可以选择继续重试、结束收集或中止任务。
- 没有待重试文献时自动结束收集阶段。

收集报告沿用 EvidenceGate-new 标准，包含尝试总数、成功数量、失败数量、来源、路径、文件大小、失败原因和总下载量。

## 7. 阶段三：文献检察人员

对每个下载成功的 PDF 逐篇执行以下检查：

1. 文件是否存在。
2. 文件大小是否超过最低阈值，默认 10 KB。
3. 文件头是否为有效 PDF。
4. 是否能够使用 PDF 文本提取器读取内容。
5. 提取文本是否达到最低字符数，默认 200 字符。
6. 是否疑似下载到 HTML 错误页、空白 PDF 或扫描版 PDF。

校验结果分为：

- `pass`：文件存在、格式正常且文本可读。
- `fail`：文件不存在、损坏、过小或明显不是目标文献。
- `uncertain`：文件存在但文本过少、疑似扫描版或无法确认内容。

校验报告沿用 EvidenceGate-new 标准，列出检查总数、通过、失败、存疑文献及对应备注。只有 `pass` 的文献才能写入 `verified` 状态。

## 8. 报告设计

报告使用 Markdown，并保留 EvidenceGate-new 的文件头格式：

```yaml
---
title: <topic>
type: <report_type>
generated_at: <ISO timestamp>
---
```

报告类型包括：

- `search`：检索报告
- `need_to_download`：待下载清单
- `download_collection`：单轮收集报告
- `verification`：单轮校验报告
- `final_download`：最终汇总报告

最终汇总报告需要包含：

- 所有轮次的统计
- 通过校验的文献列表
- 未下载成功和校验失败的文献列表
- 每篇文献的 PDF 路径、来源和大小
- 报告文件和 PDF 压缩包路径

## 9. API 规划

- `POST /literature-download`：创建下载任务。
- `GET /literature-download/{task_id}`：查询任务状态和实时进度。
- `POST /literature-download/{task_id}/approve`：确认检索清单。
- `POST /literature-download/{task_id}/retry`：开始下一轮重试。
- `POST /literature-download/{task_id}/finish`：结束收集并确认结果。
- `GET /literature-download/{task_id}/report`：读取最终报告。
- `GET /literature-download/{task_id}/report/download`：下载 Markdown 报告。
- `GET /literature-download/{task_id}/files/download`：下载已通过校验的 PDF ZIP。

后台任务需要支持服务重启后的恢复，并避免同一用户重复启动冲突任务。

## 10. UI 规划

使用 Gradio 实现以下界面流程：

1. 输入研究主题并启动检索。
2. 展示检索统计和待下载清单。
3. 点击“确认并开始下载”。
4. 展示当前轮次、逐篇进度、成功和失败列表。
5. 展示收集报告和校验报告。
6. 对失败文献提供“重试”按钮。
7. 最终提供：
   - PDF 下载按钮，下载已通过校验的 PDF 压缩包。
   - Markdown 报告下载按钮。

## 11. 测试计划

1. 文献字段标准化和去重测试。
2. 本地库查询和状态更新测试。
3. OpenAlex、Crossref、arXiv、Semantic Scholar 响应解析测试。
4. arXiv、DOI、Semantic Scholar fallback 下载测试，使用 mock HTTP。
5. 有效 PDF、损坏 PDF、过小文件、HTML 错误页和扫描版 PDF 校验测试。
6. 多轮重试状态机和中止流程测试。
7. 报告 Markdown 格式固定样例测试。
8. API 创建、轮询、审批、重试和下载测试。
9. 临时 SQLite 和临时 PDF 目录下的端到端测试。

测试不依赖真实学术 API；网络集成测试单独标记并支持手动运行。

## 12. 实施顺序

1. 创建模块目录、配置和 SQLite 数据层。
2. 适配本地检索及 OpenAlex、Crossref、arXiv、Semantic Scholar 查询。
3. 实现 PDF 下载策略和单轮收集。
4. 实现 PDF 校验和 EvidenceGate-new 风格报告。
5. 串联三阶段流水线，加入审批和多轮重试状态机。
6. 接入 FastAPI 后台任务和状态查询。
7. 接入 Gradio UI、PDF 下载按钮和报告下载按钮。
8. 完成单元测试、API 测试和本地端到端验证。
9. 按现有 Agent 约定接入 Kubernetes：专属 API 镜像基于 `agent-api-base`，UI 复用通用 `agent-ui` 镜像和 UI Deployment。

## 13. 完成标准

- 能从主题生成去重后的待下载清单。
- 至少支持 arXiv、DOI、Semantic Scholar 三种下载来源。
- 下载失败可以进入下一轮，并保留每轮报告。
- PDF 校验结果能够区分通过、失败和存疑。
- 只有通过校验的 PDF 被标记为正式文献。
- 用户可以在页面查看并下载最终 Markdown 报告。
- 用户可以下载所有通过校验的 PDF 压缩包。
- 任务、文献状态、下载尝试和报告在服务重启后仍然保留。
