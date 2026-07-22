from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineDefinition:
    pipeline_version: str
    scoring_policy_version: str
    prompt_version: str
    adapter_version: str
    evidence_schema_version: str
    result_schema_version: str
    stage_versions: dict[str, str]


PIPELINE_V1 = PipelineDefinition(
    pipeline_version="1.0.0",
    scoring_policy_version="relevance-1.0.0",
    prompt_version="relevance-1.0.0",
    adapter_version="v1",
    evidence_schema_version="1.0.0",
    result_schema_version="1.0.0",
    stage_versions={
        name: "1.0.0"
        for name in (
            "provider_search",
            "candidate_filtering",
            "website_evidence",
            "industry_enrichment",
            "ai_relevance_scoring",
            "rule_validation",
            "result_classification",
            "contact_enrichment",
        )
    },
)

PIPELINE_V2 = PipelineDefinition(
    pipeline_version="2.0.0",
    scoring_policy_version="relevance-2.0.0",
    prompt_version="intent-2.0.2|company-profile-2.0.0|concept-match-2.0.0",
    adapter_version="hunter-v11|apollo-v8",
    evidence_schema_version="2.0.0",
    result_schema_version="2.0.0",
    stage_versions={
        name: "2.0.0"
        for name in (
            "intent_parsing",
            "knowledge_resolution",
            "provider_query_planning",
            "provider_search",
            "candidate_normalization",
            "candidate_prefiltering",
            "website_evidence",
            "company_profile_extraction",
            "concept_scope_matching",
            "rule_validation",
            "result_classification",
            "contact_enrichment",
        )
    },
)
