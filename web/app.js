const API_BASE = "/api/v1";
const today = new Date().toISOString().slice(0, 10);

function addDays(dateString, amount) {
  const d = new Date(`${dateString}T00:00:00`);
  d.setDate(d.getDate() + amount);
  return d.toISOString().slice(0, 10);
}

// 界面文字大小缩放。以数字倍数(如 1 为标准、1.1 较大)持久化到
// localStorage，通过给 documentElement 切换缩放 class 生效
// （严格 CSP 下不允许 inline style，故用 class + CSS 缩放）。
const FONT_SIZE_KEY = "research_hub.font_scale";
const FONT_SIZE_DEFAULT = 1;

function loadFontScale() {
  const raw = localStorage.getItem(FONT_SIZE_KEY);
  const value = raw === null ? FONT_SIZE_DEFAULT : Number(raw);
  return Number.isFinite(value) && value > 0 ? value : FONT_SIZE_DEFAULT;
}

function fontSizeClass(scale) {
  return scale === 0.85 ? "font-small" : scale === 1.1 ? "font-large" : scale >= 1.25 ? "font-xlarge" : "font-normal";
}

function applyFontSize(scale) {
  const root = document.documentElement;
  const current = root.dataset.fontScale;
  if (current) root.classList.remove("font-small", "font-normal", "font-large", "font-xlarge");
  root.classList.add(fontSizeClass(scale));
  root.dataset.fontScale = String(scale);
}

function initFontSize() {
  applyFontSize(loadFontScale());
  const input = document.getElementById("fontSizeInput");
  if (input) input.value = String(loadFontScale());
}

function saveFontSize() {
  const input = document.getElementById("fontSizeInput");
  if (!input) return;
  const scale = (Number(input.value) || FONT_SIZE_DEFAULT);
  localStorage.setItem(FONT_SIZE_KEY, String(scale));
  applyFontSize(scale);
  const stamp = document.getElementById("fontSaveHint");
  if (stamp) stamp.textContent = "已应用并保存。";
}

const state = {
  health: null,
  adapterHealth: null,
  papers: [],
  topics: [],
  jobs: [],
  candidates: [],
  drafts: [],
  digest: null,
  workflows: null,
  runtimeConfig: null,
  selectedTopicDigest: null,
  candidateStages: new Map(),
  workspaces: new Map(),
  artifactText: new Map(),
  selectedPaperId: null,
  selectedTab: "pdf",
  selectedForPatent: new Set(),
  selectedCandidateId: null,
  selectedDraftId: null,
  endpointResults: {},
  jobActionErrors: new Map(),
  topicOverrideNote: "",
  loading: true,
  notebookPapers: new Set(),
  notebookItems: [],
  expandedMetric: null,
  relations: null,
  relationsLoading: false,
  relationsError: null,
  relationsLimit: 60,
  allPapers: [],
  historyPapers: [],
  historyLoading: false,
  historyError: null,
  browseDate: new Date().toISOString().slice(0, 10),
  searchMode: "local",
  onlineSearching: false,
  onlineError: null,
  expandedTopics: new Set(),
  editingTopicId: null,
  expandedRuns: new Set(),
  activeView: "dashboard",
  jobsPollTimer: null,
  jobsPolling: false,
};

const endpoints = {
  health: ["/health"],
  adapterHealth: ["/adapter-health"],
  papers: [`/papers?date=${encodeURIComponent(today)}`, "/papers"],
  notebook: ["/papers?selected=true"],
  topics: ["/topics"],
  jobs: ["/jobs"],
  candidates: ["/invention-candidates"],
  drafts: ["/patent-drafts"],
  digest: [`/daily-digests/${encodeURIComponent(today)}`],
  workflows: ["/workflows"],
  runtimeConfig: ["/runtime-config"],
};

const viewTitles = {
  dashboard: "仪表盘",
  papers: "论文库",
  reader: "阅读台",
  topics: "主题中心",
  workflows: "工作流",
  jobs: "任务中心",
  relations: "关系视图",
  patents: "专利候选",
  notebook: "笔记本",
  settings: "设置",
};

const viewPaths = {
  dashboard: "/daily",
  papers: "/papers",
  reader: "/papers/read",
  topics: "/topics",
  workflows: "/workflows",
  jobs: "/jobs",
  relations: "/relations",
  patents: "/patents/candidates",
  notebook: "/notebook",
  settings: "/settings",
};

const gates = [
  { id: "count", label: "已选择 2 到 5 篇论文", check: () => state.selectedForPatent.size >= 2 && state.selectedForPatent.size <= 5 },
  { id: "versions", label: "每篇论文都有 paper_id 和 paper_version_id", check: () => selectedPapers().every((paper) => getPaperId(paper) && getVersionId(paper)) },
  { id: "topics", label: "存在共同或互补 AI Infra 主题", check: hasTopicOverlap },
  { id: "reports", label: "至少一篇论文已有研读报告或技术卡片", check: hasAnyReportEvidence },
  { id: "structured", label: "已填写耦合、接口、数据/控制流、非并列说明和联合效果", check: hasStructuredCandidateInput },
  { id: "approval", label: "已完成四项人工确认", check: hasApprovalConfirmations },
  { id: "api", label: "专利候选接口可用", check: () => endpointOk("candidates") },
  { id: "human", label: "创建后必须人工审批，审批前不能生成草稿", check: () => false, advisory: true },
];

document.addEventListener("DOMContentLoaded", () => {
  bindNavigation();
  bindControls();
  bindOverlays();
  initFontSize();
  setDateDefaults();
  applyRoute(location.pathname, false);
  window.addEventListener("popstate", () => applyRoute(location.pathname, false));
  loadAll();
});

function bindNavigation() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
}

function bindControls() {
  document.getElementById("refreshButton").addEventListener("click", loadAll);
  document.getElementById("globalSearch").addEventListener("input", render);
  document.getElementById("topicFilter").addEventListener("change", render);
  document.getElementById("statusFilter").addEventListener("change", render);
  document.getElementById("dateFilter").addEventListener("change", loadAll);
  document.getElementById("runDiscoveryButton").addEventListener("click", runDiscovery);
  document.getElementById("helpButton").addEventListener("click", openHelp);
  document.getElementById("helpCloseButton").addEventListener("click", closeHelp);
  document.getElementById("directedDiscoveryButton").addEventListener("click", openDirectedDiscovery);
  document.getElementById("directedCloseButton").addEventListener("click", closeDirectedDiscovery);
  document.getElementById("directedForm").addEventListener("submit", submitDirectedDiscovery);
  document.getElementById("dailyPrevDay")?.addEventListener("click", () => browseDateBy(-1));
  document.getElementById("dailyNextDay")?.addEventListener("click", () => browseDateBy(1));
  document.getElementById("dailyJumpToday")?.addEventListener("click", () => {
    state.browseDate = today;
    const dateInput = document.getElementById("dailyDateFilter");
    if (dateInput) dateInput.value = today;
    loadHistoryPapers();
  });
  document.getElementById("dailyDateFilter")?.addEventListener("change", (event) => setBrowseDateFromInput(event.target.value));
  document.getElementById("createCandidateButton").addEventListener("click", createCandidate);
  document.getElementById("downloadDraftButton").addEventListener("click", () => downloadSelectedDraft("markdown"));
  document.getElementById("downloadDraftDocxButton")?.addEventListener("click", () => downloadSelectedDraft("docx"));
  document.getElementById("saveAnalysisConfigButton")?.addEventListener("click", saveAnalysisConfig);
  document.getElementById("saveScheduleConfigButton")?.addEventListener("click", saveScheduleConfig);
  document.getElementById("saveTopicQuotaButton")?.addEventListener("click", saveTopicQuota);
  document.getElementById("saveFontSizeButton")?.addEventListener("click", saveFontSize);
  document.getElementById("analysisProviderInput")?.addEventListener("change", syncAnalysisProviderForm);
  document.getElementById("addTopicButton")?.addEventListener("click", addTopic);
  document.getElementById("topicOverrideInput")?.addEventListener("input", (event) => {
    state.topicOverrideNote = event.target.value.trim();
    renderGates();
  });
  [
    "couplingInput",
    "interfaceInput",
    "dataControlFlowInput",
    "nonParallelInput",
    "jointEffectInput",
    "approvalFactsInput",
    "approvalNoveltyInput",
    "approvalInventivenessInput",
    "approvalScopeInput",
  ].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", renderGates);
    document.getElementById(id)?.addEventListener("change", renderGates);
  });
  document.querySelectorAll("[data-doc-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedTab = button.dataset.docTab;
      document.querySelectorAll("[data-doc-tab]").forEach((item) => item.classList.toggle("active", item === button));
      renderReader();
    });
  });
}

function setDateDefaults() {
  document.getElementById("dateFilter").value = today;
  document.getElementById("todayLabel").textContent = today;
}

function switchView(view, updateHistory = true) {
  state.activeView = view;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  document.getElementById("viewTitle").textContent = viewTitles[view] || "工作台";
  if (updateHistory && viewPaths[view] && location.pathname !== viewPaths[view]) {
    history.pushState({ view }, "", viewPaths[view]);
  }
  if (view === "reader" && !state.loading) renderReader();
  if (view === "relations" && !state.loading) refreshRelationsOnView();
  if (view === "jobs") {
    // Live progress while the user watches the 任务中心.
    renderJobsPollStatus();
    startJobsPolling();
  } else {
    stopJobsPolling();
  }
}

// --- Live job progress (任务中心) ---
function startJobsPolling() {
  if (state.jobsPollTimer) return;
  // Poll immediately, then every few seconds, refreshing the job list in place
  // so the user sees discovery/parse progress without a manual refresh.
  pollJobsOnce();
  state.jobsPollTimer = setInterval(pollJobsOnce, 4000);
  state.jobsPolling = true;
}

function stopJobsPolling() {
  if (state.jobsPollTimer) {
    clearInterval(state.jobsPollTimer);
    state.jobsPollTimer = null;
  }
  state.jobsPolling = false;
}

async function pollJobsOnce() {
  if (state.jobsPolling) {
    // Skip re-entrancy while a previous poll is still in flight.
    return;
  }
  state.jobsPolling = true;
  renderJobsPollStatus();
  try {
    const [jobsData, workflowData] = await Promise.all([
      apiJson("/jobs"),
      apiJson("/workflows").catch(() => null),
    ]);
    const items = normalizeList(jobsData, ["items", "jobs", "results", "data"]);
    if (workflowData) state.workflows = workflowData;
    if (state.activeView === "jobs") {
      const prevIds = new Set(state.jobs.map((job) => job.id || job.job_id));
      const nextIds = new Set(items.map((job) => job.id || job.job_id));
      const changed = !(
        prevIds.size === nextIds.size
        && [...prevIds].every((id) => nextIds.has(id))
        && state.jobs.length === items.length
        && state.jobs.every((job, index) => (job.status || "") === (items[index]?.status || ""))
      );
      state.jobs = items;
      if (changed) renderJobsElseJobsPollStatus();
      else renderJobsPollStatus();
    }
  } catch (_) {
    // Swallow transient poll failures; a later tick will recover.
  } finally {
    state.jobsPolling = false;
    if (state.activeView === "jobs") renderJobsPollStatus();
  }
}

function renderJobsPollStatus() {
  const badge = document.getElementById("jobsPollBadge");
  if (!badge) return;
  const hasPending = (state.jobs || []).some((job) => ["queued", "running", "processing"].includes(String(job.status || "").toLowerCase()));
  const label = state.jobsPolling
    ? (hasPending ? "正在刷新..." : "自动刷新中...")
    : (hasPending ? "有新任务进行中" : "自动刷新已开启");
  badge.textContent = label;
  badge.classList.toggle("has-pending", hasPending);
}

function renderJobsElseJobsPollStatus() {
  if (state.activeView === "jobs") renderJobs();
  else renderJobsPollStatus();
}

function refreshRelationsOnView() {
  // Always refresh the relationship view when entering it, so newly analyzed
  // papers are reflected without a manual intervention.
  if (state.relationsLoading) return;
  state.relations = null;
  loadRelations();
}

function applyRoute(pathname, updateHistory = false) {
  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] === "papers" && segments[1] && segments[1] !== "read") {
    state.selectedPaperId = decodeURIComponent(segments[1]);
    switchView("reader", updateHistory);
    return;
  }
  const view = Object.entries(viewPaths).find(([, route]) => route === pathname)?.[0] || "dashboard";
  switchView(view, updateHistory);
}

async function loadAll() {
  state.loading = true;
  renderLoading();

  const selectedDate = document.getElementById("dateFilter").value || today;
  document.getElementById("todayLabel").textContent = selectedDate;
  state.selectedTopicDigest = null;
  endpoints.papers = [`/papers?date=${encodeURIComponent(selectedDate)}`, "/papers"];
  endpoints.digest = [`/daily-digests/${encodeURIComponent(selectedDate)}`];

  const keys = Object.keys(endpoints);
  const results = await Promise.all(keys.map((key) => firstJson(key, endpoints[key])));
  keys.forEach((key, index) => {
    state.endpointResults[key] = results[index];
  });

  state.health = state.endpointResults.health.data;
  state.adapterHealth = state.endpointResults.adapterHealth.data;
  state.papers = normalizeList(state.endpointResults.papers.data, ["papers", "items", "results", "data"]);
  state.notebookItems = normalizeList(state.endpointResults.notebook.data, ["papers", "items", "results", "data"]);
  state.notebookPapers = new Set(state.notebookItems.map(getPaperId).filter(Boolean));
  state.topics = normalizeList(state.endpointResults.topics.data, ["topics", "items", "results", "data"]);
  state.jobs = normalizeList(state.endpointResults.jobs.data, ["jobs", "items", "results", "data"]);
  state.candidates = normalizeList(state.endpointResults.candidates.data, ["candidates", "items", "results", "data"]);
  state.drafts = normalizeList(state.endpointResults.drafts.data, ["drafts", "items", "results", "data"]);
  state.digest = state.endpointResults.digest.data;
  state.workflows = state.endpointResults.workflows.data;
  state.runtimeConfig = state.endpointResults.runtimeConfig.data;
  // Only keep an explicitly chosen paper. The reader should be an empty
  // "enter reading" state until the user picks a paper, rather than silently
  // auto-opening the first paper's PDF (which some browsers download).
  state.selectedPaperId = state.selectedPaperId || null;
  state.selectedCandidateId = state.selectedCandidateId || state.candidates[0]?.id || null;
  state.selectedDraftId = state.selectedDraftId || state.drafts[0]?.id || null;

  state.loading = false;
  render();
  if (state.selectedPaperId) loadWorkspace(state.selectedPaperId);
  if (state.selectedCandidateId) loadCandidateStages(state.selectedCandidateId);
  // Load workspaces for notebook papers so their reading reports render.
  state.notebookItems.forEach((paper) => loadWorkspace(getPaperId(paper)));
  loadAllPapers();
  const dateInput = document.getElementById("dailyDateFilter");
  if (dateInput && dateInput.value !== state.browseDate) dateInput.value = state.browseDate;
}

async function loadAllPapers() {
  // Pull the full paper library (all dates) so the library view and history
  // browsing work across the whole corpus, independent of the selected day.
  try {
    const data = await apiJson("/papers?all=1");
    state.allPapers = normalizeList(data, ["papers", "items", "results", "data"]);
  } catch {
    state.allPapers = [...state.papers];
  }
  renderPaperLibrary();
}

async function loadHistoryPapers() {
  const dateValue = state.browseDate;
  state.historyLoading = true;
  state.historyError = null;
  renderHistoryPapers();
  try {
    const data = await apiJson(`/papers?date=${encodeURIComponent(dateValue)}`);
    const papers = normalizeList(data, ["papers", "items", "results", "data"]);
    if (state.browseDate === dateValue) {
      state.historyPapers = papers;
      state.historyLoading = false;
      renderHistoryPapers();
    }
  } catch (error) {
    if (state.browseDate === dateValue) {
      state.historyError = error.message;
      state.historyLoading = false;
      renderHistoryPapers();
    }
  }
}

function renderSearchResults(container) {
  const query = (document.getElementById("globalSearch")?.value || "").trim();
  if (!query) {
    container.innerHTML = "";
    return;
  }
  if (state.onlineSearching) {
    container.innerHTML = html`
      <div class="search-results-head">
        <span class="pill">本地未找到匹配</span>
        <span class="meta">正在联网检索“${query}”...</span>
      </div>
      ${loadingBlock("正在联网检索论文...")}
    `;
    return;
  }
  const local = filteredPapers();
  if (local.length) {
    container.innerHTML = html`<div class="search-results-head"><span class="pill">本地命中 ${local.length} 篇</span><span class="meta">已在历史/全部论文中检索</span></div>${raw(paperCards(local))}`;
    bindPaperCardActions(container);
    return;
  }
  // Remote (online) search results are stored in historyPapers and flagged
  // with remote=true — render them so the online fallback shows results.
  const remoteItems = (state.historyPapers || []).filter((paper) => paper.remote);
  if (remoteItems.length) {
    container.innerHTML = html`<div class="search-results-head"><span class="pill">联网命中 ${remoteItems.length} 篇（尚未入库）</span><span class="meta">来自“${query}”的在线检索结果</span></div>${raw(paperCards(remoteItems))}`;
    bindPaperCardActions(container);
    return;
  }
  if (state.onlineError) {
    container.innerHTML = html`
      <div class="search-results-head"><span class="pill">联网检索失败</span></div>
      ${errorBlock(`联网检索失败：${state.onlineError}`)}
    `;
    return;
  }
  if (state.allPapers && state.allPapers.length) {
    // Local library searched and found nothing.
    container.innerHTML = html`
      <div class="search-results-head">
        <span class="pill">本地未找到匹配</span>
        <span class="meta">没有在 ${state.allPapers.length} 篇已收录论文中找到“${query}”</span>
      </div>
      <button class="primary" type="button" id="onlineSearchBtn">联网搜索“${query}”</button>
    `;
    container.querySelector("#onlineSearchBtn")?.addEventListener("click", () => runOnlineSearch(query));
  }
}

async function runOnlineSearch(query) {
  state.onlineSearching = true;
  state.onlineError = null;
  renderHistoryPapers();
  try {
    const data = await apiJson(`/papers/search?q=${encodeURIComponent(query)}&online=true`);
    const items = normalizeList(data, ["items", "papers", "results"]);
    state.onlineSearching = false;
    if (data && data.items && Array.isArray(data.items)) {
      state.allPapers = state.allPapers || [];
      state.historyPapers = data.items;
    }
    if (items.length) {
      state.onlineError = null;
      showAlert(`联网检索到 ${items.length} 篇论文（尚未入库）。`);
    } else {
      state.onlineError = data?.remote_error || "未检索到相关论文";
    }
    renderHistoryPapers();
    renderPaperLibrary();
  } catch (error) {
    state.onlineSearching = false;
    state.onlineError = error.message;
    renderHistoryPapers();
  }
}

