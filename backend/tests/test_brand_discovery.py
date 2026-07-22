from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest

from app.core.database import Base
from app.modules import services
from app.modules.brand_discovery import (
    build_discovery_query,
    filter_discovery_companies,
    filter_exact_brand_companies,
    score_brand_relevance,
)
from app.modules.models import (
    Brand,
    Company,
    DiscoveryCandidate,
    ProviderConfig,
    SearchTask,
    SourceEvidence,
)
from app.modules.schemas import SearchTaskCreate
from app.shared.models import utc_now
from app.modules.services import (
    _ingest_discovery,
    _valid_email_addresses,
    approve_discovery_candidate,
    create_search_task,
    execute_search_task,
    parse_brand_website,
)


def test_search_task_uses_p1_titles_by_default():
    task = SearchTaskCreate(
        name="Default titles", mode="exact_brand", brand_keywords=["Example Brand"]
    )

    assert task.target_titles == [
        "Buyer",
        "Head of Buying",
        "Sourcing Manager",
        "Procurement Manager",
    ]


def test_brand_discovery_discards_contact_titles() -> None:
    task = SearchTaskCreate(
        name="Companies only",
        mode="brand_discovery",
        countries=["US"],
        categories=["fashion luggage"],
        target_titles=["Buyer", "Sourcing Manager"],
    )

    assert task.target_titles == []


def test_provider_email_normalization_discards_none_and_malformed_values() -> None:
    assert _valid_email_addresses(
        [None, "None", "null", "invalid", " Buyer@Example.COM ", "buyer@example.com"]
    ) == [
        "buyer@example.com",
    ]


def test_candidate_industry_enrichment_uses_website_before_hunter(monkeypatch) -> None:
    from types import SimpleNamespace
    from app.modules import industry_enrichment, website_parser

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        website_parser,
        "parse_website",
        lambda *_args, **_kwargs: SimpleNamespace(
            error=None,
            text_snippet="Official company description about women's handbags, backpacks and accessories. "
            * 3,
            page_title="Moda Bags",
            url="https://moda.example",
        ),
    )
    monkeypatch.setattr(
        industry_enrichment,
        "standardize_industry",
        lambda evidence, _settings: (
            {
                "standard_industry": "Bags & Fashion Accessories",
                "subcategories": ["Handbags", "Backpacks"],
                "confidence": 94,
                "summary": "Official website evidence",
                "evidence_terms": ["handbags", "backpacks"],
            }
            if evidence["source"] == "official_website"
            else None
        ),
    )
    monkeypatch.setattr(
        services,
        "enabled_providers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Hunter must not be called")
        ),
    )

    with Session(engine) as db:
        candidate = DiscoveryCandidate(
            name="Moda",
            normalized_name="moda",
            domain="moda.example",
            normalized_domain="moda.example",
            dedupe_key="domain:moda.example",
            website="https://moda.example",
            provider="hunter",
            raw_data={},
            status="pending",
            seen_count=1,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
        )
        db.add(candidate)
        db.flush()
        result = services.enrich_candidate_industry(db, candidate.id)

        assert result["industry"] == "Bags & Fashion Accessories"
        assert result["industry_source"] == "official_website_ai"
        assert result["industry_details"]["subcategories"] == ["Handbags", "Backpacks"]


