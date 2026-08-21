# Literature Downloader 实施计划

> 当前实现将检索与 PDF 下载分离：检索轮数用于扩展 API 分页结果，下载通过任务 ID 独立触发。

## 1. 目标

在 `panghu_agent` 下实现一个独立的文献下载工具，完成以下流程：

1. 文献检索专家 Agent：理解研究主题，生成可追溯的检索计划、术语扩展和筛选标准。
2. 文献检索员：依据检索计划查询本地文献库和外部学术 API，生成经过相关性筛选的待下载清单。
3. 文献收集：按来源优先级逐篇下载 PDF，支持多轮重试，并为每轮生成收集报告。
4. 文献检验：逐篇检查 PDF 文件大小和文本可读性，生成校验报告。
5. 最终提供 PDF 下载按钮和 Markdown 报告下载入口。

本模块第一版不改变现有 `scientific_agent` 的综述流程，而是作为独立的下载服务实现。

## 2. 参考实现

### `scientific_agent`

- 使用 CrewAI 组织 Agent 和任务。
- 使用 FastAPI 提供异步任务接口。
- 使用 Gradio 提供前端页面。
- 通过后台线程执行长时间任务，前端轮询任务状态。

参考目录：`panghu_agent/scientific_agent`、`panghu_agent/app/api/scientific_agent.py`、`panghu_agent/app/ui/scientific_agent.py`。

### `EvidenceGate-new`

- `workflow/pipeline_runner.py`：阶段编排和多轮收集循环。
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
├── k8s/configmap.yaml         # PROVIDER、DeepSeek 非敏感配置和 LLM 开关
├── models.py                  # 文献、任务、下载尝试、校验结果模型
├── db.py                      # SQLite 初始化、查询和状态更新
├── search_planner.py          # LLM 文献检索专家：查询计划和术语扩展
├── relevance_ranker.py        # LLM 可选的批量相关性重排
├── searcher.py                # 本地库 + 外部 API 检索
├── collector.py               # PDF 下载和多轮重试
├── verifier.py                # PDF 文件和文本校验
├── reports.py                 # EvidenceGate-new 风格报告生成
├── pipeline.py                # 检索专家、检索员、收集、检验流水线和状态机
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
- PDF 状态：`none`、`pending_download`、`downloading`、`scihub_fallback`、`downloaded`、`verified`、`failed`。`scihub_fallback` 表示直链失败后正在等待或执行 SciHub 备用下载，不代表最终失败。
- 最近一次错误、更新时间和校验时间

下载尝试记录至少包含：

- 任务 ID、文献 ID、轮次、下载来源
- 请求地址、耗时、文件大小
- 成功标志和错误原因

去重优先级为 DOI、arXiv ID、标准化标题加作者。数据库操作需要幂等，重复提交同一任务不能产生重复文献记录。

## 5. 阶段一：文献检索专家 Agent

文献检索专家是检索阶段的轻量 Agent，负责把自然语言研究主题转换为可执行、可审计的检索计划。它不直接访问出版社页面，不直接下载 PDF，也不允许凭空生成 DOI、文献记录或下载地址。

### 输入

- 用户研究主题
- 可选文献数量、年份范围和语言偏好
- 可选领域或排除条件

### LLM 输出

LLM 必须输出结构化 JSON，至少包含：

- `core_concepts`：核心概念及其重要性
- `synonyms`：中英文同义词、缩写、材料和方法术语
- `query_variants`：面向 OpenAlex、Crossref、arXiv 等来源的查询式
- `inclusion_criteria`：纳入标准
- `exclusion_criteria`：排除标准
- `target_count`：目标候选数量

例如 InP 干法刻蚀主题应能扩展出 `InP plasma etching`、`indium phosphide ICP-RIE`、`Cl2 InP etching`、`CH4/H2 InP etching`、`InP waveguide etching` 等术语，而不是只重复用户原句。

### 约束和降级