function browseDateBy(days) {
  const current = new Date(`${state.browseDate}T12:00:00`);
  current.setDate(current.getDate() + days);
  state.browseDate = current.toISOString().slice(0, 10);
  const dateInput = document.getElementById("dailyDateFilter");
  if (dateInput) dateInput.value = state.browseDate;
  loadHistoryPapers();
}

function setBrowseDateFromInput(value) {
  if (!value) return;
  state.browseDate = value;
  loadHistoryPapers();
}

async function loadWorkspace(paperId) {
  if (!paperId || state.workspaces.has(paperId)) return;
  state.workspaces.set(paperId, { loading: true });
  renderReader();
  try {
    const workspace = await apiJson(`/papers/${encodeURIComponent(paperId)}/workspace`);
    state.workspaces.set(paperId, workspace);
  } catch (error) {
    state.workspaces.set(paperId, { error: error.message });
  }
  render();
}

// 按需生成研读报告：点击阅读台「研读报告」tab 且尚无报告时触发。
// 先确保 PDF 已解析（有 markdown），再排队 analyze；随后轮询 workspace
// 直到报告生成完成或失败。研读报告默认不在解析后自动生成，以节省 LLM 用量。
async function requestReadingReport(versionId) {
  const paperId = state.selectedPaperId;
  const workspace = state.workspaces.get(paperId) || {};
  if (!versionId) {
    state.workspaces.set(paperId, { ...workspace, reportError: "该论文缺少可分析的版本信息。" });
    renderReader();
    return;
  }
  state.workspaces.set(paperId, { ...workspace, reportGenerating: true, reportError: null, report: null });
  renderReader();
  try {
    await apiJson(`/paper-versions/${encodeURIComponent(versionId)}/analyze`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-reading-report-${versionId}` },
      body: JSON.stringify({ force: true }),
    });
    // 轮询 workspace 直到 report 出现或超时（最多 ~5 分钟）。
    const deadline = Date.now() + 5 * 60 * 1000;
    let lastError = "";
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      let current;
      try {
        current = await apiJson(`/papers/${encodeURIComponent(paperId)}/workspace`);
        state.workspaces.set(paperId, current);
      } catch (error) {
        lastError = error.message || String(error);
        current = null;
      }
      if (current?.report) {
        state.workspaces.set(paperId, { ...current, reportGenerating: false, reportError: null });
        renderReader();
        return;
      }
      if (Date.now() > deadline) {
        state.workspaces.set(paperId, { ...(current || state.workspaces.get(paperId)), reportGenerating: false, reportError: "研读报告生成超时，请稍后重试。" });
        renderReader();
        return;
      }
      if (!current) {
        // 临时读取失败，继续等下一次轮询；先回退一次渲染以免一直转圈无提示。
        state.workspaces.set(paperId, { ...(state.workspaces.get(paperId)), reportGenerating: true });
        renderReader();
      }
    }
  } catch (error) {
    state.workspaces.set(paperId, { ...state.workspaces.get(paperId), reportGenerating: false, reportError: `触发研读报告失败：${error.message}` });
    renderReader();
  }
}

async function firstJson(key, paths) {
  const errors = [];
  for (const path of paths) {
    try {
      const response = await fetch(`${API_BASE}${path}`, { headers: { Accept: "application/json" } });
      if (!response.ok) {
        errors.push(`${path}: HTTP ${response.status}`);
        continue;
      }
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        errors.push(`${path}: 非 JSON 响应`);
        continue;
      }
      return { ok: true, path, data: await response.json(), errors };
    } catch (error) {
      errors.push(`${path}: ${error.message}`);
    }
  }
  return { ok: false, path: paths[0], data: null, errors };
}

async function loadCandidateStages(candidateId, force = false) {
  if (!candidateId || (!force && state.candidateStages.has(candidateId))) return;
  state.candidateStages.set(candidateId, { loading: true, items: [] });
  renderPatentWorkspace();
  try {
    const payload = await apiJson(`/invention-candidates/${encodeURIComponent(candidateId)}/stages`);
    state.candidateStages.set(candidateId, {
      loading: false,
      items: normalizeList(payload, ["items", "stages"]),
    });
  } catch (error) {
    state.candidateStages.set(candidateId, { loading: false, items: [], error: error.message });
  }
  renderPatentWorkspace();
}

async function apiJson(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    method,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      message = payload?.error?.message || readableDetail(payload?.detail) || message;
    } catch (_) {
      // Keep the HTTP status when the backend returns a non-JSON error.
    }
    throw new Error(message);
  }
  return response.json();
}

function readableDetail(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        return (item.msg ? item.msg + (item.loc ? ` (${item.loc.join(".")})` : "") : JSON.stringify(item));
      }
      return String(item);
    }).join("；");
  }
  if (typeof detail === "object") return JSON.stringify(detail);
  return String(detail);
}

function analysisConfigPayload() {
  const provider = document.getElementById("analysisProviderInput")?.value || "openai";
  const baseUrl = document.getElementById("analysisBaseUrlInput")?.value.trim() || "";
  const model = document.getElementById("analysisModelInput")?.value.trim() || "";
  const apiKey = document.getElementById("analysisApiKeyInput")?.value.trim() || "";
  const clearApiKey = Boolean(document.getElementById("clearAnalysisApiKeyInput")?.checked);
  const difyWorkflowId = document.getElementById("analysisDifyWorkflowIdInput")?.value.trim() || "";
  const providerConfig = {
    base_url: baseUrl,
    api_key: clearApiKey ? "" : (apiKey || null),
    ...(provider === "openai" ? { model } : {}),
    ...(provider === "dify" ? { workflow_id: difyWorkflowId } : {}),
  };
  return { analysis: { provider, [provider]: providerConfig } };
}

function scheduleConfigPayload() {
  return {
    schedule: {
      enabled: Boolean(document.getElementById("scheduleEnabledInput")?.checked),
      timezone: document.getElementById("scheduleTimezoneInput")?.value.trim() || "Asia/Shanghai",
      daily_hour: Number(document.getElementById("scheduleHourInput")?.value || 9),
      lookback_days: Number(document.getElementById("scheduleLookbackInput")?.value || 7),
      max_results: Number(document.getElementById("scheduleMaxResultsInput")?.value || 5),
      auto_process: true,
      after_parse: document.getElementById("scheduleTranslateInput")?.checked
        ? ["translate"]
        : [],
    },
  };
}

async function saveRuntimeConfig(payload, section) {
  const isAnalysis = section === "analysis";
  const button = document.getElementById(
    isAnalysis ? "saveAnalysisConfigButton" : "saveScheduleConfigButton",
  );
  const defaultLabel = isAnalysis ? "保存 LLM 配置" : "保存自动研读配置";
  try {
    if (button) {
      button.disabled = true;
      button.textContent = "保存中...";
    }
    showAlert(isAnalysis ? "正在保存 LLM 配置..." : "正在保存自动研读配置...");
    const saved = await apiJson("/runtime-config", {
      method: "PUT",
      headers: { "Idempotency-Key": `web-runtime-config-${Date.now()}` },
      body: JSON.stringify(payload),
    });
    if (isAnalysis) {
      document.getElementById("analysisApiKeyInput").value = "";
      document.getElementById("clearAnalysisApiKeyInput").checked = false;
      document.getElementById("analysisSettingsPanel")?.removeAttribute("data-config-stamp");
    }
    showAlert(
      isAnalysis
        ? `LLM 配置已保存；已为 ${saved.abstract_translation_jobs_queued || 0} 篇现有论文安排中文摘要生成，后续新论文也会自动翻译。`
        : "自动研读配置已保存；调度器会按新配置执行。 ",
    );
    await loadAll();
  } catch (error) {
    showAlert(`${isAnalysis ? "LLM" : "自动研读"}配置保存失败：${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = defaultLabel;
    }
  }
}

function saveAnalysisConfig() {
  return saveRuntimeConfig(analysisConfigPayload(), "analysis");
}

function saveScheduleConfig() {
  return saveRuntimeConfig(scheduleConfigPayload(), "schedule");
}

function syncAnalysisProviderForm() {
  const provider = document.getElementById("analysisProviderInput")?.value || "openai";
  const selected = state.runtimeConfig?.analysis?.[provider] || {};
  const bundleExplicit = !!(state.runtimeConfig?.env_backfilled);
  document.getElementById("analysisModelLabel")?.classList.toggle("hidden", provider !== "openai");
  document.getElementById("analysisDifyWorkflowLabel")?.classList.toggle("hidden", provider !== "dify");
  const baseUrlInput = document.getElementById("analysisBaseUrlInput");
  const modelInput = document.getElementById("analysisModelInput");
  const apiKeyInput = document.getElementById("analysisApiKeyInput");
  const difyWorkflowInput = document.getElementById("analysisDifyWorkflowIdInput");
  if (baseUrlInput) { baseUrlInput.value = selected.base_url || ""; baseUrlInput.readOnly = bundleExplicit; baseUrlInput.title = bundleExplicit ? "当前来自 .env；点「保存 LLM 配置」后可编辑" : ""; }
  if (modelInput) { modelInput.value = selected.model || ""; modelInput.readOnly = bundleExplicit; modelInput.title = bundleExplicit ? "当前来自 .env；点「保存 LLM 配置」后可编辑" : ""; }
  if (difyWorkflowInput) { difyWorkflowInput.value = selected.workflow_id || ""; difyWorkflowInput.readOnly = bundleExplicit; difyWorkflowInput.title = bundleExplicit ? "当前来自 .env；点「保存 LLM 配置」后可编辑" : ""; }
  if (apiKeyInput) apiKeyInput.placeholder = bundleExplicit ? "来自 .env，无需重复填写" : (selected.api_key_configured ? "已在服务器配置；留空保留" : "可留空");
}

function normalizeList(payload, keys) {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== "object") return [];
  for (const key of keys) {
    if (Array.isArray(payload[key])) return payload[key];
  }
  return [];
}

function renderLoading() {
  setApiStatus("loading", "连接中", "正在读取 Research Hub API");
  const loading = html`<div class="loading">正在从 ${API_BASE} 读取数据...</div>`;
  ["dailyPapers", "paperLibrary", "readerPaperList", "topicTree", "topicDigestPanel", "digestDistribution", "readingRoutes", "workflowCatalog", "workflowRuns", "jobList", "relationGraph", "candidatePicker", "notebookPaperList", "notebookDigest"].forEach((id) => {
    document.getElementById(id).innerHTML = loading;
  });
}

function render() {
  renderApiStatus();
  renderGlobalAlert();
  renderMetrics();
  renderTopicFilter();
  renderDashboard();
  renderAdapterHealth("adapterHealth");
  renderPaperLibrary();
  renderReader();
  renderTopics();
  renderWorkflows();
  renderJobs();
  renderRelations();
  renderPatentWorkspace();
  renderNotebookView();
  renderSettings();
}

function renderApiStatus() {
  const okCount = Object.values(state.endpointResults).filter((result) => result?.ok).length;
  const total = Object.keys(endpoints).length;
  if (okCount === total) {
    setApiStatus("ok", "全部可用", `${total} 个端点探测成功`);
  } else if (okCount > 0) {
    setApiStatus("loading", "部分降级", `${okCount}/${total} 个端点可用`);
  } else {
    setApiStatus("error", "API 不可用", `未能连接 ${API_BASE}`);
  }
}

function setApiStatus(kind, title, detail) {
  const card = document.getElementById("apiStatusCard");
  card.className = `status-card is-${kind}`;
  card.innerHTML = html`<span class="status-dot" aria-hidden="true"></span><div><strong>${title}</strong><span>${detail}</span></div>`;
}

function renderGlobalAlert() {
  const failures = Object.entries(state.endpointResults)
    .filter(([, result]) => result && !result.ok)
    .map(([key, result]) => `${key}: ${result.errors.join("；")}`);
  const alert = document.getElementById("globalAlert");
  if (!failures.length) {
    alert.classList.add("hidden");
    alert.textContent = "";
    delete alert.dataset.degraded;
    return;
  }
  alert.dataset.degraded = "true";
  alert.classList.remove("hidden");
  const close = alert.querySelector(".alert-dismiss");
  if (close) close.remove();
  alert.textContent = `当前处于降级状态：${failures.join(" | ")}`;
}

function renderMetrics() {
  const counts = state.digest?.counts || {};
  const parsed = counts.parsed ?? state.papers.filter((paper) => ["parsed", "analyzed", "success", "completed"].includes(String(paper.status || "").toLowerCase())).length;
  const analyzed = counts.analyzed ?? state.papers.filter((paper) => String(paper.status || "").toLowerCase() === "analyzed" || hasReport(paper)).length;
  const failed = counts.job_failures ?? state.jobs.filter((job) => String(job.status || "").toLowerCase().includes("fail")).length;
  const metrics = [
    ["papers", "今日论文", counts.papers ?? state.papers.length, "今日发现并入库的论文总数"],
    ["deduplicated", "去重命中", counts.deduplicated ?? 0, "多来源命中经去重后合并的重复记录数"],
    ["parsed", "已解析", parsed, "已完成 PDF 结构化解析的论文数"],
    ["analyzed", "已有研读", analyzed, "已生成中文研读报告的论文数"],
    ["job_failures", "失败任务", failed, "今日失败或需重试的后端任务数"],
  ];
  const grid = document.getElementById("metricGrid");
  grid.innerHTML = metrics
    .map(([key, label, value, hint]) => html`
      <button class="metric metric-button ${state.expandedMetric === key ? "active" : ""}" type="button" data-metric-key="${key}" title="${hint}" aria-expanded="${state.expandedMetric === key}">
        <span>${label}</span><strong>${value}</strong><small>点击查看明细</small>
      </button>
    `)
    .join("");
  grid.querySelectorAll("[data-metric-key]").forEach((button) => {
    button.addEventListener("click", () => {
      state.expandedMetric = state.expandedMetric === button.dataset.metricKey ? null : button.dataset.metricKey;
      renderMetrics();
    });
  });
  renderMetricDetails();
}

function renderMetricDetails() {
  const container = document.getElementById("metricDetails");
  const key = state.expandedMetric;
  if (!container) return;
  if (!key) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  const labels = {
    papers: "今日论文明细",
    deduplicated: "多来源去重明细",
    parsed: "已解析论文",
    analyzed: "已有研读论文",
    job_failures: "失败任务明细",
  };
  const entries = metricDetailEntries(key);
  container.classList.remove("hidden");
  container.innerHTML = html`
    <div class="panel-heading"><div><h2>${labels[key]}</h2><p>以下条目来自当前日期的后端日报。</p></div><span class="pill">${entries.length} 项</span></div>
    <div class="metric-detail-list">
      ${raw(entries.length ? entries.map((entry) => metricDetailItem(key, entry)).join("") : emptyBlock("该指标当前没有具体条目。"))}
    </div>
  `;
  container.querySelectorAll("[data-metric-paper]").forEach((button) => {
    button.addEventListener("click", () => openPaper(button.dataset.metricPaper));
  });
  container.querySelectorAll("[data-metric-jobs]").forEach((button) => {
    button.addEventListener("click", () => switchView("jobs"));
  });
}

function metricDetailEntries(key) {
  const serverEntries = state.digest?.details?.[key];
  if (Array.isArray(serverEntries)) return serverEntries;
  if (key === "job_failures") {
    return state.jobs.filter((job) => String(job.status || "").toLowerCase().includes("fail"));
  }
  if (key === "deduplicated") return [];
  if (key === "papers") return state.papers.map((paper) => ({ id: getPaperId(paper), title: paperTitle(paper), status: paper.status }));
  const accepted = key === "parsed"
    ? new Set(["parsed", "analyzed", "scored", "published"])
    : new Set(["analyzed", "scored", "published"]);
  return state.papers
    .filter((paper) => accepted.has(String(paper.status || "").toLowerCase()))
    .map((paper) => ({ id: getPaperId(paper), title: paperTitle(paper), status: paper.status }));
}

function metricDetailItem(key, entry) {
  if (key === "deduplicated") {
    const sources = Array.isArray(entry.sources) ? entry.sources.join(" + ") : "多来源";
    return html`<button class="metric-detail-item" type="button" data-metric-paper="${entry.id}"><strong>${entry.title || entry.id}</strong><span>${sources} · ${entry.source_hits || 0} 次命中，合并 ${entry.duplicate_hits || 0} 条</span></button>`;
  }
  if (key === "job_failures") {
    return html`<button class="metric-detail-item" type="button" data-metric-jobs="true"><strong>${pipelineLabel(entry.kind)} · ${entry.target_id || entry.id}</strong><span>${jobStatusLabel(entry.status)}</span></button>`;
  }
  return html`<button class="metric-detail-item" type="button" data-metric-paper="${entry.id}"><strong>${entry.title || entry.id}</strong><span>${jobStatusLabel(entry.status)}</span></button>`;
}

function renderTopicFilter() {
  const select = document.getElementById("topicFilter");
  const current = select.value;
  const names = unique([...state.topics.map(topicName), ...state.papers.flatMap((paper) => normalizeTopics(paper))]).filter(Boolean);
  select.innerHTML = `<option value="">全部主题</option>${names.map((name) => html`<option value="${name}">${name}</option>`).join("")}`;
  select.value = names.includes(current) ? current : "";
}

function renderDashboard() {
  renderDailyByTopic("dailyPapers", state.papers, "daily");
  renderHistoryPapers();
  const steps = ["discover", "download", "parse", "translate", "analyze", "relate", "patent_draft"];
  document.getElementById("pipelineSummary").innerHTML = steps
    .map((step) => {
      const count = state.jobs.filter((job) => String(job.kind || "").toLowerCase().includes(step)).length;
      return html`<div class="gate-item ${count ? "pass" : ""}"><span class="gate-icon">${count ? "✓" : "·"}</span><div><strong>${pipelineLabel(step)}</strong><p class="meta">${count ? `${count} 个任务` : "暂无后端任务记录"}</p></div></div>`;
    })
    .join("");
  renderDigestSummary();
}

