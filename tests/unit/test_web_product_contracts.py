from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_metrics_expand_real_daily_digest_details() -> None:
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'id="metricDetails"' in index
    assert 'data-metric-key="${key}"' in app
    assert "state.digest?.details?.[key]" in app
    assert 'data-metric-paper="${entry.id}"' in app


def test_notebook_uses_server_persistence_instead_of_session_storage() -> None:
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'notebook: ["/papers?selected=true"]' in app
    assert '/papers/${encodeURIComponent(paperId)}/select' in app
    assert "researchHubNotebook" not in app
    assert "state.notebookItems" in app


def test_settings_explain_llm_and_dify_api_keys() -> None:
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "用于自动中文摘要和论文研读" in index
    assert "Dify 工作流 ID（可选）" in index
    assert "Dify 填已发布工作流的 API 密钥" in index
    assert "无需重启" in index
    assert 'id="saveAnalysisConfigButton"' in index
    assert "保存 LLM 配置" in index
    assert 'id="saveScheduleConfigButton"' in index
    assert "saveRuntimeConfig(analysisConfigPayload(), \"analysis\")" in app
    assert "saveRuntimeConfig(scheduleConfigPayload(), \"schedule\")" in app
    assert "平台接口" in index
    assert "局域网内读写操作无需额外凭证" in index
    assert "公网管理凭证" not in index
    assert "平台管理 API key" not in app
    assert "state.runtimeConfig?.env_backfilled" in app
    assert "当前来自 .env；点「保存 LLM 配置」后可编辑" in app


def test_daily_papers_use_chinese_abstract_as_primary_content() -> None:
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'paper.translated_abstract ? "中文摘要" : "中文摘要待生成"' in app
    assert "中文摘要尚未生成" in app
    assert "查看英文原摘要" in app


def test_paper_card_shows_one_line_method_summary() -> None:
    """Each paper's abstract section must be prefixed by an optional one-line
    Chinese method summary (method_summary) that explains what method solves
    what problem, without breaking when the summary is absent."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # The one-liner is rendered right above the Chinese abstract in the library.
    assert "一句话摘要" in app
    assert "paper.method_summary ?" in app
    assert "paper-one-liner" in app
    # Notebook cards and the reader meta carry it too.
    assert "paper-one-liner" in app
    assert "reader-one-liner" in app
    # Styling lives in a CSS class (strict CSP: no inline styles).
    assert ".paper-one-liner" in styles
    assert ".reader-one-liner" in styles


def test_transient_alerts_auto_dismiss_and_are_dismissible() -> None:
    """Transient notifications (e.g. '主题已删除') must auto-dismiss quickly and
    expose a close button, while the persistent endpoint-degradation banner is
    exempt so it stays until endpoints recover. The transient toast lives in a
    dedicated element so page re-renders don't wipe it mid-view."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Transient toast auto-dismisses after a short (seconds) timeout — not a
    # full minute, which lingered across views.
    assert "4 * 1000" in app
    assert "setTimeout" in app
    assert "clearTimeout" in app
    # It has a dismiss button that removes the notification.
    assert ".alert-dismiss" in app
    assert 'className = "alert-dismiss"' in app
    assert "aria-label" in app
    assert "关闭通知" in app
    # The degradation banner must not be auto-dismissed mid-flight.
    assert 'alert.dataset.degraded' in app
    assert 'alert.dataset.degraded = "true"' in app
    # Transient feedback uses a dedicated #appToast element so a re-render
    # (which calls renderGlobalAlert) can't overwrite it.
    assert "appToast" in app
    # Dismiss styling is a CSS class (strict CSP: no inline styles).
    assert ".alert-dismiss" in styles
    assert ".toast" in styles


def test_reader_is_an_enter_state_that_does_not_auto_open_a_paper() -> None:
    """The reader (阅读台) is an 'enter reading page' state. Entering it via the
    nav must NOT auto-select the first paper and auto-load its PDF (which some
    browsers download). A paper is only opened when the user explicitly chooses
    one or the route names a specific paper."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # On load, do not silently fall back to the first paper.
    assert "state.selectedPaperId = state.selectedPaperId || null;" in app
    # renderReader does not force-select papers[0] when nothing is chosen.
    assert "const selected = selectedPaper();" in app
    assert "const workspace = selectedWorkspace();" in app
    assert app.count("getPaperId(papers[0])") == 0
    # The reader shows a 'choose a paper' placeholder when nothing is selected.
    assert "选择论文后显示 PDF、Markdown、研读报告或证据。" in app
    assert "选择论文后显示内容。" in app
    # A paper still opens explicitly via openPaper / the {id}/read route.
    assert "state.selectedPaperId = paperId;" in app
    assert "segments[1] !== \"read\"" in app


def test_relations_view_auto_loads_and_can_rebuild() -> None:
    """The relationship view must fetch ALL relations (not just those lazily
    loaded in workspaces), auto-load when opened, and offer a rebuild action."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Fetch all relations from a dedicated endpoint, not just loaded workspaces.
    assert "apiJson(\"/relations\")" in app
    assert "function loadRelations" in app
    # Auto-refresh when entering the view.
    assert "refreshRelationsOnView" in app
    assert 'if (view === "relations" && !state.loading) refreshRelationsOnView();' in app
    # A rebuild action posts to the rebuild endpoint then refetches.
    assert "runRelationsRebuild" in app
    assert "apiJson(\"/relations/rebuild\", { method: \"POST\" })" in app
    assert "重建论文关系" in app or "自动重建/跑论文关系" in app
    # View chrome supports the header/button (strict CSP: CSS classes only).
    assert 'id="relationsHeader"' in index
    assert ".relation-card-head" in styles
    assert ".relations-type-summary" in styles
    assert ".relation-grid-inner" in styles