def test_manual_website_parse_backfills_missing_brand_industry(monkeypatch) -> None:
    from types import SimpleNamespace
    from app.modules import industry_enrichment, website_parser

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        website_parser,
        "parse_website",
        lambda *_args, **_kwargs: SimpleNamespace(
            error=None,
            text_snippet="Official maker of handbags, backpacks and leather accessories. " * 3,
            page_title="Moda",
            url="https://moda.example",
            domain="moda.example",
            emails=[],
            phones=[],
            social_links={},
            content_hash="moda-homepage-v1",
            elapsed_ms=10,
            pages_scanned=1,
            attempted_urls=["https://moda.example"],
        ),
    )
    monkeypatch.setattr(
        industry_enrichment,
        "standardize_industry",
        lambda evidence, _settings: {
            "standard_industry": "Handbags",
            "subcategories": ["Backpacks"],
            "confidence": 91,
            "summary": "Official website evidence",
            "evidence_terms": ["handbags", "backpacks"],
        },
    )
    monkeypatch.setattr(
        services,
        "get_ai_settings",
        lambda *_args, **_kwargs: {"enabled": True, "api_key": "test-key"},
    )

    with Session(engine) as db:
        task = SearchTask(
            name="Find handbag brands",
            mode="brand_discovery",
            filters={"categories": ["handbags"]},
        )
        brand = Brand(
            name="Moda",
            normalized_name="moda",
            primary_website="https://moda.example",
        )
        db.add_all([task, brand])
        db.flush()
        db.add(
            SourceEvidence(
                entity_type="brand",
                entity_id=str(brand.id),
                source_type="commercial_api",
                provider="apollo",
                task_id=task.id,
            )
        )
        db.flush()

        result = services._parse_brand_website(db, None, brand)
        evidence = db.scalar(
            select(SourceEvidence).where(
                SourceEvidence.entity_type == "brand",
                SourceEvidence.content_hash == "moda-homepage-v1",
            )
        )

        assert brand.category == "Handbags"
        assert brand.discovery_score == 91
        assert result["industry"] == "Handbags"
        assert result["industry_source"] == "official_website_ai"
        assert evidence.normalized_evidence["industry_source"] == "official_website_ai"


def test_discovery_query_and_relevance_filtering() -> None:
    filters = {
        "brand_keywords": ["sustainable handbags"],
        "categories": ["handbags"],
        "countries": ["US"],
        "min_relevance": 45,
        "require_website": True,
    }

    assert build_discovery_query(filters) == "sustainable handbags handbags US"
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Sustainable Handbags Co",
                "category": "handbags",
                "headquarters_country": "US",
                "website": "https://bags.example",
                "source_url": "https://directory.example/bags",
            },
            {
                "brand_name": "Sustainable Furniture",
                "category": "furniture",
                "headquarters_country": "US",
                "website": "https://furniture.example",
            },
            {
                "brand_name": "Unrelated Furniture",
                "category": "furniture",
                "headquarters_country": "US",
                "website": "https://furniture.example",
            },
        ],
        filters,
    )

    assert [company["brand_name"] for company in companies] == ["Sustainable Handbags Co"]
    assert companies[0]["relevance_score"] == 0
    assert companies[0]["relevance_reasons"] == []


def test_provider_query_match_does_not_give_every_company_the_same_passing_score() -> None:
    filters = {
        "categories": ["bags"],
        "countries": ["US"],
        "min_relevance": 45,
        "require_website": True,
    }
    shared = {
        "headquarters_country": "US",
        "semantic_category_match": True,
        "semantic_category_provider_confirmed": True,
    }
    companies = filter_discovery_companies(
        [
            {**shared, "brand_name": "United Bags", "website": "https://unitedbags.example"},
            {
                **shared,
                "brand_name": "PNA Tuna",
                "category": "Fisheries Management",
                "website": "https://pnatuna.example",
            },
            {
                **shared,
                "brand_name": "Menardi Filters",
                "category": "Industrial filter bags",
                "website": "https://filters.example",
            },
        ],
        filters,
    )

    assert [company["brand_name"] for company in companies] == ["United Bags"]
    assert companies[0]["relevance_score"] == 0


def test_buyer_discovery_keeps_provider_results_for_advisory_review() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Poshyc",
                "headquarters_country": "US",
                "website": "https://poshyc.com",
                "semantic_category_match": True,
            }
        ],
        {
            "categories": ["fashion luggage"],
            "countries": ["US"],
            "company_types": ["distributor"],
            "require_website": True,
        },
    )

    assert [company["brand_name"] for company in companies] == ["Poshyc"]


