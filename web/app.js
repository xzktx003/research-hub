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

// 夜间模式。与字号缩放同理，通过 documentElement 的 class 切换主题，
// 由 CSS 定义 .theme-dark 下的配色覆盖（严格 CSP 不支持 inline style）。
const THEME_KEY = "research_hub.theme";
const THEME_DEFAULT = "light";

function loadTheme() {
  return localStorage.getItem(THEME_KEY) || THEME_DEFAULT;
}

function applyTheme(mode) {
  const root = document.documentElement;
  root.classList.toggle("theme-dark", mode === "dark");
  root.dataset.theme = mode;
  const input = document.getElementById("themeModeInput");
  if (input) input.value = mode;
}

function initTheme() {
  applyTheme(loadTheme());
}

function saveTheme() {
  const input = document.getElementById("themeModeInput");
  if (!input) return;
  const mode = input.value === "dark" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, mode);
  applyTheme(mode);
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
  batchSelected: new Set(),
  foldedCards: new Set(),
  jobsPollTimer: null,
  jobsPolling: false,
  loadSeq: 0,
  readerPollToken: 0,
  pdfDownloading: null,
  browseMode: "date", // "date" = 指定日期论文 | "all" = 全部日期论文
  allPapersLoading: false,
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
  initTheme();
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
  document.getElementById("sortFilter").addEventListener("change", render);
  document.getElementById("dateFilter").addEventListener("change", loadAll);
  bindReaderIndexSearch();
  bindReaderNavButtons();
  document.getElementById("jobKindFilter")?.addEventListener("change", () => { if (state.activeView === "jobs") renderJobs(); });
  document.getElementById("jobStatusFilter")?.addEventListener("change", () => { if (state.activeView === "jobs") renderJobs(); });
  document.getElementById("selectAllVisibleBtn")?.addEventListener("click", () => {
    const papers = filteredPapers();
    papers.forEach((paper) => state.batchSelected.add(getPaperId(paper)));
    updateBatchSelectionUi();
    showAlert(`已选择 ${papers.length} 篇论文。`);
  });
  document.getElementById("batchNotebookBtn")?.addEventListener("click", batchAddToNotebook);
  document.getElementById("batchClearBtn")?.addEventListener("click", () => {
    state.batchSelected.clear();
    updateBatchSelectionUi();
  });
  document.getElementById("runDiscoveryButton").addEventListener("click", (event) => {
    withButtonLoading(event.currentTarget, runDiscovery);
  });
  document.getElementById("helpButton").addEventListener("click", openHelp);
  document.getElementById("helpCloseButton").addEventListener("click", closeHelp);
  document.getElementById("directedDiscoveryButton").addEventListener("click", openDirectedDiscovery);
  document.getElementById("directedCloseButton").addEventListener("click", closeDirectedDiscovery);
  document.getElementById("directedForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const submit = document.getElementById("directedSubmitButton");
    withButtonLoading(submit, () => submitDirectedDiscovery(event));
  });
  document.getElementById("dailyPrevDay")?.addEventListener("click", () => browseDateBy(-1));
  document.getElementById("dailyNextDay")?.addEventListener("click", () => browseDateBy(1));
  document.getElementById("dailyJumpToday")?.addEventListener("click", () => {
    state.browseDate = today;
    const dateInput = document.getElementById("dailyDateFilter");
    if (dateInput) dateInput.value = today;
    loadHistoryPapers();
  });
  document.getElementById("dailyAllDates")?.addEventListener("click", () => setBrowseModeAll());
  document.getElementById("dailyDateFilter")?.addEventListener("change", (event) => setBrowseDateFromInput(event.target.value));
  document.getElementById("createCandidateButton").addEventListener("click", (event) => {
    withButtonLoading(event.currentTarget, createCandidate);
  });
  document.getElementById("downloadDraftButton").addEventListener("click", () => downloadSelectedDraft("markdown"));
  document.getElementById("downloadDraftDocxButton")?.addEventListener("click", () => downloadSelectedDraft("docx"));
  document.getElementById("saveAnalysisConfigButton")?.addEventListener("click", saveAnalysisConfig);
  document.getElementById("saveScheduleConfigButton")?.addEventListener("click", saveScheduleConfig);
  document.getElementById("saveTopicQuotaButton")?.addEventListener("click", saveTopicQuota);
  document.getElementById("saveFontSizeButton")?.addEventListener("click", saveFontSize);
  document.getElementById("saveThemeButton")?.addEventListener("click", saveTheme);
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
  // Reset scroll so a deep-scrolled reader/paper view doesn't leave the next
  // view starting mid-page.
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  // Leaving the reader cancels any in-flight reading-report polling.
  if (state.activeView === "reader" && view !== "reader") state.readerPollToken++;
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
      populateJobKindFilter(items);
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
  // Guard against overlapping loads: a slow earlier request must not clobber a
  // newer one that the user triggered afterwards (race: last-writer-wins).
  const seq = ++state.loadSeq;
  state.loading = true;
  renderLoading();

  const selectedDate = document.getElementById("dateFilter").value || today;
  document.getElementById("todayLabel").textContent = selectedDate;
  state.selectedTopicDigest = null;
  endpoints.papers = [`/papers?date=${encodeURIComponent(selectedDate)}`, "/papers"];
  endpoints.digest = [`/daily-digests/${encodeURIComponent(selectedDate)}`];

  const keys = Object.keys(endpoints);
  const results = await Promise.all(keys.map((key) => firstJson(key, endpoints[key])));
  if (seq !== state.loadSeq) return; // a newer load superseded this one
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
  if (seq !== state.loadSeq) return; // a newer load superseded this one
  state.digest = state.endpointResults.digest.data;
  state.workflows = state.endpointResults.workflows.data;
  state.runtimeConfig = state.endpointResults.runtimeConfig.data;
  // Only keep an explicitly chosen paper. The reader should be an empty
  // "enter reading" state until the user picks a paper, rather than silently
  // auto-opening the first paper's PDF (which some browsers download).
  state.selectedPaperId = state.selectedPaperId || null;
  state.selectedCandidateId = state.selectedCandidateId || state.candidates[0]?.id || null;
  state.selectedDraftId = state.selectedDraftId || state.drafts[0]?.id || null;
  if (seq !== state.loadSeq) {
    // A newer load superseded this one; hand loading back to that newer call.
    state.loading = false;
    return;
  }

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
  const seq = state.loadSeq;
  try {
    const data = await apiJson("/papers?all=1");
    if (seq !== state.loadSeq) return; // superseded by a newer load
    state.allPapers = normalizeList(data, ["papers", "items", "results", "data"]);
  } catch {
    if (seq !== state.loadSeq) return;
    state.allPapers = [...state.papers];
  }
  if (seq !== state.loadSeq) return;
  renderPaperLibrary();
}

