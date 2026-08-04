from __future__ import annotations

from pathlib import Path

from research_hub.retention import scan_sensitive_config


def test_sensitive_config_scan_flags_literal_values_without_exposing_secret(tmp_path: Path) -> None:
    config = tmp_path / ".env.example"
    config.write_text(
        "\n".join(
            [
                "RESEARCH_HUB_API_KEY=<set-in-secret-manager>",
                "DIFY_API_KEY=${DIFY_API_KEY}",
                "POSTGRES_PASSWORD=literal-password",
            ]
        ),
        encoding="utf-8",
    )

    report = scan_sensitive_config([config])

    assert report["status"] == "failed"
    assert report["finding_count"] == 1
    assert report["findings"][0]["key"] == "POSTGRES_PASSWORD"
    assert "literal-password" not in str(report)


def test_sensitive_config_scan_allows_blank_placeholders_and_env_references(tmp_path: Path) -> None:
    config = tmp_path / "config.env"
    config.write_text(
        "\n".join(
            [
                "RESEARCH_HUB_API_KEY=",
                "POSTGRES_PASSWORD=<redacted>",
                'PUBLIC_VERIFY_API_KEY="$RESEARCH_HUB_API_KEY"',
            ]
        ),
        encoding="utf-8",
    )

    report = scan_sensitive_config([config])

    assert report["status"] == "ok"
    assert report["findings"] == []


def test_sensitive_config_scan_rejects_secret_env_fallback_defaults(tmp_path: Path) -> None:
    config = tmp_path / "docker-compose.yml"
    config.write_text(
        "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-research_hub}\n",
        encoding="utf-8",
    )

    report = scan_sensitive_config([config])

    assert report["status"] == "failed"
    assert report["findings"][0]["key"] == "POSTGRES_PASSWORD"