def test_provider_scoped_discovery_does_not_require_company_type_or_industry_field() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Provider Bag Company",
                "headquarters_country": "US",
                "website": "https://provider-bags.example",
                "semantic_match": True,
                "semantic_category_match": True,
            }
        ],
        {
            "mode": "brand_discovery",
            "categories": ["箱包"],
            "countries": ["United States"],
            "company_types": [],
            "require_website": True,
        },
    )

    assert [company["brand_name"] for company in companies] == ["Provider Bag Company"]


def test_direct_industry_evidence_is_admitted_without_scoring_search_constraints() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Moda",
                "category": "Handbags",
                "headquarters_country": "US",
                "website": "https://moda.example",
            },
            {
                "brand_name": "Handbags Directory",
                "headquarters_country": "US",
                "website": "https://directory.example",
            },
        ],
        {"categories": ["handbags"], "countries": ["US"]},
    )

    assert [company["brand_name"] for company in companies] == ["Moda"]
    assert companies[0]["relevance_score"] == 0


def test_enriched_industry_confidence_changes_relevance_score() -> None:
    filters = {"categories": ["handbags"], "countries": ["US"]}
    high, _ = score_brand_relevance(
        {
            "brand_name": "Moda",
            "industry": "Handbags",
            "industry_source": "official_website_ai",
            "industry_confidence": 92,
            "headquarters_country": "US",
            "website": "https://moda.example",
        },
        filters,
    )
    low, _ = score_brand_relevance(
        {
            "brand_name": "Maybe Moda",
            "industry": "Handbags",
            "industry_source": "hunter_company_enrichment_ai",
            "industry_confidence": 52,
            "headquarters_country": "US",
            "website": "https://maybe.example",
        },
        filters,
    )

    assert high == 92
    assert low == 52


def test_search_constraints_do_not_change_enriched_relevance_score() -> None:
    company = {
        "brand_name": "Moda",
        "industry": "Handbags",
        "industry_source": "official_website_ai",
        "industry_confidence": 86,
        "headquarters_country": "US",
        "website": "https://moda.example",
        "source_url": "https://directory.example/moda",
    }

    score, _ = score_brand_relevance(company, {"categories": ["handbags"], "countries": ["US"]})

    assert score == 86


def test_minimum_relevance_only_applies_after_independent_evaluation() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Uncertain Moda",
                "industry": "Handbags",
                "industry_source": "official_website_ai",
                "industry_confidence": 44,
                "headquarters_country": "US",
                "website": "https://uncertain.example",
            }
        ],
        {"categories": ["handbags"], "countries": ["US"], "min_relevance": 45},
    )

    assert companies == []


def test_category_word_in_name_needs_relevant_industry_evidence() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Wire Belt Company",
                "industry": "Industrial Supplies",
                "headquarters_country": "UK",
                "website": "https://wire.example",
            },
            {
                "brand_name": "Safety Belt Solutions",
                "industry": "Automotive Safety",
                "headquarters_country": "UK",
                "website": "https://safety.example",
            },
            {
                "brand_name": "Peachy Belts",
                "industry": "Retail - Apparel & Accessories",
                "headquarters_country": "UK",
                "website": "https://peachy.example",
            },
            {
                "brand_name": "The Belt Makers",
                "industry": "Leather Goods Manufacturing",
                "headquarters_country": "UK",
                "website": "https://makers.example",
            },
        ],
        {"categories": ["belts"], "countries": ["United Kingdom"], "min_relevance": 45},
    )

    assert [(item["brand_name"], item["relevance_score"]) for item in companies] == [
        ("Peachy Belts", 0),
        ("The Belt Makers", 0),
    ]


def test_discovery_country_is_a_strict_brand_origin_filter() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Italian HQ",
                "category": "handbags",
                "headquarters_country": "IT",
                "website": "https://italian.example",
            },
            {
                "brand_name": "Italy Market Only",
                "category": "handbags",
                "country": "Italy",
                "country_scope": "operating",
                "website": "https://market.example",
            },
            {
                "brand_name": "US Brand",
                "category": "handbags",
                "registered_country": "US",
                "website": "https://us.example",
            },
            {
                "brand_name": "Country Unknown",
                "category": "handbags",
                "website": "https://unknown.example",
            },
        ],
        {"categories": ["handbags"], "countries": ["Italy"], "min_relevance": 45},
    )

    assert [company["brand_name"] for company in companies] == ["Italian HQ"]
    assert companies[0]["country"] == "IT"
    assert companies[0]["country_evidence"] == "总部国家"


