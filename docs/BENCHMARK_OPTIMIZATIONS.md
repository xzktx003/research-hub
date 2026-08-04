# 对标开源论文工作流的优化清单

本文记录借鉴 arxiv-sanity-lite/preserver、ChatPaper、gpt_academic 等开源论文
研读/管理项目的优秀功能，对 research-platform 实施的优化点。每项都通过
单元/契约测试并在浏览器中实测验证。

## 后端优化

### 1. 外部 API 统一指数退避重试
- 文件：`research_hub/adapters/retry.py`
- 借鉴：ChatPaper 用 tenacity 指数退避重试下载/总结。
- 实现：`RetryConfig` / `run_with_retry` / `retry_backoff`（纯标准库），
  仅对网络错误、超时、HTTP 429/5xx 重试；确定性错误（校验失败、4xx）不重试。
- 应用：`openai_compatible._chat`、`arxiv`、`discovery._request_json`(HF/OpenReview/
  OpenAlex)、`downloader`。

### 2. 论文规范化去重键（跨源去重）
- 文件：`research_hub/repository.py` → `normalized_paper_dedup_key`
- 借鉴：arxiv-sanity 按 id 去重，本仓库增强为标题+首作者+年份的跨源指纹。
- `create_paper` 在 identifier 未命中时用该键二次去重；仅在作者或年份有值时才
  触发标题匹配，避免同标题不同论文被误并。

### 3. 解析章节结构化存储
- 文件：`research_hub/adapters/sections.py`（`split_markdown_sections` /
  `section_anchors`）
- 借鉴：ChatPaper 按 PDF 章节切分以支持定位/导航/分段总结。
- 解析完成时将 `sections[]`（heading/level/content）与 `toc` 存入 markdown
  artifact 的 metadata，供阅读器导航与分段摘要使用。

### 4. 参考文献/引用规范化解析
- 文件：`research_hub/adapters/references.py`（`parse_references` /
  `extract_reference_links`）
- 借鉴：arxiv-sanity、ChatPaper 解析参考文献并规范化为 arXiv/DOI 以支持引用跳转。
- 从 References 章节提取条目字段（作者/标题/期刊/年份/arXiv/DOI/URL），并接入
  `services._extract_markdown_sections` 的返回结构。

## 前端优化（严格 CSP，改后需 bump index.html 版本号）

### 5. 论文卡片元数据增强
- `paperCodeUrl` / `paperCitationCount` / `paperMetaTags` / `buildCardExternalLinks`：
  展示代码仓库链接、引用数徽标、arXiv 原文外链。

### 6. 相似论文入口
- `showSimilarPapers` / `similarPapers`：基于共同主题 + 标题/摘要关键词的本地
  TF-lite 相似度，无需额外后端调用，点击卡片"相似论文"即显示 top-N 相似论文。

### 7. 论文库多因子排序
- `paperHeatScore` + 排序下拉：热度优先（引用数 + 收藏 + 研读进度）、最新优先、
  最早优先。

### 8. 全文搜索高亮 + 批量多选
- `highlightQuery`：搜索命中在标题用 `<mark>` 高亮。
- 批量工具栏：全选当前列表、批量加入笔记本、清除选择（`data-batch-check`）。

### 9. 阅读器快捷键
- `handleReaderShortcuts`：`j`/`k` 上一篇/下一篇，`[`/`]` 切换 PDF/Markdown/
  报告/证据，`s` 收藏到笔记本。仅在阅读台视图生效，输入框聚焦时忽略。

### 10. 夜间模式
- 设置面板新增主题外观（浅色/夜间），`applyTheme` 给 `<html>` 切换
  `.theme-dark`，由 CSS 变量覆盖实现（严格 CSP 支持）。

### 11. 今日推荐阅读面板
- 仪表盘新增 `recommendedReading`：综合热度 + 主题命中 + 研读进度推荐论文，
  并展示可解释的推荐原因（命中主题/在阅读路线/热度较高/已生成研读报告）。

## 测试
- 新增：`tests/unit/test_retry_backoff.py`、`test_paper_dedup.py`、
  `test_markdown_sections.py`、`test_references.py` 及契约测试扩展
  （`test_web_product_contracts.py` 增至 38）。
- 核心相关测试 105 passed；`tests/` 全量 315 passed，剩余 3 个 failed 为
  预先存在的环境问题（observability 全量 JSON 副作用 + 依赖真实 MinerU 的 e2e），
  与本次改动无关。