async function loadHistoryPapers() {
  // 「所有日期」模式无需再按日期拉取，直接用全量论文库。
  if (state.browseMode === "all") {
    state.historyLoading = false;
    state.historyError = null;
    if (!state.allPapers.length && !state.allPapersLoading) {
      state.allPapersLoading = true;
      try {
        const data = await apiJson("/papers?all=1");
        state.allPapers = normalizeList(data, ["papers", "items", "results", "data"]);
      } finally {
        state.allPapersLoading = false;
      }
    }
    state.historyPapers = state.allPapers;
    renderHistoryPapers();
    renderTopics();
    return;
  }
  const dateValue = state.browseDate;
  state.historyLoading = true;
  state.historyError = null;
  renderHistoryPapers();
  try {
    const data = await apiJson(`/papers?date=${encodeURIComponent(dateValue)}`);
    const papers = normalizeList(data, ["papers", "items", "results", "data"]);
    if (state.browseDate === dateValue && state.browseMode === "date") {
      state.historyPapers = papers;
      state.historyLoading = false;
      renderHistoryPapers();
      renderTopics();
    }
  } catch (error) {
    if (state.browseDate === dateValue && state.browseMode === "date") {
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
  state.browseMode = "date";
  const current = new Date(`${state.browseDate}T12:00:00`);
  current.setDate(current.getDate() + days);
  state.browseDate = current.toISOString().slice(0, 10);
  const dateInput = document.getElementById("dailyDateFilter");
  if (dateInput) dateInput.value = state.browseDate;
  loadHistoryPapers();
}

function setBrowseDateFromInput(value) {
  if (!value) return;
  state.browseMode = "date";
  state.browseDate = value;
  loadHistoryPapers();
}

// 「所有日期」浏览切换：未激活时进入 all 模式（基于全量论文库统计主题分布）；
// 已激活时点再次点击可退回当前/今日日期模式，避免用户被困在 all 视图。
function setBrowseModeAll() {
  if (state.browseMode === "all") {
    state.browseMode = "date";
    state.browseDate = today;
    const dateInput = document.getElementById("dailyDateFilter");
    if (dateInput) dateInput.value = today;
    loadHistoryPapers();
  } else {
    state.browseMode = "all";
    renderDailyBrowse();
  }
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
  const token = ++state.readerPollToken;
  try {
    await apiJson(`/paper-versions/${encodeURIComponent(versionId)}/analyze`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-reading-report-${versionId}` },
      body: JSON.stringify({ force: true }),
    });
    // Poll workspace until the report appears or times out (~5 min). The token
    // is bumped by switchView/openPaper so leaving the reader cancels the loop
    // instead of polling a background paper for minutes.
    const deadline = Date.now() + 5 * 60 * 1000;
    let lastError = "";
    for (;;) {
      if (token !== state.readerPollToken || state.activeView !== "reader") return;
      await new Promise((resolve) => setTimeout(resolve, 3000));
      if (token !== state.readerPollToken || state.activeView !== "reader") return;
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
    container.innerHTML = emptyBlock(
      source === "history"
        ? (state.browseMode === "all"
            ? "论文库中还没有已收录的论文。点击右上角「触发发现」获取论文。"
            : "该日期没有论文。可在「触发发现」为该日期补充论文。")
        : "今日还没有论文。点击右上角「触发发现」获取今日论文。"
    );
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
  // 批量折叠/展开工具条插在第一个 topic group 前：先渲染 sections，
  // 再把工具条 insertBefore 到最前，避免挤在已有主题卡片之间。
  container.innerHTML = sections.join("");
  const firstGroup = container.querySelector(".daily-topic-group, .empty-block");
  const groupCount = container.querySelectorAll(".daily-topic-group").length;
  if (firstGroup && groupCount > 0 && !(firstGroup.classList.contains("empty-block"))) {
    const dailyFoldedCount = container.querySelectorAll(".daily-topic-group.card-folded").length;
    const bar = document.createElement("div");
    bar.className = "fold-batch-bar";
    bar.innerHTML = html`
      <span class="batch-label">卡片折叠：${dailyFoldedCount ? `已折叠 ${dailyFoldedCount}/${groupCount}` : `共 ${groupCount} 个分区`}</span>
      <button class="secondary compact-action" type="button" data-fold-all data-fold-all-scope="${containerId}" data-fold-target=".daily-topic-group" title="把当前日期全部主题分区折叠成标题条">全部折叠</button>
      <button class="secondary compact-action" type="button" data-expand-all data-fold-all-scope="${containerId}" data-fold-target=".daily-topic-group" title="展开全部主题分区">全部展开</button>
    `;
    container.insertBefore(bar, firstGroup);
    bindCardFoldBatch(bar);
  }
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
  bindCardFoldToggles(container);
}

function topicSection(topic, list, containerId) {
  const quota = Number(topic.daily_quota) > 0 ? Number(topic.daily_quota) : list.length;
  const shown = list.slice(0, quota);
  const expanded = state.expandedTopics.has(topic.id);
  const moreCount = list.length - shown.length;
  const groupKey = `dailygroup:${topic.id}`;
  const groupFolded = state.foldedCards.has(groupKey);
  return html`
    <section class="daily-topic-group card-collapsible ${groupFolded ? "card-folded" : ""}" data-fold-key="${groupKey}">
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
      <div class="card-fold-bar">
        <span class="fold-snippet" title="${escapeHtml(topicName(topic))}">${escapeHtml(topicName(topic))}<span class="meta">${list.length} 篇</span></span>
        <button class="card-fold-toggle" type="button" data-fold="${groupKey}" data-fold-restore title="展开该主题"><span class="fold-icon">▸</span>展开</button>
      </div>
    </section>
  `;
}

function untaggedSection(list, containerId) {
  const groupKey = "dailygroup:__untagged__";
  const groupFolded = state.foldedCards.has(groupKey);
  return html`
    <section class="daily-topic-group card-collapsible ${groupFolded ? "card-folded" : ""}" data-topic-group="__untagged__" data-fold-key="${groupKey}">
      <button class="daily-topic-card" type="button" data-topic-toggle="__untagged__">
        <span class="daily-topic-head"><strong>未分类</strong><span class="tag">${list.length} 篇</span></span>
        <span class="expand-icon ${state.expandedTopics.has("__untagged__") ? "expanded" : ""}">▸</span>
      </button>
      <div class="daily-topic-papers ${state.expandedTopics.has("__untagged__") ? "" : "hidden"}" data-topic-papers="__untagged__">
        ${raw(list.map((paper) => paperCard(paper)).join(""))}
      </div>
      <div class="card-fold-bar">
        <span class="fold-snippet" title="未分类">未分类<span class="meta">${list.length} 篇</span></span>
        <button class="card-fold-toggle" type="button" data-fold="${groupKey}" data-fold-restore title="展开该主题"><span class="fold-icon">▸</span>展开</button>
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
    const papers = state.browseMode === "all" ? state.allPapers : state.historyPapers;
    renderDailyByTopic("historyPapers", papers, "history");
  } else {
    renderDailyByTopic("dailyPapers", state.papers, "daily");
  }
}

function renderHistoryPapers() {
  const container = document.getElementById("historyPapers");
  const label = document.getElementById("historyDateLabel");
  syncBrowseControls();
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
  if (state.browseMode === "all") {
    if (state.allPapersLoading) {
      container.innerHTML = loadingBlock("正在加载全部论文...");
      return;
    }
    if (label) label.textContent = "全部日期";
    renderDailyByTopic("historyPapers", state.allPapers, "history");
    renderDigestSummary();
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
  // 主题分布面板与主题卡片一起随浏览日期刷新，保证同一屏数据一致。
  renderDigestSummary();
}

// 「所有日期」浏览：无需网络请求（全量已缓存在 state.allPapers），直接刷新
// 历史论文列表与主题统计，并高亮「所有日期」按钮。
function syncBrowseControls() {
  const allBtn = document.getElementById("dailyAllDates");
  if (allBtn) allBtn.classList.toggle("active", state.browseMode === "all");
  const dateInput = document.getElementById("dailyDateFilter");
  if (dateInput) dateInput.disabled = state.browseMode === "all";
  const disabled = state.browseMode === "all";
  ["dailyPrevDay", "dailyNextDay", "dailyJumpToday"].forEach((id) => {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = disabled;
  });
}

function renderDailyBrowse() {
  syncBrowseControls();
  if (state.browseMode === "all") {
    loadHistoryPapers();
  } else {
    renderHistoryPapers();
  }
  renderTopics();
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
  // 主题覆盖统计与主题卡片保持一致：随当前浏览模式（指定日期 / 所有日期）
  // 基于真实的当前论文集统计，而不是固定在「今日日报」上。
  const topicOverviews = state.topics
    .filter((topic) => topic.deleted_at == null)
    .map((topic) => ({
      name: topicName(topic),
      count: topicPaperCount(topic.id),
    }))
    .filter((item) => item.count > 0)
    .sort((a, b) => b.count - a.count);
  const topicRows = topicOverviews.map(
    ({ name, count }) => html`<div class="compact-item"><strong>${name}</strong><span>${count} 篇</span></div>`,
  );
  const emptyCountText = state.browseMode === "all" ? "论文库中暂无已收录的主题论文。" : "该日期暂无主题论文。";
  distribution.innerHTML = `
    <h3 class="digest-subheading">来源命中</h3>
    ${[...sourceRows].join("") || emptyBlock("当日暂无来源命中。")}
    <h3 class="digest-subheading spaced">主题覆盖</h3>
    ${[...topicRows].join("") || emptyBlock(emptyCountText)}
  `;
  routes.innerHTML = Object.entries(state.digest.reading_routes || {}).map(
    ([route, paperIds]) => html`
      <article class="compact-item">
        <strong>${readingRouteLabel(route)}</strong>
        <span>${paperIds.map(digestPaperTitle).join(" · ") || "暂无推荐"}</span>
      </article>
    `,
  ).join("") || emptyBlock("当日暂无阅读路线。");
  renderRecommendedReading();
}

// 今日推荐阅读：从已收录论文中挑出"热度高 + 主题命中多 + 研读进度好"的
// 若干篇，并给出可解释的推荐原因（命中主题 / 高热度 / 研读完成）。
function renderRecommendedReading() {
  const container = document.getElementById("recommendedReading");
  if (!container) return;
  if (!endpointOk("papers")) {
    container.innerHTML = errorBlock("论文接口不可用。");
    return;
  }
  const papers = state.papers.length ? state.papers : state.allPapers;
  if (!papers.length) {
    container.innerHTML = emptyBlock("暂无已收录论文。");
    return;
  }
  const scored = papers
    .map((paper) => {
      const topics = normalizeTopics(paper);
      const heat = paperHeatScore(paper);
      const model = paperModelScore(paper);
      const routes = state.digest?.reading_routes || {};
      const inRoute = Object.values(routes).some((ids) => (ids || []).includes(getPaperId(paper)));
      const reasons = [];
      if (topics.length) reasons.push(`命中主题：${topics.slice(0, 2).join("、")}`);
      if (inRoute) reasons.push("在今日阅读路线中");
      if (heat >= 15) reasons.push("热度较高");
      const reportReady = hasReport(paper);
      if (reportReady) {
        reasons.push(model != null ? `大模型研读分 ${model}` : "已生成研读报告");
      }
      // Smart weight: LLM quality score dominates when present, else heat.
      const weight = model != null ? model * 10 + heat + 6 : heat;
      return { paper, reasons, weight };
    })
    .filter((item) => item.reasons.length)
    .sort((a, b) => b.weight - a.weight)
    .slice(0, 5);
  if (!scored.length) {
    container.innerHTML = emptyBlock("暂无符合条件的推荐论文。");
    return;
  }
  container.innerHTML = scored.map((item) => html`
    <article class="compact-item recommended-item" data-recommend-open="${getPaperId(item.paper)}">
      <div>
        <strong>${paperTitle(item.paper)}</strong>
        <span class="meta recommended-reasons">${item.reasons.join(" · ")}</span>
      </div>
      <span class="recommended-arrow">→</span>
    </article>
  `).join("");
  container.querySelectorAll("[data-recommend-open]").forEach((el) => {
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.addEventListener("click", () => openPaper(el.dataset.recommendOpen));
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPaper(el.dataset.recommendOpen);
      }
    });
  });
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
        || document.getElementById("statusFilter")?.value
        || document.getElementById("sortFilter")?.value
        || document.getElementById("dateFilter")?.value
        || state.batchSelected.size,
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
  const foldedCount = [...papers].filter((paper) => state.foldedCards.has(`paper:${getPaperId(paper)}`)).length;
  container.innerHTML = html`
    ${raw(papers.length ? html`
      <div class="fold-batch-bar">
        <span class="batch-label">卡片折叠：${foldedCount ? `已折叠 ${foldedCount}/${papers.length}` : `共 ${papers.length} 张`}</span>
        <button class="secondary compact-action" type="button" data-fold-all data-fold-all-scope="${containerId}" title="把当前列表全部卡片折叠成标题条">全部折叠</button>
        <button class="secondary compact-action" type="button" data-expand-all data-fold-all-scope="${containerId}" title="展开当前列表全部卡片">全部展开</button>
      </div>
    ` : "")}
    ${raw(papers.map((paper) => paperCard(paper)).join(""))}
  `;
  bindPaperCardActions(container);
  bindCardFoldBatch(container);
}

// Reset all paper-library filters back to defaults and re-render.
function clearPaperFilters() {
  const searchEl = document.getElementById("globalSearch");
  const topicEl = document.getElementById("topicFilter");
  const statusEl = document.getElementById("statusFilter");
  const sortEl = document.getElementById("sortFilter");
  const dateEl = document.getElementById("dateFilter");
  if (searchEl) searchEl.value = "";
  if (topicEl) topicEl.value = "";
  if (statusEl) statusEl.value = "";
  if (sortEl) sortEl.value = "";
  // Also clear the browse-by-date so "查看全部" truly shows the full corpus
  // instead of remaining empty because an old date filter is still applied.
  if (dateEl) {
    dateEl.value = "";
    state.browseDate = new Date().toISOString().slice(0, 10);
  }
  renderPaperLibrary();
}

function paperCard(paper) {
  const id = getPaperId(paper);
  const title = paperTitle(paper);
  const currentQuery = document.getElementById("globalSearch")?.value.trim() || "";
  const topics = normalizeTopics(paper);
  const status = paper.status || "discovered";
  const statusLabel = {
    discovered: "已发现", parsed: "已解析", analyzed: "已研读",
    downloaded: "已下载", translated: "已翻译", scored: "已评分",
    published: "已发布", failed: "失败",
  }[String(status).toLowerCase()] || status;
  const cardId = `paper-card-${id}`;
  const bodyId = `paper-body-${id}`;
  const folded = state.foldedCards.has(`paper:${id}`);
  return html`
    <article class="paper-card card-collapsible ${folded ? "card-folded" : ""}" id="${cardId}" data-fold-key="paper:${id}">
      <div class="paper-card-header" data-toggle-card="${id}">
        <input type="checkbox" class="paper-batch-check" data-batch-check="${id}" title="选择此论文" ${state.batchSelected.has(id) ? "checked" : ""}>
        <div>
          <h3>${raw(highlightQuery(title, currentQuery))}</h3>
          <p class="meta">${authorText(paper) || identifierText(paper) || "作者/来源未提供"}</p>
          <div class="paper-stage-tags">${raw(renderStageTags(paper))}</div>
          ${raw(paperModelScore(paper) != null ? html`<span class="paper-score-badge" title="大模型研读综合评分（0-10）">研读分 ${paperModelScore(paper)}</span>` : "")}
          ${raw(paperMetaTags(paper).map((tag) => html`<span class="paper-meta-tag ${tag.cls}">${tag.label}</span>`).join(" "))}
        </div>
        <div class="paper-card-meta-actions">${raw(topics.map((topic) => html`<span class="tag">${topic}</span>`).join(" "))} <span class="state ${String(status).toLowerCase()}">${statusLabel}</span><button class="card-fold-toggle" type="button" data-fold="paper:${id}" title="${folded ? "展开这张卡片" : "折叠这张卡片，只保留标题条"}"><span class="fold-icon">▸</span>${folded ? "展开" : "收起"}</button></div>
      </div>
      <div class="paper-card-body hidden" id="${bodyId}">
        ${raw(paper.method_summary ? html`<p class="paper-one-liner"><strong>一句话摘要：</strong>${paper.method_summary}</p>` : "")}
        <p><strong>${paper.translated_abstract ? "中文摘要" : "中文摘要待生成"}：</strong>${paperAbstract(paper)}</p>
        ${raw(paper.abstract ? html`<details class="abstract-original"><summary>查看英文原摘要</summary><p>${paper.abstract}</p></details>` : "")}
        ${raw(paper.first_publication_date ? html`<p class="meta">发表日期：${paper.first_publication_date}</p>` : "")}
        ${raw(paper.remote ? html`<p class="meta remote-paper">联网检索结果（尚未入库）</p>` : "")}
        ${raw(paperCitationCount(paper) ? html`<p class="meta">引用数：${paperCitationCount(paper)}</p>` : "")}
        ${raw(buildCardExternalLinks(paper))}
        <div class="paper-actions">
          <button class="secondary" type="button" data-open-paper="${id}">打开阅读台</button>
          ${raw(paperParseButton(paper, id))}
          <button class="secondary" type="button" data-select-patent="${id}">${state.selectedForPatent.has(id) ? "取消候选" : "加入专利候选"}</button>
          <button class="secondary" type="button" data-notebook-add="${id}">${state.notebookPapers.has(id) ? "已在笔记本" : "加入笔记本"}</button>
          <button class="secondary" type="button" data-similar-papers="${id}">相似论文</button>
        </div>
      </div>
      <div class="card-fold-bar">
        <span class="fold-snippet" title="${escapeHtml(title)}">${raw(highlightQuery(title, currentQuery))}<span class="meta">${escapeHtml(statusLabel)}</span></span>
        <button class="card-fold-toggle" type="button" data-fold="paper:${id}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
      </div>
    </article>
  `;
}

function buildCardExternalLinks(paper) {
  const links = [];
  const code = paperCodeUrl(paper);
  if (code) links.push(html`<a class="paper-ext-link" href="${code}" target="_blank" rel="noreferrer">代码仓库 ↗</a>`);
  const arxiv = paperArxivUrl(paper);
  if (arxiv) links.push(html`<a class="paper-ext-link" href="${arxiv}" target="_blank" rel="noreferrer">arXiv 原文 ↗</a>`);
  if (!links.length) return "";
  return html`<p class="paper-ext-links">${links.join(" ")}</p>`;
}

// 给容器内所有逐卡折叠按钮（[data-fold]）绑定点击。渲染后调用一次即可；
// 因为每次渲染都会创建新按钮，所以必须「渲染 → 绑定」，不能靠一次性委托。
function bindCardFoldToggles(container) {
  container.querySelectorAll("[data-fold]").forEach((button) => {
    button.addEventListener("click", () => toggleCardFold(button.dataset.fold, button));
  });
}

function bindPaperCardActions(container) {
  bindCardFoldToggles(container);
  container.querySelectorAll("[data-open-paper]").forEach((button) => {
    button.addEventListener("click", () => openPaper(button.dataset.openPaper));
  });
  container.querySelectorAll("[data-select-patent]").forEach((button) => {
    button.addEventListener("click", () => togglePatentSelection(button.dataset.selectPatent));
  });
  container.querySelectorAll("[data-toggle-card]").forEach((header) => {
    const toggleCard = () => {
      const paperId = header.dataset.toggleCard;
      const body = document.getElementById(`paper-body-${paperId}`);
      const icon = document.getElementById(`expand-${paperId}`);
      if (body && icon) {
        const collapsed = body.classList.toggle("hidden");
        icon.classList.toggle("expanded", !collapsed);
        header.setAttribute("aria-expanded", collapsed ? "false" : "true");
      }
    };
    // Make the clickable header reachable and operable by keyboard.
    header.setAttribute("role", "button");
    header.setAttribute("tabindex", "0");
    header.setAttribute("aria-expanded", "false");
    header.addEventListener("click", toggleCard);
    header.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleCard();
      }
    });
  });
  container.querySelectorAll("[data-notebook-add]").forEach((button) => {
    button.addEventListener("click", () => toggleNotebook(button.dataset.notebookAdd));
  });
  container.querySelectorAll("[data-similar-papers]").forEach((button) => {
    button.addEventListener("click", () => showSimilarPapers(button.dataset.similarPapers));
  });
  container.querySelectorAll("[data-parse-paper]").forEach((button) => {
    button.addEventListener("click", () => parsePaperCard(button.dataset.parsePaper, button));
  });
  container.querySelectorAll("[data-batch-check]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const paperId = checkbox.dataset.batchCheck;
      if (checkbox.checked) state.batchSelected.add(paperId);
      else state.batchSelected.delete(paperId);
      updateBatchSelectionUi();
    });
  });
}

