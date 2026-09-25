"""Prompt templates for PR explanation, per-file analysis, and multi-file synthesis."""

SYSTEM_PROMPT = """You are a Principal Software Engineer and Staff Security Reviewer.
Your goal is to inspect a git diff and provide a thorough, high-signal, zero-fluff Pull Request review.

Key Principles:
1. Plain-English Clarity: Explain what the code actually does and why it was changed, avoiding buzzwords.
2. High-Signal Risk Analysis: Look for actual hazards — auth bypasses, unhandled exceptions, race conditions, SQL/NoSQL injection, data leaks, breaking API contracts, or missing cleanup. Do not nitpick formatting or style unless it causes bugs.
3. Actionable Test Recommendations: Recommend precise tests with specific inputs, edge cases, and expected behaviors (e.g. "Test with expired JWT token", "Test concurrent updates to inventory table").
4. Strict Schema Adherence: You must return valid JSON matching the requested schema. Never output markdown fences (e.g. ```json) in raw mode unless explicitly requested.
"""

SINGLE_PASS_USER_PROMPT = """Analyze the following git diff and produce a structured PR review adhering strictly to the JSON schema.

### Diff Content:
```diff
{diff_content}
```

### Response Instructions:
Return a JSON object conforming to this schema:
{{
  "title": "<Concise descriptive title of what this PR achieves>",
  "summary": "<2-4 sentences explaining the core purpose and outcome of these changes>",
  "architectural_impact": "<Impact on system design, database, dependencies, or interfaces>",
  "key_changes": [
    "<Bullet point 1 of key change>",
    "<Bullet point 2 of key change>"
  ],
  "file_summaries": [
    {{
      "file_path": "path/to/file",
      "change_type": "MODIFIED|ADDED|DELETED|RENAMED",
      "additions": 10,
      "deletions": 2,
      "summary": "<Short explanation of changes in this file>",
      "flags": ["security", "refactor", "bugfix"]
    }}
  ],
  "risks": [
    {{
      "category": "SECURITY|PERFORMANCE|BREAKING_CHANGE|DATA_INTEGRITY|ERROR_HANDLING|CONCURRENCY|MAINTAINABILITY|OTHER",
      "severity": "LOW|MEDIUM|HIGH|CRITICAL",
      "file_path": "path/to/file (optional)",
      "line_hint": "line number or function (optional)",
      "description": "<Detailed explanation of the risk>",
      "mitigation": "<Actionable mitigation recommendation>"
    }}
  ],
  "suggested_tests": [
    {{
      "target": "<Function, class, or endpoint name>",
      "scenario": "<Specific edge case or scenario to test>",
      "test_type": "unit|integration|regression|e2e",
      "rationale": "<Why this test is necessary>"
    }}
  ],
  "verdict": "APPROVE|REQUEST_CHANGES|COMMENT",
  "confidence_score": 0.95,
  "estimated_review_time_mins": 5
}}
"""

PER_FILE_USER_PROMPT = """Analyze the following git diff chunk for file(s): {file_paths}
Produce a structured JSON summary of changes, risks, and test recommendations for these specific changes.

```diff
{diff_content}
```

Return a JSON object adhering to this structure:
{{
  "file_path": "{primary_file_path}",
  "change_type": "MODIFIED|ADDED|DELETED|RENAMED",
  "summary": "<Plain-English explanation of what changed and why>",
  "key_points": ["<Key detail 1>", "<Key detail 2>"],
  "risks": [
    {{
      "category": "SECURITY|PERFORMANCE|BREAKING_CHANGE|DATA_INTEGRITY|ERROR_HANDLING|CONCURRENCY|MAINTAINABILITY|OTHER",
      "severity": "LOW|MEDIUM|HIGH|CRITICAL",
      "file_path": "{primary_file_path}",
      "line_hint": "<line or function>",
      "description": "<Risk description>",
      "mitigation": "<Mitigation>"
    }}
  ],
  "suggested_tests": [
    {{
      "target": "<Target function/module>",
      "scenario": "<Edge condition or scenario>",
      "test_type": "unit|integration",
      "rationale": "<Reason>"
    }}
  ]
}}
"""

SYNTHESIS_USER_PROMPT = """You are synthesizing individual file reviews of a large Pull Request into an overall PR Review.

### Total Files Changed: {total_files}
### Total Additions: +{total_additions}, Deletions: -{total_deletions}

### Per-File Summaries:
{per_file_summaries_json}

Synthesize these findings into a unified, coherent Pull Request review.
Deduplicate risks and test recommendations. Identify any cross-file or architectural interactions.
Determine the final verdict (APPROVE, REQUEST_CHANGES, or COMMENT).

Return a JSON object matching the full PRReviewResult schema:
{{
  "title": "<Concise descriptive title of PR>",
  "summary": "<Comprehensive 2-4 sentence executive overview>",
  "architectural_impact": "<Cross-file system impact>",
  "key_changes": ["<Bullet 1>", "<Bullet 2>"],
  "file_summaries": [ ... ],
  "risks": [ ... ],
  "suggested_tests": [ ... ],
  "verdict": "APPROVE|REQUEST_CHANGES|COMMENT",
  "confidence_score": 0.90,
  "estimated_review_time_mins": 10
}}
"""