def test_daily_view_supports_date_browsing_and_history() -> None:
    """The daily view must keep today's papers and let the user select a date
    to browse historical papers, so discovering new papers never wipes old ones.
    """
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Date-browsing controls in the dashboard panel.
    assert 'id="dailyPrevDay"' in index
    assert 'id="dailyNextDay"' in index
    assert 'id="dailyDateFilter"' in index
    assert 'id="historyPapers"' in index
    # Logic wires prev/next/today and loads the selected date's papers.
    assert "function browseDateBy" in app
    assert "function loadHistoryPapers" in app
    assert "function setBrowseDateFromInput" in app
    assert "function renderHistoryPapers" in app
    assert '"dailyPrevDay"' in app and "browseDateBy(-1)" in app
    assert '"dailyNextDay"' in app and "browseDateBy(1)" in app
    # Styles use CSS classes (strict CSP).
    assert ".daily-browse" in styles


def test_search_covers_history_and_falls_back_to_online() -> None:
    """Search must work across stored (historical) papers and, when nothing is
    found locally, offer an online arXiv search fallback."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # Local search across the full corpus.
    assert "/papers/search" in app
    assert "function runOnlineSearch" in app
    assert "function renderSearchResults" in app
    # The online fallback requests the search endpoint with online=true.
    assert "online=true" in app
    # A button allows the user to trigger the online search when local is empty.
    assert 'id="onlineSearchBtn"' in app
    # Remote results are rendered back into the view (flagged remote).
    assert ".remote-paper" in (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert "paper.remote" in app


def test_daily_papers_grouped_by_topic_with_configurable_quota() -> None:
    """Today's papers must be grouped under topic cards; clicking a topic card
    expands its papers below it, capped at the topic's daily_quota which is
    editable in Settings."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Grouping logic + per-topic expand/collapse.
    assert "function renderDailyByTopic" in app
    assert "function toggleTopicPapers" in app
    assert "data-topic-toggle" in app
    assert "paperTopicObjs" in app
    assert "daily_quota" in app
    # Paper cards already carry topic tags.
    assert "normalizeTopics" in app
    # Settings exposes per-topic quota editing backed by PATCH /topics/{id}.
    assert 'id="topicQuotaGrid"' in index
    assert "function saveTopicQuota" in app
    assert 'id="saveTopicQuotaButton"' in index
    assert "apiJson(`/topics/" in app
    assert "daily-quota" in app or "topic-quota" in app
    # CSS classes (strict CSP).
    assert ".daily-topic-card" in styles
    assert ".daily-topic-papers" in styles
    assert ".topic-quota-grid" in styles


def test_topic_cards_support_inline_edit() -> None:
    """Topic centre cards must offer an edit button that reveals an inline form
    (name zh/en, aliases, quota) and persists changes via PATCH /topics/{id}."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Edit button rendered per topic card.
    assert "data-topic-edit" in app
    # Inline edit form pre-fills editable fields and offers save/cancel.
    assert "function renderTopicEditForm" in app
    assert "data-edit-name-zh" in app
    assert "data-edit-name-en" in app
    assert "data-edit-aliases" in app
    assert "data-edit-quota" in app
    assert "data-topic-edit-save" in app
    assert "data-topic-edit-cancel" in app
    # Save persists via PATCH /topics/{id} with an idempotency key.
    assert "function saveTopicEdit" in app
    assert 'apiJson(`/topics/${encodeURIComponent(topicId)}`' in app
    assert "method: \"PATCH\"" in app
    assert "function toggleTopicEdit" in app
    # Strict CSP: CSS classes only.
    assert ".topic-edit-form" in styles
    assert ".topic-edit-field" in styles
    assert ".topic-edit-actions" in styles


def test_web_ui_uses_css_classes_under_strict_csp() -> None:
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert " style=" not in app
    assert ".style." not in app
    assert " style=" not in index
    assert 'document.getElementById("view-reader").classList.contains("active")' in app
    assert ".split-layout > *" in styles
    assert "overflow-wrap: anywhere" in styles


def test_api_json_sends_content_type_for_json_bodies() -> None:
    """Regression guard: `...options` must be spread BEFORE `headers` so the
    caller-supplied headers (e.g. Idempotency-Key) never clobber the
    `Content-Type: application/json` header. If they do, FastAPI rejects the
    body with 422 "Input should be a valid dictionary or object to extract
    fields from (body)" because the request goes out as text/plain."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # apiJson must set Content-Type when a body is present.
    assert '"Content-Type": "application/json"' in app
    # The options spread must come BEFORE the headers object so the caller's
    # headers (Idempotency-Key) are layered on top and Content-Type survives.
    # Locate the spread that feeds fetch directly, scoped to apiJson.
    api_json_start = app.index("async function apiJson")
    api_json_body = app[api_json_start:app.index("function readableDetail", api_json_start)]
    spread_idx = api_json_body.index("...options,")
    headers_idx = api_json_body.index("headers: {")
    assert spread_idx < headers_idx, (
        "apiJson must spread options before defining headers so caller "
        "headers (Idempotency-Key) do not overwrite Content-Type"
    )

    # readableDetail helper must exist so 422 detail arrays render readably.
    assert "function readableDetail(detail)" in app
    assert "readableDetail(payload?.detail)" in app