// ---- 卡片折叠（collapse）通用机制 ----
// state.foldedCards 记录被折叠卡片的 key（如 paper:<id>），供重渲染恢复。
// 任何卡片元素带上 .card-collapsible + data-fold-key 即可参与折叠。

function isCardFolded(key) {
  return state.foldedCards.has(key);
}

// 切换某张卡的折叠状态，并同步更新 DOM（加上/移除 .card-folded）。
// 传 card 时直接原地更新；批量操作时传入 sequenceKey 用于区分。
function toggleCardFold(key, buttonOrCard) {
  const card = buttonOrCard && buttonOrCard.closest
    ? buttonOrCard.closest(".card-collapsible")
    : buttonOrCard;
  const nowFolded = state.foldedCards.has(key);
  if (nowFolded) state.foldedCards.delete(key);
  else state.foldedCards.add(key);
  // 折叠时顺带把卡片 body 隐藏，恢复时不自动展开 body（保持用户选择）。
  if (card) {
    card.classList.toggle("card-folded", !nowFolded);
    updateFoldButtonState(card, !nowFolded);
  }
}

// 更新卡片内部折叠按钮文字/图标，使折叠状态在重建后保持一致。
function updateFoldButtonState(card, folded) {
  card.querySelectorAll("[data-fold-restore]").forEach((btn) => {
    btn.innerHTML = `<span class="fold-icon">▸</span>展开`;
    btn.title = "展开这张卡片";
  });
  card.querySelectorAll("[data-fold]:not([data-fold-restore])").forEach((btn) => {
    btn.innerHTML = `<span class="fold-icon">▸</span>${folded ? "展开" : "收起"}`;
    btn.title = folded ? "展开这张卡片" : "折叠这张卡片，只保留标题条";
  });
}

// 对容器内所有 .card-collapsible 卡片执行折叠/展开（批量）。
// 可选 selector（如 ".daily-topic-group"）限定只折叠该类型的卡片，
// 用于「折叠分区但不连带内部论文卡片」的场景。
function setAllCardsFolded(container, folded, selector) {
  if (!container) return;
  const cards = selector ? container.querySelectorAll(selector) : container.querySelectorAll(".card-collapsible");
  cards.forEach((card) => {
    const key = card.dataset.foldKey;
    if (!key) return;
    if (folded) state.foldedCards.add(key);
    else state.foldedCards.delete(key);
    card.classList.toggle("card-folded", folded);
    updateFoldButtonState(card, folded);
  });
}

// 批量「全部折叠 / 全部展开」按钮。传 data-fold-all-scope 指定容器 id，
// 传 data-fold-target 指定只折叠的选择器（例如仪表盘只折叠主题分区，
// 不连带折叠分区内展开的论文卡片）。未指定 scope 时用按钮所在容器。
function bindCardFoldBatch(container) {
  container.querySelectorAll("[data-fold-all], [data-expand-all]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const scopeId = btn.dataset.foldAllScope;
      const target = scopeId ? document.getElementById(scopeId) : container;
      const doingFold = btn.hasAttribute("data-fold-all");
      const selector = btn.dataset.foldTarget || null;
      setAllCardsFolded(target, doingFold, selector);
      const foldable = (selector ? target?.querySelectorAll(selector) : target?.querySelectorAll(".card-collapsible"))?.length || 0;
      if (foldable) {
        // 更新与按钮同处一条 fold-batch-bar 的统计标签。
        const labelEl = btn.closest(".fold-batch-bar")?.querySelector(".batch-label");
        if (labelEl) {
          const foldedCount = (selector ? target.querySelectorAll(`${selector}.card-folded`) : target.querySelectorAll(".card-collapsible.card-folded"))?.length || 0;
          labelEl.textContent = foldedCount
            ? `已折叠 ${foldedCount}/${foldable}`
            : `共 ${foldable} 张`;
        }
      }
    });
  });
}

// 电脑卡片上的「一键解析」按钮：仅当论文有版本（可定位到 PDF）且尚未解析时显示。
// 作为依赖解析阅读的用户，PDF 已下载但解析未完成时，能从论文库直接推动解析，
// 不必先进阅读台等待。
function paperParseButton(paper, paperId) {
  const tags = paperStageTags(paper);
  const download = tags.find((t) => t.key === "download");
  const parse = tags.find((t) => t.key === "parse");
  if (!parse) return "";
  if (parse.done) return "";
  const versionId = getVersionId(paper);
  if (!versionId) return "";
  const busy = state.parsingPapers?.get?.(paperId);
  if (busy) {
    return `<button class="secondary" type="button" disabled>${busy === "downloading" ? "下载中…" : "解析中…"}</button>`;
  }
  // PDF 已下载 → 直接解析；否则按钮提示先下载（点击后会自动先下载再解析）。
  const label = download.done ? "解析" : "解析(PDF)";
  return `<button class="secondary parse-trigger" type="button" data-parse-paper="${paperId}" title="${download.done ? "在服务器解析该 PDF 为结构化 Markdown" : "先在服务器下载 PDF，随后解析为结构化 Markdown"}">${label}</button>`;
}

