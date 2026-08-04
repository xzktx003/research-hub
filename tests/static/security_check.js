const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const app = fs.readFileSync(path.join(root, "web", "app.js"), "utf8");
const index = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");

const attackPayloads = [
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "javascript:alert(1)",
];

function innerHtmlAssignments(source) {
  return source
    .split("\n")
    .map((line, index) => ({ index: index + 1, line }))
    .filter(({ line }) => line.includes("innerHTML"));
}

assert(!app.includes("localStorage"), "API keys must not use localStorage");
assert(
  !app.includes("researchHubApiKey") && !app.includes("saveApiKeyButton"),
  "Platform API key handling was removed; anonymous write should go through the same-origin local server",
);
assert(
  app.includes("override_prior_art: override"),
  "Prior-art override approval must be explicit in the backend request contract",
);
assert(
  app.includes("structured_combination: structured")
    && app.includes("coupling_interface:")
    && app.includes("data_or_control_flow: structured.data_control_flow")
    && app.includes("why_not_juxtaposition: structured.non_parallel_explanation")
    && app.includes("expected_joint_effect: structured.joint_effect")
    && app.includes("approval_confirmations: approval.confirmations")
    && app.includes("approver: approval.approver"),
  "Candidate creation must forward structured combination fields and approval confirmations",
);
assert(
  app.includes("candidateEvidence(papers, structured)")
    && app.includes('["coupling_interface", structured.coupling, "fact"]')
    && app.includes('report_field: reportField')
    && app.includes("paper_version:"),
  "Candidate creation must include field-level and source-level provenance evidence",
);
assert(
  app.includes("const approval = candidateApprovalInput();")
    && app.includes("approver: approval.approver")
    && app.includes("approval_confirmations: approval.confirmations"),
  "Candidate approval must forward approver and four confirmation fields",
);
assert(
  app.includes('headers: { "Idempotency-Key": `web-job-${action}-${jobId}` }')
    && app.includes('body: JSON.stringify({ action, reason })'),
  "Job retry/cancel actions must use the existing idempotent jobs API contract",
);
assert(
  app.includes("canRetryJob(job)") && app.includes("canCancelJob(job)"),
  "Job center must render retry/cancel controls by status",
);
assert(
  app.includes("/stages`")
    && app.includes("stageTimeline(selectedCandidate.id)")
    && app.includes("patentStageLabel(stage.stage)"),
  "Patent workspace must expose the persisted six-stage audit timeline",
);
assert(
  app.includes("/daily-digests/")
    && app.includes("/digest?date=")
    && app.includes("reading_routes"),
  "Dashboard must consume daily and topic digest contracts including reading routes",
);

assert(
  /Content-Security-Policy/.test(index)
    && /script-src 'self'/.test(index)
    && /object-src 'none'/.test(index)
    && /base-uri 'self'/.test(index),
  "index.html must define a restrictive CSP",
);

const forbiddenExternalTextTokens = [
  "cached.text",
  "selectedDraft.markdown",
  "report.summary",
  "report.motivation",
  "report.method",
  "report.results",
  "evidence.map",
  "formatEvidence",
];

const forbiddenGeneralUserAdapterNames = ["Skill"];
for (const token of forbiddenGeneralUserAdapterNames) {
  assert(!index.includes(token), `${token} must not be exposed in general user HTML copy`);
}
assert(
  !app.includes('parse: "MinerU 解析"') && !app.includes('analyze: "Dify 研读"'),
  "General workflow labels must not expose internal adapter names",
);
assert(
  !index.includes("https://arxiv.org")
    && !index.includes("https://*.arxiv.org")
    && app.includes("url.origin === window.location.origin")
    && !app.includes("artifactDownloadUrl(artifact) || currentVersion"),
  "Paper PDF rendering must use same-origin server artifacts without remote fallback",
);
assert(
  !index.includes(" style=") && !app.includes(" style=") && !app.includes(".style."),
  "Strict CSP requires all presentation and state changes to use external CSS classes",
);
assert(
  app.includes('apiJson("/runtime-config"')
    && app.includes('method: "PUT"')
    && app.includes("analysisApiKeyInput")
    && index.includes("API key 仅写入服务器权限受控配置"),
  "Model credentials must be submitted to masked server-side runtime configuration",
);
assert(
  index.includes('data-view="workflows"')
    && app.includes("renderWorkflows()")
    && app.includes("workflow-dag"),
  "The web UI must expose workflow definitions and run history",
);

for (const { index: lineNumber, line } of innerHtmlAssignments(app)) {
  for (const token of forbiddenExternalTextTokens) {
    assert(
      !line.includes(token),
      `External/imported text token ${token} must not enter innerHTML on line ${lineNumber}`,
    );
  }
  for (const payload of attackPayloads) {
    assert(
      !line.includes(payload),
      `Attack payload ${payload} must not be represented in an innerHTML assignment`,
    );
  }
}

assert(app.includes("textContent = text"), "Markdown/report body rendering must use textContent");
assert(
  app.includes('document.getElementById("selectedDraftBody").textContent'),
  "Draft body rendering must use textContent",
);
assert(
  app.includes("renderEvidenceDocument"),
  "Evidence rendering must use the safe DOM renderer",
);
