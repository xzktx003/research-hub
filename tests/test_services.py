from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from research_hub.adapters import AdapterResult
from research_hub.adapters.arxiv import ArxivDiscoveryAdapter
from research_hub.adapters.downloader import PdfDownloadAdapter
from research_hub.models import (
    CandidateApproveRequest,
    DiscoveryRunCreate,
    InventionCandidateCreate,
    InventionSourceRef,
    PaperCreate,
    PaperVersionCreate,
    VersionActionRequest,
)
from research_hub.repository import Repository
from research_hub.services import ResearchJobService, poll_running_jobs_once, run_queued_jobs_once


class StubDiscoveryAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.topics = []

    def discover(self, topic):
        self.topics.append(topic)
        return self.result


class StubParserAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return self.result

    def status(self, job_id):
        return self.result

    def download_markdown(self, markdown_path, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("# Parsed\n", encoding="utf-8")
        return AdapterResult.ok("downloaded", path=str(output_path))


class RecoveringMinerUAdapter:
    def __init__(self, submit_results, status_results) -> None:
        self.submit_results = list(submit_results)
        self.status_results = list(status_results)
        self.requests = []
        self.status_calls = []
        self.fetch_calls = []

    def submit(self, request):
        self.requests.append(request)
        return self.submit_results.pop(0)

    def status(self, job_id):
        self.status_calls.append(job_id)
        return self.status_results.pop(0)

    def fetch_result(self, job_id, output_root):
        self.fetch_calls.append(job_id)
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        markdown = root / "paper.md"
        structured = root / "paper.json"
        markdown.write_text("# Parsed\n", encoding="utf-8")
        structured.write_text('{"pages":[]}\n', encoding="utf-8")
        return AdapterResult.ok(
            "downloaded",
            manifest={
                "task_id": job_id,
                "root": str(root),
                "markdown": [str(markdown)],
                "structured_json": [str(structured)],
                "resources": [],
            },
        )


class StubReaderAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.requests = []

    def run_report(self, request):
        self.requests.append(request)
        return self.result


class StubDownloaderAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.requests = []

    def download(self, url, artifact_root):
        self.requests.append((url, artifact_root))
        return self.result


class StubRendererAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.requests = []

    def render(self, markdown, output_path):
        self.requests.append((markdown, output_path))
        if self.result.status == "ok":
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"%PDF-1.7\nrendered\n")
            return AdapterResult.ok("rendered", path=str(output_path), size_bytes=18)
        return self.result


class StubPriorArtAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.requests = []

    def search(self, request):
        self.requests.append(request)
        return self.result


def test_discovery_service_persists_arxiv_hits_and_updates_run_job(
    initialized_db,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("RESEARCH_HUB_RUNTIME_CONFIG", str(tmp_path / "runtime-config.json"))
    hit = {
        "source": "arxiv",
        "source_id": "2608.00001",
        "stable_key": "arxiv:2608.00001",
        "title": "Speculative Decode Runtime",
        "abstract": "A serving paper.",
        "authors": ["A. Researcher"],
        "published_at": "2026-08-02T01:00:00+00:00",
        "updated_at": "2026-08-02T02:00:00+00:00",
        "pdf_url": "https://arxiv.org/pdf/2608.00001",
        "landing_url": "https://arxiv.org/abs/2608.00001",
        "categories": ["cs.LG"],
        "raw": {"entry_id": "https://arxiv.org/abs/2608.00001v1"},
    }
    outside_window = {
        **hit,
        "source_id": "2607.99999",
        "stable_key": "arxiv:2607.99999",
        "published_at": "2026-08-01T23:59:59+00:00",
    }
    adapter = StubDiscoveryAdapter(AdapterResult.ok("ok", papers=[hit, outside_window]))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(
                source="arxiv",
                topics=["aif-03"],
                max_results=1,
                window_start=datetime(2026, 8, 2, tzinfo=timezone.utc),
                window_end=datetime(2026, 8, 3, tzinfo=timezone.utc),
            ),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, discovery_adapter=adapter).run_discovery_run(run.id)
        saved_run = repo.get_discovery_run(run.id)
        job = repo.get_job(run.job_id or "")
        papers = repo.list_papers(topic="aif-03", source="arxiv")
        download_jobs = repo.list_jobs(kind="download", target_type="paper_version")
        pipeline_runs = repo.list_pipeline_runs()

        assert result["status"] == "succeeded"
        assert result["papers_created"] == 1
        assert result["papers_filtered_out"] == 1
        assert saved_run.status == "succeeded"
        assert job.status == "succeeded"
        assert len(papers) == 1
        assert papers[0].canonical_title == "Speculative Decode Runtime"
        assert papers[0].current_version_id
        assert result["auto_process"] is True
        assert result["jobs_enqueued"][0]["job_id"] == download_jobs[0].id
        assert download_jobs[0].target_id == papers[0].current_version_id
        assert download_jobs[0].request["after_parse"] == ["translate"]
        assert pipeline_runs[0].id == result["pipeline_run_id"]
        assert pipeline_runs[0].discovery_run_id == run.id
        assert adapter.topics[0].topic_id == "aif-03"
        assert adapter.topics[0].max_results == 1
        hit_date = conn.execute(
            "SELECT hit_date FROM paper_source_hit WHERE paper_id = ?",
            (papers[0].id,),
        ).fetchone()["hit_date"]
        assert hit_date == "2026-08-02"


