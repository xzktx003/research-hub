"""Built-in domain workflows and run snapshots for the web control plane."""

from __future__ import annotations

from collections import Counter
from typing import Any


WORKFLOW_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "daily-paper-intelligence",
        "name": "每日论文研读",
        "description": "定时发现论文，在服务器下载 PDF，解析为结构化内容并生成可追溯研读报告。",
        "trigger": "schedule_or_manual",
        "nodes": [
            {"id": "discover", "label": "论文发现", "kind": "discover"},
            {"id": "download", "label": "服务器保存 PDF", "kind": "download"},
            {"id": "parse", "label": "文档结构化", "kind": "parse", "integration": "mineru"},
            {"id": "analyze", "label": "论文研读总结", "kind": "analyze", "integration": "analysis"},
            {"id": "translate", "label": "中文翻译（可选）", "kind": "translate", "integration": "analysis", "optional": True},
            {"id": "relate", "label": "关系与日报", "kind": "relate"},
        ],
        "edges": [
            ["discover", "download"],
            ["download", "parse"],
            ["parse", "analyze"],
            ["parse", "translate"],
            ["analyze", "relate"],
        ],
    },
    {
        "id": "patent-disclosure",
        "name": "专利交底书",
        "description": "组合论文证据，经查新与人工审批后生成版本化 Markdown 和 DOCX 交底书。",
        "trigger": "manual_gate",
        "nodes": [
            {"id": "intake", "label": "候选材料", "kind": "intake"},
            {"id": "candidate_analysis", "label": "组合分析", "kind": "candidate_analysis"},
            {"id": "prior_art", "label": "现有技术查新", "kind": "prior_art_check", "integration": "prior_art"},
            {"id": "preview", "label": "人工审批", "kind": "preview"},
            {"id": "builder", "label": "交底书生成", "kind": "patent_draft", "integration": "patent"},
            {"id": "self_check", "label": "结构与证据自检", "kind": "self_check"},
        ],
        "edges": [
            ["intake", "candidate_analysis"],
            ["candidate_analysis", "prior_art"],
            ["prior_art", "preview"],
            ["preview", "builder"],
            ["builder", "self_check"],
        ],
    },
)


def workflow_payload(repository: Any, runtime_config: dict[str, Any]) -> dict[str, Any]:
    jobs = repository.list_jobs()
    runs = repository.list_pipeline_runs(limit=30)
    snapshots = []
    for run in runs:
        run_jobs = [
            job
            for job in jobs
            if job.id == run.job_id or job.request.get("pipeline_run_id") == run.id
        ]
        snapshots.append(
            {
                **run.model_dump(mode="json"),
                "status": _run_status(run.status, run_jobs),
                "steps": _step_counts(run_jobs),
                "jobs": [job.model_dump(mode="json") for job in run_jobs[:50]],
                "tokens": _aggregate_tokens(run_jobs),
            }
        )
    return {
        "items": [
            _definition_state(item, runtime_config, jobs)
            for item in WORKFLOW_DEFINITIONS
        ],
        "runs": snapshots,
    }


def _definition_state(
    definition: dict[str, Any],
    runtime_config: dict[str, Any],
    jobs: list[Any],
) -> dict[str, Any]:
    schedule = runtime_config["schedule"]
    analysis = runtime_config["analysis"]
    selected = analysis[analysis["provider"]]
    services = runtime_config["services"]
    capabilities = {
        "analysis": bool(selected.get("base_url"))
        and (analysis["provider"] == "dify" or bool(selected.get("model"))),
        "mineru": bool(services["mineru"].get("base_url")),
        "prior_art": services["prior_art"].get("mode") == "local"
        or bool(services["prior_art"].get("base_url")),
        "patent": True,
    }
    nodes = []
    for node in definition["nodes"]:
        integration = node.get("integration")
        enabled = not integration or capabilities.get(integration, False)
        nodes.append(
            {
                **node,
                "enabled": enabled,
                "job_counts": dict(Counter(job.status for job in jobs if job.kind == node["kind"])),
            }
        )
    return {
        **definition,
        "nodes": nodes,
        "enabled": all(node["enabled"] or node.get("optional") for node in nodes),
        "schedule": schedule if definition["id"] == "daily-paper-intelligence" else None,
    }


def _step_counts(jobs: list[Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for job in jobs:
        counts = result.setdefault(job.kind, {})
        counts[job.status] = counts.get(job.status, 0) + 1
    return result


def _job_usage(job: Any) -> dict[str, int]:
    """Extract token usage from a job result regardless of where the adapter
    put it (top-level ``usage`` or nested ``response.usage``)."""
    result = job.result or {}
    usage = result.get("usage")
    if not isinstance(usage, dict):
        response = result.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return {}
    parsed: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            parsed[key] = int(value)
    return parsed


def _aggregate_tokens(jobs: list[Any]) -> dict[str, int]:
    prompt = 0
    completion = 0
    total = 0
    for job in jobs:
        usage = _job_usage(job)
        prompt += usage.get("prompt_tokens", 0)
        completion += usage.get("completion_tokens", 0)
        total += usage.get("total_tokens", 0)
    aggregated: dict[str, int] = {}
    if total:
        aggregated["prompt_tokens"] = prompt
        aggregated["completion_tokens"] = completion
        aggregated["total_tokens"] = total
    return aggregated


def _run_status(fallback: str, jobs: list[Any]) -> str:
    statuses = {job.status for job in jobs}
    if not statuses:
        return fallback
    if "running" in statuses:
        return "running"
    if "queued" in statuses:
        return "queued"
    failed = statuses & {"retryable_failed", "terminal_failed"}
    succeeded = statuses & {"succeeded", "partial_succeeded"}
    if failed and succeeded:
        return "partial_succeeded"
    if "terminal_failed" in failed:
        return "terminal_failed"
    if failed:
        return "retryable_failed"
    if "partial_succeeded" in statuses:
        return "partial_succeeded"
    if statuses == {"cancelled"}:
        return "cancelled"
    return "succeeded"