def test_topic_digest_button_scrolls_to_panel() -> None:
    """When a topic's digest is requested, the page must scroll the digest
    panel into view. The topic list is a long single-column stack on narrow
    screens and the panel sits below it, so without this the result renders
    far off-screen and the click looks like a no-op."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "scrollToDigestPanel" in app
    assert "target.scrollIntoView" in app
    # loadTopicDigest must scroll the expanded inline region into view.
    assert "scrollToDigestPanel();" in app
    assert 'data-topic-digest-inline' in app
    # Panel should carry a clear "主题摘要" header so the result is obvious.
    assert "topic-digest-label" in app
    # The digest must expand inline under the clicked card, with an editable note.
    assert "renderInlineTopicDigest" in app
    assert "data-topic-note-save" in app
    assert "topic-note-input" in app


def test_topic_digest_papers_link_to_reader() -> None:
    """Each paper listed inside a topic's inline digest must be clickable and
    open the reader view so users can go from a topic directly to its papers
    and view each paper's details."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # Papers inside the inline digest carry a clickable link with the paper id.
    assert "getPaperId(paper)" in app
    assert 'data-topic-paper="${pid}"' in app
    assert "topic-paper-link" in app
    # Clicking a paper opens the reader (paper details) via openPaper.
    assert "openPaper(button.dataset.topicPaper)" in app
    # renderTopics must bind the click handlers for each paper link.
    assert 'querySelectorAll("[data-topic-paper]")' in app


def test_topic_has_view_today_papers_button() -> None:
    """Each topic card must offer a '查看今日论文' action that jumps to the
    paper library filtered to that topic."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # The card action carries the topic id for the filter.
    assert 'data-topic-papers="${topic.id}"' in app
    # openTopicPapers sets the topic filter then switches to the papers view.
    assert "function openTopicPapers" in app
    assert "topicFilter" in app
    assert "filter.value" in app
    assert 'switchView("papers")' in app
    assert "openTopicPapers(button.dataset.topicPapers)" in app
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert ".topic-paper-link" in styles


def test_jobs_view_live_polling_shows_progress() -> None:
    """The 任务中心 must auto-refresh (poll) while visible so users see live
    discovery/parse progress, and stop polling when leaving the view. The
    progress indicator and poll-resume logic must live in the UI."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Track the active view and drive polling on entering the jobs view.
    assert 'activeView: "dashboard"' in app
    assert "function startJobsPolling" in app
    assert "function stopJobsPolling" in app
    assert "function pollJobsOnce" in app
    # Entering jobs starts polling; leaving stops it.
    assert 'if (view === "jobs")' in app
    assert "startJobsPolling()" in app
    assert "stopJobsPolling()" in app
    assert "setInterval(pollJobsOnce, 4000)" in app
    # A visible progress badge tells the user it is live/refreshing.
    assert "renderJobsPollStatus" in app
    assert 'id="jobsPollBadge"' in index
    assert ".jobs-poll-badge" in styles
    # Triggering discovery jumps to the jobs view so progress is visible.
    assert 'switchView("jobs", true)' in app



def test_jobs_pipeline_timeline_shows_midflight_progress() -> None:
    """The 任务中心 must surface the full discovery pipeline (发现→下载→解析→
    研读→翻译) with per-step progress and failure reasons, not just a flat job
    list, so the operator can see what each run is actually doing."""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # The pipeline timeline is rendered on top of the job list.
    assert "function renderPipelineRuns" in app
    assert "function summarizeRunErrors" in app
    # It walks the five real pipeline stages.
    assert "论文发现" in app and "PDF 下载" in app and "文档解析" in app
    assert "LLM 研读" in app and "中文翻译" in app
    # Step states: running / succeeded / failed / waiting.
    assert 'pipeline-step ${stateClass}' in app
    assert 'stateClass === "running"' in app
    assert 'stateClass === "failed"' in app
    assert 'stateClass === "succeeded"' in app
    assert 'stateClass = "waiting"' in app
    assert ".pipeline-run-card" in styles
    assert ".pipeline-step" in styles
    assert ".pipeline-step.failed" in styles
    assert ".pipeline-step.running" in styles
    assert ".pipeline-errors" in styles
    # The timeline is fed by the run snapshots returned by /workflows.
    assert "state.workflows?.runs" in app


