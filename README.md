# AI Infra Research Platform（论文研读与专利转化平台）

一个统一的论文研读与专利转化工作台：自动发现论文 → 服务器保存 PDF → 结构化解析 →
中文研读总结 → 组合专利候选 → 生成交底书草稿。前端为同源单页应用，后端为 FastAPI
控制面，默认使用 SQLite 持久化，可选 PostgreSQL。

## 快速开始

```bash
cd research-platform
python run.py
# 需要执行排队任务与每日发现→下载→解析→研读时，另开一个终端：
python scripts/scheduler.py worker --interval 30
```

浏览器打开 <http://127.0.0.1:8080>（默认端口，可用 `RESEARCH_HUB_PORT` 修改）。

如需局域网访问，用 `--host 0.0.0.0` 启动，然后同一局域网设备访问
`http://<本机IP>:<端口>`。

## 使用教程

### 1. 如何获取今日论文

1. 打开「仪表盘」，右上角点击「触发发现」。
2. 系统会从 arXiv、Hugging Face、OpenReview、OpenAlex 多来源抓取论文，并写入
   真实数据库（默认回看 7 天，避免周末空日报）。
3. 顶部「今日论文」「去重命中」「已解析」「已有研读」「失败任务」卡片均可点击，
  展开后显示对应论文、合并来源或失败任务明细。
4. 也可在「论文库」页按主题、状态、日期筛选，或使用顶部搜索框按标题/作者/主题/
   arXiv ID 检索。

发现任务启用自动处理且研读模型已配置时，每篇英文论文会立即创建独立的摘要翻译
任务。翻译成功后，仪表盘、论文库、阅读台和笔记本都优先显示中文摘要，并保留
「查看英文原摘要」入口。摘要翻译失败不会阻断论文发现、PDF 下载或后续处理。

### 2. 如何查看今日论文解读

1. 在「仪表盘」或「论文库」点击某篇论文卡片标题，展开详情。
2. 点击「打开阅读台」进入阅读台。
3. 阅读台提供四个标签页：PDF、Markdown、研读报告、证据。
   - 若论文尚未解析，先在阅读台点击「保存 PDF 到服务器」触发下载与解析。
   - 解析完成后，后端会调用研读模型生成中文研读报告（摘要、动机、方法、实验、
     结果、创新点、局限、工程价值、复现计划）。
4. 右侧「技术卡片」展示结构化技术点，可一键「加入候选」。

### 3. 如何选择某篇论文，生成专利

1. 在「专利候选」页左侧勾选 2–5 篇论文（或从技术卡片加入）。
2. 填写组合耦合、接口边界、数据/控制流、非并列说明、联合效果，以及审批人。
3. 勾选四项人工确认，点击「创建专利候选」。
4. 在候选卡片上「运行查新」，查新成功后「普通审批通过」。
5. 审批通过后点击「生成草稿」，即可下载 Markdown / DOCX 交底书。

> 交底书仅为技术草稿，不构成授权、新颖性或创造性的法律结论；提交前必须人工确认。

### 4. 如何使用全部系统功能

- **仪表盘**：今日论文、去重命中、已解析、已有研读、失败任务等指标；来源与主题
  分布；阅读路线；系统能力健康。
- **论文库**：按主题/状态/日期筛选与搜索全部论文。
- **阅读台**：PDF / Markdown / 研读报告 / 证据四视图，技术卡片。
- **主题中心**：查看主题树与当日摘要，可新增自定义主题。
- **工作流**：查看每日论文研读与专利交底书两条内置流水线及运行历史。
- **任务中心**：查看异步任务状态、错误原因，支持重试/取消。
- **关系视图**：论文间共同问题、互补方法、冲突约束与证据链。
- **专利候选**：组合候选、查新、审批、草稿生成与下载。
- **笔记本**：服务器持久化收藏论文，跨浏览器刷新和日期筛选保留，集中查看中文摘要、
  研读报告与技术解读。
- **设置**：配置研读模型（OpenAI 兼容或 Dify）、每日自动研读计划、管理凭证。