def test_discovery_rejects_name_match_without_target_category() -> None:
    companies = filter_discovery_companies(
        [
            {"brand_name": "Mango", "category": "fashion", "website": "https://mango.example"},
            {
                "brand_name": "Mango Bags",
                "category": "fashion accessories",
                "source_excerpt": "Handbags, totes and leather goods.",
                "website": "https://mangobags.example",
            },
        ],
        {
            "brand_keywords": ["mango"],
            "categories": ["handbags"],
            "min_relevance": 45,
            "require_website": True,
        },
    )

    assert [company["brand_name"] for company in companies] == ["Mango Bags"]


def test_hunter_query_text_is_not_used_as_company_category_evidence() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Unrelated Hotel Group",
                "domain": "unrelated-hotel.example",
                "website": "https://unrelated-hotel.example",
                "source_excerpt": "Hunter Discover returned this company for the configured semantic filters.",
                "provider_query": "Find companies related to handbags",
                "semantic_category_match": True,
                "semantic_category_selective": False,
            }
        ],
        {"brand_keywords": ["handbags"], "categories": ["handbags"], "min_relevance": 45},
    )

    assert companies == []


def test_hunter_selective_semantic_results_need_company_level_evidence() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Odeem",
                "domain": "odeem.ae",
                "website": "https://odeem.ae",
                "semantic_category_match": True,
                "semantic_category_selective": True,
            }
        ],
        {"categories": ["handbags"], "min_relevance": 45},
    )

    assert companies == []


def test_hunter_applied_category_filter_can_match_broad_semantic_results() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Prune",
                "domain": "prune.com.ar",
                "website": "https://prune.com.ar",
                "headquarters_country": "AR",
                "semantic_category_match": True,
                "semantic_category_selective": False,
                "semantic_category_provider_confirmed": True,
                "company_category_evidence": "hunter_applied_industry_filter",
            }
        ],
        {"categories": ["luggage"], "countries": ["Argentina"], "min_relevance": 0},
    )

    assert [company["brand_name"] for company in companies] == ["Prune"]
    assert companies[0]["relevance_score"] == 0


def test_hunter_confirmed_category_filter_is_admitted_without_fake_score() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Example UK Retailer",
                "domain": "example.co.uk",
                "website": "https://example.co.uk",
                "headquarters_country": "GB",
                "semantic_category_match": True,
                "semantic_category_provider_confirmed": True,
                "company_category_evidence": "hunter_applied_industry_filter",
            }
        ],
        {"categories": ["Bags & Handbags"], "countries": ["United Kingdom"], "min_relevance": 60},
    )

    assert [(item["brand_name"], item["relevance_score"]) for item in companies] == [
        ("Example UK Retailer", 0),
    ]


def test_hunter_provider_confirmation_without_company_category_evidence_is_rejected() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Unrelated Industrial Group",
                "domain": "industrial.example",
                "website": "https://industrial.example",
                "headquarters_country": "AE",
                "semantic_category_match": True,
                "semantic_category_provider_confirmed": True,
            }
        ],
        {"categories": ["Fashion Luggage and Bags"], "countries": ["United Arab Emirates"]},
    )

    assert companies == []


def test_hunter_broad_semantic_results_need_company_level_category_words() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "City Hotel",
                "domain": "cityhotel.example",
                "website": "https://cityhotel.example",
                "semantic_category_match": True,
                "semantic_category_selective": False,
            },
            {
                "brand_name": "Modern Bags",
                "domain": "modernbags.example",
                "website": "https://modernbags.example",
                "semantic_category_match": True,
                "semantic_category_selective": False,
            },
        ],
        {"categories": ["handbags"], "min_relevance": 45},
    )

    assert companies == []


