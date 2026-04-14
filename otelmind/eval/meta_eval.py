"""Judge-the-judge pipeline — audits the primary judge with a different model.

Samples a fraction of scored cases and re-scores them with an auditor
(typically Claude, so the model family differs from the OpenAI judge).
Flags cases where the auditor's score differs from the judge's by more
than a threshold on the 1-5 scale.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from otelmind.config import settings
from otelmind.eval.judge import JudgeResult
from otelmind.eval.regression import EvalCase

_AUDITOR_PROMPT = """You are auditing another model's quality score.

Question: {question}
Context: {context}
Answer: {answer}

The primary judge scored dimension "{dimension}" as {judge_raw}/5 with reasoning:
  "{judge_reason}"

Independently re-score this dimension on the same 1-5 scale and say whether
you agree with the primary judge.

Respond ONLY with a JSON object:
{{"score": 1-5, "agrees": true|false, "reasoning": "one sentence"}}
"""


@dataclass
class FlaggedCase:
    case_id: str
    dimension: str
    judge_score: float
    auditor_score: float
    auditor_reasoning: str
    score_delta: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "dimension": self.dimension,
            "judge_score": round(self.judge_score, 4),
            "auditor_score": round(self.auditor_score, 4),
            "score_delta": round(self.score_delta, 4),
            "auditor_reasoning": self.auditor_reasoning,
        }


@dataclass
class MetaEvalReport:
    total_audited: int
    agreements: int
    disagreements: int
    agreement_rate: float
    flagged_cases: list[FlaggedCase] = field(default_factory=list)
    auditor_model: str = ""
    primary_judge_model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_audited": self.total_audited,
            "agreements": self.agreements,
            "disagreements": self.disagreements,
            "agreement_rate": round(self.agreement_rate, 4),
            "auditor_model": self.auditor_model,
            "primary_judge_model": self.primary_judge_model,
            "flagged_cases": [f.to_dict() for f in self.flagged_cases],
        }


class MetaEvaluator:
    """Audit a primary judge with a different auditor model."""

    def __init__(
        self,
        auditor_model: str | None = None,
        api_key: str | None = None,
        disagreement_threshold: float = 1.0,
        seed: int = 42,
    ) -> None:
        self._auditor_model = auditor_model or settings.eval_auditor_model
        # Distinguish "explicit empty (disable)" from "not provided (use default)"
        self._api_key = api_key if api_key is not None else settings.anthropic_api_key
        self._disagreement_threshold = disagreement_threshold
        self._rng = random.Random(seed)

    async def audit_scores(
        self,
        cases: list[EvalCase],
        judge_results: list[JudgeResult],
        sample_rate: float = 0.2,
        primary_judge_model: str = "unknown",
    ) -> MetaEvalReport:
        if not cases or not judge_results:
            return MetaEvalReport(
                total_audited=0,
                agreements=0,
                disagreements=0,
                agreement_rate=0.0,
                auditor_model=self._auditor_model,
                primary_judge_model=primary_judge_model,
            )

        if len(cases) != len(judge_results):
            raise ValueError(
                f"cases ({len(cases)}) and judge_results ({len(judge_results)}) length mismatch"
            )

        sample_rate = max(0.0, min(1.0, sample_rate))
        if sample_rate == 0.0:
            return MetaEvalReport(
                total_audited=0,
                agreements=0,
                disagreements=0,
                agreement_rate=0.0,
                auditor_model=self._auditor_model,
                primary_judge_model=primary_judge_model,
            )

        pairs = list(zip(cases, judge_results, strict=True))
        sample_size = max(1, int(round(sample_rate * len(pairs))))
        sampled = self._rng.sample(pairs, min(sample_size, len(pairs)))

        flagged: list[FlaggedCase] = []
        agreements = 0
        total_audited = 0

        for case, judge_result in sampled:
            for dim, judge_dim in judge_result.scores.items():
                auditor_raw, auditor_reason, ok = await self._call_auditor(
                    case, dim, judge_dim.raw_score, judge_dim.reason
                )
                if not ok:
                    continue
                total_audited += 1
                auditor_score = (auditor_raw - 1) / 4.0
                raw_delta = abs(auditor_raw - judge_dim.raw_score)
                if raw_delta > self._disagreement_threshold:
                    flagged.append(
                        FlaggedCase(
                            case_id=case.id,
                            dimension=dim,
                            judge_score=judge_dim.score,
                            auditor_score=auditor_score,
                            auditor_reasoning=auditor_reason,
                            score_delta=auditor_score - judge_dim.score,
                        )
                    )
                else:
                    agreements += 1

        disagreements = total_audited - agreements
        agreement_rate = agreements / total_audited if total_audited > 0 else 0.0

        return MetaEvalReport(
            total_audited=total_audited,
            agreements=agreements,
            disagreements=disagreements,
            agreement_rate=agreement_rate,
            flagged_cases=flagged,
            auditor_model=self._auditor_model,
            primary_judge_model=primary_judge_model,
        )

    async def _call_auditor(
        self,
        case: EvalCase,
        dimension: str,
        judge_raw: int,
        judge_reason: str,
    ) -> tuple[int, str, bool]:
        if not self._api_key:
            logger.warning("MetaEvaluator: no ANTHROPIC_API_KEY set, skipping")
            return 0, "", False

        try:
            import anthropic
        except ImportError:
            logger.warning("MetaEvaluator: anthropic package not installed, skipping")
            return 0, "", False

        prompt = _AUDITOR_PROMPT.format(
            question=case.question[:500],
            context=(case.context or "")[:500],
            answer=case.actual[:1000],
            dimension=dimension,
            judge_raw=judge_raw,
            judge_reason=judge_reason[:300],
        )

        try:
            client = anthropic.AsyncAnthropic(api_key=self._api_key, timeout=30.0)
            response = await client.messages.create(
                model=self._auditor_model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.warning("MetaEvaluator: auditor call failed: {}", exc)
            return 0, "", False

        content = ""
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                content += text
        content = content.strip()

        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            logger.warning("MetaEvaluator: no JSON object in auditor response")
            return 0, "", False
        try:
            data = json.loads(content[start : end + 1])
        except json.JSONDecodeError as exc:
            logger.warning("MetaEvaluator: invalid JSON from auditor: {}", exc)
            return 0, "", False

        raw = data.get("score")
        if not isinstance(raw, (int, float)):
            return 0, "", False
        raw_int = max(1, min(5, int(round(raw))))
        reasoning = str(data.get("reasoning", ""))[:500]
        return raw_int, reasoning, True