// Group a day's papers under their topic cards. Clicking a topic card expands
// the papers belonging to that topic directly below it, capped at the topic's
// daily_quota (configurable in Settings).
function renderDailyByTopic(containerId, papers, source) {
  const container = document.getElementById(containerId);
  if (!endpointOk("papers")) {
    container.innerHTML = errorBlock(source === "history" ? "论文接口不可用，无法展示历史论文。" : "论文接口不可用，无法展示今日论文。");
    return;
  }
  if (!papers.length) {
    container.innerHTML = emptyBlock(source === "history" ? "该日期没有论文。可在「触发发现」为该日期补充论文。" : "今日还没有论文。点击右上角「触发发现」获取今日论文。");
    return;
  }
  // Build topic -> papers map (a paper may appear under every topic it has).
  const orderedTopics = state.topics.filter((topic) => topic.deleted_at == null);
  const byTopic = new Map();
  papers.forEach((paper) => {
    const topics = paperTopicObjs(paper);
    (topics.length ? topics : [null]).forEach((topic) => {
      const key = topic ? topic.id : "__untagged__";
      if (!byTopic.has(key)) byTopic.set(key, []);
      byTopic.get(key).push(paper);
    });
  });
  const sections = [];
  // Rendered topic order: topics that actually have papers, keeping topic
  // config order; untagged papers at the end.
  orderedTopics.forEach((topic) => {
    const list = byTopic.get(topic.id);
    if (!list || !list.length) return;
    sections.push(topicSection(topic, list, containerId));
  });
  if (byTopic.get("__untagged__")?.length) {
    sections.push(untaggedSection(byTopic.get("__untagged__"), containerId));
  }
  container.innerHTML = sections.join("");
  // Wire expand/collapse per topic card.
  container.querySelectorAll("[data-topic-toggle]").forEach((button) => {
    button.addEventListener("click", () => toggleTopicPapers(button.dataset.topicToggle, source));
  });
  // Render expanded topics' papers.
  container.querySelectorAll("[data-topic-papers]").forEach((sectionEl) => {
    const topicId = sectionEl.dataset.topicPapers;
    if (state.expandedTopics.has(topicId)) {
      sectionEl.classList.remove("hidden");
    }
  });
  bindPaperCardActions(container);
}

function topicSection(topic, list, containerId) {
  const quota = Number(topic.daily_quota) > 0 ? Number(topic.daily_quota) : list.length;
  const shown = list.slice(0, quota);
  const expanded = state.expandedTopics.has(topic.id);
  const moreCount = list.length - shown.length;
  return html`
    <section class="daily-topic-group" data-topic-group="${topic.id}">
      <button class="daily-topic-card" type="button" data-topic-toggle="${topic.id}">
        <span class="daily-topic-head">
          <strong>${topicName(topic)}</strong>
          <span class="tag">${list.length} 篇</span>
          ${raw(list.length > quota ? html`<span class="tag quota-note">每日展示 ${quota} 篇</span>` : "")}
        </span>
        <span class="expand-icon ${expanded ? "expanded" : ""}">▸</span>
      </button>
      <div class="daily-topic-papers ${expanded ? "" : "hidden"}" data-topic-papers="${topic.id}">
        ${raw(shown.map((paper) => paperCard(paper)).join(""))}
        ${raw(moreCount > 0 ? html`<p class="meta daily-topic-more">该主题共 ${list.length} 篇，此处展示其中 ${shown.length} 篇。</p>` : "")}
      </div>
    </section>
  `;
}

function untaggedSection(list, containerId) {
  return html`
    <section class="daily-topic-group" data-topic-group="__untagged__">
      <button class="daily-topic-card" type="button" data-topic-toggle="__untagged__">
        <span class="daily-topic-head"><strong>未分类</strong><span class="tag">${list.length} 篇</span></span>
        <span class="expand-icon ${state.expandedTopics.has("__untagged__") ? "expanded" : ""}">▸</span>
      </button>
      <div class="daily-topic-papers ${state.expandedTopics.has("__untagged__") ? "" : "hidden"}" data-topic-papers="__untagged__">
        ${raw(list.map((paper) => paperCard(paper)).join(""))}
      </div>
    </section>
  `;
}

function toggleTopicPapers(topicId, source) {
  if (state.expandedTopics.has(topicId)) {
    state.expandedTopics.delete(topicId);
  } else {
    state.expandedTopics.add(topicId);
  }
  if (source === "history") {
    renderDailyByTopic("historyPapers", state.historyPapers, "history");
  } else {
    renderDailyByTopic("dailyPapers", state.papers, "daily");
  }
}

function renderHistoryPapers() {
  const container = document.getElementById("historyPapers");
  const label = document.getElementById("historyDateLabel");
  const searchActive = (document.getElementById("globalSearch")?.value || "").trim();
  if (searchActive) {
    // A live search is active: show the search results panel instead of the
    // date-browsing history section.
    if (label) label.textContent = "搜索中";
    renderSearchResults(container);
    return;
  }
  if (!endpointOk("papers")) {
    container.innerHTML = errorBlock("论文接口不可用，无法读取历史论文。");
    return;
  }
  if (state.historyLoading) {
    container.innerHTML = loadingBlock(`正在读取 ${state.browseDate} 的论文...`);
    return;
  }
  if (state.historyError) {
    container.innerHTML = errorBlock(`历史论文读取失败：${state.historyError}`);
    return;
  }
  if (label) label.textContent = state.browseDate;
  renderDailyByTopic("historyPapers", state.historyPapers, "history");
}

function paperCards(papers) {
  return papers.map((paper) => paperCard(paper)).join("");
}

function renderDigestSummary() {
  const distribution = document.getElementById("digestDistribution");
  const routes = document.getElementById("readingRoutes");
  if (!endpointOk("digest") || !state.digest) {
    distribution.innerHTML = errorBlock("日报接口不可用。");
    routes.innerHTML = emptyBlock("暂无阅读路线。");
    return;
  }
  const sourceLabels = {
    arxiv: "arXiv",
    huggingface: "Hugging Face",
    openalex: "OpenAlex",
    openreview: "OpenReview",
  };
  const sourceRows = Object.entries(state.digest.source_counts || {}).map(
    ([source, count]) => html`<div class="compact-item"><strong>${sourceLabels[source] || source}</strong><span>${count} 次命中</span></div>`,
  );
  const topicRows = Object.entries(state.digest.topic_distribution || {}).map(
    ([topicId, count]) => html`<div class="compact-item"><strong>${topicDisplayName(topicId)}</strong><span>${count} 篇</span></div>`,
  );
  distribution.innerHTML = `
    <h3 class="digest-subheading">来源命中</h3>
    ${[...sourceRows].join("") || emptyBlock("当日暂无来源命中。")}
    <h3 class="digest-subheading spaced">主题覆盖</h3>
    ${[...topicRows].join("") || emptyBlock("当日暂无主题覆盖。")}
  `;
  routes.innerHTML = Object.entries(state.digest.reading_routes || {}).map(
    ([route, paperIds]) => html`
      <article class="compact-item">
        <strong>${readingRouteLabel(route)}</strong>
        <span>${paperIds.map(digestPaperTitle).join(" · ") || "暂无推荐"}</span>
      </article>
    `,
  ).join("") || emptyBlock("当日暂无阅读路线。");
}

function renderPaperLibrary() {
  renderPaperList("paperLibrary", filteredPapers());
}

function renderPaperList(containerId, papers) {
  const container = document.getElementById(containerId);
  if (!endpointOk("papers")) {
    container.innerHTML = errorBlock("论文接口不可用，无法展示真实论文数据。");
    return;
  }
  if (!papers.length) {
    if (containerId === "paperLibrary") {
      const hasActiveFilters = Boolean(
        (document.getElementById("globalSearch")?.value || "").trim()
        || document.getElementById("topicFilter")?.value
        || document.getElementById("statusFilter")?.value,
      );
      if (hasActiveFilters) {
        // Offer a one-click escape hatch from an empty filtered result.
        const inner = "当前筛选条件下没有论文。";
        container.innerHTML = html`${raw(`<div class="empty-block"><p>${escapeHtml(inner)}</p><p class="meta">可以调整筛选条件，或清除筛选查看全部论文。</p><button class="secondary" type="button" id="clearFiltersBtn">清除筛选</button></div>`)}`;
        container.querySelector("#clearFiltersBtn")?.addEventListener("click", () => clearPaperFilters());
        return;
      }
      container.innerHTML = emptyBlock("当前筛选条件下没有论文。");
      return;
    }
    container.innerHTML = emptyBlock("当前筛选条件下没有论文。");
    return;
  }
  container.innerHTML = papers.map((paper) => paperCard(paper)).join("");
  bindPaperCardActions(container);
}

// Reset all paper-library filters back to defaults and re-render.
function clearPaperFilters() {
  document.getElementById("globalSearch").value = "";
  document.getElementById("topicFilter").value = "";
  document.getElementById("statusFilter").value = "";
  renderPaperLibrary();
}

function paperCard(paper) {
  const id = getPaperId(paper);
  const title = paperTitle(paper);
  const topics = normalizeTopics(paper);
  const status = paper.status || "discovered";
  const statusLabel = {
    discovered: "已发现", parsed: "已解析", analyzed: "已研读",
    downloaded: "已下载", translated: "已翻译", scored: "已评分",
    published: "已发布", failed: "失败",
  }[String(status).toLowerCase()] || status;
  const cardId = `paper-card-${id}`;
  const bodyId = `paper-body-${id}`;
  return html`
    <article class="paper-card" id="${cardId}">
      <div class="paper-card-header" data-toggle-card="${id}">
        <div>
          <h3>${title}</h3>
          <p class="meta">${authorText(paper) || identifierText(paper) || "作者/来源未提供"}</p>
          <div class="paper-stage-tags">${raw(renderStageTags(paper))}</div>
        </div>
        <div class="paper-card-meta-actions">${raw(topics.map((topic) => html`<span class="tag">${topic}</span>`).join(" "))} <span class="state ${String(status).toLowerCase()}">${statusLabel}</span><span class="expand-icon" id="expand-${id}">▸</span></div>
      </div>
      <div class="paper-card-body hidden" id="${bodyId}">
        ${raw(paper.method_summary ? html`<p class="paper-one-liner"><strong>一句话摘要：</strong>${paper.method_summary}</p>` : "")}
        <p><strong>${paper.translated_abstract ? "中文摘要" : "中文摘要待生成"}：</strong>${paperAbstract(paper)}</p>
        ${raw(paper.abstract ? html`<details class="abstract-original"><summary>查看英文原摘要</summary><p>${paper.abstract}</p></details>` : "")}
        ${raw(paper.first_publication_date ? html`<p class="meta">发表日期：${paper.first_publication_date}</p>` : "")}
        ${raw(paper.remote ? html`<p class="meta remote-paper">联网检索结果（尚未入库）</p>` : "")}
        <div class="paper-actions">
          <button class="secondary" type="button" data-open-paper="${id}">打开阅读台</button>
          <button class="secondary" type="button" data-select-patent="${id}">${state.selectedForPatent.has(id) ? "取消候选" : "加入专利候选"}</button>
          <button class="secondary" type="button" data-notebook-add="${id}">${state.notebookPapers.has(id) ? "已在笔记本" : "加入笔记本"}</button>
        </div>
      </div>
    </article>
  `;
}

function bindPaperCardActions(container) {
  container.querySelectorAll("[data-open-paper]").forEach((button) => {
    button.addEventListener("click", () => openPaper(button.dataset.openPaper));
  });
  container.querySelectorAll("[data-select-patent]").forEach((button) => {
    button.addEventListener("click", () => togglePatentSelection(button.dataset.selectPatent));
  });
  container.querySelectorAll("[data-toggle-card]").forEach((header) => {
    header.addEventListener("click", () => {
      const paperId = header.dataset.toggleCard;
      const body = document.getElementById(`paper-body-${paperId}`);
      const icon = document.getElementById(`expand-${paperId}`);
      if (body && icon) {
        body.classList.toggle("hidden");
        icon.classList.toggle("expanded", !body.classList.contains("hidden"));
      }
    });
  });
  container.querySelectorAll("[data-notebook-add]").forEach((button) => {
    button.addEventListener("click", () => toggleNotebook(button.dataset.notebookAdd));
  });
}

function openPaper(paperId) {
  state.selectedPaperId = paperId;
  history.pushState({ view: "reader", paper: paperId }, "", `/papers/${encodeURIComponent(paperId)}/read`);
  switchView("reader", false);
  renderReader();
  loadWorkspace(paperId);
}

function renderReader() {
  if (!document.getElementById("view-reader").classList.contains("active")) return;
  const list = document.getElementById("readerPaperList");
  if (!endpointOk("papers")) {
    list.innerHTML = errorBlock("论文接口不可用。");
    document.getElementById("documentContent").innerHTML = emptyBlock("等待后端论文数据。");
    document.getElementById("technicalCards").innerHTML = emptyBlock("暂无技术卡片。");
    return;
  }
  const papers = filteredPapers();
  const currentPaper = selectedPaper();
  if (currentPaper && !papers.some((paper) => getPaperId(paper) === state.selectedPaperId)) {
    papers.unshift(currentPaper);
  }
  if (!papers.length) {
    list.innerHTML = emptyBlock("没有可阅读论文。");
    document.getElementById("documentContent").innerHTML = emptyBlock("选择论文后显示 PDF、Markdown、研读报告或证据。");
    document.getElementById("technicalCards").innerHTML = emptyBlock("暂无技术卡片。");
    return;
  }
  // Do NOT auto-select the first paper here. If no paper has been chosen yet,
  // the reader stays in its "enter reading" state (directory visible, no
  // document auto-loaded) until the user picks one.
  const selected = selectedPaper();
  const workspace = selectedWorkspace();
  list.innerHTML = papers
    .map((paper) => {
      const id = getPaperId(paper);
      return html`<button class="compact-button ${id === state.selectedPaperId ? "active" : ""}" type="button" data-reader-paper="${id}"><strong>${paperTitle(paper)}</strong><span class="meta">${normalizeTopics(paper).join(" / ") || "未标主题"}</span><span class="reader-stage-tags">${raw(renderStageTags(paper))}</span></button>`;
    })
    .join("");
  list.querySelectorAll("[data-reader-paper]").forEach((button) => {
    button.addEventListener("click", () => openPaper(button.dataset.readerPaper));
  });
  const selectedMeta = document.getElementById("selectedPaperMeta");
  if (selectedMeta) {
    // 左侧目录面板只保留默认占位；一句话描述改到右侧论文展示页上端展示。
    selectedMeta.textContent = "选择一篇论文查看详情。";
  }
  const paperSummary = document.getElementById("readerPaperSummary");
  if (paperSummary) {
    const oneLiner = selected?.method_summary || "";
    if (selected) {
      paperSummary.innerHTML = html`
        <div class="reader-paper-summary-head">
          <strong>${paperTitle(selected)}</strong>
        </div>
        ${raw(oneLiner ? html`<p class="reader-one-liner">${oneLiner}</p>` : "")}
      `;
      paperSummary.classList.remove("hidden");
    } else {
      paperSummary.classList.add("hidden");
    }
  }
  if (workspace?.loading) {
    document.getElementById("documentContent").innerHTML = loadingBlock("正在读取 /papers/{id}/workspace...");
    document.getElementById("technicalCards").innerHTML = loadingBlock("正在读取技术卡片...");
    return;
  }
  if (workspace?.error) {
    document.getElementById("documentContent").innerHTML = errorBlock(`workspace 读取失败：${workspace.error}`);
    document.getElementById("technicalCards").innerHTML = emptyBlock("workspace 不可用，无法显示技术卡片。");
    return;
  }
  renderDocument(selected, workspace);
  renderTechnicalCards(workspace);
}

function renderDocument(paper, workspace) {
  const container = document.getElementById("documentContent");
  if (!paper) {
    container.innerHTML = emptyBlock("选择论文后显示内容。");
    return;
  }
  const effectivePaper = workspace?.paper || paper;
  const artifacts = workspaceArtifacts(workspace);
  if (state.selectedTab === "pdf") {
    const artifact = findArtifact(artifacts, "pdf");
    const url = safeDocumentUrl(artifactDownloadUrl(artifact));
    if (url) {
      container.innerHTML = html`<iframe title="论文 PDF" src="${url}"></iframe><p class="meta">PDF 已保存在局域网服务器：${artifact.id}</p>`;
    } else {
      const versionId = getVersionId(effectivePaper) || currentVersion(effectivePaper, workspace)?.id || "";
      container.innerHTML = versionId
        ? html`${emptyBlock("PDF 尚未保存在服务器；平台不会回退到外部网站或下载到当前电脑。")}
          <button class="primary" type="button" data-materialize-pdf="${versionId}">保存 PDF 到服务器</button>`
        : emptyBlock("论文版本缺少可物化的 PDF 信息。");
      container.querySelector("[data-materialize-pdf]")?.addEventListener("click", () => materializePdf(versionId));
    }
    return;
  }
  if (state.selectedTab === "markdown") {
    const artifact = findArtifact(artifacts, "markdown") || findArtifact(artifacts, "md");
    renderArtifactText(container, artifact, "Markdown");
    return;
  }
  if (state.selectedTab === "report") {
    const report = workspace?.report;
    const effectivePaper = workspace?.paper || paper;
    const versionId = getVersionId(effectivePaper) || currentVersion(effectivePaper, workspace)?.id || "";
    if (report) {
      renderTextDocument(container, reportText(report));
    } else if (workspace?.reportError) {
      container.innerHTML = `${errorBlock(workspace.reportError)}`;
    } else if (workspace?.reportGenerating) {
      container.innerHTML = `${loadingBlock("正在调用 LLM 解析论文并生成详细研读报告（首次可能需要一两分钟），完成后自动刷新。")}`;
    } else {
      container.innerHTML = `${emptyBlock("尚未生成研读报告。点击下方按钮将调用 LLM 解析论文正文并生成详细研读报告。")}
        <div class="paper-actions">
          <button class="primary" type="button" data-request-report>生成研读报告</button>
        </div>`;
      container.querySelector("[data-request-report]")?.addEventListener("click", () => requestReadingReport(versionId));
    }
    return;
  }
  const evidence = collectEvidence(workspace);
  if (evidence.length) {
    renderEvidenceDocument(container, evidence);
  } else {
    container.innerHTML = emptyBlock("后端未提供证据锚点。");
  }
}

function renderArtifactText(container, artifact, label) {
  if (!artifact) {
    container.innerHTML = emptyBlock(`后端未提供 ${label} artifact。`);
    return;
  }
  const url = artifactDownloadUrl(artifact);
  const cached = state.artifactText.get(artifact.id);
  if (cached?.ok) {
    renderArtifactTextContent(container, url, label, cached.text);
    return;
  }
  if (cached?.error) {
    container.innerHTML = html`<p><a href="${url}" target="_blank" rel="noreferrer">打开 ${label} artifact</a></p>${errorBlock(`无法内联预览：${cached.error}`)}`;
    return;
  }
  container.innerHTML = loadingBlock(`正在读取 ${label} artifact...`);
  fetch(url, { headers: { Accept: "text/plain,text/markdown,*/*" } })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    })
    .then((text) => {
      state.artifactText.set(artifact.id, { ok: true, text });
      renderReader();
    })
    .catch((error) => {
      state.artifactText.set(artifact.id, { error: error.message });
      renderReader();
    });
}

