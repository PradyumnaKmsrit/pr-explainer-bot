"""Command line interface for PR Explainer Bot."""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markup import escape

# Load environment variables from .env if present
load_dotenv()

# Ensure local src directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from src.diff_parser import parse_diff
from src.llm_client import LLMClient
from src.reviewer import Reviewer, render_markdown
from src.schema import PRReviewResult, PRVerdict, RiskSeverity


console = Console()


def run_git_command(args: list[str]) -> str:
    """Executes a local git command and returns stdout."""
    try:
        res = subprocess.run(["git"] + args, capture_output=True, text=True, check=True)
        return res.stdout
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Git command failed:[/bold red] git {' '.join(args)}\n{e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        console.print("[bold red]Error:[/bold red] 'git' binary not found on PATH.")
        sys.exit(1)


def render_rich_terminal(result: PRReviewResult):
    """Renders the review result as a rich, color-coded terminal dashboard."""
    verdict_color = {
        PRVerdict.APPROVE: "green",
        PRVerdict.REQUEST_CHANGES: "red",
        PRVerdict.COMMENT: "yellow",
    }.get(result.verdict, "white")

    # Header Panel
    console.print()
    console.print(
        Panel(
            f"[bold]{escape(result.title)}[/bold]\n\n"
            f"[bold {verdict_color}]Verdict: {result.verdict.value}[/bold {verdict_color}] | "
            f"Confidence: {int(result.confidence_score * 100)}% | "
            f"Est. Review Time: ~{result.estimated_review_time_mins} min",
            title="🤖 [bold cyan]PR Explainer Bot[/bold cyan]",
            border_style=verdict_color,
        )
    )

    # Executive Summary
    console.print("\n[bold cyan]📝 Executive Summary[/bold cyan]")
    console.print(escape(result.summary))

    # Architectural Impact
    if result.architectural_impact and result.architectural_impact.lower() != "none":
        console.print("\n[bold cyan]🏗️ Architectural Impact[/bold cyan]")
        console.print(escape(result.architectural_impact))

    # Key Changes
    if result.key_changes:
        console.print("\n[bold cyan]🔑 Key Changes[/bold cyan]")
        for change in result.key_changes:
            console.print(f"  • {escape(change)}")

    # Risks Table
    console.print("\n[bold cyan]⚠️ Risk & Regression Analysis[/bold cyan]")
    if result.risks:
        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Severity", width=12)
        table.add_column("Category", width=16)
        table.add_column("Target", width=22)
        table.add_column("Description", ratio=2)
        table.add_column("Mitigation", ratio=1)

        sev_colors = {
            RiskSeverity.CRITICAL: "bold red on white",
            RiskSeverity.HIGH: "bold red",
            RiskSeverity.MEDIUM: "yellow",
            RiskSeverity.LOW: "cyan",
        }

        for r in result.risks:
            color = sev_colors.get(r.severity, "white")
            target = r.file_path or "Global"
            if r.line_hint:
                target += f"\n({r.line_hint})"
            table.add_row(
                f"[{color}]{r.severity.value}[/{color}]",
                r.category.value if hasattr(r.category, "value") else str(r.category),
                escape(target),
                escape(r.description),
                escape(r.mitigation or "N/A"),
            )
        console.print(table)
    else:
        console.print("  [green]✓ No major risks flagged.[/green]")

    # Recommended Tests
    console.print("\n[bold cyan]🧪 Recommended Tests[/bold cyan]")
    if result.suggested_tests:
        for t in result.suggested_tests:
            console.print(
                f"  • [[bold yellow]{t.test_type.upper()}[/bold yellow]] "
                f"[bold]{escape(t.target)}[/bold]: {escape(t.scenario)} "
                f"\n    [dim]Rationale: {escape(t.rationale)}[/dim]"
            )
    else:
        console.print("  [dim]No additional specific tests suggested.[/dim]")

    # File Summaries Table
    if result.file_summaries:
        console.print("\n[bold cyan]📂 File Breakdown[/bold cyan]")
        f_table = Table(show_header=True, header_style="bold blue", expand=True)
        f_table.add_column("File", ratio=1)
        f_table.add_column("Change", width=10)
        f_table.add_column("Diff", width=12)
        f_table.add_column("Summary", ratio=2)

        for fs in result.file_summaries:
            diff_stat = f"[green]+{fs.additions}[/green]/[red]-{fs.deletions}[/red]"
            f_table.add_row(
                escape(fs.file_path),
                fs.change_type,
                diff_stat,
                escape(fs.summary),
            )
        console.print(f_table)

    console.print()


@click.group()
def cli():
    """PR Explainer Bot: AI-powered git diff analysis, risk detection, and test recommender."""
    pass


@cli.command("review")
@click.argument("diff_file", required=False, type=str)
@click.option("--staged", is_flag=True, help="Review currently staged git changes (git diff --cached)")
@click.option("--branch", type=str, help="Review changes against a target branch (git diff <branch>...HEAD)")
@click.option("--provider", type=click.Choice(["gemini", "groq"], case_sensitive=False), help="LLM provider")
@click.option("--model", type=str, help="Model name (e.g. gemini-2.5-flash or llama-3.3-70b-versatile)")
@click.option("--api-key", type=str, help="API key for the selected provider")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "markdown", "json"], case_sensitive=False),
    default="terminal",
    help="Output format",
)
@click.option("--output", type=click.Path(writable=True), help="Save report to specified file path")
def review(
    diff_file: Optional[str],
    staged: bool,
    branch: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    api_key: Optional[str],
    output_format: str,
    output: Optional[str],
):
    """
    Review a git diff from a file, standard input, or active git repository.
    
    Examples:\n
      python cli.py review path/to/patch.diff\n
      python cli.py review --staged\n
      python cli.py review --branch main\n
      git diff HEAD~1 | python cli.py review -
    """
    raw_diff = ""

    # 1. Fetch diff source
    if staged:
        raw_diff = run_git_command(["diff", "--cached"])
    elif branch:
        raw_diff = run_git_command(["diff", f"{branch}...HEAD"])
    elif diff_file == "-" or (not diff_file and not sys.stdin.isatty()):
        raw_diff = sys.stdin.read()
    elif diff_file:
        file_path = Path(diff_file)
        if not file_path.exists():
            console.print(f"[bold red]Error:[/bold red] Diff file '{diff_file}' not found.")
            sys.exit(1)
        raw_diff = file_path.read_text(encoding="utf-8", errors="replace")
    else:
        # Default fallback: git diff
        console.print("[dim]No diff file provided, reading uncommitted local changes (`git diff`)...[/dim]")
        raw_diff = run_git_command(["diff"])

    if not raw_diff.strip():
        console.print("[yellow]Diff is empty. Nothing to review![/yellow]")
        return

    # 2. Initialize Reviewer
    try:
        llm = LLMClient(provider=provider, model=model, api_key=api_key)
        reviewer = Reviewer(llm_client=llm)
    except Exception as e:
        console.print(f"[bold red]Configuration error:[/bold red] {e}")
        sys.exit(1)

    # 3. Execute Review with status spinner
    with console.status("[bold cyan]Analyzing diff and running AI review pipeline...[/bold cyan]"):
        try:
            result = reviewer.review_diff(raw_diff)
        except Exception as e:
            console.print(f"[bold red]Review execution failed:[/bold red] {e}")
            sys.exit(1)

    # 4. Output handling
    rendered_text = ""
    if output_format.lower() == "terminal":
        render_rich_terminal(result)
        rendered_text = render_markdown(result)
    elif output_format.lower() == "markdown":
        rendered_text = render_markdown(result)
        console.print(rendered_text, markup=False)
    elif output_format.lower() == "json":
        rendered_text = result.model_dump_json(indent=2)
        console.print(rendered_text, markup=False)

    # 5. File save if requested
    if output:
        out_path = Path(output)
        out_content = rendered_text if output_format.lower() != "terminal" else render_markdown(result)
        out_path.write_text(out_content, encoding="utf-8")
        console.print(f"[green]✓ Report written to {output}[/green]")


