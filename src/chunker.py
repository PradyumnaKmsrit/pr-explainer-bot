"""Chunking utilities for splitting git diffs into token-conscious batches for LLM analysis."""

from dataclasses import dataclass, field
import os
import fnmatch
from typing import List, Tuple
from .diff_parser import FileDiff, ParsedDiff

# File patterns that represent lockfiles or generated files
NOISY_PATTERNS = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Cargo.lock",
    "composer.lock",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.bundle.js",
    "dist/*",
    "build/*",
    ".next/*",
    "out/*",
    "__snapshots__/*",
    "*.svg",
    "*.lock",
]


def is_noisy_file(file_path: str) -> bool:
    """Checks whether a file is an auto-generated asset, lockfile, or snapshot."""
    norm_path = file_path.replace("\\", "/")
    base_name = os.path.basename(norm_path)
    for pat in NOISY_PATTERNS:
        if fnmatch.fnmatch(base_name, pat) or fnmatch.fnmatch(norm_path, pat):
            return True
    return False


def estimate_tokens(text: str) -> int:
    """Rough estimation of token count (~4 characters per token)."""
    return max(1, len(text) // 4)


@dataclass
class DiffChunk:
    """A chunk of diff content suitable for an LLM prompt."""
    chunk_id: int
    total_chunks: int
    files: List[FileDiff] = field(default_factory=list)
    raw_text: str = ""
    estimated_tokens: int = 0
    is_per_file: bool = False

    @property
    def file_paths(self) -> List[str]:
        return [f.path for f in self.files]


class DiffChunker:
    """Splits a ParsedDiff into efficient LLM-sized chunks, filtering noise and prioritizing core changes."""

    def __init__(
        self,
        max_chunk_tokens: int = 6000,
        max_file_tokens: int = 4000,
        filter_noisy: bool = True
    ):
        self.max_chunk_tokens = max_chunk_tokens
        self.max_file_tokens = max_file_tokens
        self.filter_noisy = filter_noisy

    def prepare_diff(self, parsed: ParsedDiff) -> Tuple[List[FileDiff], List[FileDiff]]:
        """Separates active source diffs from noisy or generated files."""
        active: List[FileDiff] = []
        ignored: List[FileDiff] = []

        for f in parsed.files:
            if f.is_binary:
                ignored.append(f)
            elif self.filter_noisy and is_noisy_file(f.path):
                ignored.append(f)
            else:
                active.append(f)

        return active, ignored

    def chunk_diff(self, parsed: ParsedDiff) -> List[DiffChunk]:
        """
        Creates chunks from parsed diff.
        
        If the entire active diff fits within `max_chunk_tokens`, returns a single combined chunk.
        Otherwise, partitions into per-file chunks or grouped file chunks.
        """
        active_files, _ = self.prepare_diff(parsed)

        if not active_files:
            return []

        # Check total estimated tokens across all active files
        full_text_parts = [f.to_patch_text() for f in active_files]
        full_text = "\n\n".join(full_text_parts)
        total_tokens = estimate_tokens(full_text)

        # Single-pass case: entire diff fits nicely
        if total_tokens <= self.max_chunk_tokens:
            return [
                DiffChunk(
                    chunk_id=1,
                    total_chunks=1,
                    files=active_files,
                    raw_text=full_text,
                    estimated_tokens=total_tokens,
                    is_per_file=False
                )
            ]

        # Multi-chunk / per-file case
        raw_chunks: List[Tuple[List[FileDiff], str, int]] = []
        current_files: List[FileDiff] = []
        current_text_parts: List[str] = []
        current_tokens = 0

        for f in active_files:
            f_text = f.to_patch_text()
            f_tokens = estimate_tokens(f_text)

            # If a single file diff is larger than max_file_tokens, truncate its interior lines
            if f_tokens > self.max_file_tokens:
                truncated_text = self._truncate_large_file(f, self.max_file_tokens)
                f_tokens = estimate_tokens(truncated_text)
                f_text = truncated_text

            if current_files and (current_tokens + f_tokens > self.max_chunk_tokens):
                # Flush current chunk
                combined_text = "\n\n".join(current_text_parts)
                raw_chunks.append((current_files, combined_text, current_tokens))
                current_files = [f]
                current_text_parts = [f_text]
                current_tokens = f_tokens
            else:
                current_files.append(f)
                current_text_parts.append(f_text)
                current_tokens += f_tokens

        if current_files:
            combined_text = "\n\n".join(current_text_parts)
            raw_chunks.append((current_files, combined_text, current_tokens))

        total_count = len(raw_chunks)
        result: List[DiffChunk] = []
        for idx, (c_files, c_text, c_tokens) in enumerate(raw_chunks, start=1):
            result.append(
                DiffChunk(
                    chunk_id=idx,
                    total_chunks=total_count,
                    files=c_files,
                    raw_text=c_text,
                    estimated_tokens=c_tokens,
                    is_per_file=(len(c_files) == 1)
                )
            )

        return result

    def _truncate_large_file(self, file_diff: FileDiff, max_tokens: int) -> str:
        """Truncates a massive file diff while preserving hunk headers and initial changes."""
        header_text = "\n".join(file_diff.raw_header_lines)
        lines_budget = max_tokens * 3  # roughly 3 lines per token conservative estimate
        collected_lines: List[str] = [header_text]
        used_lines = 0

        for hunk in file_diff.hunks:
            if used_lines >= lines_budget:
                collected_lines.append(f"\n... [Remaining {len(file_diff.hunks)} hunks truncated for brevity] ...")
                break
            collected_lines.append(hunk.header)
            for l in hunk.lines:
                collected_lines.append(l)
                used_lines += 1
                if used_lines >= lines_budget:
                    collected_lines.append("... [Hunk content truncated] ...")
                    break

        return "\n".join(collected_lines)