// 从论文库卡片触发解析：确保 PDF 已在服务器，然后提交 parse 任务并轮询到
// 完成或明确失败。期间按钮显示「解析中…」，完成后刷新论文库与阅读台。
async function parsePaperCard(paperId, button) {
  const paper = knownPapers().find((p) => getPaperId(p) === paperId);
  if (!paper) {
    showAlert("未找到该论文，可能已被移除，请刷新后重试。");
    return;
  }
  const versionId = getVersionId(paper);
  if (!versionId) {
    showAlert("该论文缺少版本信息，暂时无法解析。");
    return;
  }
  if (!state.parsingPapers) state.parsingPapers = new Map();
  if (state.parsingPapers.get(paperId)) return; // 防重入
  state.parsingPapers.set(paperId, "downloading");
  if (button) {
    button.disabled = true;
    button.textContent = "下载中…";
  }
  try {
    // 1) 确保 PDF 在服务器（若已存在会幂等跳过）
    const tags = paperStageTags(paper);
    const downloadDone = tags.find((t) => t.key === "download")?.done;
    if (!downloadDone) {
      try {
        const dl = await apiJson(`/paper-versions/${encodeURIComponent(versionId)}/download`, {
          method: "POST",
          headers: { "Idempotency-Key": `web-parse-pdf-${paperId}-${Date.now()}` },
          body: JSON.stringify({ force: true, options: {} }),
        });
        const dlJobId = dl.job_id || dl.id;
        if (dlJobId) {
          state.parsingPapers.set(paperId, "downloading");
          if (button) button.textContent = "下载中…";
          const dlOk = await waitJobSettled(dlJobId, 3 * 60 * 1000);
          if (!dlOk.ok) throw new Error(dlOk.message || "PDF 下载失败");
        }
      } catch (error) {
        showAlert(`下载 PDF 失败：${friendlyPdfError(error.message)}`);
        return;
      }
    }
    // 2) 提交解析任务
    state.parsingPapers.set(paperId, "parsing");
    if (button) button.textContent = "解析中…";
    const result = await apiJson(`/paper-versions/${encodeURIComponent(versionId)}/parse`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-parse-${paperId}-${Date.now()}` },
      body: JSON.stringify({ after_parse: [], options: {} }),
    });
    const jobId = result.job_id || result.id;
    if (!jobId) throw new Error("后端未返回解析任务 ID");
    const settled = await waitJobSettled(jobId, 10 * 60 * 1000);
    if (!settled.ok) {
      showAlert(`解析未完成：${settled.message || "后端返回失败状态"}`);
      return;
    }
    showAlert("解析完成，该论文的 Markdown 与结构化内容已就绪。");
  } catch (error) {
    showAlert(`触发解析失败：${error.message}`);
  } finally {
    state.parsingPapers.delete(paperId);
    state.pdfDownloading = { versionId: null, active: false, error: null };
    // 刷新论文库与阅读台，让卡片阶段标签即时更新。
    const seq = ++state.loadSeq;
    state.allPapers = [];
    await loadAllPapers();
    if (state.activeView === "reader") {
      state.workspaces.delete(paperId);
      loadWorkspace(paperId);
    }
    renderReader && renderReader();
  }
}

// 轮询一个异步任务直到成功/失败或超时；返回 { ok, message }。
async function waitJobSettled(jobId, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 3 * 60 * 1000);
  for (;;) {
    if (Date.now() > deadline) return { ok: false, message: "任务超时，仍在后台运行，稍后可刷新查看结果。" };
    await new Promise((resolve) => setTimeout(resolve, 4000));
    let job;
    try {
      job = await apiJson(`/jobs/${encodeURIComponent(jobId)}`);
    } catch (error) {
      return { ok: false, message: error.message };
    }
    if (["succeeded", "partial_succeeded"].includes(job.status)) return { ok: true };
    if (["retryable_failed", "terminal_failed", "cancelled"].includes(job.status)) {
      const err = job.error;
      let brief = String(job.status);
      if (err) {
        brief = typeof err === "string" ? err : (err.message || err.detail || err.reason || JSON.stringify(err));
      }
      return { ok: false, message: brief };
    }
  }
}

function updateBatchSelectionUi() {
  const count = document.getElementById("batchSelectionCount");
  if (count) count.textContent = `已选 ${state.batchSelected.size} 篇`;
  const checkboxes = document.querySelectorAll("[data-batch-check]");
  checkboxes.forEach((checkbox) => {
    checkbox.checked = state.batchSelected.has(checkbox.dataset.batchCheck);
  });
}

async function batchAddToNotebook() {
  const ids = Array.from(state.batchSelected);
  if (!ids.length) {
    showAlert("请先选择要加入笔记本的论文。");
    return;
  }
  // Await the server round-trip for each so the success message reflects what
  // actually persisted, instead of optimistically claiming success up-front.
  const results = await Promise.allSettled(ids.map((id) => toggleNotebook(id)));
  const ok = results.filter((r) => r.status === "fulfilled").length;
  const failed = results.length - ok;
  state.batchSelected.clear();
  updateBatchSelectionUi();
  if (failed === 0) {
    showAlert(`已将 ${ok} 篇论文加入笔记本。`);
  } else {
    showAlert(`已加入 ${ok} 篇，${failed} 篇加入失败，请稍后重试。`);
  }
}

// "相似论文" —— 基于共同主题 + 标题/摘要关键词的本地相似度（borrowed from
// arxiv-sanity's similarity idea, computed client-side over the loaded set so
// no extra backend call is needed). Ranks the top-N shared-topic overlaps and
// renders them inline in the paper body.
function showSimilarPapers(paperId) {
  const source = knownPapers().find((p) => getPaperId(p) === paperId);
  const bodyId = `paper-body-${paperId}`;
  const body = document.getElementById(bodyId);
  if (!source || !body) return;
  const similar = similarPapers(source, 4);
  const existing = body.querySelector(".similar-papers");
  if (existing) {
    existing.remove();
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "similar-papers";
  wrap.innerHTML = html`
    <div class="similar-papers-head"><strong>相似论文（相关度为相对排序，非绝对相似度）</strong></div>
    ${similar.length
      ? raw(similar.map((item) => `
        <div class="similar-paper-row" data-similar-open="${getPaperId(item)}">
          <span class="similar-score" title="相关度（相对最高分归一化）">${item.similarity}</span>
          <span class="similar-title">${escapeHtml(paperTitle(item))}</span>
        </div>
      `).join(""))
      : raw(`<p class="meta">未找到明显相似的论文。</p>`)}
  `;
  wrap.querySelectorAll("[data-similar-open]").forEach((row) => {
    row.setAttribute("role", "button");
    row.setAttribute("tabindex", "0");
    row.addEventListener("click", () => openPaper(row.dataset.similarOpen));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPaper(row.dataset.similarOpen);
      }
    });
  });
  body.appendChild(wrap);
}

// TF-lite similarity: score shared topics (weighted) + shared significant
// tokens in title/abstract. Deterministic and dependency-free.
function similarPapers(paper, maxResults = 4) {
  const sourceId = getPaperId(paper);
  const sourceTokens = significantTokens(`${paperTitle(paper)} ${paper.abstract || ""}`);
  const sourceTopics = new Set(normalizeTopics(paper));
  const scored = knownPapers()
    .filter((p) => getPaperId(p) !== sourceId)
    .map((p) => {
      const topics = new Set(normalizeTopics(p));
      let topicOverlap = 0;
      sourceTopics.forEach((t) => { if (topics.has(t)) topicOverlap += 1; });
      const tokens = significantTokens(`${paperTitle(p)} ${p.abstract || ""}`);
      let tokenOverlap = 0;
      tokens.forEach((t) => { if (sourceTokens.has(t)) tokenOverlap += 1; });
      const score = topicOverlap * 4 + Math.min(tokenOverlap, 6);
      return { paper: p, score };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, maxResults);
  const maxScore = scored.length ? Math.max(...scored.map((s) => s.score)) : 1;
  return scored.map((item) => ({
    ...item.paper,
    similarity: `${Math.round((item.score / maxScore) * 100)}`,
  }));
}

// Tokenize a text blob into a set of normalized significant (>3 char) tokens.
function significantTokens(text) {
  const tokens = new Set();
  for (const token of String(text || "").toLowerCase().split(/[^a-z0-9]+/)) {
    if (token.length > 3) tokens.add(token);
  }
  return tokens;
}

function openPaper(paperId) {
  // Switching to a different paper cancels any background report poll on the
  // previous paper.
  state.readerPollToken++;
  state.selectedPaperId = paperId;
  // 切换论文时重置 PDF 服务器下载状态，避免残留上一论文的进度/错误提示。
  state.pdfDownloading = null;
  history.pushState({ view: "reader", paper: paperId }, "", `/papers/${encodeURIComponent(paperId)}/read`);
  switchView("reader", false);
  renderReader();
  loadWorkspace(paperId);
}

function readerIndexFilter() {
  return String(document.getElementById("readerIndexSearch")?.value || "").trim().toLowerCase();
}

function bindReaderIndexSearch() {
  const input = document.getElementById("readerIndexSearch");
  if (!input) return;
  input.addEventListener("input", () => {
    renderReader();
    const countEl = document.getElementById("readerIndexCount");
    if (countEl) countEl.textContent = `共 ${document.querySelectorAll("#readerPaperList [data-reader-paper]").length} 篇`;
  });
}

function bindReaderNavButtons() {
  const prev = document.getElementById("readerPrevPaper");
  const next = document.getElementById("readerNextPaper");
  if (!prev || !next) return;
  const jump = (delta) => {
    const papers = readerDirectoryPapers();
    if (!papers.length) return;
    const idx = papers.findIndex((p) => getPaperId(p) === state.selectedPaperId);
    const target = idx === -1 ? papers[0] : papers[(idx + delta + papers.length) % papers.length];
    if (target) openPaper(getPaperId(target));
  };
  prev.addEventListener("click", () => jump(-1));
  next.addEventListener("click", () => jump(1));
}

// 更新上/下篇导航按钮的可用状态：目录为空或只有一篇时禁用。
function renderReaderPrevNextState(selectedInList) {
  const prev = document.getElementById("readerPrevPaper");
  const next = document.getElementById("readerNextPaper");
  if (!prev || !next) return;
  const papers = readerDirectoryPapers();
  const disabled = papers.length < 2;
  prev.disabled = disabled;
  next.disabled = disabled;
  // 当选中论文不在过滤目录内时禁用导航（避免跳到无关论文）。
  if (!selectedInList && state.selectedPaperId) {
    prev.disabled = true;
    next.disabled = true;
  }
}

// 阅读台目录的当前可见论文序列（应用搜索过滤），供目录渲染与上下篇导航共用。
function readerDirectoryPapers() {
  const filter = readerIndexFilter();
  const papers = filteredPapers();
  const currentPaper = selectedPaper();
  if (currentPaper && !papers.some((paper) => getPaperId(paper) === state.selectedPaperId)) {
    papers.unshift(currentPaper);
  }
  if (!filter) return papers;
  return papers.filter((paper) => {
    const haystack = `${paperTitle(paper)} ${normalizeTopics(paper).join(" ")} ${paper.authors ? (Array.isArray(paper.authors) ? paper.authors.map((a) => (typeof a === "string" ? a : a?.name)).join(" ") : "") : ""} ${paper.abstract || ""}`.toLowerCase();
    return haystack.includes(filter);
  });
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
  const papers = readerDirectoryPapers();
  if (!papers.length) {
    list.innerHTML = readerIndexFilter() ? emptyBlock("没有匹配的论文。") : emptyBlock("没有可阅读论文。");
    document.getElementById("documentContent").innerHTML = emptyBlock("选择论文后显示 PDF、Markdown、研读报告或证据。");
    document.getElementById("technicalCards").innerHTML = emptyBlock("暂无技术卡片。");
    return;
  }
  // Do NOT auto-select the first paper here. If no paper has been chosen yet,
  // the reader stays in its "enter reading" state (directory visible, no
  // document auto-loaded) until the user picks one.
  const selected = selectedPaper();
  const workspace = selectedWorkspace();
  const selectedInList = papers.some((paper) => getPaperId(paper) === state.selectedPaperId);
  list.innerHTML = papers
    .map((paper) => {
      const id = getPaperId(paper);
      return html`<button class="compact-button ${id === state.selectedPaperId ? "active" : ""}" type="button" data-reader-paper="${id}"><strong>${paperTitle(paper)}</strong><span class="meta">${normalizeTopics(paper).join(" / ") || "未标主题"}</span><span class="reader-stage-tags">${raw(renderStageTags(paper))}</span></button>`;
    })
    .join("");
  list.querySelectorAll("[data-reader-paper]").forEach((button) => {
    button.addEventListener("click", () => openPaper(button.dataset.readerPaper));
  });
  // 当前选中论文被搜索过滤掉时，仍保持文档区可见（导航按钮仍可循目录跳转）。
  renderReaderPrevNextState(selectedInList);
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
    const versionId = getVersionId(effectivePaper) || currentVersion(effectivePaper, workspace)?.id || "";
    if (url) {
      // 有 PDF artifact：直接在文档区 iframe 浏览，并提供明确的「下载到本地」按钮。
      // 下载是用户显式操作（点「下载 PDF」才触发浏览器保存），默认只做在线阅读。
      container.innerHTML = html`
        <iframe title="论文 PDF" src="${url}"></iframe>
        <div class="pdf-viewer-bar">
          <span class="meta">PDF 已保存在服务器，可直接在线阅读。</span>
          <button class="primary" type="button" data-download-local-pdf="${artifact.id}" title="把这份 PDF 保存到你的本地设备">
            下载 PDF 到本地
          </button>
        </div>
      `;
      container.querySelector("[data-download-local-pdf]")?.addEventListener("click", () => downloadPdfToLocal(artifact, effectivePaper));
    } else if (!versionId) {
      container.innerHTML = emptyBlock("论文版本缺少可用于下载的 PDF 信息。");
    } else if (state.pdfDownloading?.versionId === versionId && state.pdfDownloading?.active) {
      // 服务器后台获取中：允许用户继续等待，也允许中断后「下载到本地」。
      // 注意：loadingBlock/errorBlock 返回的是 HTML 字符串，必须直接字符串拼接，
      // 不能用外层 html`` 再包一次（会被 escapeHtml 转义成 &lt;div&gt; 文本）。
      container.innerHTML = `${loadingBlock("正在服务器获取 PDF，完成后自动在线展示。")}
        <p class="meta">获取在服务器进行，不占用你本地磁盘；「下载到本地」需待 PDF 就绪。</p>`;
    } else if (state.pdfDownloading?.versionId === versionId && state.pdfDownloading?.error) {
      container.innerHTML = `${errorBlock(`服务器获取 PDF 失败：${state.pdfDownloading.error}`)}
        <div class="paper-actions">
          <button class="primary" type="button" data-materialize-pdf="${versionId}">重新在服务器获取</button>
        </div>`;
      container.querySelector("[data-materialize-pdf]")?.addEventListener("click", () => materializePdf(versionId));
    } else {
      // PDF 尚未在服务器就绪：不再自动触发，改为用户主动点击「获取 PDF 以便在线阅读」。
      // emptyBlock 返回 HTML 字符串，直接拼接（外层 html`` 会把它转义成文本）。
      container.innerHTML = `${emptyBlock("这篇论文的 PDF 还没有在服务器准备好，暂时无法在线阅读。")}
        <div class="paper-actions">
          <button class="primary" type="button" data-fetch-pdf-on-server="${versionId}" title="在服务器下载并保存该 PDF，然后把浏览器切换到在线阅读">
            获取 PDF 以便在线阅读
          </button>
        </div>
        <p class="meta">「获取 PDF」只会把 PDF 保存到服务器供在线阅读，不会下载到你本地；需要保存到本地时，请在在线阅读界面点「下载 PDF 到本地」。</p>`;
      container.querySelector("[data-fetch-pdf-on-server]")?.addEventListener("click", () => ensurePdfOnServer(versionId));
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
    // errorBlock 返回 HTML 字符串，直接拼接避免被外层 html`` 转义成文本。
    container.innerHTML = `<p><a href="${url}" target="_blank" rel="noreferrer">打开 ${label} artifact</a></p>${errorBlock(`无法内联预览：${cached.error}`)}`;
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

// Lightweight, XSS-safe Markdown renderer for reading reports and drafts.
// All source text is HTML-escaped first, then structured into tags, so no raw
// HTML from the server can execute. Feeds re-render via innerHTML on escaped,
// rendered output; safe by construction (no raw passthrough).
function renderMarkdownToHtml(markdown) {
  if (!markdown) return "";
  const lines = String(markdown || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n");
  const out = [];
  const inCode = { active: false, buf: [] };
  const flushCode = () => {
    if (!inCode.active) return;
    out.push(`<pre><code>${escapeHtml(inCode.buf.join("\n"))}</code></pre>`);
    inCode.active = false;
    inCode.buf = [];
  };
  const startList = { tag: null }; // track ul/ol continuity

  const inline = (raw) => {
    let s = escapeHtml(raw);
    // fenced `inline code` -> <code>
    s = s.replace(/`([^`]+)`/g, (m, c) => `<code>${c}</code>`);
    // **bold** and __bold__
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
         .replace(/__([^_]+)__/g, "<strong>$1</strong>");
    // *italic* and _italic_
    s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*/g, "$1<em>$2</em>");
    // [text](url) — only allow http(s) hrefs, whitespace-stripped
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const clean = url.trim();
      if (/^https?:\/\//i.test(clean)) {
        return `<a href="${clean}" target="_blank" rel="noreferrer">${label}</a>`;
      }
      return m;
    });
    // bare http(s) URLs -> links
    s = s.replace(/https?:\/\/[^\s<>"'()]+/g, (url) =>
      `<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`
    );
    return s;
  };

  const flushList = () => {
    if (startList.tag) {
      out.push(`</${startList.tag}>`);
      startList.tag = null;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];
    // fenced code block ``` / ~~~
    const fence = line.match(/^[ \t]*(```|~~~)(.*)$/);
    if (fence) {
      flushList();
      if (!inCode.active) {
        flushCode();
        inCode.active = true;
        inCode.buf = [];
        // ignore optional language tag on opening fence
        if (i + 1 < lines.length && !/^[ \t]*(```|~~~)/.test(lines[i + 1])) {
          inCode.buf.push(lines[i + 1]);
          i++;
        }
      } else {
        flushCode();
      }
      continue;
    }
    if (inCode.active) {
      inCode.buf.push(line);
      continue;
    }
    if (/^[ \t]*$/.test(line)) {
      flushList();
      continue;
    }
    // headings # .. ######  (also 文本后 = / - )
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushList();
      const lvl = h[1].length;
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      continue;
    }
    // horizontal rule
    if (/^[ \t]*([-*_][ \t]*){3,}$/.test(line)) {
      flushList();
      out.push("<hr>");
      continue;
    }
    // blockquote
    if (/^[ \t]*&gt;\s?/.test(line) || /^[ \t]*>\s?/.test(line)) {
      flushList();
      out.push(`<blockquote>${inline(line.replace(/^[ \t]*>\s?/, ""))}</blockquote>`);
      continue;
    }
    // ordered list
    const ol = line.match(/^[ \t]*(\d+)[.)]\s+(.*)$/);
    if (ol) {
      if (startList.tag !== "ol") {
        flushList();
        out.push("<ol>");
        startList.tag = "ol";
      }
      out.push(`<li>${inline(ol[2])}</li>`);
      continue;
    }
    // unordered list
    const ul = line.match(/^[ \t]*[-*+]\s+(.*)$/);
    if (ul) {
      if (startList.tag !== "ul") {
        flushList();
        out.push("<ul>");
        startList.tag = "ul";
      }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }
    // setext heading (===  / ---)
    if (/^[ \t]*=+[ \t]*$/.test(line) && out.length) {
      out[out.length - 1] = out[out.length - 1].replace(/^<p>(.*)<\/p>$/, "<h1>$1</h1>");
      continue;
    }
    if (/^[ \t]*-+[ \t]*$/.test(line) && startList.tag === null && out.length) {
      // avoid treating as hr which already handled; treat as h2 only if prev was a <p>
      if (/^<p>/.test(out[out.length - 1])) {
        out[out.length - 1] = out[out.length - 1].replace(/^<p>(.*)<\/p>$/, "<h2>$1</h2>");
      }
      continue;
    }
    // plain paragraph (consume consecutive lines as one <p>)
    flushList();
    const para = [];
    para.push(line);
    while (i + 1 < lines.length && lines[i + 1].trim() && !/^[ \t]*[-*+]/.test(lines[i + 1]) && !/^[ \t]*\d+[.)]/.test(lines[i + 1]) && !/^#{1,6}\s/.test(lines[i + 1]) && !/^[ \t]*($|```|~~~|>)/.test(lines[i + 1])) {
      i++;
      para.push(lines[i]);
    }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  flushCode();
  flushList();
  return out.join("\n");
}

