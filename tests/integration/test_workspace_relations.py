from __future__ import annotations

from research_hub.models import PaperCreate, PaperIdentifier, PaperVersionCreate
from research_hub.repository import Repository


def test_workspace_exposes_report_artifacts_cards_and_relations(api_client) -> None:
    response = api_client.get("/api/v1/papers/paper-1/workspace")

    assert response.status_code == 200
    payload = response.json()
    assert payload["paper"]["id"] == "paper-1"
    assert isinstance(payload["artifacts"], list)
    assert isinstance(payload["technical_cards"], list)
    assert isinstance(payload["relations"], list)


def test_relation_baseline_is_idempotent_and_supports_required_types(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repository = Repository(conn)
        for index, title in enumerate(
            (
                "Dynamic sparse speculative decoding runtime",
                "Static dense speculative decoding scheduler",
            ),
            start=1,
        ):
            repository.create_paper(
                PaperCreate(
                    canonical_title=title,
                    abstract="speculative decoding scheduler for efficient inference",
                    identifiers=[PaperIdentifier(type="test", value=f"rel-{index}")],
                    topics=["aif-03"],
                    version=PaperVersionCreate(version_label="v1", source="test"),
                )
            )

        first = repository.rebuild_relations()
        second = repository.rebuild_relations()

        assert first["created"] >= 1
        assert second["created"] == 0
        assert set(second["supported_relation_types"]) == {
            "similar",
            "extends",
            "complements",
            "conflicts",
        }