def test_discovery_service_queues_abstract_translation_when_llm_is_configured(
    initialized_db,
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "runtime-config.json"
    config_path.write_text(
        '{"analysis":{"provider":"openai","openai":{"base_url":"http://llm.local/v1","api_key":"","model":"translator"}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_HUB_RUNTIME_CONFIG", str(config_path))
    hit = {
        "source": "arxiv",
        "source_id": "2608.10001",
        "stable_key": "arxiv:2608.10001",
        "title": "Automatic Abstract Translation",
        "abstract": "An English abstract.",
        "authors": ["A. Researcher"],
        "published_at": "2026-08-02T01:00:00+00:00",
        "pdf_url": None,
        "landing_url": "https://arxiv.org/abs/2608.10001",
        "categories": ["cs.CL"],
        "raw": {},
    }

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(
                source="arxiv",
                topics=["aif-03"],
                max_results=1,
                window_start=datetime(2026, 8, 2, tzinfo=timezone.utc),
                window_end=datetime(2026, 8, 3, tzinfo=timezone.utc),
            ),
            idempotency_key=None,
        )

        result = ResearchJobService(
            conn,
            discovery_adapter=StubDiscoveryAdapter(AdapterResult.ok("ok", papers=[hit])),
        ).run_discovery_run(run.id)
        jobs = repo.list_jobs(kind="translate", target_type="paper_version")

        assert result["status"] == "succeeded"
        assert len(jobs) == 1
        assert jobs[0].request["mode"] == "abstract"
        assert result["jobs_enqueued"][0]["kind"] == "translate_abstract"


def test_discovery_abstract_translation_is_idempotent_by_abstract_content(
    initialized_db,
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "runtime-config.json"
    config_path.write_text(
        '{"analysis":{"provider":"openai","openai":{"base_url":"http://llm.local/v1","model":"translator"}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_HUB_RUNTIME_CONFIG", str(config_path))
    hit = {
        "source": "arxiv",
        "source_id": "2608.10002",
        "stable_key": "arxiv:2608.10002",
        "title": "Content Hash Translation",
        "abstract": "The original English abstract.",
        "authors": ["A. Researcher"],
        "published_at": "2026-08-02T01:00:00+00:00",
        "pdf_url": None,
        "landing_url": "https://arxiv.org/abs/2608.10002",
        "categories": ["cs.CL"],
        "raw": {},
    }
    adapter = StubDiscoveryAdapter(AdapterResult.ok("ok", papers=[hit]))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(
                source="arxiv",
                topics=["aif-03"],
                max_results=1,
                window_start=datetime(2026, 8, 2, tzinfo=timezone.utc),
                window_end=datetime(2026, 8, 3, tzinfo=timezone.utc),
            ),
            idempotency_key=None,
        )
        service = ResearchJobService(conn, discovery_adapter=adapter)

        service.run_discovery_run(run.id)
        paper = repo.list_papers()[0]
        conn.execute(
            "UPDATE paper SET translated_abstract = ? WHERE id = ?",
            ("原摘要译文。", paper.id),
        )
        service.run_discovery_run(run.id)

        assert repo.get_paper(paper.id).translated_abstract == "原摘要译文。"
        assert len(repo.list_jobs(kind="translate", target_type="paper_version")) == 1

        adapter.result = AdapterResult.ok(
            "ok",
            papers=[{**hit, "abstract": "A corrected English abstract."}],
        )
        service.run_discovery_run(run.id)

        assert repo.get_paper(paper.id).translated_abstract is None
        assert len(repo.list_jobs(kind="translate", target_type="paper_version")) == 2


def test_discovery_service_marks_degraded_adapter_as_retryable_failed(initialized_db) -> None:
    adapter = StubDiscoveryAdapter(AdapterResult.degraded("arXiv unavailable", papers=[]))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(source="arxiv", topics=["aif-02"], max_results=1),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, discovery_adapter=adapter).run_discovery_run(run.id)
        job = repo.get_job(run.job_id or "")

        assert result["status"] == "retryable_failed"
        assert result["papers_seen"] == 0
        assert repo.get_discovery_run(run.id).status == "retryable_failed"
        assert job.status == "retryable_failed"
        assert job.error["errors"][0]["message"] == "arXiv unavailable"


def test_discovery_service_marks_partial_success_when_one_source_fails(initialized_db) -> None:
    hit = {
        "source": "arxiv",
        "source_id": "2608.00001",
        "stable_key": "arxiv:2608.00001",
        "title": "Speculative Decode Runtime",
        "abstract": "A serving paper.",
        "authors": ["A. Researcher"],
        "published_at": "2026-08-02T01:00:00+00:00",
        "pdf_url": "https://arxiv.org/pdf/2608.00001",
        "landing_url": "https://arxiv.org/abs/2608.00001",
        "categories": ["cs.LG"],
        "raw": {},
    }
    adapter = StubDiscoveryAdapter(
        AdapterResult.degraded(
            "one discovery source failed",
            papers=[hit],
            failures=[{"source": "openreview", "status": "degraded", "message": "timeout"}],
        )
    )

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(source="composite", topics=["aif-03"], max_results=10),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, discovery_adapter=adapter).run_discovery_run(run.id)

        assert result["status"] == "partial_succeeded"
        assert repo.get_discovery_run(run.id).status == "partial_succeeded"
        assert repo.get_job(run.job_id or "").status == "partial_succeeded"
        assert result["papers_created"] == 1
        assert any(error.get("source") == "openreview" for error in result["errors"])