def test_jobs_view_shows_llm_failure_diagnosis() -> None:
    """失败环节不仅要标红，还必须在任务中心用已配置的 LLM 分析原因并展示，
    让用户看到「为什么失败 + 怎么修」而不只是网络错误原文。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # A helper surfaces error.llm_analysis on each failed job row.
    assert "function jobLlmErrorSummary" in app
    assert "diag.reason" in app or "llm_analysis" in app
    # The pipeline error summary prefers the LLM diagnosis and shows suggestion.
    assert "diag.reason" in app
    assert "diag.suggestion" in app
    assert "diag.detail" in app
    # The raw error is still preserved as a fallback.
    assert "err.message || res.message" in app


def test_topbar_has_usage_help_and_directed_discovery() -> None:
    """顶栏需提供「使用说明」弹层，把读论文/写专利/找论文/定向发现的步骤讲清，
    并提供「定向发现」以满足特定方向+时间段+N 篇论文的诉求。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    # Help button + overlay exist.
    assert 'id="helpButton"' in index
    assert 'id="helpOverlay"' in index
    assert "function openHelp" in app
    assert "function closeHelp" in app
    assert "function renderHelpContent" in app
    # The five usage topics are documented.
    assert "怎么读论文" in app
    assert "怎么写专利" in app
    assert "怎么找论文" in app
    assert "发现某个方向的论文" in app
    assert "发现特定方向、特定时间段的 N 篇论文" in app


def test_directed_discovery_supports_topic_window_and_count() -> None:
    """「定向发现」必须允许用户指定研究方向、起止时间段和每主题篇数，
    才能实现『发现特定方向、特定时间段的 N 篇论文』，而非固定 6 主题 + 20 篇。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'id="directedDiscoveryButton"' in index
    assert 'id="directedTopic"' in index
    assert 'id="directedStartDate"' in index
    assert 'id="directedEndDate"' in index
    assert 'id="directedMaxResults"' in index
    assert "function openDirectedDiscovery" in app
    assert "function submitDirectedDiscovery" in app
    # Payload carries user-selected topic ids, window, count and auto_process.
    assert "topics: topicIds" in app
    assert "window_start" in app
    assert "max_results: Math.min(500, Math.max(1, maxResults))" in app
    assert "auto_process: autoProcess" in app
    # Backdrop click and Escape close the overlays.
    assert "function bindOverlays" in app
    assert "event.key === \"Escape\"" in app
    # The open modal keeps focus inside (focus trap) and restores it on close.
    assert "function _focusOverlay" in app
    assert "function _unfocusOverlay" in app


def test_run_discovery_uses_lookback_window_not_single_day() -> None:
    """触发发现必须用回溯窗口而不是严格单日窗口，否则数据源滞后一天发表的
    论文会被全量过滤，导致「点了却找不到论文、0 篇入库」。修复后从所选日期
    往前回溯 lookback_days 天，才能把最近发表的论文真正发现入库。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "function addDays" in app
    assert "lookback = Math.max(1, Number(state.runtimeConfig?.schedule?.lookback_days) || 7)" in app
    assert "windowStart = `${startDate}T00:00:00`" in app
    # Payload uses the looked-back window and records the lookback for audit.
    assert "window_start: windowStart" in app
    assert "lookback_days: lookback" in app
    # The alert tells the user how much history is being scanned.
    assert "回溯 ${lookback} 天" in app


def test_pipeline_run_cards_expandable_with_job_detail() -> None:
    """发现流水线每张卡片应可点击展开，展示该卡片（run）下每个任务的
    详细条目（阶段、状态、目标论文、错误/结果、时间），让用户看得见
    daily-paper-intelligence 下每个任务的具体进展，而不只是一张概览图。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Each pipeline-run card has an expand toggle bound to run id.
    assert "state.expandedRuns" in app
    assert 'data-run-toggle="${runId}"' in app
    assert "renderRunJobDetail(run, jobs)" in app
    # Rendering the detail resolves paper titles from paper_ids / version ids.
    assert "function paperIdToTitleMap" in app
    assert "function jobTargetLabel" in app
    assert "function jobTargetKindLabel" in app
    # Detail lists every job with kind/status/message/time.
    assert "pipeline-job-row" in app
    assert "jobKindLabel(job.kind)" in app
    assert "jobStatusLabel(job.status)" in app
    assert "row" in app
    # Clicking the toggle adds/removes the run from the expanded set.
    assert "state.expandedRuns.delete(runId)" in app
    assert "state.expandedRuns.add(runId)" in app
    # The whole card is clickable to expand, not just the small toggle button.
    assert "data-card-toggle" in app
    assert "toggleRunExpanded" in app
    assert "closest(\"button, a, input, select, textarea, label\")" in app
    # CSS for the expandable detail rows exists.
    assert ".pipeline-run-detail" in styles
    assert ".pipeline-job-row" in styles
    assert ".pipeline-run-expand-caret" in styles


def test_daily_run_shows_token_usage() -> None:
    """每个任务应统计 token 使用量；daily-paper-intelligence 完成后应在其
    卡片上展示本轮流水线的 LLM token 消耗总量，展开后每个任务也展示自身
    的 prompt/completion 与 total token，方便核算成本。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Helpers to format and extract token usage exist.
    assert "function formatTokens" in app
    assert "function jobTokensFor" in app
    # Extract usage from top-level result.usage or nested response.usage.
    assert "res.usage" in app
    assert "res.response" in app
    assert "prompt_tokens" in app
    assert "completion_tokens" in app
    assert "total_tokens" in app
    # The pipeline card head shows the run-level token total.
    assert "run.tokens" in app
    assert "total_tokens" in app
    # Each expanded job row shows its own token usage.
    assert "jobTokensFor(job)" in app
    # CSS for the token pill exists.
    assert ".token-pill" in styles


