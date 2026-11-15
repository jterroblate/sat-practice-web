#!/usr/bin/env python3
"""
Generate a visual HTML classification report from a QA validation report + tagged questions.

Usage:
    python3 reports/generate_classification_report.py \
        --questions merged_tagged_questions.json \
        --qa-report classification_qa_report.json \
        --output reports/question_type_classification_report.html
"""

from __future__ import annotations

import json
import sys
import argparse
from html import escape
from pathlib import Path
from typing import Any
from datetime import datetime


SKILL_DIR = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def escape_report_data(value: Any) -> Any:
    """Treat all imported question/review strings as report text."""
    if isinstance(value, str):
        return escape(value, quote=True)
    if isinstance(value, list):
        return [escape_report_data(item) for item in value]
    if isinstance(value, dict):
        return {escape(str(key), quote=True): escape_report_data(item)
                for key, item in value.items()}
    return value


def build_html(questions: list[dict], qa_report: dict) -> str:
    """Build a report without executing markup from imported content."""
    questions = escape_report_data(questions)
    qa_report = escape_report_data(qa_report)
    total = qa_report.get("total_questions", len(questions))
    passed = qa_report.get("passed_questions", 0)
    failed = qa_report.get("failed_questions", 0)
    needs_review = qa_report.get("needs_review_count", 0)
    conflicts = qa_report.get("conflict_count", 0)
    low_conf = qa_report.get("low_confidence_count", 0)
    model_api = qa_report.get("model_api", {})
    qa_passed = qa_report.get("qa_passed", False)
    dist = qa_report.get("domain_skill_distribution", {})

    # Build question detail rows
    question_rows = []
    for q in questions:
        qid = q.get("question_id", "?")
        c = q.get("classification", {})
        domain = c.get("domain", "—")
        skill = c.get("skill", "—")
        subtype = c.get("subtype", "—")
        confidence = c.get("confidence", "—")
        agreement = c.get("model_agreement", "—")
        review = c.get("needs_review", False)
        sources = ", ".join(c.get("classifier_sources", []))
        evidence = c.get("evidence", {})

        prompt_preview = q.get("prompt", "")[:80]

        # Confidence color
        conf_color = {
            "high": "#22c55e",
            "medium": "#f59e0b",
            "low": "#ef4444",
        }.get(confidence, "#6b7280")

        # Agreement badge
        agreement_badge = {
            "agree": '<span style="background:#22c55e;color:white;padding:2px 8px;border-radius:10px;font-size:12px">agree</span>',
            "conflict": '<span style="background:#ef4444;color:white;padding:2px 8px;border-radius:10px;font-size:12px">conflict</span>',
            "model_unavailable": '<span style="background:#f59e0b;color:white;padding:2px 8px;border-radius:10px;font-size:12px">model_unavailable</span>',
            "not_checked": '<span style="background:#6b7280;color:white;padding:2px 8px;border-radius:10px;font-size:12px">not_checked</span>',
        }.get(agreement, f'<span style="background:#6b7280;color:white;padding:2px 8px;border-radius:10px;font-size:12px">{agreement}</span>')

        review_badge = '<span style="background:#ef4444;color:white;padding:2px 8px;border-radius:10px;font-size:11px">REVIEW</span>' if review else ''

        evidence_str = "; ".join(f"<b>{k}:</b> {v}" for k, v in evidence.items() if v)

        question_rows.append(f"""
        <tr>
            <td>{qid}</td>
            <td><div style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{prompt_preview}">{prompt_preview}</div></td>
            <td>{domain}</td>
            <td>{skill}</td>
            <td style="font-size:12px;color:#6b7280">{subtype}</td>
            <td style="color:{conf_color};font-weight:bold">{confidence}</td>
            <td>{agreement_badge} {review_badge}</td>
            <td style="font-size:12px">{sources}</td>
            <td style="font-size:11px;max-width:250px">{evidence_str}</td>
        </tr>""")

    # Domain/skill distribution summary
    distribution_rows = ""
    for domain, info in dist.items():
        skills_html = "<br>".join(f"&nbsp;&nbsp;{s}: {c}" for s, c in info.get("skills", {}).items())
        distribution_rows += f"<tr><td><b>{domain}</b></td><td>{info['total']}</td><td>{skills_html}</td></tr>"

    # Needs review detail
    review_items = qa_report.get("details", {}).get("needs_review", [])
    review_rows = ""
    for item in review_items:
        issues = "; ".join(item.get("issues", [])) or "confidence low/medium or conflict"
        review_rows += f"""
        <tr>
            <td>{item.get('question_id','')}</td>
            <td>{item.get('skill','')}</td>
            <td><span style="color:{'#ef4444' if item.get('confidence') in ('low','medium') else '#22c55e'}">{item.get('confidence','')}</span></td>
            <td style="font-size:12px">{issues}</td>
        </tr>"""

    # Conflict detail
    conflict_items = qa_report.get("details", {}).get("conflicts", [])
    conflict_rows = ""
    for item in conflict_items:
        ks = item.get("model_suggestion", {})
        reasoning = ks.get("reasoning") or ""
        conflict_rows += f"""
        <tr>
            <td>{item.get('question_id','')}</td>
            <td>Rule: {item.get('rule_based_skill','')}</td>
            <td>Review: {ks.get('domain','?')}/{ks.get('skill','?')}</td>
            <td style="font-size:12px">{reasoning[:100]}</td>
        </tr>"""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SAT RW Question Type Classification Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f8fafc; color: #1e293b; }}
  h1 {{ font-size: 24px; margin-bottom: 4px; }}
  .subtitle {{ color: #64748b; font-size: 14px; margin-bottom: 20px; }}
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .stat-card {{ background: white; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .stat-card .value {{ font-size: 28px; font-weight: 700; }}
  .stat-card .label {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
  .stat-card.pass {{ border-left: 4px solid #22c55e; }}
  .stat-card.fail {{ border-left: 4px solid #ef4444; }}
  .stat-card.warn {{ border-left: 4px solid #f59e0b; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 24px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; font-size: 13px; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #475569; position: sticky; top: 0; }}
  tr:hover td {{ background: #f8fafc; }}
  .section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; }}
  .badge-pass {{ background: #dcfce7; color: #166534; }}
  .badge-fail {{ background: #fce4ec; color: #c62828; }}
</style>
</head>
<body>
<h1>📊 SAT Reading and Writing — Question Type Classification Report</h1>
<div class="subtitle">Generated: {timestamp} | Source: sat-practice-generator</div>

<div class="summary">
  <div class="stat-card {'pass' if qa_passed else 'fail'}">
    <div class="value">{'PASS' if qa_passed else 'FAIL'}</div>
    <div class="label">QA Status</div>
  </div>
  <div class="stat-card">
    <div class="value">{total}</div>
    <div class="label">Total Questions</div>
  </div>
  <div class="stat-card pass">
    <div class="value">{passed}</div>
    <div class="label">Passed</div>
  </div>
  <div class="stat-card fail">
    <div class="value">{failed}</div>
    <div class="label">Failed</div>
  </div>
  <div class="stat-card warn">
    <div class="value">{needs_review}</div>
    <div class="label">Needs Review</div>
  </div>
  <div class="stat-card warn">
    <div class="value">{conflicts}</div>
    <div class="label">Model Conflicts</div>
  </div>
  <div class="stat-card warn">
    <div class="value">{low_conf}</div>
    <div class="label">Low Confidence</div>
  </div>
  <div class="stat-card {'pass' if model_api.get('available') else 'fail'}">
    <div class="value">{'✅' if model_api.get('available') else '❌'}</div>
    <div class="label">Model Review: {model_api.get('successful_calls',0)}OK / {model_api.get('failed_calls',0)}FAIL</div>
  </div>
</div>

<div class="section-title">📂 Domain / Skill Distribution</div>
<table>
  <tr><th>Domain</th><th>Total</th><th>Skills</th></tr>
  {distribution_rows}
</table>

<div class="section-title">📝 All Questions Classification</div>
<div style="overflow-x:auto">
<table>
  <tr>
    <th>ID</th><th>Prompt</th><th>Domain</th><th>Skill</th><th>Subtype</th>
    <th>Confidence</th><th>Agreement</th><th>Sources</th><th>Evidence</th>
  </tr>
  {''.join(question_rows)}
</table>
</div>

<div class="section-title">⚠️ Needs Review ({len(review_items)})</div>
<table>
  <tr><th>Question</th><th>Skill</th><th>Confidence</th><th>Issues</th></tr>
  {review_rows if review_rows else '<tr><td colspan="4" style="text-align:center;color:#64748b">No questions need review.</td></tr>'}
</table>

<div class="section-title">⚔️ Model Conflicts ({len(conflict_items)})</div>
<table>
  <tr><th>Question</th><th>Rule-based</th><th>Model Review</th><th>Review Reasoning</th></tr>
  {conflict_rows if conflict_rows else '<tr><td colspan="4" style="text-align:center;color:#64748b">No conflicts detected.</td></tr>'}
</table>
</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="Generate classification report HTML")
    parser.add_argument("--questions", required=True, type=Path, help="tagged questions JSON")
    parser.add_argument("--qa-report", required=True, type=Path, help="QA report JSON")
    parser.add_argument("--output", required=True, type=Path, help="output HTML path")
    args = parser.parse_args()

    questions_data = load_json(args.questions)
    questions = questions_data if isinstance(questions_data, list) else questions_data.get("questions", questions_data)
    qa_report = load_json(args.qa_report)

    html = build_html(questions, qa_report)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Report: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