def test_discovery_service_uses_openalex_only_as_enrichment(initialized_db) -> None:
    authoritative = {
        "source": "arxiv",
        "source_role": "authoritative",
        "source_id": "2608.00001",
        "stable_key": "doi:10.5555/spec.decode.runtime",
        "doi": "10.5555/spec.decode.runtime",
        "title": "Speculative Decode Runtime",
        "abstract": "A serving paper.",
        "authors": ["A. Researcher"],
        "published_at": "2026-08-02T01:00:00+00:00",
        "pdf_url": "https://arxiv.org/pdf/2608.00001",
        "landing_url": "https://arxiv.org/abs/2608.00001",
        "categories": ["cs.LG"],
        "raw": {},
    }
    matching_enrichment = {
        **authoritative,
        "source": "openalex",
        "source_role": "enrichment",
        "source_id": "https://openalex.org/W123",
        "landing_url": "https://openalex.org/W123",
    }
    unmatched_enrichment = {
        **matching_enrichment,
        "source_id": "https://openalex.org/W999",
        "stable_key": "openalex:https://openalex.org/W999",
        "doi": None,
        "title": "Unverified Enrichment-Only Record",
        "authors": ["U. Unknown"],
    }
    adapter = StubDiscoveryAdapter(
        AdapterResult.ok("ok", papers=[unmatched_enrichment, matching_enrichment, authoritative])
    )

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(source="composite", topics=["aif-03"], max_results=10),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, discovery_adapter=adapter).run_discovery_run(run.id)
        papers = repo.list_papers()
        sources = conn.execute(
            "SELECT source FROM paper_source_hit WHERE paper_id = ? ORDER BY source",
            (papers[0].id,),
        ).fetchall()

    assert result["status"] == "succeeded"
    assert result["papers_created"] == 1
    assert result["papers_matched"] == 1
    assert result["enrichment_skipped"] == 1
    assert len(papers) == 1
    assert [row["source"] for row in sources] == ["arxiv", "openalex"]


def test_discovery_service_merges_title_first_author_year_fallback(initialized_db) -> None:
    base = {
        "title": "Cross Layer KV Cache Control",
        "abstract": "A cross-layer runtime technique.",
        "authors": ["Ada Researcher", "Bob Builder"],
        "published_at": "2026-08-02T01:00:00+00:00",
        "updated_at": "2026-08-02T02:00:00+00:00",
        "categories": ["cs.DC"],
        "doi": None,
        "raw": {},
    }
    hits = [
        {
            **base,
            "source": "arxiv",
            "source_id": "2608.12345",
            "stable_key": "arxiv:2608.12345",
            "pdf_url": "https://arxiv.org/pdf/2608.12345",
            "landing_url": "https://arxiv.org/abs/2608.12345",
        },
        {
            **base,
            "source": "openreview",
            "source_id": "forum-kv-control",
            "stable_key": "openreview:forum-kv-control",
            "pdf_url": "https://openreview.net/pdf?id=forum-kv-control",
            "landing_url": "https://openreview.net/forum?id=forum-kv-control",
        },
    ]
    adapter = StubDiscoveryAdapter(AdapterResult.ok("ok", papers=hits))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(source="composite", topics=["aif-03"], max_results=10),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, discovery_adapter=adapter).run_discovery_run(run.id)
        paper_count = conn.execute("SELECT COUNT(*) AS n FROM paper").fetchone()["n"]
        hit_count = conn.execute("SELECT COUNT(*) AS n FROM paper_source_hit").fetchone()["n"]

    assert result["papers_created"] == 1
    assert result["papers_matched"] == 1
    assert paper_count == 1
    assert hit_count == 2


