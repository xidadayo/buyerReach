from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RelevancePolicy:
    version: str = "relevance-1.0.0"
    weights: dict[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weights is None:
            object.__setattr__(
                self,
                "weights",
                {
                    "industry_fit": 0.4,
                    "market_fit": 0.25,
                    "buyer_fit": 0.2,
                    "evidence_quality": 0.15,
                },
            )

    def evaluate(
        self, dimensions: dict[str, Any], hard_rules: dict[str, bool] | None = None
    ) -> tuple[int, str]:
        if any(value is False for value in (hard_rules or {}).values()):
            return 0, "D"
        score = min(
            100,
            round(
                sum(float(dimensions.get(key, 0)) * weight for key, weight in self.weights.items())
            ),
        )
        return score, "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 45 else "D"


@dataclass(frozen=True)
class RelevancePolicyV2:
    """Industry-neutral, deterministic policy over validated concept evidence."""

    version: str = "relevance-2.0.0"

    def evaluate(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        status = evaluation.get("evaluation_status", "pending")
        if status != "completed":
            return {**evaluation, "decision": "pending", "target_relevance_score": None,
                    "rating": "Pending", "policy_version": self.version}
        dimensions = evaluation.get("dimension_scores") or {}
        caps = {"product_fit": 40, "industry_fit": 20, "business_type_fit": 15,
                "country_fit": 10, "evidence_quality": 10, "category_coverage": 5}
        score = sum(max(0, min(caps[key], int(dimensions.get(key, 0)))) for key in caps)
        penalties = evaluation.get("penalties") or []
        score = max(0, min(100, score + sum(min(0, int(item.get("points", 0))) for item in penalties)))
        reasons = set(evaluation.get("reason_codes") or [])
        if "excluded_industry_without_direct_product_evidence" in reasons:
            score = 0
        rating = "A" if score >= 85 else "B" if score >= 65 else "C" if score >= 40 else "D"
        decision = "qualified" if rating in {"A", "B"} else "review" if rating == "C" else "rejected"
        return {**evaluation, "decision": decision, "target_relevance_score": score,
                "rating": rating, "policy_version": self.version}