def test_pipeline_steps_show_dynamic_progress() -> None:
    """流水线每个阶段（如 PDF 下载）应显示动态进度，例如下载 94 篇时
    显示「3/94 进行中」，让用户看到任务正在推进，而不只是静态任务数。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # Per-step done/total progress is computed from succeeded/failed/running
    # counts reported in the run snapshot.
    assert "const done = succeeded + failed;" in app
    assert "const progress = total ? `${done}/${total}` : \"\";" in app
    # The dynamic progress is shown in the step badge with running/failed states.
    assert "`${progress} 进行中`" in app
    assert "`${progress} 完成`" in app
    # A tooltip explains the breakdown.
    assert "progressTip" in app


def test_paper_shows_stage_completion_tags() -> None:
    """每篇论文应展示阶段完成标签（PDF 下载 / 解析 / 摘要翻译 / 研读），
    让用户一眼看出该论文各阶段是否完成，而不只是单一的总状态。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Stage tag helpers exist.
    assert "function paperStageTags" in app
    assert "function renderStageTags" in app
    # The four stages are tracked.
    assert '"PDF 下载"' in app
    assert '"摘要翻译"' in app
    assert '"研读"' in app
    # Completion is inferred from status / translated_abstract / report.
    assert "translated_abstract" in app
    assert "hasReport(paper)" in app
    assert "hasDownload" in app
    assert "hasTranslated" in app
    assert "hasAnalyzed" in app
    # They render in the paper library card and the reader list.
    assert "renderStageTags(paper)" in app
    assert ".paper-stage-tags" in styles
    assert ".stage-tag" in styles
    assert ".stage-tag.done" in styles
    assert ".stage-tag.todo" in styles


def test_pipeline_expanded_detail_is_structured_not_escaped() -> None:
    """展开后的流水线明细应渲染为分组表格（按阶段），而不是被转义成代码
    文本；每阶段带小节标题与成功/进行中/失败汇总。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Detail sections are grouped by job kind.
    assert "pipeline-job-group" in app
    assert "pipeline-job-group-head" in app
    assert "KIND_ORDER" in app
    # Per-group summary counts success/running/failed.
    assert " 个 · ${succeeded} 成功" in app
    assert "进行中" in app
    # The group CSS exists.
    assert ".pipeline-job-group" in styles
    assert ".pipeline-job-group-head" in styles


def test_settings_has_font_size_control() -> None:
    """设置页应有「显示设置 > 文字大小」控件，选择后即时缩放界面文字并
    持久化到本地浏览器（localStorage），下次打开自动沿用。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # Settings page has a font-size select and a save button.
    assert 'id="fontSizeInput"' in index
    assert 'id="saveFontSizeButton"' in index
    assert "显示设置" in index
    # JS wires up loading, applying, and saving the font scale.
    assert "function applyFontSize" in app
    assert "function loadFontScale" in app
    assert "function saveFontSize" in app
    assert "initFontSize()" in app
    # Persisted to localStorage so the choice survives reloads.
    assert "localStorage.setItem" in app
    assert "research_hub.font_scale" in app
    # JS binds the save button and select via getElementById.
    assert 'getElementById("saveFontSizeButton")' in app
    assert 'getElementById("fontSizeInput")' in app
    # Applied via CSS classes (strict CSP forbids inline style).
    assert "function applyFontSize" in app
    assert "function fontSizeClass" in app
    assert ".font-normal" in styles
    assert ".font-large" in styles


def test_patent_candidate_has_no_approver_field() -> None:
    """专利候选表单不应再有「审批人」输入项：前端不再要求填写审批人，
    只保留四项人工确认，避免创建候选时被该必填项卡住。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    # No approver input in the HTML form or JS bindings.
    assert "approverInput" not in index
    assert "approverInput" not in app
    # Gate requires only the four manual confirmations.
    assert "已完成四项人工确认" in app
    assert "approval.approver" in app
    # The approval helper hard-codes a fallback operator label.
    assert 'approver: "web ui"' in app
    # Help text no longer asks to specify an approver.
    assert "指定审批人" not in app


def test_reading_report_generated_on_demand() -> None:
    """研读报告默认不在解析后自动生成（避免每篇都烧 LLM token），而是用户
    点击阅读台「研读报告」tab 时按需触发：调用 /analyze 排队并按需刷新。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    # Reader report tab shows a "generate" action when no report yet.
    assert "requestReadingReport" in app
    assert "data-request-report" in app
    assert "生成研读报告" in app
    # Generation calls the backend analyze endpoint and polls for completion.
    assert "/paper-versions/${encodeURIComponent(versionId)}/analyze" in app
    assert "reportGenerating" in app
    assert "reportError" in app
    # The generating/loading and error states render in the reader.
    assert "正在调用 LLM 解析论文并生成详细研读报告" in app
    # Default schedule payload no longer auto-chains analyze.
    assert '? ["translate"]' in app
    assert 'after_parse: document.getElementById("scheduleTranslateInput")?.checked' in app