function renderTextDocument(container, text) {
  const body = document.createElement("div");
  body.className = "markdown-body";
  body.innerHTML = renderMarkdownToHtml(text);
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
  const topicFoldedCount = state.topics.filter((topic) => state.foldedCards.has(`topic:${topic.id}`)).length;
  container.innerHTML = html`
    <div class="fold-batch-bar">
      <span class="batch-label">卡片折叠：${topicFoldedCount ? `已折叠 ${topicFoldedCount}/${state.topics.length}` : `共 ${state.topics.length} 张`}</span>
      <button class="secondary compact-action" type="button" data-fold-all data-fold-all-scope="topicTree" title="把当前主题卡片全部折叠成标题条">全部折叠</button>
      <button class="secondary compact-action" type="button" data-expand-all data-fold-all-scope="topicTree" title="展开当前主题卡片">全部展开</button>
    </div>
    ${raw(state.topics.map((topic) => {
    const expanded = state.selectedTopicDigest && state.selectedTopicDigest.topicId === topic.id;
    const editing = state.editingTopicId === topic.id;
    const count = topicPaperCount(topic.id);
    const countLabel = state.browseMode === "all" ? "共 " : "今日 ";
    const viewLabel = state.browseMode === "all" ? "查看全部论文" : "查看今日论文";
    const topicKey = `topic:${topic.id}`;
    const topicFolded = state.foldedCards.has(topicKey);
    return html`
      <article class="topic-card card-collapsible ${topicFolded ? "card-folded" : ""}" data-fold-key="${topicKey}">
        <h3>${topicName(topic)}<button class="card-fold-toggle" type="button" data-fold="${topicKey}" title="${topicFolded ? "展开这张卡片" : "折叠这张卡片，只保留标题条"}"><span class="fold-icon">▸</span>${topicFolded ? "展开" : "收起"}</button></h3>
        <p>${topic.name_en || topic.description || "无主题说明"}</p>
        <div>${raw(normalizeKeywords(topic).map((keyword) => html`<span class="tag">${keyword}</span>`).join(" "))}</div>
        <p class="meta">${countLabel}${count} 篇</p>
        <div class="topic-card-actions">
          <button class="secondary" type="button" data-topic-edit="${topic.id}" title="编辑该主题">编辑</button>
          <button class="secondary ${expanded ? "active" : ""}" type="button" data-topic-digest="${topic.id}">${expanded ? "收起摘要" : "查看主题摘要"}</button>
          <button class="secondary" type="button" data-topic-papers="${topic.id}" title="跳转到论文库并筛选该主题">${viewLabel}</button>
          <button class="danger-secondary" type="button" data-topic-delete="${topic.id}" title="删除该主题">删除</button>
        </div>
        ${editing ? raw(renderTopicEditForm(topic)) : ""}
        <div class="card-fold-bar">
          <span class="fold-snippet" title="${escapeHtml(topicName(topic))}">${escapeHtml(topicName(topic))}<span class="meta">${escapeHtml(countLabel)}${count} 篇</span></span>
          <button class="card-fold-toggle" type="button" data-fold="${topicKey}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
        </div>
      </article>
      ${expanded ? raw(renderInlineTopicDigest(topic.id)) : ""}
    `;
  }).join(""))}
  `;
  bindCardFoldBatch(container);
  bindCardFoldToggles(container);
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
    const nbKey = `notebook:${id}`;
    const nbFolded = state.foldedCards.has(nbKey);
    const nbTitle = paperTitle(paper);
    return html`
      <article class="paper-card card-collapsible ${nbFolded ? "card-folded" : ""}" id="${cardId}" data-fold-key="${nbKey}">
        <div class="paper-card-header" data-toggle-nb="${id}">
          <div>
            <h3>${nbTitle}</h3>
            <p class="meta">${authorText(paper) || identifierText(paper)}</p>
          </div>
          <div class="paper-card-meta-actions">
            ${raw(normalizeTopics(paper).map((topic) => html`<span class="tag">${topic}</span>`).join(" "))}
            <button class="card-fold-toggle" type="button" data-fold="${nbKey}" title="${nbFolded ? "展开这张卡片" : "折叠这张卡片，只保留标题条"}"><span class="fold-icon">▸</span>${nbFolded ? "展开" : "收起"}</button>
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
        <div class="card-fold-bar">
          <span class="fold-snippet" title="${escapeHtml(nbTitle)}">${escapeHtml(nbTitle)}</span>
          <button class="card-fold-toggle" type="button" data-fold="${nbKey}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
        </div>
      </article>
    `;
  }).join("");

  bindCardFoldToggles(listContainer);
  // 笔记本列表顶部加批量折叠/展开工具条
  const firstNb = listContainer.querySelector(".paper-card, .empty-block");
  if (firstNb && !(firstNb.classList.contains("empty-block"))) {
    const nbCount = listContainer.querySelectorAll(".paper-card").length;
    const nbFoldedCount = listContainer.querySelectorAll(".paper-card.card-folded").length;
    const bar = document.createElement("div");
    bar.className = "fold-batch-bar";
    bar.innerHTML = html`
      <span class="batch-label">卡片折叠：${nbFoldedCount ? `已折叠 ${nbFoldedCount}/${nbCount}` : `共 ${nbCount} 张`}</span>
      <button class="secondary compact-action" type="button" data-fold-all data-fold-all-scope="notebookPaperList" title="把当前笔记本卡片全部折叠成标题条">全部折叠</button>
      <button class="secondary compact-action" type="button" data-expand-all data-fold-all-scope="notebookPaperList" title="展开当前笔记本卡片">全部展开</button>
    `;
    listContainer.insertBefore(bar, firstNb);
    bindCardFoldBatch(bar);
  }
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