### 5. 如何配置研读模型（解决“Invalid or missing API key”）

研读/翻译需要配置大模型。进入「设置」→「论文研读模型」：

- **提供方**：选择「OpenAI 兼容 API」或「Dify 工作流 API」。
- **OpenAI 兼容 API**：填写 Base URL（如 `http://model-server:8000/v1`）、模型名
  （如 `Qwen3-32B`）和 API key。无需鉴权的局域网模型服务可不填 key。
- **Dify 工作流 API**：填写 Dify 服务 Base URL（官方 SaaS 通常为
  `https://api.dify.ai`）和已发布工作流的 API key。标准 Dify 接口是
  `POST /v1/workflows/run`，API key 已绑定具体应用，因此「工作流 ID」留空即可；
  只有自建网关明确要求 `/v1/workflows/{id}/run` 时才填写该可选 ID。
- 点击「保存服务器配置」。API key 只写入服务器端权限受控配置（`0600`），不会保存
  在浏览器；新提交的任务立即读取新配置，无需重启 API。

这里存在两种完全不同的 key：

- **模型 / Dify API key**：位于「论文研读模型」，用于中文摘要、全文翻译与研读报告。
- **平台管理 API key**：位于「平台 API」的高级设置，只在平台启用了 RBAC 写鉴权时
  用于调用保存设置、触发发现、收藏论文、重试任务等写接口；本地无鉴权模式无需填写。

Dify 工作流至少需要接收 `title`、`abstract` 和 `metadata` 输入。平台通过
`metadata.task` 区分任务：

- `translate_abstract`：输出 `abstract_zh`（也兼容 `translated_abstract`、`text`、
  `answer`）。
- `analyze`：输出结构化 `report`，包含总结、方法、实验、结果、创新点、局限等字段。
- `translate`：输出 `markdown_zh` 或可识别的 Markdown 文本。

若仍提示 `Invalid or missing API key`，说明研读模型尚未配置或 key 无效；请确认
错误发生的位置：

1. 保存设置或点击「触发发现」返回 `401/403`：缺少的是「平台管理 API key」。
2. 任务中心的摘要/研读任务报错：缺少的是「模型 / Dify API key」，或 Base URL、
   模型名不正确。
3. Dify 返回成功但没有中文摘要：检查工作流输出变量是否为 `abstract_zh`，并在任务
   中心查看该任务的明确错误；平台会将空译文标为可重试失败，不会伪装成功。

### 6. 如何使用论文笔记本

1. 在仪表盘或论文库展开论文卡片，点击「加入笔记本」。
2. 平台通过论文的 `selected` 状态写入数据库，不依赖浏览器 `sessionStorage`；刷新、
   切换日期或换浏览器后仍可从服务器读取。
3. 打开左侧「笔记本」，展开论文即可查看中文摘要、英文原摘要、研读报告和技术解读。
4. 点击「移出笔记本」会立即更新服务器状态；若平台启用了写鉴权，需要先在设置页
   填写具有 researcher 或 admin 权限的平台 key。

### 7. 服务器文件保存在哪里

- SQLite 数据库：默认 `config/research_hub.sqlite3`。
- 模型与计划配置：默认 `config/runtime_config.json`，权限为 `0600`。
- PDF、解析中间产物：由 `RESEARCH_HUB_ARTIFACT_ROOT` 控制，默认在项目 `artifacts/`。
- 专利 Markdown / DOCX 导出：由 `RESEARCH_HUB_EXPORT_DIR` 控制，默认在 `exports/`。

前端的 PDF 下载按钮读取服务器已有 artifact；平台不会把定时任务下载的原始 PDF
默默保存到访问者电脑。

## 运行说明

```bash
cd research-platform
python run.py
# Required for queued jobs and daily discovery → download → parse → analyze:
python scripts/scheduler.py worker --interval 30
```

For a host deployment that includes the bundled MinerU checkout, start all
three processes under one lifecycle (set `MINERU_LOCAL_GPUS` explicitly on a
shared GPU host):

```bash
MINERU_LOCAL_GPUS=0 scripts/run_local_stack.sh
```