def test_paper_library_has_no_all_dates_checkbox() -> None:
    """论文库不应再有那个占据大面积的「全部日期」勾选框，避免用户误以为
    是给论文打勾的选择框。论文库默认展示全部日期，用主题/状态/搜索筛选。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    # The checkbox and its label are gone from the toolbar.
    assert "libraryShowAllDates" not in index
    assert "全部日期" not in index
    # No JS logic binds or reads the removed checkbox anymore.
    assert "libraryShowAllDates" not in app
    # The date input (used to reload a specific day) is retained.
    assert 'id="dateFilter"' in index
    # Library filtering no longer narrows by a date-id set.
    assert "dateIds" not in app


def test_reader_one_liner_shown_on_document_pane_top() -> None:
    """阅读台点击论文后，论文的一句话描述应展示在论文展示页（右侧文档面板）
    的上端，而不是左侧目录面板的顶部。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # A summary container sits above the document content in the reader pane.
    assert 'id="readerPaperSummary"' in index
    assert "readerPaperSummary" in app
    assert "reader-paper-summary" in app
    # The one-liner is filled into that pane-top container.
    assert "paperSummary" in app
    assert "reader-paper-summary" in styles
    # The left nav meta is reset to its default placeholder (no one-liner there).
    assert 'selectedMeta.textContent = "选择一篇论文查看详情。"' in app


def test_paper_card_surfaces_code_and_citation_metadata() -> None:
    """论文卡片应展示来自 metadata 的代码链接与引用数等增强信息。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert "function paperCodeUrl" in app
    assert "function paperCitationCount" in app
    assert "function paperMetaTags" in app
    assert "paperMetaTags(paper)" in app
    assert "引用 " in app  # citations tag label
    assert "代码" in app  # code tag label
    assert ".paper-meta-tag" in styles
    assert "paper-ext-link" in app
    assert ".paper-ext-link" in styles


def test_paper_card_similar_papers_entry() -> None:
    """相似论文入口：基于共同主题与关键词，展示可点击的相似论文。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert "data-similar-papers" in app
    assert "function showSimilarPapers" in app
    assert "function similarPapers" in app
    assert "相似论文" in app
    assert ".similar-paper-row" in styles
    assert ".similar-score" in styles


def test_reader_has_keyboard_shortcuts_and_theme_toggle() -> None:
    """阅读器快捷键 + 夜间模式设置。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert "function handleReaderShortcuts" in app
    assert 'key === "j"' in app
    assert 'key === "k"' in app
    assert 'key === "s"' in app
    assert "syncDocTabs" in app
    # Theme toggle UI + logic + dark CSS.
    assert 'id="themeModeInput"' in index
    assert "function applyTheme" in app
    assert "function loadTheme" in app
    assert "theme-dark" in styles
    assert "localStorage.setItem(THEME_KEY" in app


def test_paper_library_has_sort_batch_and_search_highlight() -> None:
    """论文库：多因子排序、批量选择操作、搜索高亮。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert 'id="sortFilter"' in index
    assert "function paperHeatScore" in app
    assert 'sortBy === "hot"' in app
    assert "state.batchSelected" in app
    assert 'id="selectAllVisibleBtn"' in index
    assert 'id="batchNotebookBtn"' in index
    assert "function batchAddToNotebook" in app
    assert ".paper-batch-bar" in styles
    assert "function highlightQuery" in app
    assert "<mark>" in app
    assert "mark {" in styles


def test_dashboard_has_recommended_reading_panel() -> None:
    """仪表盘新增今日推荐阅读面板，展示可解释的推荐原因。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert 'id="recommendedReading"' in index
    assert "function renderRecommendedReading" in app
    assert "今日推荐阅读" in index
    assert "data-recommend-open" in app
    assert "命中主题" in app
    assert ".recommended-item" in styles



def test_pdf_tab_reads_inline_and_downloads_only_on_explicit_button() -> None:
    """阅读台 PDF tab 必须默认内联浏览而不是自动下载；「下载 PDF 到本地」
    是唯一的本地下载入口；无 PDF 时不得自动触发服务器拉取，需用户点
    「获取 PDF 以便在线阅读」。
    PDF 在线阅读采用服务端渲染逐页 PNG 图片（<img> 轮播）——不依赖浏览器
    内置 PDF 查看器或 PDF.js canvas 渲染（VS Code 内置 Electron 等环境会白板），
    任何浏览器都能稳定显示。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    # 有 PDF artifact 时：图片轮播在线阅读 + 明确的下载按钮，而不是自动下载。
    assert 'selectedTab === "pdf"' in app
    assert "data-pdf-page-img" in app
    assert "initPdfViewer(container, {" in app
    # 前端只从服务端渲染端点取图：/pdf/pages 拿页数，/pdf/page/N 拿第 N 页 PNG。
    assert "`${baseUrl}/page/${n}`" in app
    assert "`${baseUrl}/pages`" in app
    # 缩放走 data-zoom 属性 + CSS 选择器（strict CSP 下不用 inline style）。
    assert "img.dataset.zoom" in app
    assert "data-download-local-pdf" in app
    assert "下载 PDF 到本地" in app
    assert "function downloadPdfToLocal" in app
    # 下载通过 fetch blob + a[download]，文件名用论文标题。
    assert 'link.download = `${slug || "paper"}.pdf`' in app
    assert "URL.createObjectURL(blob)" in app
    # PDF 浏览区操作条与图片轮播样式。
    assert ".pdf-viewer-bar" in styles
    assert ".pdf-page-img" in styles
    assert 'data-zoom="2"' in styles
    # 图片必须从服务端渲染端点加载，不能回落到 iframe。
    assert 'iframe title="论文 PDF"' not in app

    # 无 PDF artifact 时：不再自动触发服务器下载，而是显示主动获取按钮。
    assert "正在服务器下载 PDF" not in app
    assert "data-fetch-pdf-on-server" in app
    assert "获取 PDF 以便在线阅读" in app
    assert "不会下载到你本地" in app