def test_hunter_broad_results_match_english_names_from_chinese_categories() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Maison Belt",
                "domain": "maisonbelt.fr",
                "website": "https://maisonbelt.fr",
                "headquarters_country": "FR",
                "semantic_category_match": True,
                "semantic_category_selective": False,
            },
            {
                "brand_name": "Paris Hotel",
                "domain": "parishotel.fr",
                "website": "https://parishotel.fr",
                "headquarters_country": "FR",
                "semantic_category_match": True,
                "semantic_category_selective": False,
            },
        ],
        {
            "categories": ["箱包", "皮带"],
            "category_match_mode": "any",
            "countries": ["法国"],
            "min_relevance": 45,
        },
    )

    assert companies == []


def test_compound_categories_require_every_component() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Quick Case",
                "category": "luggage",
                "source_excerpt": "A traditional industrial luggage supplier.",
                "headquarters_country": "MX",
                "website": "https://quickcase.example",
            },
            {
                "brand_name": "Moda Bags",
                "category": "luggage",
                "source_excerpt": "A fast fashion luggage and handbag brand.",
                "headquarters_country": "MX",
                "website": "https://modabags.example",
            },
        ],
        {
            "categories": ["fast fashion", "luggage"],
            "category_match_mode": "all",
            "countries": ["Mexico"],
            "min_relevance": 45,
        },
    )

    assert [company["brand_name"] for company in companies] == ["Moda Bags"]


def test_packaging_company_is_excluded_from_bag_discovery() -> None:
    companies = filter_discovery_companies(
        [
            {
                "brand_name": "Clifton Packaging",
                "category": "bags",
                "source_excerpt": "Flexible packaging and shipping supplies.",
                "headquarters_country": "MX",
                "website": "https://clifton.example",
            }
        ],
        {"categories": ["bags"], "countries": ["Mexico"], "min_relevance": 0},
    )

    assert companies == []


def test_brand_discovery_requires_target_category() -> None:
    with pytest.raises(ValueError, match="target category"):
        SearchTaskCreate(name="Missing category", mode="brand_discovery", brand_keywords=["mango"])


def test_brand_discovery_requires_target_country() -> None:
    with pytest.raises(ValueError, match="target country"):
        SearchTaskCreate(name="Missing country", mode="brand_discovery", categories=["handbags"])


def test_brand_discovery_does_not_use_brand_keywords_as_categories() -> None:
    with pytest.raises(ValueError, match="target category"):
        SearchTaskCreate(
            name="French brands",
            mode="brand_discovery",
            brand_keywords=["箱包，钱包，皮带"],
            countries=[],
            categories=["法国"],
        )


def test_brand_discovery_discards_brand_keywords() -> None:
    task = SearchTaskCreate(
        name="Italian handbags",
        mode="brand_discovery",
        brand_keywords=["Mango", "Gucci"],
        countries=["Italy"],
        categories=["handbags"],
    )

    assert task.brand_keywords == []


def test_brand_discovery_splits_chinese_category_separators() -> None:
    task = SearchTaskCreate(
        name="French accessories",
        mode="brand_discovery",
        countries=["法国"],
        categories=["箱包，皮带；钱包"],
    )

    assert task.categories == ["箱包", "皮带", "钱包"]


def test_search_task_deduplicates_brand_keywords_case_insensitively() -> None:
    task = SearchTaskCreate(
        name="Mango",
        mode="exact_brand",
        brand_keywords=[" MANGO ", "mango", "Mango Inc", "mango inc", ""],
    )

    assert task.brand_keywords == ["MANGO", "Mango Inc"]