function renderArtifactTextContent(container, url, label, text) {
  const actions = document.createElement("div");
  actions.className = "artifact-actions";
  const link = document.createElement("a");
  link.className = "secondary as-link";
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = `下载 ${label}`;
  actions.append(link);
  const body = document.createElement("div");
  body.className = "markdown-body";
  body.textContent = text;
  container.replaceChildren(actions, body);
}

function renderTextDocument(container, text) {
  const body = document.createElement("div");
  body.className = "markdown-body";
  body.textContent = text;
  container.replaceChildren(body);
}

function renderEvidenceDocument(container, evidenceItems) {
  const body = document.createElement("div");
  body.className = "evidence-list";
  evidenceItems.forEach((item) => {
    const row = document.createElement("p");
    row.className = "evidence-row";
    const label = document.createElement("span");
    const kind = evidenceKind(item);
    label.className = `tag evidence-type ${kind}`;
    label.textContent = kind;
    const text = document.createElement("span");
    text.textContent = ` ${evidenceText(item)}`;
    row.append(label, text);
    body.append(row);
  });
  container.replaceChildren(body);
}

function reportText(report) {
  const sections = [
    ["摘要", report.summary],
    ["动机", report.motivation],
    ["方法", report.method],
    ["实验", report.experiments],
    ["结果", report.results],
    ["创新点", report.innovation],
    ["局限", report.limitations],
    ["工程价值", report.engineering_value],
    ["复现计划", report.reproduction_plan],
  ].filter(([, value]) => value);
  return sections.map(([title, value]) => `## ${title}\n\n${value}`).join("\n\n");
}

function renderTechnicalCards(workspace) {
  const cards = normalizeList(workspace?.technical_cards, ["items", "cards"]);
  document.getElementById("technicalCards").innerHTML = cards.length
    ? cards.map((card) => html`
      <div class="tech-card">
        <strong>${card.title || card.problem || "技术点"}</strong>
        <p>${card.method || card.summary || card.description || "无说明"}</p>
        <span class="tag">${card.metric || card.component || "AI Infra"}</span>
        <button class="secondary compact-action" type="button" data-card-patent="${card.paper_id || state.selectedPaperId}">加入候选</button>
      </div>
    `).join("")
    : emptyBlock("暂无结构化技术卡片。");
  document.querySelectorAll("[data-card-patent]").forEach((button) => {
    button.addEventListener("click", () => togglePatentSelection(button.dataset.cardPatent, true));
  });
}

function renderTopics() {
  const container = document.getElementById("topicTree");
  if (!endpointOk("topics")) {
    container.innerHTML = errorBlock("主题接口不可用。");
    return;
  }
  if (!state.topics.length) {
    container.innerHTML = emptyBlock("后端尚未返回主题配置。");
    return;
  }
  container.innerHTML = state.topics.map((topic) => {
    const expanded = state.selectedTopicDigest && state.selectedTopicDigest.topicId === topic.id;
    const editing = state.editingTopicId === topic.id;
    return html`
      <article class="topic-card">
        <h3>${topicName(topic)}</h3>
        <p>${topic.name_en || topic.description || "无主题说明"}</p>
        <div>${raw(normalizeKeywords(topic).map((keyword) => html`<span class="tag">${keyword}</span>`).join(" "))}</div>
        <p class="meta">今日 ${state.digest?.topic_distribution?.[topic.id] || 0} 篇</p>
        <div class="topic-card-actions">
          <button class="secondary" type="button" data-topic-edit="${topic.id}" title="编辑该主题">编辑</button>
          <button class="secondary ${expanded ? "active" : ""}" type="button" data-topic-digest="${topic.id}">${expanded ? "收起摘要" : "查看主题摘要"}</button>
          <button class="secondary" type="button" data-topic-papers="${topic.id}" title="跳转到论文库并筛选该主题">查看今日论文</button>
          <button class="danger-secondary" type="button" data-topic-delete="${topic.id}" title="删除该主题">删除</button>
        </div>
        ${editing ? raw(renderTopicEditForm(topic)) : ""}
      </article>
      ${expanded ? raw(renderInlineTopicDigest(topic.id)) : ""}
    `;
  }).join("");
  container.querySelectorAll("[data-topic-digest]").forEach((button) => {
    button.addEventListener("click", () => loadTopicDigest(button.dataset.topicDigest));
  });
  container.querySelectorAll("[data-topic-edit]").forEach((button) => {
    button.addEventListener("click", () => toggleTopicEdit(button.dataset.topicEdit));
  });
  container.querySelectorAll("[data-topic-edit-cancel]").forEach((button) => {
    button.addEventListener("click", () => toggleTopicEdit(button.dataset.topicEditCancel));
  });
  container.querySelectorAll("[data-topic-edit-save]").forEach((button) => {
    button.addEventListener("click", () => saveTopicEdit(button.dataset.topicEditSave));
  });
  container.querySelectorAll("[data-topic-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteTopic(button.dataset.topicDelete));
  });
  container.querySelectorAll("[data-topic-note-save]").forEach((button) => {
    button.addEventListener("click", () => saveTopicDigestNote(button.dataset.topicNoteSave));
  });
  container.querySelectorAll("[data-topic-paper]").forEach((button) => {
    button.addEventListener("click", () => openPaper(button.dataset.topicPaper));
  });
  container.querySelectorAll("[data-topic-papers]").forEach((button) => {
    button.addEventListener("click", () => openTopicPapers(button.dataset.topicPapers));
  });
  renderTopicDigest();
}

function renderTopicEditForm(topic) {
  const aliases = normalizeKeywords(topic).join(", ");
  return html`
    <div class="topic-edit-form" data-topic-edit-form>
      <label class="topic-edit-field">中文名称<input type="text" data-edit-name-zh value="${topic.name_zh || ""}"></label>
      <label class="topic-edit-field">英文名称<input type="text" data-edit-name-en value="${topic.name_en || ""}"></label>
      <label class="topic-edit-field">关键词（逗号分隔）<input type="text" data-edit-aliases value="${aliases}"></label>
      <label class="topic-edit-field">每日展示条数<input type="number" min="1" max="500" data-edit-quota value="${Number(topic.daily_quota) || 5}"></label>
      <div class="topic-edit-actions">
        <button class="primary" type="button" data-topic-edit-save="${topic.id}">保存修改</button>
        <button class="secondary" type="button" data-topic-edit-cancel="${topic.id}">取消</button>
      </div>
    </div>
  `;
}

function toggleTopicEdit(topicId) {
  state.editingTopicId = state.editingTopicId === topicId ? null : topicId;
  renderTopics();
}

async function saveTopicEdit(topicId) {
  const card = document.querySelector(`#topicTree [data-topic-edit-form]`);
  if (!card) return;
  const nameZh = card.querySelector("[data-edit-name-zh]")?.value.trim() || "";
  const nameEn = card.querySelector("[data-edit-name-en]")?.value.trim() || "";
  const aliases = (card.querySelector("[data-edit-aliases]")?.value || "")
    .split(",").map((item) => item.trim()).filter(Boolean);
  const dailyQuota = Number(card.querySelector("[data-edit-quota]")?.value || 5);
  if (!nameZh) {
    showAlert("中文名称不能为空。");
    return;
  }
  const patch = { name_zh: nameZh };
  if (nameEn !== undefined) patch.name_en = nameEn;
  if (aliases !== undefined) patch.aliases = aliases;
  if (dailyQuota > 0) patch.daily_quota = dailyQuota;
  try {
    showAlert("正在保存主题修改...");
    const updated = await apiJson(`/topics/${encodeURIComponent(topicId)}`, {
      method: "PATCH",
      headers: { "Idempotency-Key": `web-topic-edit-${topicId}-${Date.now()}` },
      body: JSON.stringify(patch),
    });
    state.topics = state.topics.map((item) => (item.id === topicId ? { ...updated, id: topicId } : item));
    state.editingTopicId = null;
    showAlert(`主题「${topicName(updated)}」已更新。`);
    renderTopics();
    renderTopicFilter();
    renderTopicQuotaGrid();
  } catch (error) {
    showAlert(`保存主题修改失败：${error.message}`);
  }
}

function openTopicPapers(topicId) {
  const topic = state.topics.find((item) => item.id === topicId);
  const filter = document.getElementById("topicFilter");
  if (filter && topic) {
    filter.value = topicName(topic);
  }
  switchView("papers");
}

async function loadTopicDigest(topicId) {
  const date = document.getElementById("dateFilter")?.value || today;
  if (state.selectedTopicDigest && state.selectedTopicDigest.topicId === topicId) {
    // Toggle closed.
    state.selectedTopicDigest = null;
    renderTopics();
    return;
  }
  state.selectedTopicDigest = { topicId, loading: true, note: "" };
  renderTopics();
  const note = await fetchTopicDigestNote(topicId, date);
  try {
    const digest = await apiJson(`/topics/${encodeURIComponent(topicId)}/digest?date=${encodeURIComponent(date)}`);
    state.selectedTopicDigest = { topicId, digest, note };
  } catch (error) {
    state.selectedTopicDigest = { topicId, error: error.message, note };
  }
  renderTopics();
  scrollToDigestPanel();
}

async function fetchTopicDigestNote(topicId, date) {
  try {
    const note = await apiJson(`/topics/${encodeURIComponent(topicId)}/digest-note?date=${encodeURIComponent(date)}`);
    return note.body || "";
  } catch (_) {
    return "";
  }
}