def test_discovery_service_fails_explicitly_for_unknown_topic(initialized_db) -> None:
    adapter = StubDiscoveryAdapter(AdapterResult.ok("unused", papers=[]))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(source="arxiv", topics=["unknown-topic"], max_results=1),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, discovery_adapter=adapter).run_discovery_run(run.id)

        assert result["status"] == "terminal_failed"
        assert adapter.topics == []
        assert repo.get_job(run.job_id or "").error["message"].startswith("No enabled topics")


def test_parse_job_calls_mineru_adapter_and_stores_external_task_id(initialized_db, tmp_path: Path) -> None:
    parser = StubParserAdapter(AdapterResult.ok("submitted", response={"job_id": "mineru-123"}))
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf), "gpu_id": 2}),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, parser_adapter=parser).run_parse_job(job.job_id)
        saved = repo.get_job(job.job_id)

        assert result["status"] == "running"
        assert saved.external_task_id == "mineru-123"
        assert saved.next_poll_after
        assert parser.requests[0].pdf_path == pdf.resolve()
        assert parser.requests[0].backend == "pipeline"
        assert "gpu_id" not in parser.requests[0].options


def test_download_job_registers_pdf_and_queues_parse(initialized_db, tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "downloaded.pdf"
    pdf.write_bytes(b"%PDF-1.7\npaper\n")
    downloader = StubDownloaderAdapter(
        AdapterResult.ok(
            "downloaded",
            path=str(pdf),
            sha256="abc123",
            size_bytes=12,
            content_type="application/pdf",
        )
    )
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.create_job(
            "download",
            "paper_version",
            paper.current_version_id or "",
            {"parse_options": {"gpu_id": 1}},
        )

        result = ResearchJobService(conn, downloader_adapter=downloader).run_download_job(job.job_id)
        artifacts = repo.list_version_artifacts(paper.current_version_id or "")
        parse_jobs = repo.list_jobs(kind="parse", target_id=paper.current_version_id)

        assert result["status"] == "succeeded"
        assert result["result"]["parse_job_id"] == parse_jobs[0].id
        assert any(item.artifact_type == "pdf" and item.checksum == "abc123" for item in artifacts)
        assert repo.get_paper(paper.id).status == "downloaded"


def test_run_job_claim_replay_does_not_resubmit_running_parse(initialized_db, tmp_path: Path) -> None:
    parser = StubParserAdapter(AdapterResult.ok("submitted", response={"job_id": "mineru-once"}))
    pdf = tmp_path / "claim.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf)}),
            idempotency_key=None,
        )
        service = ResearchJobService(conn, parser_adapter=parser)

        first = service.run_job(job.job_id)
        second = service.run_job(job.job_id)

        assert first["status"] == "running"
        assert second["status"] == "running"
        assert len(parser.requests) == 1


def test_parse_completion_queues_configured_followup_jobs(initialized_db, tmp_path: Path) -> None:
    parser = StubParserAdapter(AdapterResult.ok("done"))
    pdf = tmp_path / "sync.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf), "after_parse": ["analyze", "translate"]}),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, parser_adapter=parser).run_parse_job(job.job_id)

        assert result["status"] == "succeeded"
        # 研读报告（analyze）默认不在解析后自动排队，改为用户点击阅读台
        # 「研读报告」时按需触发；仅摘要翻译（translate）自动链式执行。
        assert {item["kind"] for item in result["result"]["chained_jobs"]} == {"translate"}


def test_parse_completion_queues_analyze_when_opt_in_enabled(initialized_db, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_HUB_AUTO_ANALYZE", "1")
    parser = StubParserAdapter(AdapterResult.ok("done"))
    pdf = tmp_path / "sync.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf), "after_parse": ["analyze", "translate"]}),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, parser_adapter=parser).run_parse_job(job.job_id)

        assert result["status"] == "succeeded"
        assert {item["kind"] for item in result["result"]["chained_jobs"]} == {"analyze", "translate"}


def test_analyze_job_calls_dify_adapter_and_upserts_report(initialized_db) -> None:
    fields = (
        "summary",
        "motivation",
        "method",
        "experiments",
        "results",
        "innovation",
        "limitations",
        "engineering_value",
        "reproduction_plan",
    )
    reader = StubReaderAdapter(
        AdapterResult.ok(
            "generated",
            report={
                **{field: f"Structured {field}" for field in fields},
                "evidence": [
                    {
                        "kind": "fact",
                        "source": "paper",
                        "report_field": field,
                        "section": field,
                        "page": index + 1,
                        "quote": f"Evidence for {field}",
                    }
                    for index, field in enumerate(fields)
                ],
            },
        )
    )

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "analyze",
            VersionActionRequest(),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, analyzer_adapter=reader).run_analyze_job(job.job_id)
        report = repo.get_version_report(paper.current_version_id or "")

        assert result["status"] == "succeeded"
        assert reader.requests[0].paper_id == paper.id
        assert reader.requests[0].metadata["task"] == "analyze"
        assert report.summary == "Structured summary"
        assert report.score["evidence_coverage"] == 1.0


