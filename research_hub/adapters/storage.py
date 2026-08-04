"""Filesystem artifact storage with checksum-based registration."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Any

from .types import AdapterResult, ArtifactRecord


class FileArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def register_existing(
        self,
        source_path: Path | str,
        *,
        kind: str,
        metadata: dict[str, Any] | None = None,
        copy: bool = False,
    ) -> AdapterResult:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            return AdapterResult.failed(f"artifact file does not exist: {source}", path=str(source))
        sha256 = file_sha256(source)
        target = source
        if copy:
            suffix = source.suffix
            target = self.root / kind / sha256[:2] / f"{sha256}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
        stat = target.stat()
        record = ArtifactRecord(
            artifact_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kind}:{sha256}:{target}")),
            kind=kind,
            path=target,
            size_bytes=stat.st_size,
            sha256=sha256,
            content_type=mimetypes.guess_type(str(target))[0] or "application/octet-stream",
            metadata=metadata or {},
        )
        return AdapterResult.ok("artifact registered", artifact=artifact_to_dict(record))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_to_dict(record: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": record.artifact_id,
        "kind": record.kind,
        "path": str(record.path),
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "content_type": record.content_type,
        "metadata": record.metadata,
    }
