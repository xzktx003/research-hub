"""Patent candidate and disclosure adapter built on structured paper cards."""

from __future__ import annotations

import subprocess
import uuid
import os
import re
import sys
from pathlib import Path
from typing import Any, cast

from .browser_runtime import browser_subprocess_env
from .types import AdapterResult, AdapterStatus, PatentCandidate, TechnicalCard


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_FILENAME_CHARS = re.compile(r"""[\\/:"*?<>|]+""")


class PatentEngineAdapter:
    """Generate candidate disclosures without claiming legal novelty."""

    def __init__(
        self,
        *,
        md_to_docx_path: Path | str | None = None,
        allowed_output_root: Path | str | None = None,
    ) -> None:
        if md_to_docx_path is None:
            configured_root = Path(
                os.getenv("PATENT_DISCLOSURE_ROOT", str(Path.cwd() / "patent-disclosure-skill"))
            )
            workspace_root = Path(__file__).resolve().parents[3] / "patent-disclosure-skill"
            root = configured_root if configured_root.is_dir() else workspace_root
            md_to_docx_path = root / "tools" / "md_to_docx.py"
        self.md_to_docx_path = Path(md_to_docx_path)
        self.allowed_output_root = (
            Path(allowed_output_root).expanduser().resolve()
            if allowed_output_root is not None
            else None
        )

    def _ensure_output_path_allowed(self, output_path: Path) -> Path:
        path = Path(output_path).expanduser().resolve()
        if self.allowed_output_root is not None and not (
            path == self.allowed_output_root
            or self.allowed_output_root in path.parents
        ):
            raise ValueError(
                f"output path is outside the allowed root {self.allowed_output_root}: {path}"
            )
        return path

    def build_candidate(self, cards: list[TechnicalCard], *, title: str | None = None) -> PatentCandidate:
        reasons = self._gate_cards(cards)
        gate_status = cast(AdapterStatus, "ok" if not reasons else "degraded")
        problem = _merge_unique(card.technical_problem for card in cards)
        solution = _merge_unique(card.method for card in cards)
        effects = tuple(
            f"组合 {card.title} 的方法以改善 {card.technical_problem}"
            for card in cards
            if card.method and card.technical_problem
        )
        risks = tuple(
            ["需要人工检索公开在先文献；本系统不输出法律意义的新颖性结论"]
            + [risk for card in cards for risk in card.risks]
        )
        gaps = tuple(reason for reason in reasons if "缺少" in reason or "不足" in reason)
        return PatentCandidate(
            candidate_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(card.card_id for card in cards))),
            title=title or _candidate_title(cards),
            source_cards=tuple(cards),
            technical_problem=problem,
            combined_solution=solution,
            technical_effects=effects,
            novelty_risks=risks,
            implementation_gaps=gaps,
            gate_status=gate_status,
            gate_reasons=tuple(reasons) if reasons else ("通过基础组合门禁；仍需人工确认",),
        )

    def render_disclosure_markdown(self, candidate: PatentCandidate) -> str:
        sources = "\n".join(
            f"- {card.paper_id} / {card.card_id}: {card.title}\n"
            f"  - 技术问题: {card.technical_problem}\n"
            f"  - 方法: {card.method}\n"
            f"  - 证据: {'; '.join(card.evidence) if card.evidence else '待补充'}"
            for card in candidate.source_cards
        )
        effects = "\n".join(f"- {item}" for item in candidate.technical_effects) or "- 待补充实验验证"
        risks = "\n".join(f"- {item}" for item in candidate.novelty_risks)
        gates = "\n".join(f"- {item}" for item in candidate.gate_reasons)
        return f"""# 技术交底书

## 基本信息

- 发明名称：{candidate.title}
- 技术联系人：待填写
- 版本状态：候选草案

> 说明：本文档是技术交底书候选草案，不是法律意义上的新颖性、创造性或可授权结论。

## 一、现有技术及其问题

### 1.1 现有技术

经查新的相关专利与学术方案：

<!-- PRIOR_ART_RECORDS -->

本方案的直接来源技术卡片：

{sources}

### 1.2 现有技术的不足

{candidate.technical_problem}

现有单一方案通常只覆盖局部性能、成本、质量或资源利用率目标，缺少跨阶段状态传递、统一调度决策以及可验证的联合效果闭环。

## 二、发明目的

在保留来源论文可追溯证据的前提下，将多篇论文中的技术机制组合为一个可工程实现的系统方案，解决单篇方案难以同时覆盖的性能、成本、质量或资源利用率问题。

## 三、技术方案

### 3.1 总体架构

本方案涉及 AI Infra、模型推理/训练系统优化以及相关软硬件协同实现。

```mermaid
flowchart LR
    A[任务与模型输入] --> B[状态采集模块]
    B --> C[联合决策与调度模块]
    C --> D[来源方法组合执行]
    D --> E[结果与性能指标]
    E -->|反馈| B
```

### 3.2 模块组成与连接关系

{candidate.combined_solution}

各来源机制通过统一状态对象、调度接口和反馈指标耦合，而不是并列部署。状态采集模块向联合决策模块提供运行时指标，联合决策模块生成资源分配与执行参数，组合执行模块据此调用各来源方法。

### 3.3 数据与控制流程

```mermaid
sequenceDiagram
    participant I as 输入与监控
    participant S as 联合调度器
    participant M as 组合执行模块
    participant O as 输出与评估
    I->>S: 提交任务、模型状态和资源指标
    S->>M: 下发阶段策略与执行参数
    M->>O: 返回结果和运行时测量
    O-->>S: 反馈质量、延迟和资源利用率
    S-->>M: 动态更新后续阶段策略
```

### 3.4 关键实施方式

1. 从论文报告中抽取技术问题、系统组件、核心方法和实验约束。
2. 对候选论文的技术卡片进行组合门禁检查，过滤机械摘要拼接。
3. 将互补方法映射到统一系统流程，明确输入、处理阶段、调度策略和输出。
4. 对每个关键主张保留来源证据，并标注需要新增实验验证的部分。
5. 在每个执行阶段采集性能、资源和质量指标，并将反馈用于后续阶段参数更新。

## 四、有益效果

{effects}

上述效果属于待验证的工程预期，需通过与各来源单独方案、简单并列方案及现有基线的对照实验确认。

## 五、建议保护点

1. 一种跨论文技术机制组合的 AI 系统处理方法，其特征在于：采集任务、模型与资源状态，基于统一决策逻辑生成至少两个处理阶段的联动参数，并依据运行反馈更新后续阶段策略。
2. 根据保护点 1 所述的方法，其特征在于：所述来源机制通过共享状态对象和调度接口形成数据或控制依赖，而非相互独立执行。
3. 一种实现上述方法的系统，包括状态采集模块、联合决策与调度模块、组合执行模块以及结果评估模块。
4. 一种存储计算机程序的计算机可读存储介质，所述程序被处理器执行时实现上述方法。
5. 对联合决策规则、状态字段、阶段间接口以及反馈更新条件的可选范围进行保护。

## 六、组合门禁、证据与风险

门禁结果：`{candidate.gate_status}`

{gates}

风险提示：

{risks}

## 七、实验验证与可选实施例

- 分别实现来源方法、简单并列方法和本方案的耦合实现，比较吞吐、尾延迟、资源利用率与任务质量。
- 对状态采样频率、决策周期、资源上限和回退策略进行消融实验。
- 在训练、在线推理、离线批处理或异构设备环境中验证替代实施方式。
- 对来源论文未披露的工程接口、异常处理和边界条件补充实现细节。

## 八、附图清单

- 图 1：系统总体架构图（见 3.1 Mermaid 图）
- 图 2：数据与控制时序图（见 3.3 Mermaid 图）
- 图 3：可选的调度策略状态机
- 图 4：关键模块数据结构图

## 九、待人工补充

- 具体实验数据
- 与公开在先方案的差异表
- 可选实施例
- 权利要求范围边界
"""

    def write_disclosure(self, candidate: PatentCandidate, output_path: Path | str) -> AdapterResult:
        path = self._ensure_output_path_allowed(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_disclosure_markdown(candidate), encoding="utf-8")
        return AdapterResult.ok("patent disclosure markdown written", path=str(path))

    def export_docx(self, markdown_path: Path | str, output_path: Path | str) -> AdapterResult:
        md = Path(markdown_path).expanduser().resolve()
        out = self._ensure_output_path_allowed(output_path)
        tool = self.md_to_docx_path.expanduser().resolve()
        if not md.is_file():
            return AdapterResult.failed(f"markdown file does not exist: {md}", path=str(md))
        if not tool.is_file():
            return AdapterResult.degraded(f"md_to_docx tool not found: {tool}", tool=str(tool))
        markdown = md.read_text(encoding="utf-8", errors="replace")
        mermaid_blocks = len(re.findall(r"^```mermaid\s*$", markdown, flags=re.MULTILINE | re.IGNORECASE))
        mermaid_renderer = tool.with_name("mermaid_render.py")
        if mermaid_renderer.is_file():
            command = [
                sys.executable,
                str(mermaid_renderer),
                "--input",
                str(md),
                "--output",
                str(md),
                "--docx",
                str(out),
            ]
        else:
            command = [
                sys.executable,
                str(tool),
                "--input",
                str(md),
                "--output",
                str(out),
                "--base-dir",
                str(md.parent),
            ]
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                env=browser_subprocess_env(),
            )
        except OSError as exc:
            return AdapterResult.degraded(f"md_to_docx could not be executed: {exc}", command=command)
        if completed.returncode != 0:
            return AdapterResult.degraded(
                "md_to_docx failed",
                command=command,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        if not out.is_file():
            return AdapterResult.degraded(
                "patent disclosure DOCX was not created",
                command=command,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        rendered_mermaid = 0
        if mermaid_blocks and mermaid_renderer.is_file():
            rendered_markdown = md.read_text(encoding="utf-8", errors="replace")
            rendered_mermaid = len(re.findall(r"<!--\s*!\[图示[^]]*\]\([^)]+\)\s*-->", rendered_markdown))
            if rendered_mermaid < mermaid_blocks:
                return AdapterResult.degraded(
                    "patent disclosure DOCX created without all Mermaid figures",
                    path=str(out),
                    mermaid_blocks=mermaid_blocks,
                    rendered_mermaid=rendered_mermaid,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
        return AdapterResult.ok(
            "patent disclosure docx exported",
            path=str(out),
            mermaid_blocks=mermaid_blocks,
            rendered_mermaid=rendered_mermaid,
            stdout=completed.stdout,
        )

    def _gate_cards(self, cards: list[TechnicalCard]) -> list[str]:
        reasons: list[str] = []
        if len(cards) < 2:
            reasons.append("至少需要两张来自论文的技术卡片，避免单篇论文改写")
        if not all(card.technical_problem.strip() for card in cards):
            reasons.append("缺少明确技术问题")
        if not all(card.method.strip() for card in cards):
            reasons.append("缺少可实施方法描述")
        unique_methods = {card.method.strip().lower() for card in cards if card.method.strip()}
        if len(unique_methods) < len(cards):
            reasons.append("组合方法差异不足，可能只是重复摘要")
        if not any(card.evidence for card in cards):
            reasons.append("缺少来源证据，不能进入交底书定稿")
        return reasons


def technical_card_from_dict(data: dict[str, Any]) -> TechnicalCard:
    return TechnicalCard(
        card_id=str(data.get("card_id") or data.get("id") or uuid.uuid4()),
        paper_id=str(data.get("paper_id") or ""),
        title=str(data.get("title") or "未命名技术点"),
        technical_problem=str(data.get("technical_problem") or data.get("problem") or ""),
        method=str(data.get("method") or data.get("solution") or ""),
        system_components=tuple(data.get("system_components") or data.get("components") or ()),
        evidence=tuple(data.get("evidence") or ()),
        risks=tuple(data.get("risks") or ()),
    )


def candidate_to_dict(candidate: PatentCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "title": candidate.title,
        "source_cards": [card.__dict__ for card in candidate.source_cards],
        "technical_problem": candidate.technical_problem,
        "combined_solution": candidate.combined_solution,
        "technical_effects": list(candidate.technical_effects),
        "novelty_risks": list(candidate.novelty_risks),
        "implementation_gaps": list(candidate.implementation_gaps),
        "gate_status": candidate.gate_status,
        "gate_reasons": list(candidate.gate_reasons),
    }


def _candidate_title(cards: list[TechnicalCard]) -> str:
    head = cards[0].title if cards else "未命名技术组合"
    return f"基于跨论文技术组合的{head}优化方案"


def _merge_unique(values: Any) -> str:
    seen: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.append(value)
    return "；".join(seen) if seen else "待补充"


def safe_patent_filename(name: str, *, extension: str, max_stem_length: int = 80) -> str:
    """Return a filename safe for temporary patent export paths."""

    ext = extension if extension.startswith(".") else f".{extension}"
    stem = _CONTROL_CHARS.sub("_", str(name))
    stem = _UNSAFE_FILENAME_CHARS.sub("_", stem)
    stem = stem.replace("..", "_")
    stem = re.sub(r"\s+", " ", stem).strip(" ._")
    if not stem:
        stem = "patent-disclosure"
    stem = stem[:max_stem_length].rstrip(" ._") or "patent-disclosure"
    return f"{stem}{ext}"


def ensure_path_within_root(path: Path | str, root: Path | str) -> Path:
    """Resolve `path` and reject traversal outside `root`."""

    resolved_root = Path(root).expanduser().resolve()
    resolved_path = Path(path).expanduser().resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"path escapes root: {resolved_path}")
    return resolved_path