1. 使用统一 LLM 配置（`PROVIDER`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 及对应 API Key），不新增独立的 OAuth 或 Semantic Scholar 密钥要求。
2. LLM 返回必须经过 JSON Schema 校验；格式错误、超时、限流或模型不可用时，自动回退到通用查询规范化和词法相关性，不启用任何内置领域规则。
3. 同一主题的检索计划可缓存，缓存键至少包含规范化主题、年份范围、目标数量和模型版本。
4. 检索计划必须写入检索报告，记录模型是否启用、模型标识、生成时间、原始主题和最终采用的查询式。
5. LLM 生成的术语只能用于检索和筛选，不能覆盖 API 返回的标题、作者、DOI、arXiv ID、元数据 URL 或 PDF URL。

### 确定性范围规则

- 检索专家 LLM 通过 `scope_requirements` 在每个任务中生成“候选文献必须命中的术语组”。
- 代码不包含 InP、GaN 或其他单一材料的特殊判断；任务范围由当前 LLM 计划决定。
- LLM 不可用时严格范围过滤关闭，报告必须明确记录降级状态。

## 6. 阶段二：文献检索员

### 输入

- 研究主题
- 可选检索数量上限
- 可选年份范围和 API 配置

### 流程

1. 调用文献检索专家 Agent 获取检索计划；不可用时使用内置规则计划。
2. 先查本地 SQLite 文献库。
3. 使用计划中的查询式查询 OpenAlex、Crossref、arXiv。
4. 使用 Semantic Scholar 作为补充来源（无需 API Key 时仅调用公开可用能力）。
5. 统一外部 API 返回的字段格式，并保留原始来源数据。
6. 合并结果并按 DOI、arXiv ID 或规范化标题去重。
7. 对 API 返回的候选文献执行相关性重排；LLM 可用时批量输出 `relevance_score`、纳入/排除结果和理由，LLM 不可用时使用规则评分。
8. 标记本地已验证文献和需要下载 PDF 的文献。

### 输出

- 检索统计：总数、本地命中数、各 API 命中数、待下载数。
- 检索报告：记录主题、检索专家计划、LLM 配置和降级状态、查询变体、纳入/排除标准、各来源结果和错误；每篇文献必须保留来源数据库、DOI/arXiv ID、元数据 URL、公开 PDF URL、本地 PDF 状态、相关性评分和筛选理由。
- `need_to_download` 清单：标题、作者、DOI、arXiv ID、来源和 URL。

检索结果必须通过查询词相关性门槛；仅命中“研究进展”等泛化词而未命中主题术语的记录不得进入待下载清单，防止不同主题任务之间串入文献。

检索完成后状态为 `ready:download`，由用户在下载标签通过任务 ID 触发文献收集。

### 相关性重排规则

- LLM 只能基于 API 返回的标题、摘要、作者、年份、DOI、来源和查询命中词进行判断。
- 缺少摘要或元数据不完整时降低置信度，不允许模型自行补写缺失字段。
- 原始 API 记录和 LLM 判断同时保存，确保可以复核模型是否误筛。
- 仅命中“研究进展”等泛化词、未命中主题核心术语或纳入标准的记录不得进入待下载清单。

## 7. 阶段三：文献收集专家

每篇文献按照以下顺序尝试获取 PDF：

1. arXiv 直接 PDF。
2. 检索结果中已有的公开 PDF URL。
3. OpenAlex 返回的全部机构仓储落地页，并从标准学术 HTML 元数据中发现 PDF。
4. Unpaywall 全部开放获取位置，以及 Crossref/OpenAlex 补充的 OA 地址。
5. DOI 跳转页；解析 `citation_pdf_url`、`application/pdf` link 和明确的 PDF 链接。
6. Semantic Scholar Open Access 地址（未配置 API Key 时仅查询已有 Semantic Scholar ID 的记录）。
7. PubMed Central 和文献元数据详情 URL（作为最后回退，并校验是否确实返回 PDF）。

每种策略失败后记录原因并继续尝试下一种策略。同一篇文献的落地页和 PDF 请求复用临时 Cookie 会话及 Referer。对超时、HTTP 429 和 5xx 使用有限指数退避，对 403/405 不盲目重试；按域名限速，避免并发触发来源限制。下载时先写入临时文件，确认响应包含 PDF 文件签名且文件完整后再移动到正式目录。

### 多轮重试