// 用当前任务数据填充「类型」筛选下拉的选项（保留用户已选值）。
function populateJobKindFilter(jobs) {
  const select = document.getElementById("jobKindFilter");
  if (!select) return;
  const kinds = [...new Set(jobs.map((job) => job.kind).filter(Boolean))].sort();
  const current = select.value;
  const options = ['<option value="">全部类型</option>'];
  kinds.forEach((kind) => {
    options.push(`<option value="${escapeHtml(kind)}">${escapeHtml(jobKindLabel(kind) || kind)}</option>`);
  });
  select.innerHTML = options.join("");
  if (current && kinds.includes(current)) select.value = current;
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
  const kindFilter = document.getElementById("jobKindFilter")?.value || "";
  const statusFilter = document.getElementById("jobStatusFilter")?.value || "";
  const visibleJobs = state.jobs.filter((job) => {
    if (kindFilter && String(job.kind || "") !== kindFilter) return false;
    if (statusFilter && String(job.status || "") !== statusFilter) return false;
    return true;
  });
  if (!visibleJobs.length) {
    container.innerHTML = pipelineHtml + html`<h3 class="daily-history-title">全部异步任务（按最近更新倒序）</h3>` + emptyBlock("没有符合筛选条件的任务。");
    return;
  }
  const jobRows = visibleJobs.map((job, index) => {
    const status = job.status || "unknown";
    const actionError = state.jobActionErrors.get(job.id);
    const jobKey = `job:${job.id || job.job_id || index}`;
    const jobFolded = state.foldedCards.has(jobKey);
    const jobLabel = job.kind || job.id || "任务";
    return html`
      <div class="job-row card-collapsible ${jobFolded ? "card-folded" : ""}" data-fold-key="${jobKey}">
        <div>
          <strong>${jobLabel}</strong>
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
          <button class="card-fold-toggle" type="button" data-fold="${jobKey}" title="${jobFolded ? "展开这张卡片" : "折叠这张任务，只保留标题条"}"><span class="fold-icon">▸</span>${jobFolded ? "展开" : "收起"}</button>
        </div>
        <div class="card-fold-bar">
          <span class="fold-snippet" title="${escapeHtml(jobLabel)}">${escapeHtml(jobLabel)}<span class="meta">${escapeHtml(jobStatusLabel(status))}</span></span>
          <button class="card-fold-toggle" type="button" data-fold="${jobKey}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
        </div>
      </div>
    `;
  }).join("");
  const jobFoldedCount = visibleJobs.filter((job, index) => state.foldedCards.has(`job:${job.id || job.job_id || index}`)).length;
  container.innerHTML = pipelineHtml
    + html`<h3 class="daily-history-title">全部异步任务（按最近更新倒序）</h3>`
    + html`
      <div class="fold-batch-bar">
        <span class="batch-label">卡片折叠：${jobFoldedCount ? `已折叠 ${jobFoldedCount}/${visibleJobs.length}` : `共 ${visibleJobs.length} 张`}</span>
        <button class="secondary compact-action" type="button" data-fold-all data-fold-all-scope="jobList" title="把当前任务列表全部折叠成标题条">全部折叠</button>
        <button class="secondary compact-action" type="button" data-expand-all data-fold-all-scope="jobList" title="展开当前任务列表">全部展开</button>
      </div>
    `
    + jobRows;
  bindCardFoldToggles(container);
  bindCardFoldBatch(container);
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
  // 按论文过滤：找出与搜索词相关的所有关系（来源或目标论文标题/ID 命中）。
  const relFilter = String(document.getElementById("relationsSearch")?.value || "").trim().toLowerCase();
  const filteredItems = relFilter
    ? items.filter((rel) => {
        const hay = `${rel.from_title || ""} ${rel.to_title || ""} ${rel.from_paper_id || ""} ${rel.to_paper_id || ""}`.toLowerCase();
        return hay.includes(relFilter);
      })
    : items;
  if (header) {
    header.innerHTML = html`
      <div class="relations-header-row">
        <span class="pill">共 ${items.length} 条关系</span>
        <span class="pill">主题/关键词规则自动生成</span>
        <input type="search" id="relationsSearch" placeholder="按论文标题/ID过滤…" aria-label="按论文过滤关系" value="${(document.getElementById("relationsSearch")?.value || "")}">
        ${raw(filteredItems.length !== items.length ? html`<button class="secondary compact-action" type="button" id="relationsClearFilter">清除过滤</button>` : "")}
        ${raw(items.length ? html`<button class="secondary compact-action" type="button" id="relationsRebuildBtn">重建关系</button>` : "")}
      </div>
    `;
    const search = header.querySelector("#relationsSearch");
    if (search) {
      search.addEventListener("input", () => renderRelations());
      const clear = header.querySelector("#relationsClearFilter");
      if (clear) clear.addEventListener("click", () => { search.value = ""; renderRelations(); });
    }
  }
  if (!items.length) {
    container.innerHTML = html`
      <div class="relations-empty">
        <p>当前没有已生成的论文关系。</p>
        <p class="meta">点击下方按钮可基于共同主题与技术关键词，自动计算全部论文之间的关系。</p>
        <button class="primary" type="button" id="relationsRebuildBtn">自动重建/跑论文关系</button>
      </div>
    `;
  } else if (!filteredItems.length) {
    container.innerHTML = html`<div class="relations-empty"><p>没有命中「${escapeHtml(document.getElementById("relationsSearch")?.value || "")}」的论文关系。</p><p class="meta">可尝试其他关键词，或清除过滤查看全部关系。</p></div>`;
  } else {
    const byType = {};
    items.forEach((r) => { const t = r.relation_type || "relation"; byType[t] = (byType[t] || 0) + 1; });
    const typeSummary = Object.entries(byType)
      .map(([type, count]) => html`<span class="tag">${type} ${count}</span>`).join(" ");
    // Sort by confidence descending and paginate so the view stays responsive
    // even when a full rebuild produces thousands of edges. Filtering applies
    // before pagination so a search shows all matches.
    const sorted = [...filteredItems].sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0));
    const limit = Math.max(1, Number(state.relationsLimit) || 60);
    const visible = sorted.slice(0, limit);
    const totalShown = filteredItems.length;
    const showMore = filteredItems.length > limit;
    const extraSummary = relFilter
      ? html`<span class="tag">命中 ${totalShown}</span>`
      : "";
    const relFoldedCount = visible.filter((_, index) => state.foldedCards.has(`relation:${index}`)).length;
    container.innerHTML = html`
      <div class="fold-batch-bar">
        <span class="batch-label">卡片折叠：${relFoldedCount ? `已折叠 ${relFoldedCount}/${visible.length}` : `共 ${visible.length} 张`}</span>
        <button class="secondary compact-action" type="button" data-fold-all data-fold-all-scope="relationGraph" title="把当前关系卡片全部折叠成标题条">全部折叠</button>
        <button class="secondary compact-action" type="button" data-expand-all data-fold-all-scope="relationGraph" title="展开当前关系卡片">全部展开</button>
      </div>
      <div class="relations-type-summary">${raw(typeSummary)}${raw(extraSummary)}</div>
      <div class="relation-grid-inner">
        ${raw(visible.map((relation, index) => {
          const relKey = `relation:${index}`;
          const relFolded = state.foldedCards.has(relKey);
          const relTitle = `${relation.from_title || relation.from_paper_id || "来源论文"} → ${relation.to_title || relation.to_paper_id || "目标论文"}`;
          return html`
          <article class="relation-card card-collapsible ${relFolded ? "card-folded" : ""}" data-fold-key="${relKey}">
            <div class="relation-card-head">
              <span class="tag">${relation.relation_type || "relation"}</span>
              <span class="pill confidence-pill">置信度 ${(Number(relation.confidence) || 0).toFixed(2)}</span>
              <button class="card-fold-toggle" type="button" data-fold="${relKey}" title="${relFolded ? "展开这张卡片" : "折叠这张卡片，只保留标题条"}"><span class="fold-icon">▸</span>${relFolded ? "展开" : "收起"}</button>
            </div>
            <h3 title="${relation.from_title || relation.from_paper_id || ""}">${relation.from_title || relation.from_paper_id || "来源论文"}</h3>
            <p class="relation-arrow">→ 关联到</p>
            <h3 class="relation-to" title="${relation.to_title || relation.to_paper_id || ""}">${relation.to_title || relation.to_paper_id || "目标论文"}</h3>
            <p>${relation.reason || relation.summary || relation.description || "无关系说明"}</p>
            <p class="meta">${normalizeList(relation.evidence, ["items"]).map(evidenceText).join("；")}</p>
            <div class="card-fold-bar">
              <span class="fold-snippet" title="${escapeHtml(relTitle)}">${escapeHtml(relTitle)}</span>
              <button class="card-fold-toggle" type="button" data-fold="${relKey}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
            </div>
          </article>
        `;
        }).join(""))}
      </div>
      ${raw(showMore ? html`<button class="secondary" type="button" id="relationsShowMoreBtn">显示更多（${totalShown - limit} 条）</button>` : "")}
    `;
    bindCardFoldBatch(container);
    bindCardFoldToggles(container);
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
        <div class="draft-revise">
          <details class="draft-revise-details">
            <summary>按章节修订（由大模型改写该章节）</summary>
            <label>选择要修订的章节
              <select id="reviseSection">
                <option value="">（整篇草稿）</option>
                <option value="技术问题">技术问题</option>
                <option value="发明内容">发明内容</option>
                <option value="技术方案">技术方案</option>
                <option value="有益效果">有益效果</option>
                <option value="实施例">实施例</option>
                <option value="权利要求">权利要求</option>
                <option value="摘要">摘要</option>
              </select>
            </label>
            <label>修订要求
              <textarea id="reviseInstruction" rows="3" placeholder="例如：把技术问题写得更加聚焦在显存占用上，并补充一行与现有方案的对比。"></textarea>
            </label>
            <button class="primary" type="button" id="submitReviseButton">提交修订</button>
            <p class="meta draft-revise-hint">提交后将生成一个修订版本；生成期间请在「任务中心」查看进度。</p>
          </details>
        </div>
      </section>
    `
    : emptyBlock(selectedCandidate ? "该候选尚未生成草稿；需先人工审批，再点击生成草稿。" : "暂无交底书草稿。");
  preview.innerHTML = html`${raw(candidateHtml)}${raw(stageShell)}${raw(draftShell)}`;
  bindCardFoldToggles(preview);
  if (selectedDraft) {
    document.getElementById("selectedDraftBody").textContent = selectedDraft.markdown || JSON.stringify(selectedDraft, null, 2);
    const reviseBtn = document.getElementById("submitReviseButton");
    if (reviseBtn) reviseBtn.addEventListener("click", () => submitDraftRevise(selectedDraft.id));
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
    button.addEventListener("click", () => withButtonLoading(button, () => approveCandidate(button.dataset.candidateApprove, false)));
  });
  preview.querySelectorAll("[data-candidate-override]").forEach((button) => {
    button.addEventListener("click", () => withButtonLoading(button, () => approveCandidate(button.dataset.candidateOverride, true)));
  });
  preview.querySelectorAll("[data-candidate-prior-art]").forEach((button) => {
    button.addEventListener("click", () => withButtonLoading(button, () => runPriorArtCheck(button.dataset.candidatePriorArt)));
  });
  preview.querySelectorAll("[data-candidate-draft]").forEach((button) => {
    button.addEventListener("click", () => withButtonLoading(button, () => generateDraft(button.dataset.candidateDraft)));
  });
  preview.querySelectorAll("[data-draft-select]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDraftId = button.dataset.draftSelect;
      renderPatentWorkspace();
    });
  });
}

// Submit a section-level LLM revision of the selected patent draft, reusing the
// existing /patent-drafts/{id}/revise endpoint so "按章节修订" is genuinely usable.
async function submitDraftRevise(draftId) {
  if (!draftId) return;
  const section = document.getElementById("reviseSection")?.value || "";
  const instruction = document.getElementById("reviseInstruction")?.value?.trim() || "";
  if (!instruction) {
    showAlert("请填写修订要求后再提交。");
    return;
  }
  const button = document.getElementById("submitReviseButton");
  try {
    await withButtonLoading(button, async () => {
      await apiJson(`/patent-drafts/${encodeURIComponent(draftId)}/revise`, {
        method: "POST",
        headers: {
          "Idempotency-Key": `web-revise-${draftId}-${section || "full"}-${Date.now()}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ section: section || null, instruction, notes: "web 修订" }),
      });
      showAlert("修订任务已提交，可在「任务中心」查看进度。");
    });
  } catch (error) {
    showAlert(`修订提交失败：${error.message}`);
  }
}