def test_analyze_job_rejects_unstructured_unanchored_legacy_output(initialized_db) -> None:
    reader = StubReaderAdapter(AdapterResult.ok("generated", markdown="# Legacy report"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "analyze",
            VersionActionRequest(),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, analyzer_adapter=reader).run_analyze_job(job.job_id)
        report = repo.get_version_report(paper.current_version_id or "")

        assert result["status"] == "retryable_failed"
        assert report.score["quality_status"] == "incomplete"
        assert report.score["evidence_coverage"] == 0.0


def test_translate_job_degraded_adapter_is_explicit_retryable_failure(initialized_db) -> None:
    translator = StubReaderAdapter(AdapterResult.degraded("Dify is not configured"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "translate",
            VersionActionRequest(),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, translator_adapter=translator).run_translate_job(job.job_id)
        saved = repo.get_job(job.job_id)

        assert result["status"] == "retryable_failed"
        assert saved.error["message"] == "Dify is not configured"
        assert translator.requests[0].metadata["task"] == "translate"


def test_abstract_translate_job_persists_chinese_abstract_and_method_summary(initialized_db) -> None:
    translator = StubReaderAdapter(
        AdapterResult.ok(
            "translated",
            abstract_zh="这是中文摘要。",
            method_summary="本文提出一种新的注意力压缩方法，以解决长上下文推理显存开销过大的问题。",
        )
    )

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        conn.execute("UPDATE paper SET status = 'analyzed' WHERE id = ?", (paper.id,))
        job = repo.create_job(
            "translate",
            "paper_version",
            paper.current_version_id or "",
            {"mode": "abstract", "source": "discovery_chain"},
        )

        result = ResearchJobService(conn, translator_adapter=translator).run_translate_job(job.job_id)
        saved = repo.get_paper(paper.id)

        assert result["status"] == "succeeded"
        assert result["result"]["translated_abstract"] == "这是中文摘要。"
        assert result["result"]["method_summary"] == (
            "本文提出一种新的注意力压缩方法，以解决长上下文推理显存开销过大的问题。"
        )
        assert saved.translated_abstract == "这是中文摘要。"
        assert saved.method_summary == "本文提出一种新的注意力压缩方法，以解决长上下文推理显存开销过大的问题。"
        assert saved.status == "analyzed"
        assert translator.requests[0].metadata["task"] == "translate_abstract"
        assert translator.requests[0].markdown is None


def test_abstract_translate_job_allows_missing_method_summary(initialized_db) -> None:
    """A provider may omit the one-line method summary; the paper still keeps
    its translated abstract and the persisted method_summary stays empty."""
    translator = StubReaderAdapter(AdapterResult.ok("translated", abstract_zh="这是中文摘要。"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        conn.execute("UPDATE paper SET status = 'analyzed' WHERE id = ?", (paper.id,))
        job = repo.create_job(
            "translate",
            "paper_version",
            paper.current_version_id or "",
            {"mode": "abstract", "source": "discovery_chain"},
        )

        result = ResearchJobService(conn, translator_adapter=translator).run_translate_job(job.job_id)
        saved = repo.get_paper(paper.id)

        assert result["status"] == "succeeded"
        assert result["result"]["translated_abstract"] == "这是中文摘要。"
        assert "method_summary" not in result["result"]
        assert saved.translated_abstract == "这是中文摘要。"
        assert saved.method_summary in (None, "")


def test_abstract_translate_job_retries_when_llm_returns_no_translation(initialized_db) -> None:
    translator = StubReaderAdapter(AdapterResult.ok("completed", report={}))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.create_job(
            "translate",
            "paper_version",
            paper.current_version_id or "",
            {"mode": "abstract"},
        )

        result = ResearchJobService(conn, translator_adapter=translator).run_translate_job(job.job_id)
        saved = repo.get_job(job.job_id)

        assert result["status"] == "retryable_failed"
        assert saved.status == "retryable_failed"
        assert saved.error["message"] == "LLM response did not contain a translated abstract"


def test_abstract_translate_failure_preserves_original_paper(initialized_db) -> None:
    translator = StubReaderAdapter(AdapterResult.degraded("LLM unavailable"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.create_job(
            "translate",
            "paper_version",
            paper.current_version_id or "",
            {"mode": "abstract"},
        )

        result = ResearchJobService(conn, translator_adapter=translator).run_translate_job(job.job_id)
        saved = repo.get_paper(paper.id)

        assert result["status"] == "retryable_failed"
        assert saved.abstract == paper.abstract
        assert saved.translated_abstract is None


def test_translate_job_can_queue_render_pdf_when_requested(initialized_db) -> None:
    translator = StubReaderAdapter(AdapterResult.ok("translated", markdown="# 中文报告\n\nEnglish report."))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "translate",
            VersionActionRequest(options={"render_pdf": True}),
            idempotency_key=None,
        )

        result = ResearchJobService(conn, translator_adapter=translator).run_translate_job(job.job_id)

        assert result["status"] == "succeeded"
        assert result["result"]["render_pdf_job_id"]
        assert repo.get_job(result["result"]["render_pdf_job_id"]).kind == "render_pdf"


def test_render_pdf_job_reports_missing_engine_as_degraded(initialized_db) -> None:
    renderer = StubRendererAdapter(AdapterResult.degraded("PDF renderer is not configured"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        repo.create_artifact_for_version(
            paper.current_version_id or "",
            _artifact("translation_markdown", "inline://translation/test", {"content": "# 中文报告"}),
        )
        job = repo.create_job("render_pdf", "paper_version", paper.current_version_id or "", {})

        result = ResearchJobService(conn, renderer_adapter=renderer).run_render_pdf_job(job.job_id)

        assert result["status"] == "retryable_failed"
        assert result["error"]["message"] == "PDF renderer is not configured"


def test_prior_art_job_requires_structured_patent_and_academic_records(initialized_db) -> None:
    adapter = StubDiscoveryAdapter(
        AdapterResult.ok(
            "found",
            papers=[
                {
                    "source": "arxiv",
                    "source_id": "2608.00001",
                    "title": "Related inference control",
                    "abstract": "A related runtime-control technique.",
                    "landing_url": "https://arxiv.org/abs/2608.00001",
                    "pdf_url": "https://arxiv.org/pdf/2608.00001",
                }
            ],
        )
    )
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        first = _create_versioned_paper(repo)
        second = _create_versioned_paper(repo)
        candidate = repo.create_invention_candidate(
            InventionCandidateCreate(
                title="Speculative runtime control",
                sources=[
                    InventionSourceRef(
                        paper_id=first.id,
                        paper_version_id=first.current_version_id,
                    ),
                    InventionSourceRef(
                        paper_id=second.id,
                        paper_version_id=second.current_version_id,
                    ),
                ],
                problem_statement="Speculative verification and runtime scheduling need joint control to reduce serving waste.",
                integration_mechanism="Coordinate speculative verification confidence with runtime scheduling decisions.",
                coupling_interface="Expose draft verification confidence and queue pressure through a scheduler control interface.",
                data_or_control_flow="Verification confidence flows into scheduling priority and queue pressure feeds back into draft acceptance thresholds.",
                why_not_juxtaposition="The scheduler changes verification thresholds and verification confidence changes scheduling priority, so the mechanisms form feedback.",
                expected_joint_effect="Expected lower wasted compute and tail latency, with magnitude left for later experiments.",
                technical_effects="May lower wasted compute and tail latency after validation.",
                evidence=[
                    {
                        "kind": "fact",
                        "source": f"paper:{first.id}",
                        "report_field": "problem_statement",
                        "note": "Speculative verification has runtime coordination costs.",
                    },
                    {
                        "kind": "fact",
                        "source": f"paper:{first.id}",
                        "report_field": "integration_mechanism",
                        "note": "Verification confidence can drive control decisions.",
                    },
                    {
                        "kind": "fact",
                        "source": f"paper:{second.id}",
                        "report_field": "coupling_interface",
                        "note": "Scheduler can consume runtime control signals.",
                    },
                    {
                        "kind": "fact",
                        "source": f"paper:{second.id}",
                        "report_field": "data_or_control_flow",
                        "note": "Queue pressure feeds back into scheduler decisions.",
                    },
                    {
                        "kind": "fact",
                        "source": f"paper:{first.id}",
                        "report_field": "why_not_juxtaposition",
                        "note": "The controls affect each other through feedback.",
                    },
                    {
                        "kind": "hypothesis",
                        "source": "user",
                        "report_field": "expected_joint_effect",
                        "note": "Latency and compute reduction require experiments.",
                    },
                    {
                        "kind": "hypothesis",
                        "source": "user",
                        "report_field": "technical_effects",
                        "note": "Technical effects remain unverified.",
                    },
                ],
            )
        )
        repo.approve_candidate(
            candidate.id,
            CandidateApproveRequest(
                approver="service-test",
                contribution_confirmed=True,
                sanitization_confirmed=True,
                protection_focus_confirmed=True,
                unverified_facts_confirmed=True,
                override_prior_art=True,
                override_reason="prior-art service test creates the check job after approval",
            ),
        )
        job = repo.candidate_job(candidate.id, "prior_art_check", {}, None)

        prior_art = StubPriorArtAdapter(
            AdapterResult.ok(
                "patents found",
                records=[
                    {
                        "source_type": "patent",
                        "source": "cnipa",
                        "title": "一种推理运行时控制方法",
                        "publication_number": "CN123456789A",
                        "url": "https://pss-system.cponline.cnipa.gov.cn/example/CN123456789A",
                        "abstract": "公开一种基于队列压力的推理调度方法。",
                        "analysis_basis": "CNIPA abstract",
                        "bibliographic_match": True,
                        "limitations": "未公开跨论文反馈控制。",
                    }
                ],
            )
        )
        result = ResearchJobService(
            conn,
            discovery_adapter=adapter,
            prior_art_adapter=prior_art,
        ).run_prior_art_job(job.job_id)

        assert result["status"] == "succeeded"
        assert result["result"]["coverage"] == "complete"
        assert result["result"]["academic_results"][0]["landing_url"].startswith("https://arxiv.org/")
        assert result["result"]["patent_database_status"] == "ok"
        assert len(result["result"]["prior_art_records"]) == 2
        assert prior_art.requests[0]["candidate_id"] == candidate.id
        assert {
            item.stage: item.status for item in repo.list_patent_stage_runs(candidate.id)
        }["prior_art"] == "succeeded"


def test_run_queued_jobs_once_dispatches_executable_jobs(initialized_db, tmp_path: Path) -> None:
    parser = StubParserAdapter(AdapterResult.ok("submitted", response={"task_id": "mineru-queued"}))
    pdf = tmp_path / "queued.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf)}),
            idempotency_key=None,
        )

        results = run_queued_jobs_once(conn, limit=5, parser_adapter=parser)

        assert [item["job_id"] for item in results] == [job.job_id]
        assert repo.get_job(job.job_id).status == "running"


def test_run_queued_jobs_once_prefers_fast_kinds_fifo(initialized_db, tmp_path: Path, monkeypatch) -> None:
    """Downloads (fast) drain before slow LLM jobs, oldest first (FIFO)."""
    pdf = tmp_path / "prefer.pdf"
    pdf.write_bytes(b"%PDF-1.7\npaper\n")
    downloader = StubDownloaderAdapter(
        AdapterResult.ok(
            "downloaded",
            path=str(pdf),
            sha256="abcdef",
            size_bytes=13,
            content_type="application/pdf",
        )
    )
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        version_id = paper.current_version_id or ""

        # A slow LLM job created first (oldest), then a fast download job.
        slow = repo.version_action(
            version_id,
            "translate",
            VersionActionRequest(options={}),
            idempotency_key="slow-1",
        )
        fast = repo.version_action(
            version_id,
            "download",
            VersionActionRequest(options={"pdf_url": "https://example.test/paper.pdf"}),
            idempotency_key="fast-1",
        )

        # Even though the slow job was created first, a limit=1 batch must pick
        # the fast download job so downloads always make progress.
        results = run_queued_jobs_once(conn, limit=1, downloader_adapter=downloader)

        assert [item["job_id"] for item in results] == [fast.job_id]
        assert repo.get_job(results[0]["job_id"]).kind == "download"
        assert repo.get_job(fast.job_id).status == "succeeded"
        assert repo.get_job(slow.job_id).status == "queued"


def test_poll_running_jobs_once_respects_next_poll_after(initialized_db, tmp_path: Path) -> None:
    parser = StubParserAdapter(AdapterResult.ok("complete", response={"status": "completed", "markdown_path": "x.md"}))
    pdf = tmp_path / "poll.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat()
        due = (datetime.now(timezone.utc) - timedelta(seconds=1)).replace(microsecond=0).isoformat()
        first = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf)}),
            idempotency_key="future",
        )
        second = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf)}),
            idempotency_key="due",
        )
        conn.execute("UPDATE job SET status = 'running', external_task_id = 'future', next_poll_after = ? WHERE id = ?", (future, first.job_id))
        conn.execute("UPDATE job SET status = 'running', external_task_id = 'due', next_poll_after = ? WHERE id = ?", (due, second.job_id))

        results = poll_running_jobs_once(conn, parser_adapter=parser)

        assert [item["job_id"] for item in results] == [second.job_id]


