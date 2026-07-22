"""Evidence-based industry standardization for discovery candidates."""

import json
import re
from urllib.request import Request, urlopen


def standardize_industry(evidence: dict, settings: dict) -> dict | None:
    """Map source evidence to a standard industry without using search inputs."""
    if not settings.get("enabled") or not str(settings.get("api_key") or "").strip():
        return None
    body = {
        "model": settings["model_name"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Classify a company using ONLY the supplied official website or enrichment evidence. "
                    "Never use or infer from user search terms. Return JSON with standard_industry (string), "
                    "subcategories (array of strings), confidence (0-100), summary (string), evidence_terms, "
                    "products, services, business_types, and market_contexts (arrays of short strings), plus "
                    "physical_goods (boolean or null). Products and services must be explicitly supported by "
                    "the evidence; do not infer them from the requested search or from a broad industry label. "
                    "(array of short phrases copied or closely paraphrased from evidence). If evidence is "
                    "insufficient, return an empty standard_industry and confidence 0."
                ),
            },
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
        ],
    }
    request = Request(
        f"{str(settings['base_url']).rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=min(int(settings.get("request_timeout_seconds") or 30), 30)) as response:  # nosec B310 - configured by administrator
        payload = json.loads(response.read().decode("utf-8"))
    content = str(payload["choices"][0]["message"]["content"])
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("AI industry response did not contain JSON")
    result = json.loads(match.group(0))
    industry = str(result.get("standard_industry") or "").strip()
    if not industry:
        return None
    confidence = min(max(int(result.get("confidence") or 0), 0), 100)
    return {
        "standard_industry": industry[:255],
        "subcategories": [str(item).strip()[:120] for item in result.get("subcategories", []) if str(item).strip()][:20],
        "confidence": confidence,
        "summary": str(result.get("summary") or "").strip()[:1000],
        "evidence_terms": [str(item).strip()[:200] for item in result.get("evidence_terms", []) if str(item).strip()][:20],
        "products": _string_list(result.get("products"), 40),
        "services": _string_list(result.get("services"), 30),
        "business_types": _string_list(result.get("business_types"), 20),
        "market_contexts": _string_list(result.get("market_contexts"), 20),
        "physical_goods": result.get("physical_goods") if isinstance(result.get("physical_goods"), bool) else None,
    }


def match_company_concepts(profile: dict, intent: dict, settings: dict) -> list[dict]:
    """Return evidence-bounded semantic relations; final scoring remains deterministic."""
    if not settings.get("enabled") or not str(settings.get("api_key") or "").strip():
        return []
    body = {
        "model": settings["model_name"], "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": (
                "Match every frozen target concept to the supplied company profile only. Return JSON object "
                "with matches array. Each item: target_concept_id, company_concept, relationship (exact, "
                "synonym, child, descendant, parent, related, conflicting, or unknown), hierarchy_distance "
                "(0-10), context_compatible (boolean), evidence_level (company_description, official_website, "
                "official_product_page, or provider_company), confidence (0-100), evidence_refs (array of "
                "zero-based profile evidence indexes). Never use the search prompt itself as company evidence. "
                "Use unknown when the profile does not support a relation; use conflicting for a same-word but "
                "incompatible meaning such as digital wallet versus physical wallet." )},
            {"role": "user", "content": json.dumps({"search_intent": intent, "company_profile": profile}, ensure_ascii=False)},
        ],
    }
    request = Request(f"{str(settings['base_url']).rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=min(int(settings.get("request_timeout_seconds") or 30), 30)) as response:  # nosec B310
        payload = json.loads(response.read().decode("utf-8"))
    content = str(payload["choices"][0]["message"]["content"])
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("AI concept match response did not contain JSON")
    items = json.loads(match.group(0)).get("matches", [])
    return items if isinstance(items, list) else []


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:200] for item in value if str(item).strip()][:limit]