def test_reader_falls_back_to_full_library_when_today_empty() -> None:
    """阅读台必须能从全库（allPapers）兜底，避免今日列表为空时直链论文空白。
    覆盖 P0-A：URL 直链一篇不属于今日列表的论文时目录与文档区仍可渲染。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # knownPapers 合并今日 + 全库 + 笔记本，保证 selectedPaper() 能在全库找到。
    assert "state.allPapers" in app
    assert "...state.allPapers, ...state.notebookItems" in app
    # readerDirectoryPapers 在 filteredPapers 为空时把当前选中论文收入目录。
    assert "papers.unshift(currentPaper)" in app
    # renderReader 目录空但有选中论文时仍渲染文档区。
    assert "selectedPaperCandidate" in app
    # 全库加载完成后重渲染阅读台/专利候选，解决初始时序空态。
    assert 'if (state.activeView === "reader") renderReader();' in app
    assert 'if (state.activeView === "patents") renderPatentWorkspace();' in app


def test_patent_picker_uses_full_library_and_limits_rows() -> None:
    """专利候选选择器必须从全库选论文（不再只盯着空的今日列表），
    并对大批量列表做限制避免渲染卡顿。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "knownPapers()" in app
    assert "candidate-picker-limit" in app
    assert "仅展示前 120 篇供选择" in app
    assert 'state.allPapersLoading)' in app


def test_search_uses_debounce_and_escape_fix() -> None:
    """全局搜索输入必须防抖渲染（大列表防卡顿），且联网失败/加载
    不再被外层 html`` 转义（错误框应真实显示）。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "debounceRenderSearch" in app
    assert "addEventListener(\"input\", debounceRenderSearch)" in app
    # renderSearchResults 的错误/加载块不再被转义
    assert "loadingBlock(\"正在联网检索论文...\")" in app
    assert "errorBlock(`联网检索失败：${state.onlineError}`)" in app


def test_dark_mode_uses_semantic_surfaces_not_literal_whites() -> None:
    """暗色模式不得残留大量字面浅色背景导致白斑：核心组件应使用语义变量/
    theme-dark 覆盖。抽查若干关键组件不再出现 #fff / #f2fbf5 等字面色。"""
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert "var(--amber-weak)" in styles          # .alert / .compliance-note
    assert "var(--green-weak)" in styles           # .toast
    assert "var(--red-weak)" in styles             # .state.failed / pipeline-step.failed
    assert "var(--surface-2)" in styles            # .tech-card / .stage-tag
    assert "var(--brand-gradient)" in styles
    assert "prefers-reduced-motion" in styles
    assert "html.theme-dark body" in styles


def test_jobs_kind_filter_offers_full_catalog_including_discover_and_relate() -> None:
    """任务中心的类型筛选必须始终提供完整流水线类型（发现/下载/解析/研读/
    翻译/关系），而不是只列出当前任务数据里存在的类型；否则早期创建的发现任
    务被后续任务刷走后，「发现」选项消失，用户会误以为"筛选全是全部类型"。
    也要求任务中心直接提供「触发发现」入口，方便暂停/重新触发发现任务。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "JOB_KIND_CATALOG" in app
    assert '"discover", "download", "parse", "analyze", "translate", "relate"' in app
    assert 'jobKindLabel(kind) || kind' in app
    assert 'jobRunDiscoveryButton' in index
    assert 'id="jobRunDiscoveryButton"' in app or "jobRunDiscoveryButton" in app


def test_jobs_pipeline_steps_include_relate_in_order() -> None:
    """任务中心顶部流水线必须与后端工作流定义一致的顺序：发现→下载→解析→
    研读/翻译（并行）→关系与日报，不得漏掉最后的关系步骤，也不得把翻译排在
    研读之前造成"工作流不按顺序"的观感。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert '"relate", "关系与日报"' in app
    assert '"analyze", "LLM 研读"' in app
    assert '"translate", "中文翻译"' in app
    # 并行分支标记：研读⟂ / 翻译∥
    assert 'pipeline-branch-mark' in app
    assert 'branchSet' in app
    assert 'pipeline-step-arrow' in app


def test_discover_job_can_retry_partial_succeeded() -> None:
    """发现任务（discover）在部分成功 / 可重试失败时必须在任务中心提供「重
    试」（= 重新触发）按钮；后端 retry 也必须放行 partial_succeeded，否则用户
    无法对已开启的发现任务做重新触发。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    repository = (PROJECT_ROOT / "research_hub" / "repository.py").read_text(encoding="utf-8")

    assert '"partial_succeeded"' in app or "partial_succeeded" in app
    assert 'canRetryJob' in app
    assert "partial_succeeded" in repository
    assert "cannot be retried" in repository


def test_workflow_dag_renders_parallel_branch_and_merge() -> None:
    """工作流卡片必须基于后端 edges 渲染有向 DAG，能体现并行分叉（⇉ 并行）与
    多路汇聚（⇊ 汇聚），而不是只能顺序排一条线；让用户从可视化上一眼看出
    「解析后研读与翻译并行、最后汇聚到关系」。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert "renderWorkflowDag" in app
    assert "workflow-branch" in app
    assert "workflow-merge" in app
    assert "⇉ 并行" in app
    assert "⇊ 汇聚" in app
    assert ".workflow-arrow.workflow-branch" in styles
    assert ".workflow-arrow.workflow-merge" in styles