def test_poll_parse_job_registers_completed_mineru_markdown(
    initialized_db, tmp_path: Path, monkeypatch
) -> None:
    parser = StubParserAdapter(AdapterResult.ok("submitted", response={"job_id": "mineru-complete"}))
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf)}),
            idempotency_key=None,
        )
        service = ResearchJobService(conn, parser_adapter=parser)
        service.run_parse_job(job.job_id)
        parser.result = AdapterResult.ok(
            "complete",
            response={"status": "completed", "markdown_path": "paper/paper.md"},
        )
        result = service.poll_parse_job(job.job_id)

        assert result["status"] == "succeeded"
        artifacts = repo.list_version_artifacts(paper.current_version_id or "")
        assert any(item.artifact_type == "markdown_original" for item in artifacts)


def test_poll_parse_job_resubmits_disappeared_mineru_task_and_completes_once(
    initialized_db, tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "recover.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    parser = RecoveringMinerUAdapter(
        submit_results=[
            AdapterResult.ok("submitted", response={"task_id": "mineru-original"}),
            AdapterResult.ok("resubmitted", response={"task_id": "mineru-recovered"}),
        ],
        status_results=[
            AdapterResult.degraded(
                "missing",
                job_id="mineru-original",
                http_status=404,
            ),
            AdapterResult.ok("complete", response={"status": "completed"}),
        ],
    )

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf)}),
            idempotency_key=None,
        )
        service = ResearchJobService(conn, parser_adapter=parser)

        submitted = service.run_parse_job(job.job_id)
        recovered = service.poll_parse_job(job.job_id)
        completed = service.poll_parse_job(job.job_id)
        saved = repo.get_job(job.job_id)
        artifacts = repo.list_version_artifacts(paper.current_version_id or "")

        assert submitted["status"] == "running"
        assert recovered["status"] == "running"
        assert completed["status"] == "succeeded"
        assert saved.external_task_id == "mineru-recovered"
        assert recovered["result"]["recovery"]["resubmissions"] == 1
        assert recovered["result"]["recovery"]["external_task_ids"] == [
            "mineru-original",
            "mineru-recovered",
        ]
        assert parser.requests[0].pdf_path == parser.requests[1].pdf_path == pdf.resolve()
        assert parser.status_calls == ["mineru-original", "mineru-recovered"]
        assert parser.fetch_calls == ["mineru-recovered"]
        assert [item.artifact_type for item in artifacts].count("markdown_original") == 1
        assert [item.artifact_type for item in artifacts].count("mineru_structured_json") == 1


