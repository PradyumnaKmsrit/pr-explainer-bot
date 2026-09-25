"""Pydantic schemas and models for structured LLM review output."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class RiskSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskCategory(str, Enum):
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    BREAKING_CHANGE = "BREAKING_CHANGE"
    DATA_INTEGRITY = "DATA_INTEGRITY"
    ERROR_HANDLING = "ERROR_HANDLING"
    CONCURRENCY = "CONCURRENCY"
    MAINTAINABILITY = "MAINTAINABILITY"
    OTHER = "OTHER"


class PRVerdict(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


class RiskItem(BaseModel):
    """Specific risk flagged during the diff review."""
    category: RiskCategory = Field(description="Category of the risk")
    severity: RiskSeverity = Field(description="Severity level of the risk")
    file_path: Optional[str] = Field(default=None, description="Affected file path if localized")
    line_hint: Optional[str] = Field(default=None, description="Relevant line or function hint if known")
    description: str = Field(description="Clear explanation of the potential risk or regression")
    mitigation: Optional[str] = Field(default=None, description="Recommended remediation or preventative check")


class FileSummary(BaseModel):
    """Summary of changes within an individual file."""
    file_path: str = Field(description="Relative path of the changed file")
    change_type: str = Field(description="Type of change: ADDED, MODIFIED, DELETED, or RENAMED")
    additions: int = Field(default=0, description="Number of added lines")
    deletions: int = Field(default=0, description="Number of deleted lines")
    summary: str = Field(description="Plain-English explanation of what changed in this file and why")
    flags: List[str] = Field(default_factory=list, description="Tags like 'auth-logic', 'schema-migration', 'refactor', 'test'")


class TestSuggestion(BaseModel):
    """Actionable test recommendation to ensure safety and coverage."""
    __test__ = False

    target: str = Field(description="Target function, class, endpoint, or feature to test")
    scenario: str = Field(description="Specific test case or edge condition that should be verified")
    test_type: str = Field(default="unit", description="Recommended test type (unit, integration, regression, e2e)")
    rationale: str = Field(description="Why this test is needed given the diff")


class PerFileAnalysis(BaseModel):
    """Intermediate analysis per file used during chunked reviews."""
    file_path: str
    change_type: str
    summary: str
    key_points: List[str] = Field(default_factory=list)
    risks: List[RiskItem] = Field(default_factory=list)
    suggested_tests: List[TestSuggestion] = Field(default_factory=list)


class PRReviewResult(BaseModel):
    """Complete synthesized review of a Pull Request / Git Diff."""
    title: str = Field(description="Concise, informative title summarizing the PR")
    summary: str = Field(description="Executive plain-English summary of what this PR does and why")
    architectural_impact: str = Field(description="Assessment of system architecture, dependency, or lifecycle impact")
    key_changes: List[str] = Field(description="Bullet points of the most important functional changes")
    file_summaries: List[FileSummary] = Field(description="Breakdown of changes per file")
    risks: List[RiskItem] = Field(default_factory=list, description="Identified risks, regressions, and safety warnings")
    suggested_tests: List[TestSuggestion] = Field(default_factory=list, description="Recommended tests to add or verify")
    verdict: PRVerdict = Field(description="High-level review assessment: APPROVE, REQUEST_CHANGES, or COMMENT")
    confidence_score: float = Field(default=0.9, ge=0.0, le=1.0, description="Review confidence score (0.0 - 1.0)")
    estimated_review_time_mins: int = Field(default=5, ge=1, description="Estimated minutes a human reviewer would need")