def test_workflow_daily_pipeline_edges_connect_translate_to_relate() -> None:
    """后端每日论文研读工作流必须包含 translate → relate 边，这样并行分叉
    （解析→研读/翻译）最终能汇聚到「关系与日报」，可视化才不会出现翻译分支
    悬空、顺序感缺失。"""
    workflows = (PROJECT_ROOT / "research_hub" / "workflows.py").read_text(encoding="utf-8")

    assert '["translate", "relate"]' in workflows
    assert '["parse", "analyze"]' in workflows
    assert '["parse", "translate"]' in workflows
    assert '["analyze", "relate"]' in workflows


def test_backend_serves_pdf_preview_render_endpoints() -> None:
    """后端必须为 PDF 在线阅读提供服务端渲染端点：/pdf/pages 返回页数，
    /pdf/page/{n} 返回第 n 页 PNG。这样前端 <img> 轮播在任何浏览器（含
    VS Code 内置 Electron）都能显示，不依赖浏览器内置 PDF 查看器。"""
    api = (PROJECT_ROOT / "research_hub" / "app.py").read_text(encoding="utf-8")

    # 端点路由与函数。
    assert '"/api/v1/artifacts/{artifact_id}/pdf/pages"' in api
    assert "def pdf_pages_meta(" in api
    assert '"/api/v1/artifacts/{artifact_id}/pdf/page/{page_number}"' in api
    assert "def pdf_page_image(" in api
    # 用 PyMuPDF（fitz）渲染 PNG，返回 image/png 并带缓存。
    assert "import fitz" in api
    assert "total_pages" in api
    assert "media_type=\"image/png\"" in api
    assert "Cache-Control" in api
    # 本地文件型 PDF 才可渲染；不落地服务端依赖时给出明确 501 提示。
    assert "未安装 PDF 渲染依赖" in api
    # 依赖清单声明 PyMuPDF。
    req = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pymupdf" in req


def test_jobs_offer_batch_retry_for_failed() -> None:
    """任务中心必须提供「全部重试失败任务」批量操作：当存在 >1 个可重试任务
    时，批量条显示一键重试按钮，点击后逐个重排队失败任务，避免用户 127 个
    失败任务只能逐个点击重试。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "data-batch-retry" in app
    assert "retryAllFailedJobs(" in app
    assert "failedJobs.length > 1" in app
    assert "全部重试失败任务" in app
    assert "Web UI batch retry all failed." in app
    assert "批量重试完成" in app


def test_report_tab_offers_copy_and_export() -> None:
    """阅读台「研读报告」tab 生成后必须提供「复制报告」与「导出 Markdown」，
    让用户能带走完整研读内容，而只能在界面上读。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "data-copy-report" in app
    assert "data-export-report-md" in app
    assert "复制报告" in app
    assert "导出 Markdown" in app
    assert "navigator.clipboard.writeText" in app
    assert "text/markdown;charset=utf-8" in app
    # 文件名复用公共 slugify（与 PDF 下载一致）。
    assert "function slugify(title)" in app
    assert ".md`" in app


def test_pdf_zoom_offers_fit_width_option() -> None:
    """PDF 在线阅读的缩放下拉必须默认「适应宽度」（100%=一页满容器宽），
    并保留 150/200/300% 放大档位。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'value="1" selected>适应宽度' in app
    assert 'value="1.5">150%' in app
    assert 'value="2">200%' in app
    assert 'value="3">300%' in app
    # CSS data-zoom 选择器支撑缩放。
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert '.pdf-page-img[data-zoom="1"]' in styles
    assert '.pdf-page-img[data-zoom="1.5"]' in styles
    assert '.pdf-page-img[data-zoom="3"]' in styles


def test_remaining_user_audit_actions_are_exposed() -> None:
    """用户审计剩余的高价值操作必须可直接完成，而不是只在报告中记录。"""
    app = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'data-workflow-action="run-discovery"' in app
    assert 'data-workflow-action="open-patent"' in app
    assert "withButtonLoading(event.currentTarget, runDiscovery)" in app
    assert "function userActionNonce()" in app
    assert 'id="jobSearch"' in index
    assert 'document.getElementById("jobSearch")' in app
    assert "titleMap.get(target)" in app
    assert "...(state.allPapers || [])" in app
    assert 'id="notebookClearButton"' in index
    assert 'id="notebookCopyDigestButton"' in index
    assert "async function clearNotebook()" in app
    assert "window.confirm" in app
    assert "offset += 5" in app
    assert "Promise.allSettled" in app
    assert "async function copyNotebookDigest()" in app
    assert "async function writeClipboardText(text)" in app
    assert "document.execCommand(\"copy\")" in app
    assert "previousFocus.focus()" in app
    assert "function relationTypeLabel(type)" in app
    assert 'title="原始类型：${type}"' in app