def test_poll_parse_job_stops_after_mineru_recovery_limit(
    initialized_db, tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "bounded.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setenv("RESEARCH_HUB_MINERU_RECOVERY_LIMIT", "0")
    parser = RecoveringMinerUAdapter(
        submit_results=[AdapterResult.ok("submitted", response={"task_id": "mineru-original"})],
        status_results=[
            AdapterResult.degraded(
                "missing",
                job_id="mineru-original",
                http_status=404,
            )
        ],
    )

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.version_action(
            paper.current_version_id or "",
            "parse",
            VersionActionRequest(options={"pdf_path": str(pdf)}),
            idempotency_key=None,
        )
        service = ResearchJobService(conn, parser_adapter=parser)

        service.run_parse_job(job.job_id)
        result = service.poll_parse_job(job.job_id)
        saved = repo.get_job(job.job_id)

        assert result["status"] == "terminal_failed"
        assert saved.external_task_id == "mineru-original"
        assert saved.error["recovery"]["exhausted"] is True
        assert saved.error["missing_task_id"] == "mineru-original"
        assert len(parser.requests) == 1


def test_pdf_downloader_rejects_non_http_urls(tmp_path: Path) -> None:
    result = PdfDownloadAdapter().download("file:///tmp/paper.pdf", tmp_path)

    assert result.status == "failed"
    assert "HTTP(S)" in result.message


def test_pdf_downloader_validates_content_type_and_pdf_magic(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/not-pdf":
                body = b"%PDF-1.7\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            elif self.path == "/empty.pdf":
                body = b""
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
            else:
                body = b"%PDF-1.7\nok\n"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        downloader = PdfDownloadAdapter(max_bytes=1024)

        wrong_type = downloader.download(f"{base}/not-pdf", tmp_path)
        empty = downloader.download(f"{base}/empty.pdf", tmp_path)
        ok = downloader.download(f"{base}/paper.pdf", tmp_path)

        assert wrong_type.status == "failed"
        assert wrong_type.data["content_type"] == "text/plain"
        assert empty.status == "failed"
        assert "empty" in empty.message
        assert ok.status == "ok"
        assert Path(ok.data["path"]).read_bytes().startswith(b"%PDF-")
        assert len(ok.data["sha256"]) == 64
    finally:
        server.shutdown()


def test_arxiv_rate_limit_is_process_scoped(monkeypatch) -> None:
    ArxivDiscoveryAdapter._last_request_at_by_url.clear()
    sleeps: list[float] = []
    ticks = iter([100.0, 100.0, 101.0, 101.0])
    monkeypatch.setattr("research_hub.adapters.arxiv.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("research_hub.adapters.arxiv.time.sleep", lambda seconds: sleeps.append(seconds))

    first = ArxivDiscoveryAdapter(api_url="https://example.test/arxiv", request_interval_seconds=3)
    second = ArxivDiscoveryAdapter(api_url="https://example.test/arxiv", request_interval_seconds=3)
    first._respect_rate_limit()
    second._respect_rate_limit()

    assert sleeps == [2.0]


def _create_versioned_paper(repo: Repository):
    return repo.create_paper(
        PaperCreate(
            canonical_title="Efficient Serving Paper",
            abstract="An AI Infra paper about serving.",
            topics=["aif-04"],
            version=PaperVersionCreate(
                version_label="v1",
                source="test",
                source_version_id="test-1",
                pdf_url="https://example.test/paper.pdf",
            ),
        )
    )


def _artifact(kind: str, uri: str, metadata: dict[str, object]):
    from research_hub.models import ArtifactCreate

    return ArtifactCreate(
        artifact_type=kind,
        uri=uri,
        media_type="text/markdown; charset=utf-8",
        metadata=metadata,
    )