- 默认最多 3 轮，可通过配置调整。
- 下一轮只处理上一轮下载失败或校验未通过的文献。
- 每轮结束后生成独立目录，例如：

```text
data/reports/<task_id>/collection_round_1/
├── collection_report.md
└── verification_report.md
```

- 每轮完成后由后台流水线自动判断是否进入下一轮。
- 下一轮只处理失败或校验未通过的文献，达到用户设置的最大轮数后自动结束收集。
- 没有待重试文献时立即生成最终报告。

收集报告沿用 EvidenceGate-new 标准，包含尝试总数、成功数量、失败数量、文献元数据、实际下载来源、每次尝试的 URL、路径、文件大小、失败原因和总下载量。服务器本地路径只作为产物位置，不能替代来源 URL。

## 8. 阶段四：文献检察人员

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

校验报告沿用 EvidenceGate-new 标准，列出检查总数、通过、失败、存疑文献及对应备注，并关联原始来源 URL、实际下载 URL 和本地文件路径。只有 `pass` 的文献才能写入 `verified` 状态。

## 9. 报告设计

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

检索报告需单独呈现“文献检索专家 Agent”部分和“文献检索员”部分：前者说明主题拆解、查询计划、纳入/排除标准及 LLM 状态，后者说明实际调用的数据库、返回数量、去重结果和最终清单。每篇候选文献同时保留原始 API 字段和相关性重排结果，不能只保留模型的摘要性结论。

最终汇总报告必须按“文献检索专家 Agent -> 文献检索员 -> 文献收集专家 -> 文献检察人员 -> 最终通过校验文献”的顺序呈现来源链和处理结果，不能只列服务器本地 PDF 路径。

最终汇总报告需要包含：

- 所有轮次的统计
- 通过校验的文献列表
- 未下载成功和校验失败的文献列表
- 每篇文献的 PDF 路径、来源和大小
- 报告文件和 PDF 压缩包路径

## 10. API 规划

- `POST /literature-download`：创建下载任务。
- `GET /literature-download/{task_id}`：查询任务状态和实时进度。
- `GET /literature-reports`：按主题、任务 ID 或状态查询历史任务。
- `GET /literature-download/{task_id}/report`：读取最终报告。
- `GET /literature-download/{task_id}/report/download`：下载 Markdown 报告。
- `GET /literature-download/{task_id}/files/download`：下载已通过校验的 PDF ZIP。

后台任务需要支持服务重启后的恢复；不同任务始终按 `task_id` 隔离，允许同一用户并行提交多个任务。

默认不对去重后的相关检索结果设置服务层数量上限（`LITERATURE_SEARCH_LIMIT=0`；正数可作为运维资源保护值），每个查询变体和外部来源每页返回 20 篇，主题最多使用 6 个查询变体；收集阶段默认并发下载和校验 6 篇，可通过 `LITERATURE_SEARCH_LIMIT`、`LITERATURE_PER_PROVIDER`、`LITERATURE_MAX_SEARCH_VARIANTS` 和 `LITERATURE_DOWNLOAD_CONCURRENCY` 调整。学术 API 按域名节流，并对 429/5xx 使用 `Retry-After` 和指数退避；可通过 `ACADEMIC_API_REQUEST_INTERVAL_MS`、`ACADEMIC_API_RETRY_BACKOFF_SECONDS` 和 `ACADEMIC_API_RATE_LIMIT_MAX_WAIT_SECONDS` 调整。

## 11. UI 规划

使用 Gradio 实现以下界面流程：

1. 输入研究主题、检索轮数和通知邮箱，点击“开始检索”。
2. 后台完成多轮检索并生成检索报告；用户复制任务 ID 到下载标签后，再启动下载和校验。
3. 保留“刷新状态”供用户主动查询长任务进度。
4. 最终提供：
   - PDF 下载按钮，下载已通过校验的 PDF 压缩包。
   - Markdown 报告下载按钮。
5. 历史报告页仅查询任务状态和任务 ID；复制任务 ID 到新任务页并刷新即可恢复当前任务下载入口。

## 12. 测试计划