async function saveTopicDigestNote(topicId) {
  const date = document.getElementById("dateFilter")?.value || today;
  const noteId = `topic-note-${String(topicId).replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  const textarea = document.getElementById(noteId);
  const body = textarea ? textarea.value : "";
  try {
    await apiJson(`/topics/${encodeURIComponent(topicId)}/digest-note?date=${encodeURIComponent(date)}`, {
      method: "PUT",
      headers: { "Idempotency-Key": `web-topic-note-${topicId}-${Date.now()}` },
      body: JSON.stringify({ body }),
    });
    if (state.selectedTopicDigest) state.selectedTopicDigest.note = body;
    showAlert("主题摘要已保存。");
  } catch (error) {
    showAlert(`保存失败：${error.message}`);
  }
}

function renderInlineTopicDigest(topicId) {
  const selected = state.selectedTopicDigest;
  const date = document.getElementById("dateFilter")?.value || today;
  if (!selected || selected.loading) {
    return `<div class="topic-digest-inline">${emptyBlock("正在读取主题摘要...")}</div>`;
  }
  if (selected.error) {
    return `<div class="topic-digest-inline">${errorBlock(`主题摘要读取失败：${selected.error}`)}</div>`;
  }
  const digest = selected.digest;
  const noteId = `topic-note-${String(topicId).replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  const note = String(selected.note || "");
  return html`
    <div class="topic-digest-inline" data-topic-digest-inline="${topicId}">
      <div class="topic-digest-header">
        <span class="topic-digest-label">主题摘要</span>
        <h3>${topicDisplayName(topicId)} · ${digest.date}</h3>
      </div>
      <p class="meta">${digest.counts.papers} 篇 · ${digest.counts.source_hits} 次来源命中 · 去重 ${digest.counts.deduplicated} 条</p>
      <label class="topic-note-label">我的摘要笔记（可随时编辑并保存）
        <textarea id="${noteId}" class="topic-note-input" rows="4">${escapeHtml(note)}</textarea>
      </label>
      <div class="topic-note-actions">
        <button class="primary" type="button" data-topic-note-save="${topicId}">保存笔记</button>
      </div>
      <div class="compact-list">${raw((digest.papers || []).map((paper) => { const pid = getPaperId(paper); return html`<div class="compact-item"><button class="topic-paper-link" type="button" data-topic-paper="${pid}" title="查看论文详情"><strong>${paperTitle(paper)}</strong></button><span class="state ${String(paper.status).toLowerCase()}">${paper.status}</span></div>`; }).join(""))}</div>
    </div>
  `;
}

function renderTopicDigest() {
  // The digest now renders inline under each topic card via renderTopics; this
  // hook is retained for the legacy bottom panel and any external callers.
  const panel = document.getElementById("topicDigestPanel");
  if (!panel) return;
  const selected = state.selectedTopicDigest;
  panel.innerHTML = selected
    ? renderInlineTopicDigest(selected.topicId)
    : emptyBlock("点击某个主题的「查看主题摘要」可在其下方展开。");
}

function scrollToDigestPanel() {
  const selected = state.selectedTopicDigest;
  let target = document.querySelector(
    `[data-topic-digest-inline="${selected ? selected.topicId : ""}"]`
  );
  if (!target) {
    target = document.getElementById("topicDigestPanel");
  }
  if (!target) return;
  try {
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (_) {
    target.scrollIntoView();
  }
}

async function addTopic() {
  const nameZh = document.getElementById("newTopicZhInput")?.value.trim() || "";
  const nameEn = document.getElementById("newTopicEnInput")?.value.trim() || "";
  const aliases = (document.getElementById("newTopicAliasesInput")?.value || "")
    .split(",").map((item) => item.trim()).filter(Boolean);
  const dailyQuota = Number(document.getElementById("newTopicQuotaInput")?.value || 5);
  if (!nameZh) {
    showAlert("请填写主题中文名称。");
    return;
  }
  try {
    showAlert("正在添加主题...");
    const topic = await apiJson("/topics", {
      method: "POST",
      headers: { "Idempotency-Key": `web-add-topic-${Date.now()}` },
      body: JSON.stringify({
        name_zh: nameZh,
        name_en: nameEn,
        aliases,
        daily_quota: dailyQuota,
      }),
    });
    state.topics = [...state.topics.filter((item) => item.id !== topic.id), topic];
    document.getElementById("newTopicZhInput").value = "";
    document.getElementById("newTopicEnInput").value = "";
    document.getElementById("newTopicAliasesInput").value = "";
    showAlert(`主题「${topicName(topic)}」已添加。`);
    renderTopics();
    renderTopicFilter();
  } catch (error) {
    showAlert(`添加主题失败：${error.message}`);
  }
}

async function deleteTopic(topicId) {
  const topic = state.topics.find((item) => item.id === topicId);
  const label = topic ? topicName(topic) : topicId;
  if (!window.confirm(`确定要删除主题「${label}」吗？该主题将移出主题中心（关联的论文记录会保留），删除后暂不可恢复。`)) {
    return;
  }
  try {
    showAlert("正在删除主题...");
    await apiJson(`/topics/${encodeURIComponent(topicId)}`, { method: "DELETE" });
    state.topics = state.topics.filter((item) => item.id !== topicId);
    if (state.selectedTopicDigest && state.selectedTopicDigest.topicId === topicId) {
      state.selectedTopicDigest = null;
    }
    showAlert(`主题「${label}」已删除。`);
    renderTopics();
    renderTopicFilter();
  } catch (error) {
    showAlert(`删除主题失败：${error.message}`);
  }
}

function renderNotebookView() {
  const listContainer = document.getElementById("notebookPaperList");
  const digestContainer = document.getElementById("notebookDigest");
  const countEl = document.getElementById("notebookCount");
  if (!listContainer || !digestContainer) return;

  const notebookPaperIds = Array.from(state.notebookPapers);
  if (countEl) countEl.textContent = `${notebookPaperIds.length} 篇`;

  const notebookPapers = state.notebookItems.filter((paper) => state.notebookPapers.has(getPaperId(paper)));

  if (!notebookPapers.length) {
    listContainer.innerHTML = emptyBlock("笔记本为空。在论文库或仪表盘中点击「加入笔记本」来收藏论文。");
    digestContainer.innerHTML = emptyBlock("添加论文到笔记本后，这里会汇总展示研读摘要。");
    return;
  }

  listContainer.innerHTML = notebookPapers.map((paper) => {
    const id = getPaperId(paper);
    const workspace = state.workspaces.get(id);
    const technicalCards = normalizeList(workspace?.technical_cards, ["items"]);
    const cardId = `nb-card-${id}`;
    const bodyId = `nb-body-${id}`;
    const iconId = `nb-expand-${id}`;
    return html`
      <article class="paper-card" id="${cardId}">
        <div class="paper-card-header" data-toggle-nb="${id}">
          <div>
            <h3>${paperTitle(paper)}</h3>
            <p class="meta">${authorText(paper) || identifierText(paper)}</p>
          </div>
          <div class="paper-card-meta-actions">
            ${raw(normalizeTopics(paper).map((topic) => html`<span class="tag">${topic}</span>`).join(" "))}
            <span class="expand-icon" id="${iconId}">▸</span>
          </div>
        </div>
        <div class="paper-card-body hidden" id="${bodyId}">
          ${raw(paper.method_summary ? html`<p class="paper-one-liner"><strong>一句话摘要：</strong>${paper.method_summary}</p>` : "")}
          <p><strong>${paper.translated_abstract ? "中文摘要" : "中文摘要待生成"}：</strong>${paperAbstract(paper)}</p>
          ${raw(paper.abstract ? html`<details class="abstract-original"><summary>查看英文原摘要</summary><p>${paper.abstract}</p></details>` : "")}
          ${raw(workspace?.report ? html`
            <div class="notebook-report">
              <h4>研读报告</h4>
              ${raw(workspace.report.summary ? html`<p><strong>总结：</strong>${workspace.report.summary}</p>` : "")}
              ${raw(workspace.report.method ? html`<p><strong>方法：</strong>${workspace.report.method}</p>` : "")}
              ${raw(workspace.report.innovation ? html`<p><strong>创新点：</strong>${workspace.report.innovation}</p>` : "")}
              ${raw(workspace.report.engineering_value ? html`<p><strong>工程价值：</strong>${workspace.report.engineering_value}</p>` : "")}
            </div>
          ` : html`<p class="meta">尚未生成研读报告。请在阅读台中打开此论文并等待后端处理。</p>`)}
          ${raw(technicalCards.map((card) => html`
            <div class="notebook-report">
              <h4>技术解读</h4>
              ${raw(card.technical_problem ? html`<p><strong>问题：</strong>${card.technical_problem}</p>` : "")}
              ${raw(card.method ? html`<p><strong>方法：</strong>${card.method}</p>` : "")}
              ${raw(card.metrics ? html`<p><strong>指标：</strong>${card.metrics}</p>` : "")}
            </div>
          `).join(""))}
          <div class="paper-actions">
            <button class="secondary" type="button" data-open-paper="${id}">打开阅读台</button>
            <button class="secondary" type="button" data-notebook-remove="${id}">移出笔记本</button>
          </div>
        </div>
      </article>
    `;
  }).join("");

  listContainer.querySelectorAll("[data-toggle-nb]").forEach((header) => {
    header.addEventListener("click", () => {
      const paperId = header.dataset.toggleNb;
      const body = document.getElementById(`nb-body-${paperId}`);
      const icon = document.getElementById(`nb-expand-${paperId}`);
      if (body && icon) {
        body.classList.toggle("hidden");
        icon.classList.toggle("expanded", !body.classList.contains("hidden"));
      }
    });
  });
  listContainer.querySelectorAll("[data-open-paper]").forEach((button) => {
    button.addEventListener("click", () => openPaper(button.dataset.openPaper));
  });
  listContainer.querySelectorAll("[data-notebook-remove]").forEach((button) => {
    button.addEventListener("click", () => toggleNotebook(button.dataset.notebookRemove));
  });

  // Build digest summary
  const reports = notebookPapers
    .map((paper) => {
      const workspace = state.workspaces.get(getPaperId(paper));
      return { paper, workspace };
    })
    .filter(({ workspace }) => workspace?.report || normalizeList(workspace?.technical_cards, ["items"]).length);

  digestContainer.innerHTML = reports.length
    ? reports.map(({ paper, workspace }) => html`
      <div class="compact-item">
        <strong>${paperTitle(paper)}</strong>
        <span>${workspace.report?.summary?.slice(0, 100) || normalizeList(workspace.technical_cards, ["items"])[0]?.method?.slice(0, 100) || "已有技术解读"}...</span>
      </div>
    `).join("")
    : emptyBlock("笔记本中的论文尚未生成研读报告。在阅读台中打开论文即可触发后端分析。");
}

function renderJobs() {
  const container = document.getElementById("jobList");
  // Always show the pipeline timeline on top so the user can see what each
  // discovery run is actually doing (发现→下载→解析→研读→翻译).
  const pipelineHtml = renderPipelineRuns();
  if (!endpointOk("jobs")) {
    container.innerHTML = pipelineHtml + errorBlock("任务接口不可用。");
    return;
  }
  if (!state.jobs.length) {
    container.innerHTML = pipelineHtml + emptyBlock("暂无细化任务记录。");
    return;
  }
  const jobRows = state.jobs.map((job) => {
    const status = job.status || "unknown";
    const actionError = state.jobActionErrors.get(job.id);
    return html`
      <div class="job-row">
        <div>
          <strong>${job.kind || job.id || "任务"}</strong>
          <p class="meta">${job.id || job.job_id || ""}</p>
        </div>
        <span class="state ${String(status).toLowerCase()}">${jobStatusLabel(status)}</span>
        <span>${job.updated_at || job.created_at || ""}</span>
        <div>
          <p class="meta">${jobLlmErrorSummary(job) || jsonSummary(job.error) || jsonSummary(job.result) || job.target_id || "无错误信息"}</p>
          ${raw(actionError ? html`<p class="error inline-error">${actionError}</p>` : "")}
        </div>
        <div class="job-actions">
          ${raw(canRetryJob(job) ? html`<button class="secondary compact-action" type="button" data-job-action="retry" data-job-id="${job.id || job.job_id}">重试</button>` : "")}
          ${raw(canCancelJob(job) ? html`<button class="danger-secondary compact-action" type="button" data-job-action="cancel" data-job-id="${job.id || job.job_id}">取消</button>` : "")}
        </div>
      </div>
    `;
  }).join("");
  container.innerHTML = pipelineHtml
    + html`<h3 class="daily-history-title">全部异步任务（按最近更新倒序）</h3>`
    + jobRows;
  container.querySelectorAll("[data-job-action]").forEach((button) => {
    button.addEventListener("click", () => runJobAction(button.dataset.jobId, button.dataset.jobAction));
  });
  container.querySelectorAll("[data-run-toggle]").forEach((button) => {
    button.addEventListener("click", (event) => {
      const guard = state.activeView === "jobs";
      if (!guard) return;
      const runId = button.dataset.runToggle;
      toggleRunExpanded(runId);
      event.stopPropagation();
    });
  });
  // 点击整张工作流卡片也可展开/收起详细进程，无需只点小按钮。
  container.querySelectorAll("[data-card-toggle]").forEach((card) => {
    card.addEventListener("click", (event) => {
      const guard = state.activeView === "jobs";
      if (!guard) return;
      if (event.target.closest("button, a, input, select, textarea, label")) return;
      toggleRunExpanded(card.dataset.cardToggle);
    });
  });
}

function toggleRunExpanded(runId) {
  if (state.expandedRuns.has(runId)) state.expandedRuns.delete(runId);
  else state.expandedRuns.add(runId);
  renderJobs();
}

// ---- Pipeline run timeline -------------------------------------------
// Show the mid-flight progress of each discovery run (发现 → 下载 → 解析 →
// 研读 → 翻译) so the user can see what the worker is actually doing instead
// of only a flat list of jobs. Data comes from the run snapshots returned by
// /workflows (state.workflows.runs).
const PIPELINE_STEPS = [
  ["discover", "论文发现"],
  ["download", "PDF 下载"],
  ["parse", "文档解析"],
  ["analyze", "LLM 研读"],
  ["translate", "中文翻译"],
];

function renderPipelineRuns() {
  const runs = normalizeList(state.workflows?.runs, ["items", "runs"]);
  if (!runs.length) {
    return emptyBlock("还没有发现任务的流水线记录；点击「触发发现」后会在此展示每一步进展。");
  }
  const recent = runs.slice(0, 5);
  const cards = recent.map((run) => {
    const runId = run.id || run.run_id || JSON.stringify(run.created_at || "run");
    const expanded = state.expandedRuns.has(runId);
    const steps = run.steps || {};
    const runStatus = jobStatusLabel(run.status) || run.status;
    const started = String(run.created_at || "").slice(0, 19).replace("T", " ");
    const seen = run.input_counts?.papers_seen ?? "–";
    const persisted = run.output_counts?.papers_persisted ?? 0;
    const flow = PIPELINE_STEPS.map(([kind, label]) => {
      const counts = steps[kind] || {};
      const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
      const failed = (counts.retryable_failed || 0) + (counts.terminal_failed || 0) + (counts.failed || 0);
      const running = (counts.running || 0) + (counts.queued || 0) + (counts.processing || 0);
      const succeeded = (counts.succeeded || 0) + (counts.partial_succeeded || 0);
      let stateClass = "waiting";
      let badge = total ? `${total} 个任务` : "未开始";
      const done = succeeded + failed;
      // 动态进度：已完成/总数（如 3/94），进行中或失败也分别标注。
      const progress = total ? `${done}/${total}` : "";
      const progressTip = total ? `已完成 ${done} / 共 ${total}（${succeeded} 成功 · ${failed} 失败 · ${running} 进行中）` : "";
      if (running) { stateClass = "running"; badge = progress ? `${progress} 进行中` : `${running} 个进行中`; }
      else if (failed) { stateClass = "failed"; badge = progress ? `${progress} 完成·含失败` : `${failed} 个失败`; }
      else if (succeeded) { stateClass = "succeeded"; badge = progress ? `${progress} 完成` : total ? `${succeeded} 个完成` : "0 个"; }
      const icon = stateClass === "running" ? "↻" : stateClass === "failed" ? "!" : stateClass === "succeeded" ? "✓" : "·";
      return html`
        <div class="pipeline-step ${stateClass}">
          <span class="gate-icon">${icon}</span>
          <div><strong>${label}</strong><p class="meta" title="${progressTip}">${badge}</p></div>
        </div>
      `;
    }).join("");
    const errors = summarizeRunErrors(run);
    const jobs = run.jobs || [];
    const runTokens = tokenSummary(run.tokens && run.tokens.total_tokens ? { total: run.tokens.total_tokens } : null);
    const toggleLabel = expanded ? "收起条目" : `展开条目 (${jobs.length})`;
    return html`
      <article class="pipeline-run-card ${expanded ? "expanded" : ""}" data-card-toggle="${runId}" data-expanded="${expanded ? "1" : "0"}">
        <div class="pipeline-run-head" data-card-toggle-head="${runId}">
          <div><strong>${run.run_type || "每日论文研读"}</strong><span class="meta"> ${started} · ${runId.slice(0, 8)}</span></div>
          <span class="pill">${seen} 篇发现 · ${persisted} 篇入库</span>
          ${raw(runTokens ? html`<span class="pill token-pill" title="本流水线消耗的 LLM token 总量">⚡ ${runTokens}</span>` : "")}
          <span class="state ${String(run.status).toLowerCase()}">${runStatus}</span>
          <span class="pipeline-expand-caret" aria-hidden="true">${expanded ? "▾" : "▸"}</span>
        </div>
        <div class="pipeline-flow">${raw(flow)}</div>
        ${raw(errors.length ? html`<div class="pipeline-errors">${raw(errors)}</div>` : "")}
        <button class="secondary compact-action pipeline-run-toggle" type="button" data-run-toggle="${runId}" data-expanded="${expanded ? "1" : "0"}">${toggleLabel}</button>
        ${raw(expanded ? html`<div class="pipeline-run-detail">${renderRunJobDetail(run, jobs)}</div>` : "")}
      </article>
    `;
  }).join("");
  return html`
    <h3 class="daily-history-title">发现流水线（最新 ${recent.length} 次）</h3>
    ${raw(cards)}
  `;
}

function paperIdToTitleMap() {
  // Build a lookup from paper id / paper_version id to a display title so job
  // targets can be shown as readable paper titles instead of raw ids.
  const map = new Map();
  const all = [...(state.papers || []), ...(state.notebookItems || [])];
  all.forEach((paper) => {
    const title = paperTitle(paper);
    const pid = getPaperId(paper);
    const vid = getVersionId(paper);
    if (pid) map.set(`paper:${pid}`, title);
    if (vid) map.set(`version:${vid}`, title);
    if (pid) map.set(pid, title);
    if (vid) map.set(vid, title);
  });
  return map;
}

function jobTargetLabel(job, titleMap) {
  const targetType = job.target_type || "";
  const targetId = job.target_id || "";
  if (!targetId) return "";
  const title = titleMap.get(`version:${targetId}`) || titleMap.get(targetId) || titleMap.get(`paper:${targetId}`);
  if (title) return title;
  if (targetType) {
    const kindLabel = jobTargetKindLabel(targetType);
    return `${kindLabel} ${targetId.slice(0, 20)}`;
  }
  return targetId.slice(0, 24);
}

function jobTargetKindLabel(targetType) {
  return {
    paper_version: "论文版本",
    paper: "论文",
    discovery_run: "发现任务",
    invention_candidate: "专利候选",
    patent_draft: "交底书",
  }[targetType] || targetType || "目标";
}

function renderRunJobDetail(run, jobs) {
  const titleMap = paperIdToTitleMap();
  if (!jobs.length) {
    return html`<p class="meta">该流水线暂无关联任务条目。</p>`;
  }
  // 按任务类型分组，避免 50+ 行平铺堆叠，改为每个阶段一个小节，
  // 每节含小节标题 + 汇总（总数/进行中/成功/失败）+ 紧凑表格行。
  const KIND_ORDER = ["discover", "download", "parse", "analyze", "translate", "relate", "patent_draft"];
  const groups = new Map();
  for (const job of jobs) {
    const kind = job.kind || "other";
    if (!groups.has(kind)) groups.set(kind, []);
    groups.get(kind).push(job);
  }
  const ordered = Array.from(groups.keys()).sort(
    (a, b) => (KIND_ORDER.indexOf(a) === -1 ? 999 : KIND_ORDER.indexOf(a)) - (KIND_ORDER.indexOf(b) === -1 ? 999 : KIND_ORDER.indexOf(b))
  );
  const sections = ordered.map((kind) => {
    const groupJobs = groups.get(kind);
    let running = 0, failed = 0, succeeded = 0;
    groupJobs.forEach((job) => {
      const st = String(job.status || "").toLowerCase();
      if (["running", "queued", "processing"].includes(st)) running += 1;
      else if (["retryable_failed", "terminal_failed", "failed", "error"].includes(st)) failed += 1;
      else if (["succeeded", "partial_succeeded"].includes(st)) succeeded += 1;
    });
    const summary = `${groupJobs.length} 个 · ${succeeded} 成功 · ${running} 进行中${failed ? ` · ${failed} 失败` : ""}`;
    const rows = groupJobs.map((job) => {
      const status = String(job.status || "queued").toLowerCase();
      const err = job.error || {};
      const res = job.result || {};
      const diag = err.llm_analysis && err.llm_analysis.reason ? err.llm_analysis : null;
      const message = diag ? diag.reason : (err.message || err.analysis_error || res.message || "");
      const jobTokens = jobTokensFor(job);
      return html`
        <div class="pipeline-job-row">
          <span class="tag pipeline-job-kind">${jobKindLabel(job.kind)}</span>
          <div class="pipeline-job-main">
            <div class="pipeline-job-title">${jobTargetLabel(job, titleMap) || job.id || ""}<span class="meta"> ${job.id ? job.id.slice(0, 10) : ""}</span></div>
            <p class="meta pipeline-job-note">${raw(message ? html`<span class="pipeline-job-message ${status}">${message}</span>` : "")}${raw(diag && diag.suggestion ? html`<span class="pipeline-error-suggestion">建议：${diag.suggestion}</span>` : "")}</p>
          </div>
          ${raw(jobTokens ? html`<span class="pill token-pill job-token" title="本任务消耗的 LLM token">⚡ ${formatTokens(jobTokens.completion)}</span>` : "")}
          <span class="state ${status}">${jobStatusLabel(job.status)}</span>
          <span class="meta pipeline-job-time">${String(job.updated_at || job.created_at || "").slice(5, 19).replace("T", " ")}</span>
        </div>
      `;
    }).join("");
    return html`
      <div class="pipeline-job-group">
        <div class="pipeline-job-group-head">
          <strong>${jobKindLabel(kind)}</strong>
          <span class="meta">${summary}</span>
        </div>
        ${raw(rows)}
      </div>
    `;
  }).join("");
  return raw(html`
    <div class="pipeline-job-list">
      <div class="pipeline-job-list-head"><strong>关联任务明细（${jobs.length}）</strong></div>
      ${raw(sections)}
    </div>
  `);
}

function summarizeRunErrors(run) {
  // Collect distinct failure/degradation reasons from this run's tagged jobs so
  // the user can see why a step did not complete (e.g. "LLM 未配置") instead of
  // guessing. When a job carries a structured LLM diagnosis (error.llm_analysis),
  // we surface its reason/detail/suggestion first since it is the most useful
  // explanation of what went wrong and how to fix it. Success-only messages are
  // skipped.
  const seen = [];
  const lines = [];
  const push = (kind, reason, detail, suggestion) => {
    if (!reason) return;
    const key = `${kind}::${reason}`;
    if (seen.includes(key)) return;
    seen.push(key);
    if (lines.length >= 5) return;
    lines.push(html`<p class="meta pipeline-error-line"><strong>${jobKindLabel(kind)}：</strong>${reason}${raw(detail ? html`<span class="pipeline-error-detail"> — ${detail}</span>` : "")}${raw(suggestion ? html`<span class="pipeline-error-suggestion"> 建议：${suggestion}</span>` : "")}</p>`);
  };
  for (const job of (run.jobs || [])) {
    const status = String(job.status || "").toLowerCase();
    const isFailed = ["retryable_failed", "terminal_failed", "failed", "error", "degraded"].includes(status);
    if (!isFailed) continue;
    const err = job.error || {};
    const res = job.result || {};
    const diag = err.llm_analysis && err.llm_analysis.reason ? err.llm_analysis : null;
    if (diag) {
      push(job.kind, diag.reason, diag.detail, diag.suggestion);
      // Fall through: still record the raw cause when it differs.
    }
    if (diag && err.message) continue;
    // discover jobs keep an ordered list of per-source problems in error.errors.
    const errs = Array.isArray(err.errors) ? err.errors : null;
    if (errs && errs.length) {
      errs.forEach((item) => {
        const reason = item.message || item.error || "";
        const srcLabel = item.source ? `${item.source} ` : "";
        push(job.kind, `${srcLabel}${reason}`.trim());
      });
      continue;
    }
    push(job.kind, err.message || res.message || "");
  }
  return lines.join("");
}

function jobLlmErrorSummary(job) {
  // Prefer the LLM-generated failure diagnosis when available so the operator
  // immediately sees why this job failed and how to fix it.
  const err = job?.error || job?.result || {};
  const diag = err.llm_analysis;
  if (!diag) return "";
  if (diag.reason) {
    const extra = diag.suggestion ? ` 建议：${diag.suggestion}` : (diag.detail ? ` 详情：${diag.detail}` : "");
    return `${diag.reason}${extra}`;
  }
  if (diag.analysis_error) return `诊断失败：${diag.analysis_error}`;
  return "";
}

function formatTokens(count) {
  if (!count && count !== 0) return "";
  return Number(count).toLocaleString("en-US");
}

function jobTokensFor(job) {
  // Extract {prompt, completion, total} token usage from a job result wherever
  // the adapter recorded it (top-level usage or nested response.usage).
  const res = job?.result || {};
  let usage = res.usage;
  if (!usage && res.response && typeof res.response === "object") usage = res.response.usage;
  if (!usage || typeof usage !== "object") return null;
  const pick = (k) => (typeof usage[k] === "number" ? usage[k] : 0);
  return { prompt: pick("prompt_tokens"), completion: pick("completion_tokens"), total: pick("total_tokens") };
}

function tokenSummary(tokens) {
  if (!tokens || !tokens.total) return "";
  return `${formatTokens(tokens.total)} tokens`;
}

// 每篇论文的阶段完成标签：下载→解析→翻译→研读。基于论文的 status 与
// 已填充字段（translated_abstract / method_summary / 研读报告）推断每个
// 阶段是否完成，供论文库卡片与阅读台列表展示。
function paperStageTags(paper) {
  const s = String(paper?.status || "").toLowerCase();
  const reached = (min) => ["downloaded", "parsed", "translated", "analyzed", "scored", "published", "rejected"].includes(s)
    && ["downloaded", "parsed", "translated", "analyzed", "scored", "published", "rejected"].indexOf(s) >= ["downloaded", "parsed", "translated", "analyzed", "scored", "published", "rejected"].indexOf(min);
  const hasDownload = !!(paper?.current_version?.pdf_url) || reached("downloaded");
  const hasParsed = reached("parsed") || !!(paper?.metadata && paper.metadata.parsed);
  const hasTranslated = !!(paper?.translated_abstract) || reached("translated");
  const hasAnalyzed = reached("analyzed") || hasReport(paper);
  const stages = [
    { key: "download", label: "PDF 下载", done: hasDownload },
    { key: "parse", label: "解析", done: hasParsed },
    { key: "translate", label: "摘要翻译", done: hasTranslated },
    { key: "analyze", label: "研读", done: hasAnalyzed },
  ];
  return stages;
}

function renderStageTags(paper) {
  const tags = paperStageTags(paper);
  return tags.map((t) => `<span class="stage-tag ${t.done ? "done" : "todo"}" title="${t.done ? "已完成" : "未完成"}">${t.done ? "✓" : "○"} ${t.label}</span>`).join("");
}

function renderRelations() {
  const container = document.getElementById("relationGraph");
  if (!endpointOk("papers")) {
    container.innerHTML = errorBlock("论文接口不可用，无法读取关系。");
    return;
  }
  const header = document.getElementById("relationsHeader");
  if (state.relationsLoading) {
    container.innerHTML = loadingBlock("正在读取论文关系...");
    return;
  }
  if (state.relationsError) {
    container.innerHTML = errorBlock(`关系读取失败：${state.relationsError}`);
    return;
  }
  if (!state.relations) {
    // Lazy-load on first render so the view is always fresh/automatic.
    loadRelations();
    container.innerHTML = loadingBlock("正在读取论文关系...");
    return;
  }
  const items = normalizeList(state.relations, ["items"]);
  if (header) {
    header.innerHTML = html`
      <span class="pill">共 ${items.length} 条关系</span>
      <span class="pill">主题/关键词规则自动生成</span>
      ${raw(items.length ? html`<button class="secondary compact-action" type="button" id="relationsRebuildBtn">重建关系</button>` : "")}
    `;
  }
  if (!items.length) {
    container.innerHTML = html`
      <div class="relations-empty">
        <p>当前没有已生成的论文关系。</p>
        <p class="meta">点击下方按钮可基于共同主题与技术关键词，自动计算全部论文之间的关系。</p>
        <button class="primary" type="button" id="relationsRebuildBtn">自动重建/跑论文关系</button>
      </div>
    `;
  } else {
    const byType = {};
    items.forEach((r) => { const t = r.relation_type || "relation"; byType[t] = (byType[t] || 0) + 1; });
    const typeSummary = Object.entries(byType)
      .map(([type, count]) => html`<span class="tag">${type} ${count}</span>`).join(" ");
    // Sort by confidence descending and paginate so the view stays responsive
    // even when a full rebuild produces thousands of edges.
    const sorted = [...items].sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0));
    const limit = Math.max(1, Number(state.relationsLimit) || 60);
    const visible = sorted.slice(0, limit);
    const showMore = items.length > limit;
    container.innerHTML = html`
      <div class="relations-type-summary">${raw(typeSummary)}</div>
      <div class="relation-grid-inner">
        ${raw(visible.map((relation) => html`
          <article class="relation-card">
            <div class="relation-card-head">
              <span class="tag">${relation.relation_type || "relation"}</span>
              <span class="pill confidence-pill">置信度 ${(Number(relation.confidence) || 0).toFixed(2)}</span>
            </div>
            <h3 title="${relation.from_title || relation.from_paper_id || ""}">${relation.from_title || relation.from_paper_id || "来源论文"}</h3>
            <p class="relation-arrow">→ 关联到</p>
            <h3 class="relation-to" title="${relation.to_title || relation.to_paper_id || ""}">${relation.to_title || relation.to_paper_id || "目标论文"}</h3>
            <p>${relation.reason || relation.summary || relation.description || "无关系说明"}</p>
            <p class="meta">${normalizeList(relation.evidence, ["items"]).map(evidenceText).join("；")}</p>
          </article>
        `).join(""))}
      </div>
      ${raw(showMore ? html`<button class="secondary" type="button" id="relationsShowMoreBtn">显示更多（${items.length - limit} 条）</button>` : "")}
    `;
    const showMoreBtn = container.querySelector("#relationsShowMoreBtn");
    if (showMoreBtn) {
      showMoreBtn.addEventListener("click", () => {
        state.relationsLimit = (Number(state.relationsLimit) || 60) + 200;
        renderRelations();
      });
    }
  }
  const rebuildBtn = document.getElementById("relationsRebuildBtn");
  if (rebuildBtn) {
    rebuildBtn.addEventListener("click", () => runRelationsRebuild());
  }
}