function candidateCard(candidate) {
  const status = String(candidate.status || "created").toLowerCase();
  const draft = state.drafts.find((item) => item.invention_candidate_id === candidate.id);
  const priorArt = priorArtJob(candidate.id);
  const priorArtSucceeded = priorArt?.status === "succeeded";
  const overrideId = `override-${candidate.id}`;
  const candKey = `candidate:${candidate.id}`;
  const candFolded = state.foldedCards.has(candKey);
  const candTitle = candidate.title || candidate.id;
  return html`
    <article class="candidate-card ${candidate.id === state.selectedCandidateId ? "active" : ""} card-collapsible ${candFolded ? "card-folded" : ""}" data-fold-key="${candKey}">
      <button class="link-button" type="button" data-candidate-select="${candidate.id}"><strong>${candidate.title || candidate.id}</strong></button>
      <span class="state ${status}">${status}</span>
      <button class="card-fold-toggle" type="button" data-fold="${candKey}" title="${candFolded ? "展开这张卡片" : "折叠这张卡片，只保留标题条"}"><span class="fold-icon">▸</span>${candFolded ? "展开" : "收起"}</button>
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
      <div class="card-fold-bar">
        <span class="fold-snippet" title="${escapeHtml(candTitle)}">${escapeHtml(candTitle)}<span class="meta">${escapeHtml(status)}</span></span>
        <button class="card-fold-toggle" type="button" data-fold="${candKey}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
      </div>
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
  catalog.innerHTML = definitions.length ? definitions.map((workflow, wfIndex) => {
    const wfKey = `workflow:${workflow.id || workflow.name || wfIndex}`;
    const wfFolded = state.foldedCards.has(wfKey);
    return html`
    <section class="panel workflow-card card-collapsible ${wfFolded ? "card-folded" : ""}" data-fold-key="${wfKey}">
      <div class="panel-heading">
        <div><h2>${workflow.name}</h2><p>${workflow.description}</p></div>
        <span class="state ${workflow.enabled ? "success" : "retryable_failed"}">${workflow.enabled ? "可运行" : "待配置"}</span>
        <button class="card-fold-toggle" type="button" data-fold="${wfKey}" title="${wfFolded ? "展开这张卡片" : "折叠这张卡片，只保留标题条"}"><span class="fold-icon">▸</span>${wfFolded ? "展开" : "收起"}</button>
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
      <div class="card-fold-bar">
        <span class="fold-snippet" title="${escapeHtml(workflow.name)}">${escapeHtml(workflow.name)}<span class="meta">${workflow.enabled ? "可运行" : "待配置"}</span></span>
        <button class="card-fold-toggle" type="button" data-fold="${wfKey}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
      </div>
    </section>
  `;
  }).join("") : emptyBlock("后端未返回内置工作流。");
  bindCardFoldToggles(catalog);
  bindCardFoldBatch(catalog);
  const history = normalizeList(state.workflows?.runs, ["items", "runs"]);
  runs.innerHTML = history.length ? html`
    <div class="fold-batch-bar">
      <span class="batch-label">卡片折叠：共 ${history.length} 张</span>
      <button class="secondary compact-action" type="button" data-fold-all data-fold-all-scope="workflowRuns" title="把当前运行历史卡片全部折叠成标题条">全部折叠</button>
      <button class="secondary compact-action" type="button" data-expand-all data-fold-all-scope="workflowRuns" title="展开当前运行历史卡片">全部展开</button>
    </div>
    ${raw(history.map((run, runIndex) => {
      const runKey = `workflowrun:${run.id || runIndex}`;
      const runFolded = state.foldedCards.has(runKey);
      const runTitle = run.run_type || run.id;
      return html`
    <article class="workflow-run card-collapsible ${runFolded ? "card-folded" : ""}" data-fold-key="${runKey}">
      <div><strong>${runTitle}</strong><p class="meta">${run.created_at || ""} · ${run.id}</p></div>
      <div class="workflow-step-counts">${raw(Object.entries(run.steps || {}).map(([kind, counts]) => html`<span class="tag">${jobKindLabel(kind)} ${Object.values(counts).reduce((sum, value) => sum + value, 0)}</span>`).join(""))}</div>
      <span class="state ${String(run.status || "queued").toLowerCase()}">${jobStatusLabel(run.status)}</span>
      <button class="card-fold-toggle" type="button" data-fold="${runKey}" title="${runFolded ? "展开这张卡片" : "折叠这张运行记录"}"><span class="fold-icon">▸</span>${runFolded ? "展开" : "收起"}</button>
      <div class="card-fold-bar">
        <span class="fold-snippet" title="${escapeHtml(runTitle)}">${escapeHtml(runTitle)}<span class="meta">${escapeHtml(jobStatusLabel(run.status))}</span></span>
        <button class="card-fold-toggle" type="button" data-fold="${runKey}" data-fold-restore title="展开这张卡片"><span class="fold-icon">▸</span>展开</button>
      </div>
    </article>
  `;
    }).join(""))}
  ` : emptyBlock("尚无工作流运行；触发论文发现后会自动创建。 ");
  bindCardFoldToggles(runs);
  bindCardFoldBatch(runs);
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
  const healthy = adapters.filter(adapterHealthy);
  const degraded = adapters.filter((a) => !adapterHealthy(a));
  if (containerId !== "settingsAdapterHealth") {
    const allOk = degraded.length === 0;
    container.innerHTML = adapters.length
      ? html`
        <div class="gate-item ${allOk ? "pass" : "warn"}">
          <span class="gate-icon">${allOk ? "✓" : "!"}</span>
          <div>
            <strong>系统能力摘要</strong>
            <p class="meta">${healthy.length}/${adapters.length} 项后端能力可用。</p>
            ${degraded.length ? raw(degraded.map((a) => html`
              <p class="meta adapter-degraded">
                ${escapeHtml(adapterLabel(a))} 不可用：${escapeHtml(a.message || a.error || "状态未知")}
              </p>
            `).join("")) : ""}
            ${degraded.length ? raw(`<button class="secondary adapter-fix-link" type="button" data-goto-settings>前往设置页查看详情</button>`) : ""}
          </div>
        </div>
      `
      : emptyBlock("健康接口未返回系统能力明细。");
    const fixLink = container.querySelector("[data-goto-settings]");
    if (fixLink) fixLink.addEventListener("click", () => switchView("settings", true));
    return;
  }
  container.innerHTML = adapters.length ? adapters.map(adapterHealthCard).join("") : emptyBlock("健康接口未返回适配器明细。");
}

function adapterLabel(adapter) {
  const names = {
    mineru: "文档解析（MinerU）",
    arxiv: "论文发现（arXiv）",
    analysis: "大模型研读/翻译",
    patent: "专利查新",
    dify: "Dify 工作流",
    openai: "大模型研读/翻译",
  };
  return names[String(adapter.name || adapter.adapter || "").toLowerCase()] || adapter.name || adapter.adapter || "适配器";
}

// Compose the text a paper search should match against. We deliberately avoid
// JSON.stringify(paper) (which matched hidden ids/dates/metadata too broadly) and
// instead match the human-meaningful fields only.
function paperSearchableText(paper) {
  return [
    paper?.canonical_title,
    paper?.title,
    (paper?.authors || []).map((a) => (typeof a === "string" ? a : a?.name)).join(" "),
    (paper?.identifiers || []).map((i) => i?.value).join(" "),
    paper?.abstract,
    paper?.translated_abstract,
    paper?.method_summary,
    (paper?.topics || []).map((t) => (typeof t === "string" ? t : `${t?.name_zh || ""} ${t?.name_en || ""}`)).join(" "),
  ].filter(Boolean).join(" ").toLowerCase();
}

function filteredPapers() {
  const query = document.getElementById("globalSearch")?.value.trim().toLowerCase() || "";
  const topic = document.getElementById("topicFilter")?.value || "";
  const status = document.getElementById("statusFilter")?.value || "";
  const sortBy = document.getElementById("sortFilter")?.value || "";
  // When a specific date is selected it is backed by loadAll(), which loads the
  // date-scoped papers into state.papers; prefer that so the '日期' filter
  // actually narrows the library grid instead of silently doing nothing.
  // When a specific date is selected it is backed by loadAll(), which loads the
  // date-scoped papers into state.papers; prefer that so the '日期' filter
  // actually narrows the library grid instead of silently doing nothing.
  const dateValue = document.getElementById("dateFilter")?.value || "";
  const source = dateValue ? state.papers : (state.allPapers.length ? state.allPapers : state.papers);
  const filtered = source.filter((paper) => {
    const text = paperSearchableText(paper);
    const topics = normalizeTopics(paper);
    const paperStatus = String(paper.status || "").toLowerCase();
    return (!query || text.includes(query))
      && (!topic || topics.includes(topic))
      && (!status || paperStatus.includes(status));
  });
  // Default sort is "smart" (LLM quality ⊕ community heat), giving high-value
  // analyzed papers top billing and keeping hot-but-unanalyzed ones visible.
  if (sortBy === "" || sortBy === "smart") {
    return [...filtered].sort((a, b) => paperRankScore(b) - paperRankScore(a));
  }
  if (sortBy === "score") {
    return [...filtered].sort((a, b) => (paperModelScore(b) ?? -1) - (paperModelScore(a) ?? -1));
  }
  if (sortBy === "hot") {
    return [...filtered].sort((a, b) => paperHeatScore(b) - paperHeatScore(a));
  }
  if (sortBy === "newest") {
    return [...filtered].sort((a, b) => String(b.first_publication_date || "").localeCompare(String(a.first_publication_date || "")));
  }
  if (sortBy === "oldest") {
    return [...filtered].sort((a, b) => String(a.first_publication_date || "").localeCompare(String(b.first_publication_date || "")));
  }
  return filtered;
}

// Multi-factor "hotness" score: citation count + notebook selection + progress
// depth + recency, mirroring arxiv-sanity's popular/recent ranking idea.
function paperHeatScore(paper) {
  let score = 0;
  const citations = paperCitationCount(paper);
  score += Math.min(citations, 50) * 2;
  if (state.notebookPapers.has(getPaperId(paper))) score += 20;
  else if (paper.selected) score += 8;
  const stageTags = paperStageTags(paper);
  const done = stageTags.filter((tag) => tag.done).length;
  score += done * 5;
  return score;
}

// The model's overall quality score (0-10) from the reading report, when one
// exists for the paper. Reads through the loaded workspace so a paper card can
// show the LLM's judgement of innovation/quality, not just citation heat.
function paperModelScore(paper) {
  const id = getPaperId(paper);
  const ws = state.workspaces?.get(id);
  const score = ws?.report?.score || paper?.metadata?.report_score;
  if (score && typeof score.overall === "number") return Math.round(score.overall * 10) / 10;
  if (typeof score === "number") return Math.round(score * 10) / 10;
  return null;
}

// Composite smart score: prefers the LLM's innovation/quality judgement when
// available, blended with community heat so analyzed papers surface first while
// hot-but-unanalyzed papers still rank reasonably.
function paperRankScore(paper) {
  const model = paperModelScore(paper);
  const heat = paperHeatScore(paper);
  if (model == null) return heat; // nothing analyzed yet → fall back to heat
  return model * 10 + heat; // model score (0-10 → 0-100) dominates
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
  const enabledTopics = state.topics.filter((topic) => topic.enabled !== false);
  const topicIds = enabledTopics.slice(0, 6).map((topic) => topic.id);
  try {
    if (enabledTopics.length > 6) {
      showAlert(`注意：本次发现仅覆盖前 6 个启用主题（共 ${enabledTopics.length} 个），其余主题请用「定向发现」单独跑。`);
    } else {
      showAlert(`正在提交发现任务（回溯 ${lookback} 天）...`);
    }
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

// Keyboard-accessible overlay lifecycle. Maintains a simple focus trap: focus
// moves into the dialog on open, Tab cycles inside the panel, and focus is
// returned to the element that opened it on close.
function _focusOverlay(overlayId) {
  const overlay = document.getElementById(overlayId);
  if (!overlay) return;
  overlay.classList.remove("hidden");
  document.body.classList.add("has-overlay");
  const panel = overlay.querySelector(".overlay-panel");
  const first = panel?.querySelector("button, input, select, textarea, [href], [tabindex]:not([tabindex='-1'])");
  if (first) first.focus();
  else if (panel) { panel.setAttribute("tabindex", "-1"); panel.focus(); }
  overlay._lastFocus = document.activeElement === overlay ? null : document.activeElement;
}

function _unfocusOverlay(overlay) {
  overlay.classList.add("hidden");
  document.body.classList.remove("has-overlay");
  if (overlay._lastFocus && typeof overlay._lastFocus.focus === "function") {
    overlay._lastFocus.focus();
  }
  overlay._lastFocus = null;
}

function openHelp() {
  renderHelpContent();
  _focusOverlay("helpOverlay");
}

function closeHelp() {
  _unfocusOverlay(document.getElementById("helpOverlay"));
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
  _focusOverlay("directedDiscoveryOverlay");
}

function closeDirectedDiscovery() {
  _unfocusOverlay(document.getElementById("directedDiscoveryOverlay"));
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
        _unfocusOverlay(overlay);
      }
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      document.querySelectorAll(".overlay:not(.hidden)").forEach((overlay) => {
        _unfocusOverlay(overlay);
      });
      return;
    }
    // Simple focus trap for the open modal: keep Tab within the panel.
    if (event.key === "Tab") {
      const open = [...document.querySelectorAll(".overlay:not(.hidden)")].filter(
        (o) => document.body.classList.contains("has-overlay"),
      );
      const overlay = open[open.length - 1];
      if (!overlay) return;
      const panel = overlay.querySelector(".overlay-panel");
      if (!panel) return;
      const focusables = panel.querySelectorAll("button, input, select, textarea, [href], [tabindex]:not([tabindex='-1'])");
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  document.addEventListener("keydown", handleReaderShortcuts);
}

// Reader keyboard shortcuts (j/k *view navigation*, [ ] *tab switch*, s *bookmark*).
// Guards: only active on the reader view, and never while a form control has
// focus so the user can type search queries / notes freely.
function handleReaderShortcuts(event) {
  if (state.activeView !== "reader") return;
  const target = event.target;
  const tag = target && target.tagName ? target.tagName.toLowerCase() : "";
  if (["input", "textarea", "select"].includes(tag) || target?.isContentEditable) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const key = event.key.toLowerCase();
  const tabs = ["pdf", "markdown", "report", "evidence"];
  if (key === "j" || key === "k") {
    const papers = filteredPapers();
    if (papers.length < 2 || !state.selectedPaperId) return;
    const idx = papers.findIndex((p) => getPaperId(p) === state.selectedPaperId);
    if (idx === -1) return;
    const nextIdx = key === "j" ? (idx + 1) % papers.length : (idx - 1 + papers.length) % papers.length;
    event.preventDefault();
    openPaper(getPaperId(papers[nextIdx]));
  } else if (key === "[") {
    const idx = tabs.indexOf(state.selectedTab);
    state.selectedTab = tabs[(idx - 1 + tabs.length) % tabs.length];
    event.preventDefault();
    syncDocTabs();
    renderReader();
  } else if (key === "]") {
    const idx = tabs.indexOf(state.selectedTab);
    state.selectedTab = tabs[(idx + 1) % tabs.length];
    event.preventDefault();
    syncDocTabs();
    renderReader();
  } else if (key === "s") {
    if (!state.selectedPaperId) return;
    event.preventDefault();
    toggleNotebook(state.selectedPaperId);
  }
}

function syncDocTabs() {
  document.querySelectorAll("[data-doc-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.docTab === state.selectedTab);
  });
}

// 在服务器端下载论文 PDF 并持久化为 artifact，成功后强制刷新 workspace
// 使阅读台自动切换到 iframe 在线展示。全程只在服务器执行，不涉及浏览器
// 「下载到本地」。state.pdfDownloading 记录进行中/失败状态，避免重复触发。
async function ensurePdfOnServer(versionId) {
  if (!versionId) return;
  const paperId = state.selectedPaperId;
  // 防重入：同一版本正在自动下载中，直接复用进行中的任务。
  if (state.pdfDownloading?.versionId === versionId && state.pdfDownloading?.active) {
    renderReader();
    return;
  }
  state.pdfDownloading = { versionId, active: true, error: null };
  renderReader();
  try {
    const result = await apiJson(`/paper-versions/${encodeURIComponent(versionId)}/download`, {
      method: "POST",
      headers: { "Idempotency-Key": `web-server-pdf-${versionId}` },
      body: JSON.stringify({ force: false, options: {} }),
    });
    const jobId = result.job_id || result.id;
    if (!jobId) throw new Error("后端未返回下载任务 ID");
    // 服务器下载可能需要一段时间（PDF 较大/网络较慢），轮询最多约 3 分钟。
    const deadline = Date.now() + 3 * 60 * 1000;
    for (;;) {
      if (Date.now() > deadline) {
        state.pdfDownloading = { versionId, active: false, error: null };
        renderReader();
        showAlert("服务器仍在后台下载 PDF，稍后重开会话即可在线查看。");
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 3000));
      const job = await apiJson(`/jobs/${encodeURIComponent(jobId)}`);
      if (["succeeded", "partial_succeeded"].includes(job.status)) break;
      if (["retryable_failed", "terminal_failed", "cancelled"].includes(job.status)) {
        // 优先提取后端错误对象里简短可读的 message，避免把完整 JSON 诊断抛给用户。
        const brief = (() => {
          const err = job.error;
          if (!err) return job.status;
          if (typeof err === "string") return err;
          const msg = err.message || err.detail || err.reason;
          return msg ? String(msg) : JSON.stringify(err);
        })();
        throw new Error(brief);
      }
    }
    // 下载成功：清空缓存让 workspace 重新拉取（此时应包含 pdf artifact）。
    state.workspaces.delete(paperId);
    await loadWorkspace(paperId);
    if (state.activeView !== "reader") return;
    const ws = state.workspaces.get(paperId) || {};
    const hasPdf = ws.artifacts?.some((a) =>
      ["pdf", "source_pdf"].includes(a.artifact_type) || (a.media_type || "").includes("pdf")
    );
    state.pdfDownloading = { versionId, active: false, error: hasPdf ? null : "已下载但未生成可在线展示的 PDF。" };
    showAlert(hasPdf ? "PDF 已就绪，可直接在线阅读。" : "PDF 下载完成，但暂不可在线展示。");
    renderReader();
  } catch (error) {
    const friendly = friendlyPdfError(error.message);
    state.pdfDownloading = { versionId, active: false, error: friendly };
    renderReader();
    showAlert(`服务器下载 PDF 失败：${friendly}`);
  }
}

// 把后端原始错误映射为面向用户的中文友好说明：主要是处理元数据缺 PDF URL
// 这类「非网络故障」的数据缺失场景，避免用户看到英文报错无从下手。
function friendlyPdfError(message) {
  const text = String(message || "");
  if (/no pdf url|缺少pdf链接|无pdf地址/i.test(text)) {
    return "该论文服务器的源数据中缺少 PDF 地址，暂时无法自动下载；可稍后再试，或由管理员补充该论文的 PDF 来源后重试。";
  }
  return text;
}

// 手动重试入口：仅在自动下载失败后由「重新在服务器下载」按钮触发。
async function materializePdf(versionId) {
  await ensurePdfOnServer(versionId);
}

// 把已在服务器就绪的 PDF artifact「下载到本地」。只有用户点击「下载 PDF 到
// 本地」按钮才触发；fetch PDF 字节 → 生成 blob → 用 a[download] 触发浏览器
// 保存，等价于普通文件下载（浏览器下载栏会出现）。
async function downloadPdfToLocal(artifact, paper) {
  if (!artifact?.id) {
    showAlert("暂无可下载的 PDF 文件。");
    return;
  }
  const button = document.querySelector("[data-download-local-pdf]");
  try {
    if (button) {
      button.disabled = true;
      button.textContent = "正在准备下载…";
    }
    const url = `${API_BASE}/artifacts/${encodeURIComponent(artifact.id)}/download`;
    const response = await fetch(url);
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload?.error?.message || payload?.detail || message;
      } catch (_) { /* keep HTTP status */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    // 用论文标题构造文件名（保留 .pdf 后缀）。
    const title = paperTitle(paper) || artifact.id;
    const slug = String(title).slice(0, 80).replace(/[\\/:*?"<>|\s]+/g, "_");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${slug || "paper"}.pdf`;
    link.rel = "noreferrer";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    showAlert("PDF 已开始下载到本地。");
  } catch (error) {
    showAlert(`下载 PDF 失败：${friendlyPdfError(error.message)}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "下载 PDF 到本地";
    }
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

// Disable a button and show a "处理中" state while an async action runs, then
// restore it. Prevents double-submits of strong business actions (candidate
// approval, draft generation, discovery submission, prior-art checks).
async function withButtonLoading(button, fn) {
  if (!button || button.disabled) return;
  const original = button.textContent;
  button.disabled = true;
  button.classList.add("is-loading");
  button.setAttribute("aria-busy", "true");
  try {
    await fn();
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    button.textContent = original;
  }
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

// Wrap query hits in <mark> for search results. Returns an html() fragment;
// use inside an html`...` template (it is not pre-escaped).
function highlightQuery(text, query) {
  const q = String(query || "").trim();
  if (!q || !text) return html`${text}`;
  const lower = text.toLowerCase();
  const idx = lower.indexOf(q.toLowerCase());
  if (idx === -1) return html`${text}`;
  return html`${text.slice(0, idx)}<mark>${text.slice(idx, idx + q.length)}</mark>${text.slice(idx + q.length)}`;
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

// Enriched metadata surfaced on the paper card (borrowed from enrichment /
// adapter fields). All reads are defensive so a missing field degrades to a
// silent no-op rather than breaking the card.
function paperCodeUrl(paper) {
  const meta = paper?.metadata || {};
  return String(meta.code_url || meta.codeUrl || meta.github_url || meta.repo_url || "").trim() || "";
}

function paperCitationCount(paper) {
  const meta = paper?.metadata || {};
  const raw = meta.citations ?? meta.citation_count ?? meta.influence_score ?? meta.stars;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function paperArxivUrl(paper) {
  const identifiers = normalizeList(paper?.identifiers, ["items"]);
  const arxiv = identifiers.find((item) => String(item.type).toLowerCase() === "arxiv");
  if (arxiv && arxiv.value) {
    const value = String(arxiv.value);
    return `https://arxiv.org/abs/${value.replace(/^arXiv:/i, "").replace(/^abs\//, "").replace(/^pdf\//, "")}`;
  }
  return "";
}