def test_candidate_list_orders_by_most_recent_discovery_time() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = utc_now()

    with Session(engine) as db:
        db.add_all(
            [
                DiscoveryCandidate(
                    name="Older High Score",
                    normalized_name="older-high-score",
                    domain="older.example",
                    normalized_domain="older.example",
                    dedupe_key="domain:older.example",
                    website="https://older.example",
                    relevance_score=100,
                    provider="hunter-discover",
                    raw_data={},
                    status="pending",
                    seen_count=1,
                    first_seen_at=now - timedelta(days=2),
                    last_seen_at=now - timedelta(days=2),
                ),
                DiscoveryCandidate(
                    name="Newest Lower Score",
                    normalized_name="newest-lower-score",
                    domain="newest.example",
                    normalized_domain="newest.example",
                    dedupe_key="domain:newest.example",
                    website="https://newest.example",
                    relevance_score=40,
                    provider="hunter-discover",
                    raw_data={},
                    status="pending",
                    seen_count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                ),
            ]
        )
        db.commit()

        result = services.list_discovery_candidates(db, page=1, page_size=50)

        assert [item["name"] for item in result["items"]] == [
            "Newest Lower Score",
            "Older High Score",
        ]


def test_exact_brand_filter_rejects_similar_company_names() -> None:
    companies = filter_exact_brand_companies(
        [
            {"brand_name": "Mango Advisors, Inc.", "domain": "mangoadvisors.com"},
            {"brand_name": "Mango IT Solutions", "domain": "mangoitsolutions.com"},
            {"brand_name": "Mango, Inc.", "domain": "mangomicro.com"},
            {"brand_name": "MANGO", "website": "https://shop.mango.com"},
            {"brand_name": "Nike Communications, Inc.", "domain": "nikecomm.com"},
            {"brand_name": "Nike, Inc.", "domain": "www.nike.com"},
        ],
        {
            "brand_keywords": ["Mango", "Nike"],
            "official_domains": ["mango.com", "https://nike.com"],
        },
    )

    assert [company["brand_name"] for company in companies] == ["MANGO", "Nike, Inc."]


def test_exact_brand_filter_requires_an_official_domain() -> None:
    companies = filter_exact_brand_companies(
        [{"brand_name": "Mango, Inc.", "domain": "mangomicro.com"}],
        {"brand_keywords": ["Mango"]},
    )

    assert companies == []


def test_exact_brand_task_stops_before_provider_without_official_domain() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        task = SearchTask(
            name="Mango exact search",
            mode="exact_brand",
            status="pending",
            filters={"brand_keywords": ["Mango"]},
            progress={},
        )
        db.add(task)
        db.flush()

        execute_search_task(db, task.id)

        assert task.status == "failed"
        assert "mango.com" in (task.error_message or "")


def test_pending_review_brand_cannot_parse_website() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        brand = Brand(name="Candidate", normalized_name="candidate", status="pending_review")
        db.add(brand)
        db.flush()

        with pytest.raises(ValueError, match="Approve the brand discovery candidate"):
            parse_brand_website(db, brand)


@pytest.mark.skip(reason="legacy candidate approval workflow was retired")
def test_discovery_task_creates_isolated_candidate_and_approval_task() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        db.add(
            ProviderConfig(
                provider="public-directory",
                type="company_search",
                priority=10,
                enabled=True,
                config={
                    "adapter": "builtin",
                    "source_type": "public_directory",
                    "companies": [
                        {
                            "brand_name": "Sustainable Handbags Co",
                            "website": "https://bags.example",
                            "country": "US",
                            "category": "handbags",
                            "source_url": "https://directory.example/bags",
                            "source_title": "Sustainable handbag suppliers",
                            "source_excerpt": "A sustainable handbag brand in the United States.",
                        }
                    ],
                },
            )
        )
        db.flush()
        task = create_search_task(
            db,
            SearchTaskCreate(
                name="Sustainable handbag discovery",
                mode="brand_discovery",
                brand_keywords=["sustainable handbags"],
                categories=["handbags"],
                countries=["US"],
                min_relevance=45,
            ),
        )

        execute_search_task(db, task.id)
        assert task.progress["brands"] == 0
        assert task.progress["new_candidates"] == 1

        brand = db.scalar(select(Brand).where(Brand.name == "Sustainable Handbags Co"))
        candidate = db.scalar(
            select(DiscoveryCandidate).where(DiscoveryCandidate.name == "Sustainable Handbags Co")
        )
        assert brand is None
        assert candidate is not None
        assert candidate.status == "pending"
        assert candidate.relevance_score == 0

        enrichment_task = approve_discovery_candidate(db, candidate)
        assert candidate.status == "enriching"
        assert enrichment_task.mode == "exact_brand"
        assert enrichment_task.filters["brand_keywords"] == ["Sustainable Handbags Co"]
        assert enrichment_task.filters["official_domains"] == ["bags.example"]
        assert enrichment_task.filters["discovery_candidate_id"] == str(candidate.id)