async function loadRelations() {
  if (state.relationsLoading || state.relations) return;
  state.relationsLoading = true;
  state.relationsError = null;
  renderRelations();
  try {
    const payload = await apiJson("/relations");
    state.relations = normalizeList(payload, ["items"]);
  } catch (error) {
    state.relationsError = error.message;
    state.relations = [];
  }
  state.relationsLoading = false;
  renderRelations();
}

async function runRelationsRebuild() {
  const container = document.getElementById("relationGraph");
  const btn = document.getElementById("relationsRebuildBtn");
  if (btn) btn.disabled = true;
  showAlert("正在重建论文关系...");
  container.innerHTML = loadingBlock("正在计算论文关系（基于共同主题与关键词）...");
  try {
    await apiJson("/relations/rebuild", { method: "POST" });
    state.relations = null; // force refetch
    await loadRelations();
    showAlert("论文关系已重建。");
  } catch (error) {
    container.innerHTML = errorBlock(`关系重建失败：${error.message}`);
    showAlert(`关系重建失败：${error.message}`);
  }
  if (btn) btn.disabled = false;
}

function renderPatentWorkspace() {
  renderCandidatePicker();
  renderGates();
  renderDraftPreview();
}

function renderCandidatePicker() {
  const container = document.getElementById("candidatePicker");
  if (!endpointOk("papers")) {
    container.innerHTML = errorBlock("论文接口不可用，无法选择专利候选输入。");
    return;
  }
  if (!state.papers.length) {
    container.innerHTML = emptyBlock("没有可选择论文。");
    return;
  }
  container.innerHTML = state.papers.map((paper) => {
    const id = getPaperId(paper);
    return html`
      <label class="candidate-row">
        <input type="checkbox" data-patent-checkbox="${id}" ${state.selectedForPatent.has(id) ? "checked" : ""}>
        <span><strong>${paperTitle(paper)}</strong><span class="meta">${getVersionId(paper) || "无版本 ID"} · ${normalizeTopics(paper).join(" / ") || "未标主题"}</span></span>
      </label>
    `;
  }).join("");
  container.querySelectorAll("[data-patent-checkbox]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => togglePatentSelection(checkbox.dataset.patentCheckbox, checkbox.checked));
  });
}

function renderGates() {
  const results = gates.map((gate) => ({ ...gate, pass: Boolean(gate.check()) }));
  document.getElementById("selectionCount").textContent = `${state.selectedForPatent.size} / 5`;
  document.getElementById("gateChecklist").innerHTML = results.map((gate) => html`
    <div class="gate-item ${gate.pass ? "pass" : "warn"}">
      <span class="gate-icon">${gate.pass ? "✓" : "!"}</span>
      <div><strong>${gate.label}</strong><p class="meta">${gate.pass ? "通过" : gate.advisory ? "人工审批在候选创建后执行" : "待补足"}</p></div>
    </div>
  `).join("");
  document.getElementById("createCandidateButton").disabled = !results.filter((gate) => !gate.advisory).every((gate) => gate.pass);
}

function renderDraftPreview() {
  const preview = document.getElementById("draftPreview");
  const mdButton = document.getElementById("downloadDraftButton");
  const docxButton = document.getElementById("downloadDraftDocxButton");
  const selectedCandidate = state.candidates.find((candidate) => candidate.id === state.selectedCandidateId) || state.candidates[0] || null;
  const candidateDraft = selectedCandidate ? state.drafts.find((draft) => draft.invention_candidate_id === selectedCandidate.id) : null;
  const selectedDraftById = state.drafts.find((draft) => draft.id === state.selectedDraftId);
  const selectedDraft = selectedCandidate
    ? (selectedDraftById?.invention_candidate_id === selectedCandidate.id ? selectedDraftById : candidateDraft)
    : (selectedDraftById || state.drafts[0] || null);
  mdButton.disabled = !selectedDraft;
  if (docxButton) docxButton.disabled = !selectedDraft;
  if (selectedDraft) state.selectedDraftId = selectedDraft.id;
  if (!endpointOk("drafts") && !endpointOk("candidates")) {
    preview.innerHTML = errorBlock("专利候选和草稿接口不可用。");
    return;
  }
  const candidateHtml = state.candidates.length
    ? html`
      <h3>候选</h3>
      <div class="draft-list">
        ${raw(state.candidates.map(candidateCard).join(""))}
      </div>
    `
    : emptyBlock("暂无专利候选。");
  const stageShell = selectedCandidate
    ? html`
      <section class="draft-section">
        <h3>阶段审计</h3>
        <p class="meta">intake → candidate analysis → prior art → preview → builder → self-check</p>
        <div class="stage-timeline">${raw(stageTimeline(selectedCandidate.id))}</div>
      </section>
    `
    : "";
  const draftShell = selectedDraft
    ? html`
      <section class="draft-section">
        <h3>草稿：${selectedDraft.case_name || selectedDraft.id}</h3>
        <p class="meta">状态 ${selectedDraft.status} · ${selectedDraft.version_label || "v1"}</p>
        <div id="selectedDraftBody" class="markdown-body"></div>
      </section>
    `
    : emptyBlock(selectedCandidate ? "该候选尚未生成草稿；需先人工审批，再点击生成草稿。" : "暂无交底书草稿。");
  preview.innerHTML = html`${raw(candidateHtml)}${raw(stageShell)}${raw(draftShell)}`;
  if (selectedDraft) {
    document.getElementById("selectedDraftBody").textContent = selectedDraft.markdown || JSON.stringify(selectedDraft, null, 2);
  }
  preview.querySelectorAll("[data-candidate-select]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedCandidateId = button.dataset.candidateSelect;
      const draft = state.drafts.find((item) => item.invention_candidate_id === state.selectedCandidateId);
      state.selectedDraftId = draft?.id || null;
      renderPatentWorkspace();
      loadCandidateStages(state.selectedCandidateId);
    });
  });
  preview.querySelectorAll("[data-candidate-approve]").forEach((button) => {
    button.addEventListener("click", () => approveCandidate(button.dataset.candidateApprove, false));
  });
  preview.querySelectorAll("[data-candidate-override]").forEach((button) => {
    button.addEventListener("click", () => approveCandidate(button.dataset.candidateOverride, true));
  });
  preview.querySelectorAll("[data-candidate-prior-art]").forEach((button) => {
    button.addEventListener("click", () => runPriorArtCheck(button.dataset.candidatePriorArt));
  });
  preview.querySelectorAll("[data-candidate-draft]").forEach((button) => {
    button.addEventListener("click", () => generateDraft(button.dataset.candidateDraft));
  });
  preview.querySelectorAll("[data-draft-select]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDraftId = button.dataset.draftSelect;
      renderPatentWorkspace();
    });
  });
}

function candidateCard(candidate) {
  const status = String(candidate.status || "created").toLowerCase();
  const draft = state.drafts.find((item) => item.invention_candidate_id === candidate.id);
  const priorArt = priorArtJob(candidate.id);
  const priorArtSucceeded = priorArt?.status === "succeeded";
  const overrideId = `override-${candidate.id}`;
  return html`
    <article class="candidate-card ${candidate.id === state.selectedCandidateId ? "active" : ""}">
      <button class="link-button" type="button" data-candidate-select="${candidate.id}"><strong>${candidate.title || candidate.id}</strong></button>
      <span class="state ${status}">${status}</span>
      <p>${candidate.problem_statement || "未填写技术问题。"}</p>
      <p class="meta">查新状态：${priorArt ? `${priorArt.status} ${jsonSummary(priorArt.error) || jsonSummary(priorArt.result)}` : "尚未运行"}</p>
      <div class="paper-actions">
        <button class="secondary" type="button" data-candidate-prior-art="${candidate.id}">运行查新</button>
        <button class="secondary" type="button" data-candidate-approve="${candidate.id}" ${status === "approved" || !priorArtSucceeded ? "disabled" : ""}>普通审批通过</button>
        <button class="primary" type="button" data-candidate-draft="${candidate.id}" ${status !== "approved" ? "disabled" : ""}>生成草稿</button>
        ${raw(draft ? html`<button class="secondary" type="button" data-draft-select="${draft.id}">查看草稿</button>` : "")}
      </div>
      ${raw(status !== "approved" && !priorArtSucceeded ? html`
        <label class="override-box">
          查新未通过时的人工 override reason
          <textarea id="${overrideId}" rows="3" placeholder="必须说明为何在查新任务未成功时仍允许审批；会写入审批备注。"></textarea>
        </label>
        <button class="danger-secondary" type="button" data-candidate-override="${candidate.id}">带警告 override 审批</button>
      ` : "")}
      <p class="meta">${candidate.sources?.length || 0} 个来源 · 普通审批仅在查新任务成功后可用</p>
    </article>
  `;
}

function stageTimeline(candidateId) {
  const stageState = state.candidateStages.get(candidateId);
  if (!stageState || stageState.loading) return emptyBlock("正在读取阶段历史...");
  if (stageState.error) return errorBlock(`阶段历史读取失败：${stageState.error}`);
  if (!stageState.items.length) return emptyBlock("候选尚未进入可执行专利阶段。");
  return stageState.items.map((stage, index) => html`
    <article class="stage-card">
      <span class="stage-order">${index + 1}</span>
      <div>
        <strong>${patentStageLabel(stage.stage)}</strong>
        <p class="meta">${stage.started_at || stage.created_at || ""} ${stage.completed_at ? `→ ${stage.completed_at}` : ""}</p>
        <p>${jsonSummary(stage.output) || jsonSummary(stage.input) || "无阶段摘要"}</p>
      </div>
      <span class="state ${String(stage.status || "pending").toLowerCase()}">${jobStatusLabel(stage.status)}</span>
    </article>
  `).join("");
}

function patentStageLabel(stage) {
  return {
    intake: "材料接收",
    candidate_analysis: "候选分析",
    prior_art: "现有技术查新",
    preview: "人工预览确认",
    builder: "交底书生成",
    self_check: "交底书自检",
  }[stage] || stage || "未知阶段";
}

function renderSettings() {
  const config = state.runtimeConfig;
  const panel = document.getElementById("analysisSettingsPanel");
  if (config && panel) {
    const stamp = JSON.stringify(config);
    if (panel.dataset.configStamp !== stamp) {
      panel.dataset.configStamp = stamp;
      document.getElementById("analysisProviderInput").value = config.analysis?.provider || "openai";
      syncAnalysisProviderForm();
      document.getElementById("scheduleEnabledInput").checked = config.schedule?.enabled !== false;
      document.getElementById("scheduleTimezoneInput").value = config.schedule?.timezone || "Asia/Shanghai";
      document.getElementById("scheduleHourInput").value = config.schedule?.daily_hour ?? 9;
      document.getElementById("scheduleLookbackInput").value = config.schedule?.lookback_days ?? 7;
      document.getElementById("scheduleMaxResultsInput").value = config.schedule?.max_results ?? 5;
      document.getElementById("scheduleTranslateInput").checked = (config.schedule?.after_parse || []).includes("translate");
    }
  }
  document.getElementById("endpointList").innerHTML = html`
    <dt>API Base</dt><dd>${config?.platform?.api_base || API_BASE}</dd>
    <dt>访问模式</dt><dd>${config?.platform?.public_mode ? "公网只读 + 写操作鉴权" : "局域网同源模式"}</dd>
  `;
  const services = config?.services || {};
  document.getElementById("managedServiceList").innerHTML = html`
    <dt>MinerU</dt><dd>服务器托管 · ${services.mineru?.api_key_configured ? "含服务凭证" : "无需凭证"}</dd>
    <dt>专利查新</dt><dd>${services.prior_art?.mode === "remote" ? "远程服务 + 本地 CNIPA 回退" : "本地 CNIPA 工具"}</dd>
    <dt>专利导出</dt><dd>服务器内置 Markdown / DOCX 工具</dd>
  `;
  document.getElementById("capabilityList").innerHTML = Object.entries(state.endpointResults)
    .map(([key, result]) => html`
      <div class="gate-item ${result?.ok ? "pass" : "warn"}">
        <span class="gate-icon">${result?.ok ? "✓" : "!"}</span>
        <div><strong>${key}</strong><p class="meta">${result?.ok ? `使用 ${result.path}` : (result?.errors || ["未探测"]).join("；")}</p></div>
      </div>
    `)
    .join("");
  renderTopicQuotaGrid();
  renderAdapterHealth("settingsAdapterHealth");
}

function renderTopicQuotaGrid() {
  const grid = document.getElementById("topicQuotaGrid");
  if (!grid) return;
  const topics = state.topics.filter((topic) => topic.deleted_at == null);
  if (!topics.length) {
    grid.innerHTML = emptyBlock("暂无主题配置。");
    return;
  }
  grid.innerHTML = topics.map((topic) => html`
    <label class="topic-quota-item">
      <span>${topicName(topic)}</span>
      <input type="number" min="1" max="500" data-topic-quota="${topic.id}" value="${Number(topic.daily_quota) || 5}">
    </label>
  `).join("");
}

async function saveTopicQuota() {
  const inputs = Array.from(document.querySelectorAll("[data-topic-quota]"));
  if (!inputs.length) return;
  try {
    showAlert("正在保存主题展示条数...");
    for (const input of inputs) {
      const topicId = input.dataset.topicQuota;
      const value = Math.max(1, Number(input.value) || 5);
      await apiJson(`/topics/${encodeURIComponent(topicId)}`, {
        method: "PATCH",
        headers: { "Idempotency-Key": `web-topic-quota-${topicId}-${value}` },
        body: JSON.stringify({ daily_quota: value }),
      });
    }
    showAlert("主题展示条数已保存。");
    await loadAll();
  } catch (error) {
    showAlert(`保存主题条数失败：${error.message}`);
  }
}

function renderWorkflows() {
  const catalog = document.getElementById("workflowCatalog");
  const runs = document.getElementById("workflowRuns");
  if (!catalog || !runs) return;
  const result = state.endpointResults.workflows;
  if (!result?.ok) {
    catalog.innerHTML = errorBlock("工作流接口不可用。");
    runs.innerHTML = emptyBlock("暂无可追踪运行。");
    return;
  }
  const definitions = normalizeList(state.workflows, ["items", "workflows"]);
  catalog.innerHTML = definitions.length ? definitions.map((workflow) => html`
    <section class="panel workflow-card">
      <div class="panel-heading">
        <div><h2>${workflow.name}</h2><p>${workflow.description}</p></div>
        <span class="state ${workflow.enabled ? "success" : "retryable_failed"}">${workflow.enabled ? "可运行" : "待配置"}</span>
      </div>
      <div class="workflow-dag">
        ${raw((workflow.nodes || []).map((node, index) => html`
          <div class="workflow-node ${node.enabled ? "enabled" : "disabled"}">
            <span class="stage-order">${index + 1}</span>
            <strong>${node.label}</strong>
            <span class="meta">${node.optional ? "可选 · " : ""}${workflowNodeSummary(node)}</span>
          </div>
          ${raw(index < workflow.nodes.length - 1 ? '<span class="workflow-arrow" aria-hidden="true">→</span>' : "")}
        `).join(""))}
      </div>
      ${raw(workflow.schedule ? html`<p class="meta workflow-schedule">每日 ${workflow.schedule.daily_hour}:00 · ${workflow.schedule.timezone} · 回看 ${workflow.schedule.lookback_days} 天 · 最多 ${workflow.schedule.max_results} 篇/主题</p>` : "")}
    </section>
  `).join("") : emptyBlock("后端未返回内置工作流。");
  const history = normalizeList(state.workflows?.runs, ["items", "runs"]);
  runs.innerHTML = history.length ? history.map((run) => html`
    <article class="workflow-run">
      <div><strong>${run.run_type || run.id}</strong><p class="meta">${run.created_at || ""} · ${run.id}</p></div>
      <div class="workflow-step-counts">${raw(Object.entries(run.steps || {}).map(([kind, counts]) => html`<span class="tag">${jobKindLabel(kind)} ${Object.values(counts).reduce((sum, value) => sum + value, 0)}</span>`).join(""))}</div>
      <span class="state ${String(run.status || "queued").toLowerCase()}">${jobStatusLabel(run.status)}</span>
    </article>
  `).join("") : emptyBlock("尚无工作流运行；触发论文发现后会自动创建。 ");
}

