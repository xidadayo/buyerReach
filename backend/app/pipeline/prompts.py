from dataclasses import dataclass
from typing import Any, Literal

PromptStatus = Literal["draft", "test", "shadow", "review", "active", "deprecated"]


@dataclass(frozen=True)
class PromptDefinition:
    name: str
    version: str
    status: PromptStatus
    template: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    examples: tuple[dict[str, Any], ...]
    compatible_models: tuple[str, ...]
    changelog: str


RELEVANCE_PROMPT_V1 = PromptDefinition(
    name="candidate_relevance",
    version="relevance-1.0.0",
    status="active",
    template="Assess only the supplied evidence. Return dimension scores and evidence judgments as JSON.",
    input_schema={"type": "object", "required": ["candidate", "evidence"]},
    output_schema={"type": "object", "required": ["dimensions", "evidence_judgments"]},
    examples=(
        {
            "input": {"candidate": "Example", "evidence": []},
            "output": {"dimensions": {}, "evidence_judgments": []},
        },
    ),
    compatible_models=("gpt-4o-mini", "gpt-4.1-mini"),
    changelog="Initial evidence-only dimensional scoring prompt.",
)

INTENT_PROMPT_V2 = PromptDefinition(
    name="search_intent", version="intent-2.0.0", status="review",
    template=("Parse shared and local qualifiers by grammar. Create bounded target concepts; "
              "never score companies and never silently resolve material low-confidence ambiguity."),
    input_schema={"type": "object", "required": ["original_prompt"]},
    output_schema={"type": "object", "required": ["schema_version", "target_concepts"]},
    examples=(), compatible_models=("gpt-4o-mini", "gpt-4.1-mini"),
    changelog="Industry-neutral scoped-intent contract.")
COMPANY_PROFILE_PROMPT_V2 = PromptDefinition(
    name="company_profile", version="company-profile-2.0.0", status="review",
    template="Extract only supplied company/provider evidence; never read or infer from search intent.",
    input_schema={"type": "object", "required": ["evidence"]},
    output_schema={"type": "object", "required": ["schema_version", "evidence"]}, examples=(),
    compatible_models=("gpt-4o-mini", "gpt-4.1-mini"), changelog="Evidence-only company profile.")
CONCEPT_MATCH_PROMPT_V2 = PromptDefinition(
    name="concept_match", version="concept-match-2.0.0", status="review",
    template="Match frozen bounded concepts to a frozen company profile. Return relations, not a business score.",
    input_schema={"type": "object", "required": ["search_intent", "company_profile"]},
    output_schema={"type": "array"}, examples=(), compatible_models=("gpt-4o-mini", "gpt-4.1-mini"),
    changelog="Bounded relation matching contract.")

INTENT_PROMPT_V2_1 = PromptDefinition(
    name="search_intent", version="intent-2.0.1", status="review",
    template=("Parse shared and local grammatical modifiers into bounded concept scopes. Normalize 0-1 numeric values, "
              "preserve physical/digital distinctions, and return repairable structured JSON."),
    input_schema=INTENT_PROMPT_V2.input_schema, output_schema=INTENT_PROMPT_V2.output_schema,
    examples=(), compatible_models=("deepseek-v4-flash", "deepseek-v4-pro", "gpt-4o-mini", "gpt-4.1-mini"),
    changelog="Adds provider-tolerant normalization and explicit shared-vs-local modifier guidance.")

INTENT_PROMPT_V2_2 = PromptDefinition(
    name="search_intent", version="intent-2.0.2", status="active",
    template=("Parse company-discovery geography and product/industry concepts only. Keep contact titles empty; "
              "contact and email retrieval require a separate downstream action."),
    input_schema=INTENT_PROMPT_V2.input_schema, output_schema=INTENT_PROMPT_V2.output_schema,
    examples=(), compatible_models=INTENT_PROMPT_V2_1.compatible_models,
    changelog="Separates company discovery from contact-role and email planning.")
