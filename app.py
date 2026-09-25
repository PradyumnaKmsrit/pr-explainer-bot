"""Streamlit Web UI for PR Explainer Bot."""

import os
import re
import requests
import streamlit as st
from dotenv import load_dotenv

# Load local environment variables if available
load_dotenv()

from src.llm_client import LLMClient
from src.reviewer import Reviewer, render_markdown
from src.schema import PRReviewResult, PRVerdict, RiskSeverity

# Page configuration
st.set_page_config(
    page_title="PR Explainer & Code Review Bot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E88E5;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #6c757d;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 1rem;
        border-left: 4px solid #1E88E5;
        margin-bottom: 1rem;
    }
    .risk-critical {
        background-color: #ffebee;
        border-left: 4px solid #d32f2f;
        padding: 0.8rem;
        border-radius: 6px;
        margin-bottom: 0.8rem;
    }
    .risk-high {
        background-color: #fff3e0;
        border-left: 4px solid #f57c00;
        padding: 0.8rem;
        border-radius: 6px;
        margin-bottom: 0.8rem;
    }
    .risk-medium {
        background-color: #fffde7;
        border-left: 4px solid #fbc02d;
        padding: 0.8rem;
        border-radius: 6px;
        margin-bottom: 0.8rem;
    }
    .risk-low {
        background-color: #e8f5e9;
        border-left: 4px solid #388e3c;
        padding: 0.8rem;
        border-radius: 6px;
        margin-bottom: 0.8rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Helper function to fetch PR diff from URL
def fetch_pr_diff(pr_url: str) -> str:
    """Fetches raw diff from a GitHub PR URL (e.g. https://github.com/owner/repo/pull/123)."""
    clean_url = pr_url.strip().rstrip("/")
    if not clean_url.endswith(".diff"):
        clean_url += ".diff"

    headers = {"User-Agent": "PR-Explainer-Bot/1.0"}
    gh_token = os.getenv("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    res = requests.get(clean_url, headers=headers, timeout=20)
    if res.status_code == 404:
        raise ValueError("Pull Request not found. Verify the URL is public or check repository permissions.")
    res.raise_for_status()
    return res.text


# Sidebar configuration
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2115/2115955.png", width=70)
    st.title("Settings & LLM")

    # Provider and Key settings
    provider = st.selectbox("LLM Provider", ["gemini", "groq"], index=0)

    env_key = os.getenv("GEMINI_API_KEY") if provider == "gemini" else os.getenv("GROQ_API_KEY")
    api_key = st.text_input(
        f"{provider.capitalize()} API Key",
        value=env_key or "",
        type="password",
        help="Reads automatically from .env if present.",
    )

    default_model = "gemini-flash-latest" if provider == "gemini" else "llama-3.3-70b-versatile"
    model = st.text_input("Model Name", value=os.getenv("GEMINI_MODEL", default_model))

    st.markdown("---")
    st.markdown("### 📌 About")
    st.info(
        "**PR Explainer Bot** is an AI agent that parses git diffs, detects security flaws and regressions, "
        "recommends unit/integration tests, and generates senior-level review summaries."
    )


# Main Content Area
st.markdown('<div class="main-header">🤖 AI Code Review & PR Explainer</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Automated git diff analysis, risk detection, and test recommender</div>', unsafe_allow_html=True)

# Input Tabs
input_tab1, input_tab2, input_tab3 = st.tabs(["🔗 GitHub PR URL", "📁 Sample / Upload Diff", "📝 Paste Raw Diff"])

diff_content = ""

with input_tab1:
    pr_url = st.text_input(
        "Enter GitHub Pull Request URL",
        placeholder="https://github.com/owner/repository/pull/123",
        help="Paste any public pull request link",
    )
    if pr_url:
        try:
            with st.spinner("Fetching diff from GitHub..."):
                diff_content = fetch_pr_diff(pr_url)
                st.success(f"✓ Successfully fetched diff ({len(diff_content.splitlines())} lines)")
        except Exception as e:
            st.error(f"Failed to fetch diff: {e}")

with input_tab2:
    sample_choice = st.selectbox(
        "Choose a sample test diff or upload your own:",
        [
            "None",
            "Risky Auth Change (High Risk / Vulnerability)",
            "Small Feature (Discount Calculator)",
            "Large Multi-File (Subscription System & Migrations)",
        ],
    )
    sample_map = {
        "Risky Auth Change (High Risk / Vulnerability)": "tests/sample_diffs/risky_auth_change.diff",
        "Small Feature (Discount Calculator)": "tests/sample_diffs/small_feature.diff",
        "Large Multi-File (Subscription System & Migrations)": "tests/sample_diffs/large_multi_file.diff",
    }
    if sample_choice in sample_map:
        path = sample_map[sample_choice]
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                diff_content = f.read()

    uploaded_file = st.file_uploader("Or upload a .diff / .patch file", type=["diff", "patch", "txt"])
    if uploaded_file is not None:
        diff_content = uploaded_file.read().decode("utf-8", errors="replace")

with input_tab3:
    pasted_diff = st.text_area("Paste raw unified git diff here", height=200, placeholder="diff --git a/... b/...")
    if pasted_diff.strip():
        diff_content = pasted_diff

# Review Trigger Button
st.markdown("---")
col_btn, col_info = st.columns([1, 4])
with col_btn:
    analyze_clicked = st.button("🚀 Analyze & Review PR", type="primary", use_container_width=True)

if analyze_clicked:
    if not diff_content.strip():
        st.warning("Please provide a diff via PR URL, sample selection, file upload, or text area.")
    elif not api_key.strip():
        st.error(f"Please provide a {provider.capitalize()} API Key in the sidebar or in your .env file.")
    else:
        try:
            with st.spinner("Running AI analysis pipeline (parsing -> chunking -> risk detection -> test generation)..."):
                llm = LLMClient(provider=provider, model=model, api_key=api_key)
                reviewer = Reviewer(llm_client=llm)
                result: PRReviewResult = reviewer.review_diff(diff_content)
                st.session_state["review_result"] = result
                st.session_state["markdown_review"] = render_markdown(result)
        except Exception as e:
            st.error(f"Review failed: {e}")

# Display Results if available
if "review_result" in st.session_state:
    res: PRReviewResult = st.session_state["review_result"]
    md_review = st.session_state["markdown_review"]

    st.markdown("## 📊 Review Dashboard")

    # Header Metrics
    m1, m2, m3, m4 = st.columns(4)
    verdict_emoji = {
        PRVerdict.APPROVE: "🟢 APPROVE",
        PRVerdict.REQUEST_CHANGES: "🔴 REQUEST CHANGES",
        PRVerdict.COMMENT: "🟡 COMMENT",
    }.get(res.verdict, str(res.verdict))

    m1.metric("Verdict", verdict_emoji)
    m2.metric("Confidence", f"{int(res.confidence_score * 100)}%")
    m3.metric("Est. Review Time", f"~{res.estimated_review_time_mins} min")
    m4.metric("Flagged Risks", len(res.risks))

    st.subheader(f"🤖 {res.title}")

    tab_exec, tab_risks, tab_tests, tab_files, tab_markdown = st.tabs(
        ["📝 Summary & Impact", "⚠️ Risk Matrix", "🧪 Test Recommendations", "📂 File Breakdown", "📋 Markdown Export"]
    )

    with tab_exec:
        st.markdown("### Executive Summary")
        st.write(res.summary)

        if res.architectural_impact and res.architectural_impact.lower() != "none":
            st.markdown("### 🏗️ Architectural Impact")
            st.write(res.architectural_impact)

        if res.key_changes:
            st.markdown("### 🔑 Key Changes")
            for c in res.key_changes:
                st.markdown(f"- {c}")

    with tab_risks:
        st.markdown("### ⚠️ Risk & Regression Analysis")
        if not res.risks:
            st.success("✅ No major security, architectural, or regression risks flagged.")
        else:
            for r in res.risks:
                css_class = {
                    RiskSeverity.CRITICAL: "risk-critical",
                    RiskSeverity.HIGH: "risk-high",
                    RiskSeverity.MEDIUM: "risk-medium",
                    RiskSeverity.LOW: "risk-low",
                }.get(r.severity, "risk-low")

                target_str = f" `{r.file_path}`" if r.file_path else ""
                if r.line_hint:
                    target_str += f" ({r.line_hint})"

                st.markdown(
                    f"""
                    <div class="{css_class}">
                        <b>[{r.severity.value}] {r.category.value}</b> — {target_str}<br/>
                        <p style="margin-top:0.3rem; margin-bottom:0.3rem;">{r.description}</p>
                        <b>Suggested Mitigation:</b> {r.mitigation or 'N/A'}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    with tab_tests:
        st.markdown("### 🧪 Recommended Test Scenarios")
        if not res.suggested_tests:
            st.info("No specific additional tests suggested.")
        else:
            for t in res.suggested_tests:
                with st.expander(f"**[{t.test_type.upper()}]** {t.target} — {t.scenario}", expanded=True):
                    st.write(f"**Scenario:** {t.scenario}")
                    st.write(f"**Why this test is needed:** {t.rationale}")

    with tab_files:
        st.markdown(f"### 📂 Changed Files ({len(res.file_summaries)})")
        for fs in res.file_summaries:
            col_f1, col_f2 = st.columns([3, 1])
            with col_f1:
                st.markdown(f"**`{fs.file_path}`** ({fs.change_type})")
                st.write(fs.summary)
            with col_f2:
                st.metric("Diff Stats", f"+{fs.additions} / -{fs.deletions}")
            st.markdown("---")

    with tab_markdown:
        st.markdown("### 📋 Formatted GitHub Review Markdown")
        st.code(md_review, language="markdown")
        st.download_button(
            "💾 Download review.md",
            data=md_review,
            file_name="review.md",
            mime="text/markdown",
            use_container_width=True,
        )
