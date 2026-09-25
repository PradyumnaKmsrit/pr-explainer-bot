"""Unit and integration tests for PR Explainer Bot."""

import json
from pathlib import Path
import pytest

from src.diff_parser import parse_diff
from src.chunker import DiffChunker, is_noisy_file
from src.schema import (
    PRReviewResult,
    FileSummary,
    RiskItem,
    RiskCategory,
    RiskSeverity,
    TestSuggestion,
    PRVerdict,
)
from src.reviewer import Reviewer, render_markdown
from src.llm_client import LLMClient, clean_json_text

SAMPLES_DIR = Path(__file__).parent / "sample_diffs"


def test_clean_json_text():
    raw_with_fences = "Here is your output:\n```json\n{\"title\": \"Test\"}\n```\nHope it helps!"
    assert clean_json_text(raw_with_fences) == '{"title": "Test"}'

    raw_plain = '{"title": "Test Plain"}'
    assert clean_json_text(raw_plain) == '{"title": "Test Plain"}'


def test_diff_parser_small_feature():
    diff_path = SAMPLES_DIR / "small_feature.diff"
    raw = diff_path.read_text(encoding="utf-8")
    parsed = parse_diff(raw)

    assert parsed.total_files == 2
    assert parsed.total_additions > 0

    math_file = parsed.get_file("src/math_utils.py")
    assert math_file is not None
    assert math_file.change_type == "MODIFIED"
    assert math_file.additions == 8
    assert math_file.deletions == 0

    test_file = parsed.get_file("tests/test_math_utils.py")
    assert test_file is not None
    assert test_file.additions == 10


def test_diff_parser_large_multi_file():
    diff_path = SAMPLES_DIR / "large_multi_file.diff"
    raw = diff_path.read_text(encoding="utf-8")
    parsed = parse_diff(raw)

    assert parsed.total_files == 4

    sub_file = parsed.get_file("src/db/models/subscription.py")
    assert sub_file is not None
    assert sub_file.change_type == "ADDED"

    legacy_file = parsed.get_file("src/legacy/billing.py")
    assert legacy_file is not None
    assert legacy_file.change_type == "DELETED"

    lock_file = parsed.get_file("poetry.lock")
    assert lock_file is not None
    assert lock_file.change_type == "MODIFIED"


def test_chunker_noisy_filter():
    assert is_noisy_file("poetry.lock") is True
    assert is_noisy_file("package-lock.json") is True
    assert is_noisy_file("dist/bundle.min.js") is True
    assert is_noisy_file("src/main.py") is False

    diff_path = SAMPLES_DIR / "large_multi_file.diff"
    raw = diff_path.read_text(encoding="utf-8")
    parsed = parse_diff(raw)

    chunker = DiffChunker()
    active, ignored = chunker.prepare_diff(parsed)

    active_paths = [f.path for f in active]
    ignored_paths = [f.path for f in ignored]

    assert "poetry.lock" in ignored_paths
    assert "src/db/models/subscription.py" in active_paths
    assert "src/legacy/billing.py" in active_paths


def test_chunker_splits_when_budget_small():
    diff_path = SAMPLES_DIR / "large_multi_file.diff"
    raw = diff_path.read_text(encoding="utf-8")
    parsed = parse_diff(raw)

    # Use a tiny chunk budget to force splitting across files
    chunker = DiffChunker(max_chunk_tokens=50)
    chunks = chunker.chunk_diff(parsed)

    assert len(chunks) > 1
    assert chunks[0].total_chunks == len(chunks)


def test_schema_serialization():
    result = PRReviewResult(
        title="Add discount calculation and auth security fixes",
        summary="Introduces calculate_discount helper with boundary checks.",
        architectural_impact="Low; local utility function additions.",
        key_changes=["Added calculate_discount", "Added unit tests"],
        file_summaries=[
            FileSummary(
                file_path="src/math_utils.py",
                change_type="MODIFIED",
                additions=7,
                deletions=0,
                summary="Added calculate_discount function.",
                flags=["feature"],
            )
        ],
        risks=[
            RiskItem(
                category=RiskCategory.SECURITY,
                severity=RiskSeverity.HIGH,
                file_path="src/auth/jwt_handler.py",
                line_hint="verify_access_token",
                description="Bypasses signature verification when secret key is unset.",
                mitigation="Enforce secret key validation on app boot.",
            )
        ],
        suggested_tests=[
            TestSuggestion(
                target="calculate_discount",
                scenario="Boundary test with 0% and 100% discount",
                test_type="unit",
                rationale="Ensure boundary floats don't introduce rounding bugs",
            )
        ],
        verdict=PRVerdict.REQUEST_CHANGES,
        confidence_score=0.95,
        estimated_review_time_mins=8,
    )

    json_str = result.model_dump_json()
    reloaded = PRReviewResult.model_validate_json(json_str)

    assert reloaded.title == result.title
    assert reloaded.verdict == PRVerdict.REQUEST_CHANGES
    assert len(reloaded.risks) == 1
    assert reloaded.risks[0].severity == RiskSeverity.HIGH


def test_render_markdown():
    result = PRReviewResult(
        title="Refactor Subscription Models",
        summary="Migrates billing logic to new subscription system.",
        architectural_impact="Database schema update with user relationships.",
        key_changes=["Add subscriptions table", "Deprecate legacy billing"],
        file_summaries=[
            FileSummary(
                file_path="src/db/models/subscription.py",
                change_type="ADDED",
                additions=19,
                deletions=0,
                summary="Added Subscription model.",
            )
        ],
        risks=[],
        suggested_tests=[
            TestSuggestion(
                target="upgrade_subscription",
                scenario="Test upgrading non-existent user",
                test_type="integration",
                rationale="Prevent unhandled integrity error",
            )
        ],
        verdict=PRVerdict.APPROVE,
        confidence_score=0.92,
        estimated_review_time_mins=5,
    )

    md = render_markdown(result)
    assert "## 🤖 PR Review: Refactor Subscription Models" in md
    assert "🟢 **APPROVE**" in md
    assert "### 📝 Executive Summary" in md
    assert "### 🧪 Recommended Tests" in md
    assert "✅ *No major security, architectural, or regression risks flagged.*" in md


def test_reviewer_mocked(monkeypatch):
    diff_path = SAMPLES_DIR / "small_feature.diff"
    raw = diff_path.read_text(encoding="utf-8")

    mock_result = PRReviewResult(
        title="Implement Discount Calculation Utility",
        summary="Adds discount helper and unit tests.",
        architectural_impact="None",
        key_changes=["Added calculate_discount"],
        file_summaries=[
            FileSummary(
                file_path="src/math_utils.py",
                change_type="MODIFIED",
                additions=7,
                deletions=0,
                summary="Added calculate_discount",
            )
        ],
        risks=[],
        suggested_tests=[],
        verdict=PRVerdict.APPROVE,
        confidence_score=0.98,
        estimated_review_time_mins=3,
    )

    class MockLLMClient:
        def call_structured(self, system_prompt, user_prompt, schema):
            return mock_result

    reviewer = Reviewer(llm_client=MockLLMClient())
    res = reviewer.review_diff(raw)

    assert res.title == "Implement Discount Calculation Utility"
    assert res.verdict == PRVerdict.APPROVE
    assert len(res.file_summaries) == 1
