"""Reviewer orchestrator: parses diff, chunks, calls LLM, validates, and renders output."""

import json
from typing import Optional, List
from .diff_parser import parse_diff, ParsedDiff, FileDiff
from .chunker import DiffChunker, DiffChunk
from .llm_client import LLMClient
from .schema import PRReviewResult, PerFileAnalysis, FileSummary, PRVerdict
from .prompts import (
    SYSTEM_PROMPT,
    SINGLE_PASS_USER_PROMPT,
    PER_FILE_USER_PROMPT,
    SYNTHESIS_USER_PROMPT,
)


class Reviewer:
    """Orchestrates the git diff review process from parsing to synthesis."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        chunker: Optional[DiffChunker] = None,
    ):
        self.client = llm_client or LLMClient()
        self.chunker = chunker or DiffChunker()

    def review_diff(self, raw_diff: str) -> PRReviewResult:
        """
        Main entry point:
        1. Parses raw git diff.
        2. Filters noise (lockfiles, binaries).
        3. Dynamically selects single-pass or multi-stage chunked synthesis.
        4. Validates output against PRReviewResult schema.
        """
        parsed: ParsedDiff = parse_diff(raw_diff)

        if not parsed.files:
            return PRReviewResult(
                title="Empty Diff",
                summary="No code changes detected in the provided diff.",
                architectural_impact="None",
                key_changes=["No changes to review"],
                file_summaries=[],
                risks=[],
                suggested_tests=[],
                verdict=PRVerdict.APPROVE,
                confidence_score=1.0,
                estimated_review_time_mins=1,
            )

        active_files, ignored_files = self.chunker.prepare_diff(parsed)
        chunks: List[DiffChunk] = self.chunker.chunk_diff(parsed)

        # Case 1: All active changes fit into a single prompt
        if len(chunks) <= 1:
            diff_text = chunks[0].raw_text if chunks else ""
            user_prompt = SINGLE_PASS_USER_PROMPT.format(diff_content=diff_text)
            result = self.client.call_structured(SYSTEM_PROMPT, user_prompt, PRReviewResult)
            
            # Append notes for any ignored files
            for ig in ignored_files:
                note = "Binary asset changed" if ig.is_binary else "Automated lockfile/generated file (skipped detailed review)"
                result.file_summaries.append(
                    FileSummary(
                        file_path=ig.path,
                        change_type=ig.change_type,
                        additions=ig.additions,
                        deletions=ig.deletions,
                        summary=note,
                        flags=["skipped-noisy"],
                    )
                )
            return result

        # Case 2: Multi-stage review (Map-Reduce for large PRs)
        per_file_analyses: List[PerFileAnalysis] = []
        for chunk in chunks:
            chunk_prompt = PER_FILE_USER_PROMPT.format(
                file_paths=", ".join(chunk.file_paths),
                primary_file_path=chunk.file_paths[0],
                diff_content=chunk.raw_text,
            )
            analysis = self.client.call_structured(SYSTEM_PROMPT, chunk_prompt, PerFileAnalysis)
            per_file_analyses.append(analysis)

        # Synthesize per-file reviews into cohesive PR review
        summaries_json = json.dumps([a.model_dump() for a in per_file_analyses], indent=2)
        synthesis_prompt = SYNTHESIS_USER_PROMPT.format(
            total_files=parsed.total_files,
            total_additions=parsed.total_additions,
            total_deletions=parsed.total_deletions,
            per_file_summaries_json=summaries_json,
        )
        final_review = self.client.call_structured(SYSTEM_PROMPT, synthesis_prompt, PRReviewResult)

        # Append ignored files to summary
        for ig in ignored_files:
            note = "Binary asset changed" if ig.is_binary else "Automated lockfile/generated file"
            final_review.file_summaries.append(
                FileSummary(
                    file_path=ig.path,
                    change_type=ig.change_type,
                    additions=ig.additions,
                    deletions=ig.deletions,
                    summary=note,
                    flags=["skipped-noisy"],
                )
            )

        return final_review


def render_markdown(result: PRReviewResult) -> str:
    """Formats the PRReviewResult into a polished GitHub Markdown comment."""
    verdict_badge = {
        PRVerdict.APPROVE: "🟢 **APPROVE**",
        PRVerdict.REQUEST_CHANGES: "🔴 **REQUEST CHANGES**",
        PRVerdict.COMMENT: "🟡 **COMMENT**",
    }.get(result.verdict, str(result.verdict))

    lines = [
        f"## 🤖 PR Review: {result.title}",
        "",
        f"**Verdict:** {verdict_badge}  |  **Confidence:** {int(result.confidence_score * 100)}%  |  **Est. Review Time:** ~{result.estimated_review_time_mins} min",
        "",
        "### 📝 Executive Summary",
        result.summary,
        "",
    ]

    if result.architectural_impact and result.architectural_impact.lower() != "none":
        lines.extend([
            "### 🏗️ Architectural Impact",
            result.architectural_impact,
            "",
        ])

    if result.key_changes:
        lines.extend(["### 🔑 Key Changes"])
        for change in result.key_changes:
            lines.append(f"- {change}")
        lines.append("")

    if result.risks:
        lines.extend([
            "### ⚠️ Identified Risks & Regressions",
            "| Severity | Category | Target | Description | Suggested Mitigation |",
            "|:---:|:---|:---|:---|:---|",
        ])
        for r in result.risks:
            loc = r.file_path or "Global"
            if r.line_hint:
                loc += f" (`{r.line_hint}`)"
            mitigation = r.mitigation or "N/A"
            sev_badge = {
                "CRITICAL": "🚨 **CRITICAL**",
                "HIGH": "🔴 **HIGH**",
                "MEDIUM": "🟡 **MEDIUM**",
                "LOW": "🔵 **LOW**",
            }.get(str(r.severity), str(r.severity))
            lines.append(f"| {sev_badge} | `{r.category}` | `{loc}` | {r.description} | {mitigation} |")
        lines.append("")
    else:
        lines.extend([
            "### ⚠️ Identified Risks & Regressions",
            "✅ *No major security, architectural, or regression risks flagged.*",
            "",
        ])

    if result.suggested_tests:
        lines.extend(["### 🧪 Recommended Tests"])
        for t in result.suggested_tests:
            lines.append(f"- **[{t.test_type.upper()}]** `{t.target}`: {t.scenario} *(Rationale: {t.rationale})*")
        lines.append("")

    if result.file_summaries:
        lines.extend([
            "<details>",
            f"<summary><b>📂 File-by-File Breakdown ({len(result.file_summaries)} files)</b></summary>",
            "",
            "| File | Change | Diff Stats | Summary |",
            "|:---|:---:|:---:|:---|",
        ])
        for fs in result.file_summaries:
            stats = f"+{fs.additions}/-{fs.deletions}"
            lines.append(f"| `{fs.file_path}` | {fs.change_type} | `{stats}` | {fs.summary} |")
        lines.extend(["", "</details>", ""])

    lines.extend([
        "---",
        "*Generated with automated PR Explainer Bot powered by Gemini / Groq.*",
    ])

    return "\n".join(lines)