@pytest.mark.skip(reason="legacy candidate approval workflow was retired")
def test_repeated_discovery_refreshes_candidate_and_excludes_existing_customer() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        provider = ProviderConfig(
            provider="public-directory",
            type="company_search",
            priority=10,
            enabled=True,
            config={
                "adapter": "builtin",
                "companies": [
                    {
                        "brand_name": "Fresh Bags",
                        "website": "https://freshbags.example",
                        "country": "Italy",
                        "category": "handbags",
                    },
                    {
                        "brand_name": "Existing Bags",
                        "website": "https://existing.example",
                        "country": "Italy",
                        "category": "handbags",
                    },
                ],
            },
        )
        db.add(provider)
        company = Company(legal_name="Existing Bags", domain="existing.example", country="Italy")
        db.add(company)
        db.flush()
        db.add(
            Brand(
                company_id=company.id,
                name="Existing Bags",
                normalized_name="existing-bags",
                primary_website="https://existing.example",
                country="Italy",
                status="active",
            )
        )
        db.flush()

        for index in range(2):
            task = create_search_task(
                db,
                SearchTaskCreate(
                    name=f"Italian handbags {index}",
                    mode="brand_discovery",
                    brand_keywords=["handbags"],
                    categories=["handbags"],
                    countries=["Italy"],
                ),
            )
            execute_search_task(db, task.id)

        candidates = list(db.scalars(select(DiscoveryCandidate)))
        assert len(candidates) == 1
        assert candidates[0].name == "Fresh Bags"
        assert candidates[0].seen_count == 2
        assert task.progress["refreshed_candidates"] == 1
        assert task.progress["excluded_customers"] == 1


def test_discovery_limit_applies_after_excluded_candidates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        provider = ProviderConfig(
            provider="hunter-company-search",
            type="company_search",
            priority=10,
            enabled=True,
            config={"adapter": "hunter"},
        )
        task = SearchTask(
            name="Companies after exclusions",
            mode="brand_discovery",
            status="running",
            filters={},
            progress={},
        )
        rejected = DiscoveryCandidate(
            name="Rejected Bags",
            normalized_name="rejected-bags",
            domain="rejected.example",
            normalized_domain="rejected.example",
            dedupe_key="domain:rejected.example",
            provider=provider.provider,
            status="rejected",
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
        )
        db.add_all([provider, task, rejected])
        db.flush()

        finished = services._ingest_discovery_candidates(
            db,
            task,
            provider,
            [
                {"brand_name": "Rejected Bags", "domain": "rejected.example", "country": "US"},
                {"brand_name": "New Bags", "domain": "new.example", "country": "US"},
                {"brand_name": "Unused Bags", "domain": "unused.example", "country": "US"},
            ],
            result_limit=1,
        )

        assert finished is True
        assert task.progress["discovered"] == 2
        assert task.progress["excluded_rejected"] == 1
        assert task.progress["new_candidates"] == 1
        assert db.scalar(
            select(DiscoveryCandidate).where(DiscoveryCandidate.domain == "new.example")
        ) is not None
        assert db.scalar(
            select(DiscoveryCandidate).where(DiscoveryCandidate.domain == "unused.example")
        ) is None