1. 文献字段标准化和去重测试。
2. 文献检索专家 Agent 的 JSON Schema、查询计划缓存和主题术语扩展测试。
3. LLM 超时、格式错误、限流和未配置时的规则回退测试。
4. 本地库查询和状态更新测试。
5. OpenAlex、Crossref、arXiv、Semantic Scholar 响应解析测试。
6. LLM 相关性重排、缺少摘要降置信度和原始字段不可覆盖测试。
7. arXiv、DOI、Semantic Scholar fallback 下载测试，使用 mock HTTP。
8. 有效 PDF、损坏 PDF、过小文件、HTML 错误页和扫描版 PDF 校验测试。
9. 多轮重试状态机和中止流程测试。
10. 检索、收集、校验报告 Markdown 格式和 LLM 审计字段测试。
11. API 创建、轮询、自动流水线和下载测试。
12. 临时 SQLite 和临时 PDF 目录下的端到端测试。

测试不依赖真实学术 API；网络集成测试单独标记并支持手动运行。

## 13. 实施顺序

1. 创建模块目录、配置和 SQLite 数据层。
2. 接入统一 LLM 配置，实现检索专家 Agent 的结构化查询计划、缓存和规则回退。
3. 适配本地检索及 OpenAlex、Crossref、arXiv、Semantic Scholar 查询。
4. 实现候选文献批量相关性重排，并保存原始 API 记录和模型判断。
5. 实现 PDF 下载策略和单轮收集。
6. 实现 PDF 校验和 EvidenceGate-new 风格报告。
7. 串联检索专家、检索员、收集专家和检察人员，加入自动多轮重试状态机。
8. 接入 FastAPI 后台任务和状态查询。
9. 接入 Gradio UI、PDF 下载按钮和报告下载按钮。
10. 完成单元测试、API 测试和本地端到端验证。
11. 按现有 Agent 约定接入 Kubernetes：专属 API 镜像基于 `agent-api-base`，UI 复用通用 `agent-ui` 镜像和 UI Deployment。

## 14. 完成标准

- 能从主题生成去重后的待下载清单。
- 配置 LLM 时，能生成结构化检索计划、术语扩展、纳入/排除标准，并对候选文献进行批量相关性重排。
- 未配置或无法调用 LLM 时，能自动回退到规则查询和规则相关性筛选，主流程仍可完成。
- 检索报告能区分检索专家的计划与检索员的实际 API 结果，并记录模型、输入、输出和降级状态。
- 至少支持 arXiv、DOI、Semantic Scholar 三种下载来源。
- 下载失败可以进入下一轮，并保留每轮报告。
- PDF 校验结果能够区分通过、失败和存疑。
- 只有通过校验的 PDF 被标记为正式文献。
- 用户可以在页面查看并下载最终 Markdown 报告。
- 用户可以下载所有通过校验的 PDF 压缩包。
- 任务、文献状态、下载尝试和报告在服务重启后仍然保留。
## Task-level retrieval scope (current design)

The retrieval expert is responsible for producing topic-specific scope rules
at runtime. Its JSON plan must include `scope_requirements`, where each group
contains a name, literal terms or synonyms, and `required`. The searcher and
the batch relevance ranker consume the same plan for the current task.

There is no built-in subject rule in the service. The deterministic fallback
only performs domain-neutral query normalization and lexical scoring. If the
LLM is unavailable or its plan is malformed, strict scope filtering is
disabled and the reports explicitly record that reduced-precision fallback.

## Current workflow revision

This section supersedes earlier draft text that described automatic collection
retries. Search rounds are the only user-facing rounds setting. PDF collection
is a separate, single user-triggered pass; transient retries inside one HTTP
request remain collector implementation details and are not task rounds.

The task setting is `search_rounds`. A search task performs
that many paginated calls to each selected provider, merges and deduplicates the
pages, and sends a search-completion email. Search never starts PDF collection.

The search stage writes `search_report.md`, `doi_list.md`, and
`need_to_download.md`, and ends in `ready:download`. A separate
`POST /literature-download/{task_id}/download` starts one download/verification
pass. The Gradio UI therefore has independent “文献检索” and “下载文献” tabs;
the download tab requires the task ID produced by the search tab. The old
approval/retry workflow is removed. The public task actions are search,
status/history lookup, download-by-task-ID, and report/file downloads.
