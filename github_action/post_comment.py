"""GitHub Action comment poster: formats PR review output and posts/updates comments via GitHub REST API."""

import os
import sys
import json
import argparse
from pathlib import Path
import requests

# Ensure parent directory is in path to import src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schema import PRReviewResult, PRVerdict
from src.reviewer import render_markdown

BOT_SIGNATURE = "<!-- PR-EXPLAINER-BOT-COMMENT -->"


def get_pr_number(event_path: str) -> int:
    """Extracts PR number from GitHub Actions event.json file."""
    if not os.path.exists(event_path):
        raise FileNotFoundError(f"GitHub event payload file not found: {event_path}")

    with open(event_path, "r", encoding="utf-8") as f:
        event = json.load(f)

    if "pull_request" in event:
        return event["pull_request"]["number"]
    if "issue" in event and "pull_request" in event["issue"]:
        return event["issue"]["number"]

    raise ValueError("Event does not contain a Pull Request context.")


def post_or_update_comment(
    token: str,
    repo: str,
    pr_number: int,
    markdown_body: str,
    mode: str = "sticky",
    verdict: PRVerdict = PRVerdict.COMMENT,
):
    """Posts or updates comment on GitHub PR using REST API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_url = f"https://api.github.com/repos/{repo}"
    signed_body = f"{BOT_SIGNATURE}\n{markdown_body}"

    # Formal Pull Request Review mode
    if mode == "review":
        url = f"{base_url}/pulls/{pr_number}/reviews"
        gh_event = {
            PRVerdict.APPROVE: "APPROVE",
            PRVerdict.REQUEST_CHANGES: "REQUEST_CHANGES",
            PRVerdict.COMMENT: "COMMENT",
        }.get(verdict, "COMMENT")

        payload = {
            "body": markdown_body,
            "event": gh_event,
        }
        res = requests.post(url, headers=headers, json=payload, timeout=30)
        res.raise_for_status()
        print(f"Successfully submitted formal PR review ({gh_event}) on PR #{pr_number}.")
        return

    # Sticky comment mode: find and update existing comment
    if mode == "sticky":
        comments_url = f"{base_url}/issues/{pr_number}/comments"
        res = requests.get(comments_url, headers=headers, timeout=30)
        res.raise_for_status()
        existing_comments = res.json()

        for comment in existing_comments:
            if BOT_SIGNATURE in comment.get("body", ""):
                # Update comment
                update_url = f"{base_url}/issues/comments/{comment['id']}"
                patch_res = requests.patch(update_url, headers=headers, json={"body": signed_body}, timeout=30)
                patch_res.raise_for_status()
                print(f"Updated existing sticky PR Explainer comment (ID: {comment['id']}).")
                return

    # Post new comment
    post_url = f"{base_url}/issues/{pr_number}/comments"
    res = requests.post(post_url, headers=headers, json={"body": signed_body}, timeout=30)
    res.raise_for_status()
    print(f"Posted new PR Explainer comment on PR #{pr_number}.")


def main():
    parser = argparse.ArgumentParser(description="Post PR Review to GitHub")
    parser.add_argument("--result", required=True, help="Path to JSON review result file")
    parser.add_argument("--mode", default="sticky", choices=["sticky", "new", "review"], help="Comment strategy")
    parser.add_argument("--pr-number", type=int, help="Optional manual PR number")
    parser.add_argument("--repo", help="Optional repo override (owner/repo)")

    args = parser.parse_args()

    result_path = Path(args.result)
    if not result_path.exists():
        print(f"Error: Review result file '{args.result}' does not exist.", file=sys.stderr)
        sys.exit(1)

    with open(result_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)

    review_result = PRReviewResult.model_validate(raw_json)
    markdown_content = render_markdown(review_result)

    token = os.getenv("GITHUB_TOKEN")
    repo = args.repo or os.getenv("GITHUB_REPOSITORY")
    event_path = os.getenv("GITHUB_EVENT_PATH")

    if not token or not repo:
        print("Dry-run mode: GITHUB_TOKEN or GITHUB_REPOSITORY not set. Printing markdown to stdout:")
        print("=" * 60)
        print(markdown_content)
        print("=" * 60)
        return

    pr_number = args.pr_number
    if not pr_number:
        if not event_path:
            print("Error: GITHUB_EVENT_PATH not set and no --pr-number supplied.", file=sys.stderr)
            sys.exit(1)
        pr_number = get_pr_number(event_path)

    try:
        post_or_update_comment(
            token=token,
            repo=repo,
            pr_number=pr_number,
            markdown_body=markdown_content,
            mode=args.mode,
            verdict=review_result.verdict,
        )
    except Exception as e:
        print(f"Failed to post PR comment to GitHub: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