def test_exact_enrichment_promotes_candidate_into_customer_data() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        provider = ProviderConfig(
            provider="apollo-company", type="company_search", priority=10, enabled=True, config={}
        )
        task = SearchTask(
            name="Enrich Example Bags",
            mode="exact_brand",
            status="running",
            filters={},
            progress={},
        )
        candidate = DiscoveryCandidate(
            name="Example Bags",
            normalized_name="example-bags",
            domain="example.it",
            normalized_domain="example.it",
            dedupe_key="domain:example.it",
            website="https://example.it",
            country="Italy",
            industry="handbags",
            emails_count=4,
            relevance_score=90,
            provider="hunter-discover",
            raw_data={},
            status="enriching",
            seen_count=1,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
        )
        db.add_all([provider, task, candidate])
        db.flush()
        task.filters = {"discovery_candidate_id": str(candidate.id)}
        db.flush()

        completed = _ingest_discovery(
            db,
            task,
            provider,
            [
                {
                    "brand_name": "Example Bags",
                    "legal_name": "Example Bags",
                    "domain": "example.it",
                    "country": "Italy",
                    "category": "handbags",
                }
            ],
        )

        brand = db.scalar(select(Brand).where(Brand.name == "Example Bags"))
        assert completed is True
        assert brand is not None
        assert candidate.status == "promoted"
        assert candidate.promoted_brand_id == brand.id


@pytest.mark.skip(reason="legacy candidate approval workflow was retired")
def test_approved_candidate_uses_archived_vendor_source_and_bypasses_company_search(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    company_search_called = False

    def fake_waterfall(_db, provider_type, _payload, _items_path, **_kwargs):
        nonlocal company_search_called
        if provider_type == "company_search":
            company_search_called = True
        return None, [], []

    monkeypatch.setattr(services, "execute_provider_waterfall", fake_waterfall)
    monkeypatch.setattr(services, "_parse_brand_website", lambda *_args, **_kwargs: {})

    with Session(engine, autoflush=False) as db:
        candidate = DiscoveryCandidate(
            name="Pelletteria P.A.M. srl",
            normalized_name="pelletteria-p-a-m-srl",
            domain="pambags.it",
            normalized_domain="pambags.it",
            dedupe_key="domain:pambags.it",
            website="https://pambags.it",
            country="Italy",
            industry="Handbags & Purses",
            emails_count=0,
            relevance_score=90,
            provider="hunter-company-search",
            raw_data={},
            status="enrichment_failed",
            seen_count=1,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
        )
        db.add(candidate)
        db.flush()
        task = SearchTask(
            name="精准丰富 Pelletteria P.A.M. srl",
            mode="exact_brand",
            status="queued",
            filters={
                "mode": "exact_brand",
                "brand_keywords": ["Pelletteria P.A.M. srl"],
                "official_domains": ["pambags.it"],
                "discovery_candidate_id": str(candidate.id),
                "brand_limit": 1,
            },
            progress={},
        )
        db.add(task)
        db.flush()

        execute_search_task(db, task.id)

        brand = db.scalar(select(Brand).where(Brand.name == "Pelletteria P.A.M. srl"))
        assert company_search_called is False
        assert task.status == "completed"
        assert brand is not None and brand.primary_website == "https://pambags.it"
        assert candidate.status == "promoted"


@pytest.mark.skip(reason="legacy candidate approval workflow was retired")
def test_discovery_task_failure_message_is_actionable() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        db.add(
            ProviderConfig(
                provider="public-directory",
                type="company_search",
                priority=10,
                enabled=True,
                config={
                    "adapter": "builtin",
                    "companies": [
                        {
                            "brand_name": "Mango",
                            "website": "https://mango.example",
                            "headquarters_country": "US",
                            "category": "fashion",
                        }
                    ],
                },
            )
        )
        db.flush()
        task = create_search_task(
            db,
            SearchTaskCreate(
                name="Mango handbag discovery",
                mode="brand_discovery",
                brand_keywords=["Mango"],
                categories=["handbang"],
                countries=["US"],
            ),
        )

        execute_search_task(db, task.id)

        assert task.status == "failed"
        assert "同时满足品牌归属国家（US）和目标品类" in (task.error_message or "")
        assert "品类字段映射" in (task.error_message or "")
