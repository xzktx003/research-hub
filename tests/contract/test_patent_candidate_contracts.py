from __future__ import annotations

from research_hub.adapters.patent import PatentEngineAdapter
from research_hub.adapters.types import TechnicalCard


def card(card_id: str, method: str, *, evidence: tuple[str, ...] = ("paper:p1:section:3",)) -> TechnicalCard:
    return TechnicalCard(
        card_id=card_id,
        paper_id=f"paper-{card_id}",
        title=f"技术卡片 {card_id}",
        technical_problem="降低长上下文推理显存与尾延迟",
        method=method,
        evidence=evidence,
    )


def test_complementary_cards_pass_basic_candidate_gate() -> None:
    candidate = PatentEngineAdapter().build_candidate(
        [
            card("a", "在 prefill 阶段独立调度计算资源"),
            card("b", "按 KV cache 热度动态量化并迁移存储"),
        ]
    )

    assert candidate.gate_status == "ok"


def test_aggregation_candidate_is_rejected_by_gate() -> None:
    candidate = PatentEngineAdapter().build_candidate(
        [
            card("a", "使用相同摘要中的 KV cache 量化方法"),
            card("b", "使用相同摘要中的 KV cache 量化方法"),
        ]
    )

    assert candidate.gate_status != "ok"


def test_disclosure_markdown_warns_that_output_is_not_legal_novelty_opinion() -> None:
    candidate = PatentEngineAdapter().build_candidate(
        [
            card("a", "在 prefill 阶段独立调度计算资源"),
            card("b", "按 KV cache 热度动态量化并迁移存储"),
        ]
    )

    markdown = PatentEngineAdapter().render_disclosure_markdown(candidate)

    assert "不是法律意义上的新颖性" in markdown
    assert "## 一、现有技术及其问题" in markdown
    assert "## 五、建议保护点" in markdown
    assert markdown.count("```mermaid") == 2
    assert "一种实现上述方法的系统" in markdown
