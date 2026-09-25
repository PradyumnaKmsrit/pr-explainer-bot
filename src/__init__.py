"""PR Explainer Bot package."""

from .diff_parser import parse_diff, ParsedDiff, FileDiff, DiffHunk
from .chunker import DiffChunker, DiffChunk
from .llm_client import LLMClient
from .schema import PRReviewResult, RiskItem, TestSuggestion, FileSummary, PRVerdict
from .reviewer import Reviewer, render_markdown

__all__ = [
    "parse_diff",
    "ParsedDiff",
    "FileDiff",
    "DiffHunk",
    "DiffChunker",
    "DiffChunk",
    "LLMClient",
    "PRReviewResult",
    "RiskItem",
    "TestSuggestion",
    "FileSummary",
    "PRVerdict",
    "Reviewer",
    "render_markdown",
]