@cli.command("models")
@click.option("--provider", type=click.Choice(["gemini", "groq"], case_sensitive=False), default="gemini")
@click.option("--api-key", type=str, help="API key override")
def list_models_cmd(provider: str, api_key: Optional[str]):
    """List all available models for your configured API key."""
    import requests

    key = api_key or (os.getenv("GEMINI_API_KEY") if provider.lower() == "gemini" else os.getenv("GROQ_API_KEY"))
    if not key:
        console.print("[bold red]Error:[/bold red] API key not found in .env or arguments.")
        sys.exit(1)

    if provider.lower() == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            resp = requests.get(url, timeout=15)
            data = resp.json()
            if "error" in data:
                console.print(f"[bold red]API Error:[/bold red] {data['error'].get('message')}")
                return
            models = data.get("models", [])
            table = Table(title="Available Gemini Models", show_header=True, header_style="bold cyan")
            table.add_column("Model Name", style="bold green")
            table.add_column("Display Name")
            table.add_column("Supported Methods")

            for m in models:
                methods = ", ".join(m.get("supportedGenerationMethods", []))
                if "generateContent" in methods:
                    name = m.get("name", "").replace("models/", "")
                    table.add_row(name, m.get("displayName", ""), methods)
            console.print(table)
        except Exception as e:
            console.print(f"[bold red]Request failed:[/bold red] {e}")


def main():
    cli()


if __name__ == "__main__":
    main()