function paperMetaTags(paper) {
  const tags = [];
  const citations = paperCitationCount(paper);
  if (citations > 0) tags.push({ cls: "citation", label: `引用 ${citations}` });
  if (paperCodeUrl(paper)) tags.push({ cls: "code", label: "代码" });
  return tags;
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

// 当前浏览基准论文集：date 模式用浏览日期的论文，all 模式用全量论文库。
// 这是主题卡片数字、主题覆盖统计等的统一来源，保证切换日期时主题数量随之变化。
// 默认/今日视图时 state.papers（今日论文，loadAll 已加载）即为当日论文；
// 浏览历史日期时 historyPapers 已按 browseDate 拉取，使用它（可为空=该日无论文）。
function activeBrowsePapers() {
  if (state.browseMode === "all") return state.allPapers || [];
  const browsingToday = state.browseDate === today;
  if (state.historyPapers && state.historyPapers.length) return state.historyPapers;
  if (browsingToday) return state.papers || [];
  return state.historyPapers || [];
}

// 统计某主题在当前浏览基准下的论文数。与 renderDailyByTopic 的分组逻辑一致：
// 一篇论文只统计一次（按论文的 topics 精确匹配），父/子主题各自按其自身 id 计数。
// 若主题在基准中一篇都没有，回退到今日日报中的命中数（用于「今日」默认视图）。
function topicPaperCount(topicId) {
  const papers = activeBrowsePapers();
  let count = 0;
  const seen = new Set();
  papers.forEach((paper) => {
    const pid = paper?.id || paper?.paper_id || "";
    if (seen.has(pid)) return;
    const topics = paperTopicObjs(paper);
    if (topics.some((topic) => topic.id === topicId)) {
      seen.add(pid);
      count += 1;
    }
  });
  return count;
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
  const label = adapterLabel(adapter);
  const detail = adapter.detail || {};
  const resolved = detail.resolved_url;
  const detailText = resolved
    ? ` · ${detail.used_discovered ? "自动发现连接 " : "连接 "}${resolved}`
    : "";
  return html`
    <div class="gate-item ${ok ? "pass" : "warn"}">
      <span class="gate-icon">${ok ? "✓" : "!"}</span>
      <div><strong>${label}</strong><p class="meta">${status} ${adapter.message || adapter.error || adapter.version || ""}${detailText}</p></div>
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