function workflowNodeSummary(node) {
  const total = Object.values(node.job_counts || {}).reduce((sum, value) => sum + value, 0);
  if (!node.enabled) return "服务未配置";
  return total ? `${total} 个任务` : "已就绪";
}

function renderAdapterHealth(containerId) {
  const container = document.getElementById(containerId);
  const result = state.endpointResults.adapterHealth;
  if (!container) return;
  if (!result?.ok) {
    container.innerHTML = errorBlock(containerId === "settingsAdapterHealth" ? "适配器健康接口不可用，内部适配器状态未知。" : "系统能力健康接口不可用。");
    return;
  }
  const adapters = normalizeList(result.data, ["adapters", "integrations", "services", "items"]);
  if (containerId !== "settingsAdapterHealth") {
    const okCount = adapters.filter(adapterHealthy).length;
    container.innerHTML = adapters.length
      ? html`
        <div class="gate-item ${okCount === adapters.length ? "pass" : "warn"}">
          <span class="gate-icon">${okCount === adapters.length ? "✓" : "!"}</span>
          <div><strong>系统能力摘要</strong><p class="meta">${okCount}/${adapters.length} 项后端能力可用；管理员可在设置页查看适配器明细。</p></div>
        </div>
      `
      : emptyBlock("健康接口未返回系统能力明细。");
    return;
  }
  container.innerHTML = adapters.length ? adapters.map(adapterHealthCard).join("") : emptyBlock("健康接口未返回适配器明细。");
}

function filteredPapers() {
  const query = document.getElementById("globalSearch")?.value.trim().toLowerCase() || "";
  const topic = document.getElementById("topicFilter")?.value || "";
  const status = document.getElementById("statusFilter")?.value || "";
  const source = state.allPapers.length ? state.allPapers : state.papers;
  // 论文库默认展示全部日期论文；用主题 / 状态 / 搜索过滤即可。
  // 如需按日期浏览，可使用「日期」输入框通过 loadAll 重新加载特定日期。
  return source.filter((paper) => {
    const id = getPaperId(paper);
    const text = JSON.stringify(paper).toLowerCase();
    const topics = normalizeTopics(paper);
    const paperStatus = String(paper.status || "").toLowerCase();
    return (!query || text.includes(query))
      && (!topic || topics.includes(topic))
      && (!status || paperStatus.includes(status));
  });
}

function selectedPaper() {
  return knownPapers().find((paper) => getPaperId(paper) === state.selectedPaperId) || null;
}

function knownPapers() {
  const papers = new Map();
  [...state.papers, ...state.notebookItems].forEach((paper) => papers.set(getPaperId(paper), paper));
  return Array.from(papers.values());
}

function paperAbstract(paper) {
  if (paper?.translated_abstract) return paper.translated_abstract;
  if (paper?.abstract) return "中文摘要尚未生成。请在「设置」中保存真实 LLM 配置，系统会自动翻译。";
  return "暂无摘要";
}

function selectedWorkspace() {
  return state.workspaces.get(state.selectedPaperId) || null;
}

function selectedPapers() {
  return state.papers.filter((paper) => state.selectedForPatent.has(getPaperId(paper)));
}

function togglePatentSelection(id, explicit) {
  if (!id) return;
  const shouldSelect = explicit ?? !state.selectedForPatent.has(id);
  if (shouldSelect) {
    if (state.selectedForPatent.size >= 5 && !state.selectedForPatent.has(id)) {
      showAlert("最多选择 5 篇论文。");
      return;
    }
    state.selectedForPatent.add(id);
  } else {
    state.selectedForPatent.delete(id);
  }
  renderPatentWorkspace();
  renderPaperLibrary();
  renderDashboard();
}

async function toggleNotebook(paperId) {
  if (!paperId) return;
  const wasSelected = state.notebookPapers.has(paperId);
  const paper = knownPapers().find((item) => getPaperId(item) === paperId);
  if (wasSelected) {
    state.notebookPapers.delete(paperId);
    state.notebookItems = state.notebookItems.filter((item) => getPaperId(item) !== paperId);
  } else {
    state.notebookPapers.add(paperId);
    if (paper) state.notebookItems = [{ ...paper, selected: true }, ...state.notebookItems];
  }
  state.papers = state.papers.map((item) => getPaperId(item) === paperId ? { ...item, selected: !wasSelected } : item);
  renderDashboard();
  renderPaperLibrary();
  renderNotebookView();
  try {
    const saved = await apiJson(`/papers/${encodeURIComponent(paperId)}/select`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-notebook-${paperId}-${wasSelected ? "remove" : "add"}-${Date.now()}` },
      body: JSON.stringify({ selected: !wasSelected }),
    });
    if (!wasSelected) {
      state.notebookItems = [saved, ...state.notebookItems.filter((item) => getPaperId(item) !== paperId)];
      loadWorkspace(paperId);
      showAlert("已加入服务器笔记本。在左侧导航可查看相关解读。");
    } else {
      showAlert("已从服务器笔记本移除。");
    }
    renderNotebookView();
  } catch (error) {
    if (wasSelected) {
      state.notebookPapers.add(paperId);
      if (paper) state.notebookItems = [paper, ...state.notebookItems.filter((item) => getPaperId(item) !== paperId)];
    } else {
      state.notebookPapers.delete(paperId);
      state.notebookItems = state.notebookItems.filter((item) => getPaperId(item) !== paperId);
    }
    state.papers = state.papers.map((item) => getPaperId(item) === paperId ? { ...item, selected: wasSelected } : item);
    showAlert(`笔记本保存失败：${error.message}`);
    renderDashboard();
    renderPaperLibrary();
    renderNotebookView();
  }
}

async function runDiscovery() {
  const selectedDate = document.getElementById("dateFilter").value || today;
  // 用回溯窗口而非严格单日：数据源（arXiv/HF 等）通常滞后一天，若只取当天
  // 发表的论文会被全量过滤导致"0 篇入库"。默认从所选日往前回溯 7 天，捕获
  // 最近发表（含 8-03 这类滞后一天）的论文并入库。
  const lookback = Math.max(1, Number(state.runtimeConfig?.schedule?.lookback_days) || 7);
  const startDate = addDays(selectedDate, -(lookback - 1));
  const windowStart = `${startDate}T00:00:00`;
  const windowEnd = `${selectedDate}T23:59:59`;
  const topicIds = state.topics.filter((topic) => topic.enabled !== false).slice(0, 6).map((topic) => topic.id);
  try {
    showAlert(`正在提交发现任务（回溯 ${lookback} 天）...`);
    await apiJson("/discovery-runs", {
      method: "POST",
      headers: { "Idempotency-Key": `web-discovery-${startDate}-${selectedDate}-${topicIds.join(".") || "all"}` },
      body: JSON.stringify({
        source: "multi",
        window_start: windowStart,
        window_end: windowEnd,
        topics: topicIds,
        max_results: 20,
        metadata: { trigger: "web", auto_process: true, hit_date: selectedDate, lookback_days: lookback },
      }),
    });
    showAlert("发现任务已提交，任务中心会显示真实执行状态。");
    await loadAll();
    // Jump to 任务中心 so the user sees the job progress live.
    switchView("jobs", true);
  } catch (error) {
    showAlert(`发现任务提交失败：${error.message}`);
  }
}

// ---- 使用说明弹层 ----------------------------------------
const HELP_SECTIONS = [
  {
    key: "read",
    title: "怎么读论文",
    steps: [
      "在左侧导航点「论文库」，用主题 / 状态 / 日期筛选，或用顶部搜索框按标题、作者、arXiv ID 查找。",
      "点任意论文进入「阅读台」，可切换 PDF / Markdown / 研读报告 / 证据四个标签页。",
      "「研读报告」由 LLM 自动生成，含摘要、动机、方法、实验、创新点、局限与复现计划。",
      "没有中文摘要时，到「设置」页确认已填写论文研读模型（Base URL + 模型名），系统会自动翻译摘要。",
      "把重要论文加入「笔记本」，方便随时回看与积累。",
    ],
  },
  {
    key: "patent",
    title: "怎么写专利",
    steps: [
      "在「论文库」勾选 2 到 5 篇有研读报告的论文，进入「专利候选」。",
      "填写候选信息：技术耦合、接口、数据/控制流、非并列说明、联合效果，并完成四项人工确认。",
      "系统先做现有技术查新（prior art），列出最接近的专利与学术记录。",
      "人工审批通过后，才能生成专利交底书。",
      "在「专利候选」里可预览、下载 Markdown / DOCX 草稿，并支持按章节修订。",
    ],
  },
  {
    key: "find",
    title: "怎么找论文（全文检索）",
    steps: [
      "顶部搜索框按标题、作者、主题、arXiv ID 过滤当前论文库。",
      "「关系视图」可展示论文间的相似、延伸、互补、冲突关系，帮助发现更广的关联。",
      "「主题中心」可按研究方向浏览已入库论文与每日摘要。",
    ],
  },
  {
    key: "discover-direction",
    title: "发现某个方向的论文",
    steps: [
      "到「主题中心」确认该方向已有主题（或新增一个主题，填写英文名、别名、arXiv 分类）。",
      "点顶栏「定向发现」，选择这个方向（主题），设定起止日期与每主题篇数，点「开始发现」。",
      "后台会从 arXiv/OpenAlex/OpenReview/HuggingFace 等来源抓取并并入论文库。",
    ],
  },
  {
    key: "discover-window",
    title: "发现特定方向、特定时间段的 N 篇论文",
    steps: [
      "点顶栏「定向发现」。",
      "研究方向：选中目标主题（可多选）。",
      "时间段：起始日期与结束日期即自然语言里的「最近一周 / 8 月 1 日到 8 月 4 日」。",
      "数量：在「每主题最多论文数」填 N。",
      "勾选「自动后续处理」可让系统自动下载、解析、研读、翻译，完成后在论文库与阅读台看到结果，任务中心可看到实时进展。",
    ],
  },
];

function renderHelpContent() {
  const content = HELP_SECTIONS.map((section) => html`
    <section class="help-section">
      <h3>${section.key === "read" ? "📖" : section.key === "patent" ? "🧾" : section.key === "find" ? "🔎" : section.key === "discover-direction" ? "🧭" : "📅"} ${section.title}</h3>
      <ol class="help-steps">
        ${raw(section.steps.map((step) => html`<li>${step}</li>`).join(""))}
      </ol>
    </section>
  `).join("");
  document.getElementById("helpContent").innerHTML = content;
}

function openHelp() {
  renderHelpContent();
  document.getElementById("helpOverlay").classList.remove("hidden");
  document.body.classList.add("has-overlay");
}

function closeHelp() {
  document.getElementById("helpOverlay").classList.add("hidden");
  document.body.classList.remove("has-overlay");
}

function openDirectedDiscovery() {
  const select = document.getElementById("directedTopic");
  select.innerHTML = html`<option value="">（全部启用主题）</option>` + state.topics.map((topic) => html`
    <option value="${topic.id}" ${topic.enabled === false ? "" : "selected"}>${topic.name_zh || topic.name_en || topic.id}</option>
  `).join("");
  document.getElementById("directedStartDate").value = document.getElementById("dateFilter").value || today;
  document.getElementById("directedEndDate").value = document.getElementById("dateFilter").value || today;
  document.getElementById("directedMaxResults").value = 20;
  document.getElementById("directedAutoProcess").checked = true;
  document.getElementById("directedDiscoveryOverlay").classList.remove("hidden");
  document.body.classList.add("has-overlay");
}

function closeDirectedDiscovery() {
  document.getElementById("directedDiscoveryOverlay").classList.add("hidden");
  document.body.classList.remove("has-overlay");
}

async function submitDirectedDiscovery(event) {
  event.preventDefault();
  const select = document.getElementById("directedTopic");
  const topicIds = Array.from(select.selectedOptions).map((option) => option.value).filter(Boolean);
  const start = document.getElementById("directedStartDate").value;
  const end = document.getElementById("directedEndDate").value;
  const maxResults = parseInt(document.getElementById("directedMaxResults").value, 10) || 20;
  const autoProcess = document.getElementById("directedAutoProcess").checked;
  if (!start && !end) {
    showAlert("请至少填写起始或结束日期。");
    return;
  }
  const payload = {
    source: "multi",
    topics: topicIds,
    max_results: Math.min(500, Math.max(1, maxResults)),
    metadata: { trigger: "web-directed", auto_process: autoProcess, hit_date: end || start },
  };
  if (start) payload.window_start = `${start}T00:00:00`;
  if (end) payload.window_end = `${end}T23:59:59`;
  const keyParts = [topicIds.join(".") || "all", start, end, maxResults];
  try {
    showAlert("正在提交定向发现任务...");
    await apiJson("/discovery-runs", {
      method: "POST",
      headers: { "Idempotency-Key": `web-directed-${keyParts.join("-")}` },
      body: JSON.stringify(payload),
    });
    closeDirectedDiscovery();
    showAlert("定向发现已提交，任务中心会显示真实执行状态与失败原因。");
    await loadAll();
    switchView("jobs", true);
  } catch (error) {
    showAlert(`定向发现提交失败：${error.message}`);
  }
}

function bindOverlays() {
  document.querySelectorAll(".overlay").forEach((overlay) => {
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        overlay.classList.add("hidden");
        document.body.classList.remove("has-overlay");
      }
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".overlay:not(.hidden)").forEach((overlay) => {
      overlay.classList.add("hidden");
    });
    document.body.classList.remove("has-overlay");
  });
}

async function materializePdf(versionId) {
  if (!versionId) return;
  try {
    showAlert("正在将论文 PDF 保存到局域网服务器...");
    const result = await apiJson(`/paper-versions/${encodeURIComponent(versionId)}/download`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-server-pdf-${versionId}` },
      body: JSON.stringify({ force: false, options: {} }),
    });
    const jobId = result.job_id || result.id;
    if (!jobId) throw new Error("后端未返回下载任务 ID");
    for (let attempt = 0; attempt < 15; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const job = await apiJson(`/jobs/${encodeURIComponent(jobId)}`);
      if (["succeeded", "partial_succeeded"].includes(job.status)) {
        state.workspaces.delete(state.selectedPaperId);
        showAlert("PDF 已保存到服务器，正在打开同源文档。 ");
        await loadWorkspace(state.selectedPaperId);
        return;
      }
      if (["retryable_failed", "terminal_failed", "cancelled"].includes(job.status)) {
        throw new Error(jsonSummary(job.error) || job.status);
      }
    }
    showAlert("服务器下载仍在进行，可在任务中心查看；完成后重新打开论文。 ");
    await loadAll();
  } catch (error) {
    showAlert(`服务器保存 PDF 失败：${error.message}`);
  }
}

async function createCandidate() {
  const papers = selectedPapers();
  const structured = candidateStructuredInput();
  const approval = candidateApprovalInput();
  if (!hasStructuredCandidateInput()) {
    showAlert("请先填写耦合、接口、数据/控制流、非并列说明和联合效果，每项至少 12 个字符。");
    return;
  }
  if (!hasApprovalConfirmations()) {
    showAlert("请完成四项人工确认。");
    return;
  }
  const title = buildCandidateTitle(papers);
  const topicOverride = state.topicOverrideNote ? `\n主题/关系门禁人工 override：${state.topicOverrideNote}` : "";
  const payload = {
    title,
    sources: papers.map((paper) => ({
      paper_id: getPaperId(paper),
      paper_version_id: getVersionId(paper),
      contribution: contributionForPaper(paper),
    })),
    problem_statement: `围绕 ${commonTopicText(papers)} 的论文组合存在可联合优化的工程约束，需要人工确认共同问题边界。`,
    integration_mechanism: [
      `耦合关系：${structured.coupling}`,
      `接口边界：${structured.interface}`,
      `数据/控制流：${structured.data_control_flow}`,
      `非并列说明：${structured.non_parallel_explanation}`,
      ...papers.map((paper) => contributionForPaper(paper)),
    ].join("；"),
    coupling_interface: `${structured.coupling}\n\n接口边界：${structured.interface}`,
    data_or_control_flow: structured.data_control_flow,
    why_not_juxtaposition: structured.non_parallel_explanation,
    expected_joint_effect: structured.joint_effect,
    technical_effects: structured.joint_effect,
    risk_notes: `需人工核验事实来源、保护范围、新颖性、创造性、侵权风险和论文许可边界。${topicOverride}`,
    structured_combination: structured,
    approval_confirmations: approval.confirmations,
    approver: approval.approver,
    evidence: candidateEvidence(papers, structured),
  };
  try {
    showAlert("正在创建专利候选...");
    const candidate = await apiJson("/invention-candidates", {
      method: "POST",
      headers: { "Idempotency-Key": candidateIdempotencyKey(payload) },
      body: JSON.stringify(payload),
    });
    state.candidates = [candidate, ...state.candidates.filter((item) => item.id !== candidate.id)];
    state.selectedCandidateId = candidate.id;
    state.candidateStages.delete(candidate.id);
    showAlert("专利候选已创建。下一步需要人工审批。");
    switchView("patents");
    renderPatentWorkspace();
  } catch (error) {
    showAlert(`专利候选创建失败：${error.message}`);
  }
}

async function runJobAction(jobId, action) {
  if (!jobId || !["retry", "cancel"].includes(action)) return;
  const reason = action === "retry" ? "Web UI retry requested by operator." : "Web UI cancel requested by operator.";
  try {
    state.jobActionErrors.delete(jobId);
    showAlert(action === "retry" ? "正在重试任务..." : "正在取消任务...");
    const job = await apiJson(`/jobs/${encodeURIComponent(jobId)}`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-job-${action}-${jobId}` },
      body: JSON.stringify({ action, reason }),
    });
    state.jobs = [job, ...state.jobs.filter((item) => (item.id || item.job_id) !== (job.id || job.job_id))];
    showAlert(action === "retry" ? "任务已重新排队。" : "任务已取消。");
    await loadAll();
  } catch (error) {
    state.jobActionErrors.set(jobId, error.message);
    showAlert(`${action === "retry" ? "重试" : "取消"}任务失败：${error.message}`);
    renderJobs();
  }
}

async function runPriorArtCheck(candidateId) {
  try {
    showAlert("正在提交查新任务...");
    const job = await apiJson(`/invention-candidates/${encodeURIComponent(candidateId)}/prior-art-check`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-prior-art-${candidateId}` },
    });
    state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)];
    showAlert("查新任务已提交，请在任务中心或候选卡片查看状态。");
    state.candidateStages.delete(candidateId);
    await loadAll();
  } catch (error) {
    showAlert(`查新任务提交失败：${error.message}`);
  }
}