The host stack optionally loads `config/service.env`; keep that file at mode
`0600` and store deployment-only RBAC keys there. It is separate from the
browser-editable model/schedule runtime configuration.

### 配置研读模型：写入 `.env`，刷新自动回填

论文研读模型（自动中文摘要 + 论文研读）的配置可以放进仓库根 `.env`，这样
设置页的「论文研读模型」卡片会以这些值为默认值自动回填，刷新也不会丢失，
无需每次重新填写。

OpenAI 兼容服务（默认）：

```bash
# research-platform/.env
RESEARCH_HUB_ANALYSIS_PROVIDER=openai
LLM_BASE_URL=http://model-server:8000/v1
LLM_MODEL=Qwen3-32B        # 本地/内部服务可留空
LLM_API_KEY=
```

Dify 工作流：

```bash
# research-platform/.env
RESEARCH_HUB_ANALYSIS_PROVIDER=dify
DIFY_BASE_URL=https://your-dify.example.com
DIFY_API_KEY=app-xxxx
DIFY_WORKFLOW_ID=           # 可选；标准 /v1/workflows/run 无需填写
```

保存与优先级：

- 本地启动 `scripts/run_local_stack.sh` 会按顺序加载项目根 `.env` 与
  `config/service.env`（可用 `RESEARCH_HUB_ENV_FILE` 覆盖）。
- 后端默认配置以「进程环境 → 根 `.env` → 内建默认值」的顺序读取；设置页在
  `/api/v1/runtime-config` 读到非空值后会把 Base URL、模型名、Dify workflow
  回填到对应输入框（`env_backfilled` 标记来自 `.env` 的值）。
- 填入 `.env` 后需重启一次栈使其生效。之后在设置页点「保存 LLM 配置」可把
  新值持久化到 `config/runtime_config.json`（0600）并立即覆盖 `.env` 默认值。
- 部署启用了「公网只读 + 写操作鉴权」时，前端保存仍需先在设置页填写平台管理
  API key（模型/Dify key 与它是两种不同凭证）。

Useful environment variables:

- `RESEARCH_HUB_DB`: sqlite database path. Default:
  `research-platform/config/research_hub.sqlite3`
- `RESEARCH_HUB_RUNTIME_CONFIG`: server-side model and schedule configuration.
  The Settings page writes this file with mode `0600`; in Compose it is stored
  in the shared `/data` volume so the API and worker use the same values.
- `RESEARCH_HUB_POSTGRES_DSN`: optional PostgreSQL runtime DSN for deployments
  that use PostgreSQL. App startup automatically selects the PostgreSQL runtime
  when this value is non-empty; SQLite remains the default local/test fallback.
- `RESEARCH_HUB_API_KEY`: legacy admin API key. When set, the key retains full
  write access for backward compatibility and `/api/v1/*` accepts either
  `X-API-Key: <key>` or `Authorization: Bearer <key>`.
- `RESEARCH_HUB_ADMIN_API_KEY`: optional explicit admin key.
- `RESEARCH_HUB_RESEARCHER_API_KEY`: optional key for research writes
  (`discovery-runs`, paper selection, parsing, translation, analysis, relation rebuild).
- `RESEARCH_HUB_PATENT_EDITOR_API_KEY`: optional key for patent workflow writes
  (`invention-candidates`, prior-art checks, approvals, draft generation, draft revision).
- `RESEARCH_HUB_READ_ONLY_API_KEY`: optional key for an authenticated read-only identity.
- `RESEARCH_HUB_STATIC_DIR`: same-origin static directory mounted at `/static`.
- `RESEARCH_HUB_HOST`: default `127.0.0.1`
- `RESEARCH_HUB_PORT`: default `8080`

If no RBAC keys are configured, the API keeps its prior local behavior and
allows writes without enforcing API-key checks. When any RBAC key is configured,
anonymous requests remain able to read public endpoints, but write routes require
the appropriate role.

RBAC matrix:

- `admin`: all writes, job replay, and operational controls.
- `researcher`: discovery, paper ingestion/selection, parse/translate/analyze,
  paper-version artifact creation, relation rebuild, and job retry/cancel.
- `patent-editor`: invention candidate creation, prior-art checks, approvals,
  patent draft generation/revision, and job retry/cancel.
- `read-only`: authenticated read access only.
- anonymous: public read access only.

## Core Endpoints

- `GET /health`
- `GET /api/v1/adapter-health`
- `GET/PUT /api/v1/runtime-config` (secrets are masked on reads)
- `GET /api/v1/workflows`
- `GET /api/v1/stats`
- `GET /api/v1/metrics`
- `GET /api/v1/topics`
- `GET /api/v1/topics/{id}/digest?date=YYYY-MM-DD`
- `PATCH /api/v1/topics/{topic_id}`
- `POST /api/v1/discovery-runs`
- `GET /api/v1/discovery-runs/{id}`
- `GET /api/v1/papers?topic=&date=&publication_date=&status=&source=&selected=` (`date`
  is the discovery/source-hit date; `publication_date` filters first publication)
- `POST /api/v1/papers`
- `GET /api/v1/papers/{paper_id}`
- `GET /api/v1/papers/{paper_id}/versions`
- `POST /api/v1/papers/{paper_id}/select`
- `POST /api/v1/paper-versions/{id}/parse`
- `POST /api/v1/paper-versions/{id}/download`
- `GET /api/v1/paper-versions/{id}/document`
- `POST /api/v1/paper-versions/{id}/translate`
- `POST /api/v1/paper-versions/{id}/analyze`
- `GET /api/v1/paper-versions/{id}/artifacts`
- `POST /api/v1/paper-versions/{id}/artifacts`
- `GET /api/v1/paper-versions/{id}/report`
- `GET /api/v1/daily-digests/{date}`
- `GET /api/v1/papers/{id}/relations`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/dead-letter`
- `POST /api/v1/jobs/dead-letter/{id}/replay`
- `GET /api/v1/jobs/{id}`
- `POST /api/v1/jobs/{id}` with `{"action":"retry"}` or `{"action":"cancel"}`
- `POST /api/v1/jobs/{id}/retry`
- `POST /api/v1/jobs/{id}/cancel`
- `GET /api/v1/artifacts`
- `GET /api/v1/artifacts/{id}/download?download=false` (`inline` by default for PDF/text/image)
- `GET /api/v1/invention-candidates`
- `POST /api/v1/invention-candidates`
- `GET /api/v1/invention-candidates/{id}`
- `GET /api/v1/invention-candidates/{id}/stages`
- `POST /api/v1/invention-candidates/{id}/prior-art-check`
- `POST /api/v1/invention-candidates/{id}/approve`
- `POST /api/v1/invention-candidates/{id}/draft`
- `GET /api/v1/patent-drafts/{id}`
- `GET /api/v1/patent-drafts/{id}/versions`
- `GET /api/v1/patent-drafts/{id}/export?format=markdown`
- `GET /api/v1/patent-drafts/{id}/artifacts`
- `POST /api/v1/patent-drafts/{id}/revise`

All write endpoints accept `Idempotency-Key`. Reusing the same key with the
same body returns the existing response; reusing it with a different body returns
`409 conflict`.

Patent draft output is a technical disclosure draft only. It does not represent
legal novelty, inventiveness, freedom-to-operate, or grantability conclusions.

## Deployment

Container and public URL deployment files are included:

- `Dockerfile`
- `requirements.txt`
- `docker-compose.yml`
- `.env.example`
- `docs/DEPLOYMENT.md`
- `docs/PLAN_ACCEPTANCE.md`
- `scripts/public_verify.sh`

Quick local container run:

```bash
cp .env.example .env
# Set RESEARCH_HUB_API_KEY in .env before any public tunnel or shared deployment.
docker compose up --build -d
scripts/public_verify.sh http://127.0.0.1:8310 2026-07-30
```

See `docs/DEPLOYMENT.md` for SQLite persistence, public tunnel, reverse proxy,
API key, required worker, and scheduler notes.
