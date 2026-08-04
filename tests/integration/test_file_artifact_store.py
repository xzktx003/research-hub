from __future__ import annotations

from pathlib import Path

from research_hub.adapters.storage import FileArtifactStore, file_sha256


def test_register_existing_artifact_returns_checksum_and_metadata(tmp_path: Path) -> None:
    source = tmp_path / "paper.md"
    source.write_text("# Paper\n\ncontent", encoding="utf-8")

    result = FileArtifactStore(tmp_path / "artifacts").register_existing(
        source,
        kind="markdown",
        metadata={"paper_version_id": "pv-1"},
    )

    artifact = result.data["artifact"]
    assert artifact["sha256"] == file_sha256(source)
    assert artifact["metadata"] == {"paper_version_id": "pv-1"}


def test_register_existing_artifact_can_copy_into_checksum_path(tmp_path: Path) -> None:
    source = tmp_path / "paper.md"
    source.write_text("# Paper\n\ncontent", encoding="utf-8")

    result = FileArtifactStore(tmp_path / "artifacts").register_existing(source, kind="markdown", copy=True)

    copied = Path(result.data["artifact"]["path"])
    assert copied.is_file()


def test_register_missing_artifact_fails_without_throwing(tmp_path: Path) -> None:
    result = FileArtifactStore(tmp_path / "artifacts").register_existing(tmp_path / "missing.md", kind="markdown")

    assert result.status == "failed"