async function approveCandidate(candidateId, override) {
  const job = priorArtJob(candidateId);
  const overrideReason = document.getElementById(`override-${candidateId}`)?.value.trim() || "";
  const approval = candidateApprovalInput();
  if (!override && job?.status !== "succeeded") {
    showAlert("普通审批被阻止：查新任务必须先成功。");
    return;
  }
  if (!hasApprovalConfirmations()) {
    showAlert("审批前请完成四项人工确认。");
    return;
  }
  if (override && overrideReason.length < 12) {
    showAlert("override 审批需要填写明确原因，至少 12 个字符。");
    return;
  }
  try {
    showAlert(override ? "正在提交带警告的 override 审批..." : "正在提交人工审批...");
    const candidate = await apiJson(`/invention-candidates/${encodeURIComponent(candidateId)}/approve`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-approve-${candidateId}-${override ? "override" : "normal"}` },
      body: JSON.stringify({
        approved: true,
        override_prior_art: override,
        approver: approval.approver,
        approval_confirmations: approval.confirmations,
        notes: override
          ? `WARNING: prior-art check did not succeed; approver ${approval.approver}; human override reason: ${overrideReason}`
          : `Web UI human approval gate confirmed by ${approval.approver} after prior-art check succeeded.`,
        override_reason: override ? overrideReason : "",
      }),
    });
    state.candidates = [candidate, ...state.candidates.filter((item) => item.id !== candidate.id)];
    state.selectedCandidateId = candidate.id;
    await loadCandidateStages(candidate.id, true);
    showAlert(override ? "override 审批已提交；生成草稿前请再次确认风险。" : "人工审批已通过，可以生成草稿。");
    renderPatentWorkspace();
  } catch (error) {
    showAlert(`审批失败：${error.message}`);
  }
}

async function generateDraft(candidateId) {
  const candidate = state.candidates.find((item) => item.id === candidateId);
  try {
    showAlert("正在生成专利交底书草稿...");
    const result = await apiJson(`/invention-candidates/${encodeURIComponent(candidateId)}/draft`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-draft-${candidateId}` },
      body: JSON.stringify({
        case_name: candidate?.title || "AI Infra 组合技术交底书",
        protection_focus: "保护由论文组合推导出的系统、方法、装置、存储介质，以及关键控制策略。",
        notes: "由 Web UI 在人工审批后触发。",
      }),
    });
    const draft = result.draft || result;
    state.drafts = [draft, ...state.drafts.filter((item) => item.id !== draft.id)];
    state.selectedDraftId = draft.id;
    await loadCandidateStages(candidateId, true);
    showAlert("草稿已生成，可下载 Markdown；DOCX 若后端未接入会显示失败原因。");
    renderPatentWorkspace();
  } catch (error) {
    showAlert(`草稿生成失败：${error.message}`);
  }
}

async function downloadSelectedDraft(format) {
  const draftId = state.selectedDraftId || state.drafts[0]?.id;
  if (!draftId) return;
  try {
    const url = `${API_BASE}/patent-drafts/${encodeURIComponent(draftId)}/export?format=${encodeURIComponent(format)}`;
    const response = await fetch(url);
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload?.error?.message || payload?.detail || message;
      } catch (_) {
        // Keep the HTTP status when the backend returns a non-JSON error.
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = exportFilename(response, format);
    link.click();
    URL.revokeObjectURL(link.href);
    showAlert(`${format.toUpperCase()} 导出已开始下载。`);
  } catch (error) {
    showAlert(`${format.toUpperCase()} 导出失败：${error.message}`);
  }
}

let _alertDismissTimer = null;

function dismissAlert(toast, btn) {
  if (toast) toast.classList.add("hidden");
  if (btn) btn.remove();
  if (_alertDismissTimer) {
    clearTimeout(_alertDismissTimer);
    _alertDismissTimer = null;
  }
}

// Transient user feedback shown in the dedicated #appToast, independent of the
// persistent degradation banner (#globalAlert) so a re-render can't wipe it.
function showAlert(message) {
  const toast = document.getElementById("appToast");
  if (!toast) {
    // Fallback: keep old behavior on the global alert element if missing.
    const alert = document.getElementById("globalAlert");
    if (!alert) return;
    alert.classList.remove("hidden");
    alert.textContent = "";
    const span = document.createElement("span");
    span.textContent = message;
    alert.appendChild(span);
    if (_alertDismissTimer) clearTimeout(_alertDismissTimer);
    _alertDismissTimer = setTimeout(() => {
      alert.classList.add("hidden");
      if (_alertDismissTimer) { clearTimeout(_alertDismissTimer); _alertDismissTimer = null; }
    }, 4 * 1000);
    return;
  }
  if (_alertDismissTimer) clearTimeout(_alertDismissTimer);
  toast.classList.remove("hidden");
  toast.textContent = "";
  const text = document.createElement("span");
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "alert-dismiss";
  close.setAttribute("aria-label", "关闭通知");
  close.textContent = "×";
  close.addEventListener("click", () => dismissAlert(toast, toast.querySelector(".alert-dismiss")));
  toast.appendChild(text);
  toast.appendChild(close);
  _alertDismissTimer = setTimeout(() => dismissAlert(toast, close), 4 * 1000);
}

function endpointOk(key) {
  return Boolean(state.endpointResults[key]?.ok);
}

function getPaperId(paper) {
  if (!paper) return "";
  return String(paper.id || paper.paper_id || "");
}

function getVersionId(paper) {
  return String(paper?.current_version_id || paper?.current_version?.id || "");
}

function paperTitle(paper) {
  return paper?.canonical_title || paper?.title || paper?.name || "未命名论文";
}

function authorText(paper) {
  const authors = paper?.metadata?.authors || paper?.authors;
  if (Array.isArray(authors)) return authors.join(", ");
  return authors || "";
}

function identifierText(paper) {
  const identifiers = normalizeList(paper?.identifiers, ["items"]);
  const arxiv = identifiers.find((item) => String(item.type).toLowerCase() === "arxiv");
  return arxiv ? `arXiv:${arxiv.value}` : getPaperId(paper);
}

function normalizeTopics(paper) {
  const raw = paper?.topics || [];
  if (Array.isArray(raw)) return raw.map((item) => typeof item === "string" ? item : topicName(item)).filter(Boolean);
  return String(raw).split(/[,/|]/).map((item) => item.trim()).filter(Boolean);
}

// Return the topic objects a paper belongs to (matched by id against
// state.topics so we can group papers under their topic cards).
function paperTopicObjs(paper) {
  const raw = paper?.topics || [];
  const ids = new Set();
  raw.forEach((item) => {
    if (item && typeof item === "object" && item.id) ids.add(item.id);
    else if (typeof item === "string") ids.add(item);
  });
  return state.topics.filter((topic) => ids.has(topic.id));
}

function normalizeKeywords(topic) {
  const raw = topic.aliases || topic.keywords || topic.include_terms || [];
  if (Array.isArray(raw)) return raw.slice(0, 8).map(String);
  return String(raw).split(/[,/|]/).map((item) => item.trim()).filter(Boolean).slice(0, 8);
}

function topicName(topic) {
  if (!topic) return "";
  if (typeof topic === "string") return topic;
  return topic.name_zh || topic.zh_name || topic.name_en || topic.name || topic.title || topic.id || "";
}

function topicDisplayName(topicId) {
  return topicName(state.topics.find((topic) => topic.id === topicId)) || topicId;
}

function digestPaperTitle(paperId) {
  const paper = (state.digest?.papers || []).find((item) => getPaperId(item) === paperId)
    || state.papers.find((item) => getPaperId(item) === paperId);
  return paper ? paperTitle(paper) : paperId;
}

function readingRouteLabel(route) {
  return { "30_minutes": "30 分钟", "2_hours": "2 小时", "half_day": "半天" }[route] || route;
}

function hasReport(paper) {
  const workspace = state.workspaces.get(getPaperId(paper));
  return Boolean(workspace?.report || normalizeList(workspace?.technical_cards, ["items"]).length || String(paper?.status || "").toLowerCase() === "analyzed");
}

function hasTopicOverlap() {
  const topicSets = selectedPapers().map((paper) => new Set(normalizeTopics(paper)));
  if (topicSets.length < 2) return false;
  const [first, ...rest] = topicSets;
  const shared = Array.from(first).some((topic) => rest.every((set) => set.has(topic)));
  return shared || hasSelectedRelation(["complements", "extends"]) || state.topicOverrideNote.length >= 12;
}

function hasAnyReportEvidence() {
  return selectedPapers().some(hasReport);
}

function workspaceArtifacts(workspace) {
  return normalizeList(workspace?.artifacts, ["items", "artifacts"]);
}

function currentVersion(paper, workspace) {
  return paper?.current_version || normalizeList(workspace?.versions, ["items"]).find((version) => version.id === paper?.current_version_id) || null;
}

function findArtifact(artifacts, kind) {
  return artifacts.find((artifact) => {
    const text = `${artifact.artifact_type || ""} ${artifact.media_type || ""} ${artifact.uri || ""}`.toLowerCase();
    return text.includes(kind);
  });
}

function artifactDownloadUrl(artifact) {
  return artifact?.id ? `${API_BASE}/artifacts/${encodeURIComponent(artifact.id)}/download` : "";
}

function safeDocumentUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) && url.origin === window.location.origin
      ? url.href
      : "";
  } catch (_) {
    return "";
  }
}

function jobKindLabel(kind) {
  return {
    discover: "发现",
    download: "下载",
    parse: "解析",
    analyze: "研读",
    translate: "翻译",
    relate: "关系",
    prior_art_check: "查新",
    revise: "修订",
  }[kind] || kind;
}

function collectEvidence(workspace) {
  const reportEvidence = normalizeList(workspace?.report?.evidence, ["items"]);
  const cardEvidence = normalizeList(workspace?.technical_cards, ["items"]).flatMap((card) => normalizeList(card.evidence, ["items"]));
  const relationEvidence = normalizeList(workspace?.relations, ["items"]).flatMap((relation) => normalizeList(relation.evidence, ["items"]));
  return [...reportEvidence, ...cardEvidence, ...relationEvidence];
}

function evidenceText(item) {
  if (typeof item === "string") return item;
  const text = item.note || item.text || item.quote || item.summary || JSON.stringify(item);
  const source = item.source || item.section || item.page || "证据";
  return `${source}: ${text}`;
}

function evidenceKind(item) {
  if (typeof item === "string") return "fact";
  const type = String(item.kind || item.type || item.evidence_type || "fact").toLowerCase();
  return ["analysis", "hypothesis"].includes(type) ? type : "fact";
}

function adapterHealthCard(adapter) {
  const status = adapterStatus(adapter);
  const ok = adapterHealthy(adapter);
  return html`
    <div class="gate-item ${ok ? "pass" : "warn"}">
      <span class="gate-icon">${ok ? "✓" : "!"}</span>
      <div><strong>${adapter.name || adapter.adapter || adapter.service || "adapter"}</strong><p class="meta">${status} ${adapter.message || adapter.error || adapter.version || ""}</p></div>
    </div>
  `;
}

function adapterStatus(adapter) {
  return String(adapter.status || adapter.state || (adapter.ok ? "ok" : "unknown")).toLowerCase();
}

function adapterHealthy(adapter) {
  return ["ok", "healthy", "success", "up", "online"].includes(adapterStatus(adapter));
}

function candidateStructuredInput() {
  return {
    coupling: inputValue("couplingInput"),
    interface: inputValue("interfaceInput"),
    data_control_flow: inputValue("dataControlFlowInput"),
    non_parallel_explanation: inputValue("nonParallelInput"),
    joint_effect: inputValue("jointEffectInput"),
  };
}

function candidateApprovalInput() {
  return {
    // 审批人不再由前端填写（设置项已移除），交由审批流程记录当前操作者。
    approver: "web ui",
    confirmations: {
      facts_and_citations_confirmed: checkboxValue("approvalFactsInput"),
      novelty_risk_recorded: checkboxValue("approvalNoveltyInput"),
      inventive_step_not_parallel_confirmed: checkboxValue("approvalInventivenessInput"),
      scope_and_license_review_confirmed: checkboxValue("approvalScopeInput"),
    },
  };
}

function hasStructuredCandidateInput() {
  return Object.values(candidateStructuredInput()).every((value) => value.length >= 12);
}

function hasApprovalConfirmations() {
  const approval = candidateApprovalInput();
  return Object.values(approval.confirmations).every(Boolean);
}

function inputValue(id) {
  return document.getElementById(id)?.value.trim() || "";
}

function checkboxValue(id) {
  return Boolean(document.getElementById(id)?.checked);
}

function candidateIdempotencyKey(payload) {
  const sourceKey = payload.sources.map((item) => item.paper_version_id || item.paper_id).join("-");
  const formKey = stableTextKey([
    payload.structured_combination.coupling,
    payload.structured_combination.interface,
    payload.structured_combination.data_control_flow,
    payload.structured_combination.non_parallel_explanation,
    payload.structured_combination.joint_effect,
    payload.approver,
  ].join("|"));
  return `web-candidate-${sourceKey}-${formKey}`;
}

function candidateEvidence(papers, structured) {
  const sourceAnchors = papers.map((paper) => {
    const versionId = getVersionId(paper);
    return {
      kind: "fact",
      source: versionId ? `paper_version:${versionId}` : `paper:${getPaperId(paper)}`,
      report_field: "integration_mechanism",
      note: `${paperTitle(paper)} 作为组合候选来源；版本 ${versionId || "未提供"}`,
    };
  });
  const fieldAnchors = [
    ["problem_statement", `共同技术问题来自 ${papers.map(paperTitle).join("、")} 的工程约束归纳。`, "fact"],
    ["integration_mechanism", "组合机制由所选论文贡献、耦合关系、接口边界和控制流共同限定。", "fact"],
    ["coupling_interface", structured.coupling, "fact"],
    ["data_or_control_flow", structured.data_control_flow, "fact"],
    ["why_not_juxtaposition", structured.non_parallel_explanation, "fact"],
    ["expected_joint_effect", structured.joint_effect, "hypothesis"],
    ["technical_effects", structured.joint_effect, "hypothesis"],
  ].map(([reportField, note, kind]) => ({
    kind,
    source: "source:web-candidate-form",
    report_field: reportField,
    note,
  }));
  return [...sourceAnchors, ...fieldAnchors];
}

function stableTextKey(text) {
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
  }
  return Math.abs(hash).toString(36);
}

function contributionForPaper(paper) {
  const workspace = state.workspaces.get(getPaperId(paper));
  const card = normalizeList(workspace?.technical_cards, ["items"])[0];
  if (card?.method) return card.method.slice(0, 180);
  if (workspace?.report?.innovation) return workspace.report.innovation.slice(0, 180);
  if (workspace?.report?.method) return workspace.report.method.slice(0, 180);
  return `${paperTitle(paper)} 提供 ${normalizeTopics(paper).join(" / ") || "AI Infra"} 相关机制或约束`;
}

function priorArtJob(candidateId) {
  return state.jobs
    .filter((job) => job.kind === "prior_art_check" && job.target_id === candidateId)
    .sort((a, b) => String(b.updated_at || b.created_at).localeCompare(String(a.updated_at || a.created_at)))[0] || null;
}

function canRetryJob(job) {
  return ["retryable_failed", "terminal_failed", "cancelled", "failed", "error"].includes(String(job.status || "").toLowerCase());
}

function canCancelJob(job) {
  return ["queued", "running", "processing"].includes(String(job.status || "").toLowerCase());
}

function jobStatusLabel(status) {
  return {
    queued: "排队中",
    running: "运行中",
    processing: "处理中",
    succeeded: "已成功",
    success: "已成功",
    completed: "已完成",
    retryable_failed: "可重试失败",
    terminal_failed: "终止失败",
    failed: "失败",
    error: "错误",
    cancelled: "已取消",
  }[String(status || "").toLowerCase()] || status || "未知";
}

function hasSelectedRelation(types) {
  const selected = new Set(Array.from(state.selectedForPatent));
  if (selected.size < 2) return false;
  return Array.from(state.workspaces.values())
    .filter((workspace) => workspace && !workspace.loading && !workspace.error)
    .flatMap((workspace) => normalizeList(workspace.relations, ["items", "relations"]))
    .some((relation) => {
      const type = String(relation.relation_type || relation.type || "").toLowerCase();
      const from = relation.from_paper_id || relation.paper_id;
      const to = relation.to_paper_id || relation.target_paper_id;
      return types.includes(type) && selected.has(from) && selected.has(to);
    });
}

function buildCandidateTitle(papers) {
  const topics = commonTopicText(papers);
  return `面向${topics}的跨论文组合技术`;
}

function commonTopicText(papers) {
  const topics = unique(papers.flatMap(normalizeTopics)).slice(0, 3);
  return topics.join("、") || "AI Infra";
}

function jsonSummary(value) {
  if (!value || (typeof value === "object" && !Object.keys(value).length)) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 360 ? `${text.slice(0, 357)}...` : text;
}

function exportFilename(response, format) {
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  if (match?.[1]) return decodeURIComponent(match[1]);
  const draft = state.drafts.find((item) => item.id === state.selectedDraftId);
  const extension = format === "docx" ? "docx" : "md";
  return `${draft?.case_name || "patent-draft"}.${extension}`;
}

function raw(value) {
  return { __raw: String(value) };
}

function pipelineLabel(step) {
  return {
    discover: "论文发现",
    download: "PDF 下载",
    parse: "论文解析",
    translate: "翻译生成",
    analyze: "智能研读",
    relate: "历史关联",
    patent_draft: "专利草稿",
  }[step] || step;
}

function unique(items) {
  return Array.from(new Set(items));
}

function emptyBlock(message) {
  return html`<div class="empty">${message}</div>`;
}

function loadingBlock(message) {
  return html`<div class="loading">${message}</div>`;
}

function errorBlock(message) {
  return html`<div class="error">${message}</div>`;
}

function html(strings, ...values) {
  return strings.reduce((result, string, index) => {
    const value = values[index] ?? "";
    return result + string + (value && value.__raw !== undefined ? value.__raw : escapeHtml(value));
  }, "");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
