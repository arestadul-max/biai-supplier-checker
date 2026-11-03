"""
LangGraph pipeline: extract → verify_grounding → compare_with_pr → check_rules → decide
                                                                                       ↓
                                                                          (if unsure) human_review

The pipeline runs the same fixed steps for every document.
The only branch is the final routing to human_review.
All decisions are made by plain Python in rules.py — no LLM.

Rule 1 (never guess): any ambiguity or error at any step routes to NEEDS_PERSON.
Rule 2 (every answer has a reason): the decide node enforces non-empty reasons
and a final safety check raises AssertionError if either rule is violated.
"""

import json
import os
from pathlib import Path
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from .models import (
    DocumentResult, ExtractedQuote, PRItem, RuleResult,
    APPROVE, REJECT, NEEDS_PERSON, VALID_DECISIONS,
)
from .extractor import extract_from_pdf, verify_grounding
from .rules import apply_all_rules, make_decision


# ── Pipeline state (one dict per document) ────────────────────────────────────

class DocState(TypedDict):
    pdf_path:    str
    pr_items:    dict           # {item_letter: PRItem}
    config:      dict           # from rules_config.json

    pdf_text:    str
    extracted:   Optional[ExtractedQuote]
    grounding_issues: list[str]
    rule_results: list[RuleResult]

    decision:    str
    reasons:     list[str]
    evidence:    list[str]
    error:       Optional[str]
    needs_human: bool           # True when decision is NEEDS_PERSON


# ── Node 1: extract ───────────────────────────────────────────────────────────

def extract_node(state: DocState) -> dict:
    """Extract all fields from the PDF. On any failure, escalate to NEEDS_PERSON."""
    try:
        q = extract_from_pdf(state["pdf_path"])
        return {"pdf_text": q.raw_text, "extracted": q, "error": None}
    except Exception as exc:
        msg = f"PDF extraction failed: {exc}"
        return {
            "pdf_text": "",
            "extracted": None,
            "error": msg,
            "decision": NEEDS_PERSON,
            "reasons": [msg],
            "evidence": [],
            "needs_human": True,
        }


# ── Node 2: verify_grounding ──────────────────────────────────────────────────

def verify_grounding_node(state: DocState) -> dict:
    """
    Check that every evidence quote exists verbatim in the PDF text.
    If not, the extraction is untrusted and we escalate.
    """
    if state.get("error"):
        return {"grounding_issues": []}   # already failed

    q: ExtractedQuote = state["extracted"]
    issues = verify_grounding(q)

    if issues:
        msg = "Grounding check failed — evidence quotes not found verbatim in PDF: " + "; ".join(issues)
        return {
            "grounding_issues": issues,
            "error": msg,
            "decision": NEEDS_PERSON,
            "reasons": [msg],
            "evidence": [],
            "needs_human": True,
        }
    return {"grounding_issues": []}


# ── Node 3: compare_with_pr ───────────────────────────────────────────────────

def compare_with_pr_node(state: DocState) -> dict:
    """
    Match the extracted item letter to the purchase request.
    If the item is not in the PR, escalate to NEEDS_PERSON.
    """
    if state.get("error"):
        return {}

    q: ExtractedQuote = state["extracted"]

    if q.item_letter is None or q.item_letter.is_ambiguous:
        msg = "Could not extract item letter (A–E) from document"
        return {
            "error": msg,
            "decision": NEEDS_PERSON,
            "reasons": [msg],
            "evidence": [],
            "needs_human": True,
        }

    letter = q.item_letter.parsed
    if letter not in state["pr_items"]:
        msg = f"Item '{letter}' is not in the uploaded purchase request"
        return {
            "error": msg,
            "decision": NEEDS_PERSON,
            "reasons": [msg],
            "evidence": [],
            "needs_human": True,
        }

    return {}   # item found; state already holds pr_items for the rule checks


# ── Node 4: check_rules ───────────────────────────────────────────────────────

def check_rules_node(state: DocState) -> dict:
    """Apply all deterministic rules and store the full list of results."""
    if state.get("error"):
        return {"rule_results": []}

    q: ExtractedQuote = state["extracted"]
    letter = q.item_letter.parsed
    pr_item: PRItem = state["pr_items"][letter]

    results = apply_all_rules(q, pr_item, state["config"])
    return {"rule_results": results}


# ── Node 5: decide ────────────────────────────────────────────────────────────

def decide_node(state: DocState) -> dict:
    """
    Derive the final decision from the rule results.
    Also enforces Rule 2: every result must have a non-empty reason and a valid decision.
    """
    if state.get("error"):
        # Already set by an earlier node
        assert state.get("decision") in VALID_DECISIONS, "Bug: invalid decision set by error path"
        assert state.get("reasons"), "Bug: error path set no reasons"
        return {"needs_human": state["decision"] == NEEDS_PERSON}

    decision, reasons, evidence = make_decision(state["rule_results"])

    # ── Safety checks (Rule 2) ────────────────────────────────────────────────
    assert decision in VALID_DECISIONS, f"Bug: decide() returned invalid decision '{decision}'"
    assert reasons, "Bug: decide() returned empty reasons list"

    return {
        "decision":    decision,
        "reasons":     reasons,
        "evidence":    evidence,
        "needs_human": decision == NEEDS_PERSON,
    }


# ── Node 6: human_review ─────────────────────────────────────────────────────

def human_review_node(state: DocState) -> dict:
    """
    Marks the document as needing human review.
    Actual human input is collected outside the pipeline (CLI interactive mode or web UI).
    """
    return {"needs_human": True}


# ── Routing ───────────────────────────────────────────────────────────────────

def _route_after_decide(state: DocState) -> str:
    """Route to human_review if the decision is NEEDS_PERSON, otherwise END."""
    return "human_review" if state.get("needs_human") else END


# ── Build the graph ───────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(DocState)

    g.add_node("extract",          extract_node)
    g.add_node("verify_grounding", verify_grounding_node)
    g.add_node("compare_with_pr",  compare_with_pr_node)
    g.add_node("check_rules",      check_rules_node)
    g.add_node("decide",           decide_node)
    g.add_node("human_review",     human_review_node)

    g.set_entry_point("extract")
    g.add_edge("extract",          "verify_grounding")
    g.add_edge("verify_grounding", "compare_with_pr")
    g.add_edge("compare_with_pr",  "check_rules")
    g.add_edge("check_rules",      "decide")

    # The ONLY branch in the whole pipeline
    g.add_conditional_edges(
        "decide",
        _route_after_decide,
        {"human_review": "human_review", END: END},
    )
    g.add_edge("human_review", END)

    return g


_COMPILED_GRAPH = _build_graph().compile()


# ── Public API ────────────────────────────────────────────────────────────────

def load_config(config_path: str = None) -> dict:
    """Load the threshold config from rules_config.json."""
    if config_path is None:
        config_path = Path(__file__).parent.parent / "rules_config.json"
    with open(config_path) as f:
        return json.load(f)


def load_pr_items(csv_path: str) -> dict[str, PRItem]:
    """
    Parse the purchase-request CSV into a dict keyed by item letter.
    Raises ValueError if required columns are missing.
    """
    import csv
    required = {"PR number", "Item", "Description", "Unit",
                "Quantity requested", "Maximum unit price (BDT)"}
    items: dict[str, PRItem] = {}

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not required.issubset(set(reader.fieldnames or [])):
            missing = required - set(reader.fieldnames or [])
            raise ValueError(f"Purchase-request CSV missing columns: {missing}")
        for row in reader:
            letter = row["Item"].strip()
            items[letter] = PRItem(
                pr_number=row["PR number"].strip(),
                item=letter,
                description=row["Description"].strip(),
                unit=row["Unit"].strip(),
                quantity=float(row["Quantity requested"].replace(",", "")),
                max_unit_price=float(row["Maximum unit price (BDT)"].replace(",", "")),
            )
    return items


def run_pipeline(
    pdf_path: str,
    pr_items: dict[str, PRItem],
    config:   dict,
) -> DocumentResult:
    """
    Run the full pipeline for one PDF file.
    Never raises — all errors are caught and become NEEDS_PERSON results.
    """
    filename = os.path.basename(pdf_path)

    try:
        initial: DocState = {
            "pdf_path":        pdf_path,
            "pr_items":        pr_items,
            "config":          config,
            "pdf_text":        "",
            "extracted":       None,
            "grounding_issues": [],
            "rule_results":    [],
            "decision":        NEEDS_PERSON,
            "reasons":         [],
            "evidence":        [],
            "error":           None,
            "needs_human":     False,
        }

        final = _COMPILED_GRAPH.invoke(initial)

        # Pull supplier name and item from the extracted data (if available)
        q: ExtractedQuote = final.get("extracted")
        supplier = (q.supplier_name.parsed if q and q.supplier_name else "Unknown")
        item_letter = (q.item_letter.parsed if q and q.item_letter else "?")

        return DocumentResult(
            pdf_filename=filename,
            supplier_name=supplier,
            item_letter=item_letter,
            decision=final["decision"],
            reasons=final["reasons"],
            evidence=final["evidence"],
            rule_results=final["rule_results"],
            error=final.get("error"),
        )

    except Exception as exc:
        # Catch-all: a bug in the pipeline itself → NEEDS_PERSON (never crash the batch)
        msg = f"Unexpected pipeline error: {exc}"
        return DocumentResult(
            pdf_filename=filename,
            supplier_name="Unknown",
            item_letter="?",
            decision=NEEDS_PERSON,
            reasons=[msg],
            evidence=[],
            error=msg,
        )


def run_batch(
    pdf_paths: list[str],
    pr_items:  dict[str, PRItem],
    config:    dict,
) -> list[DocumentResult]:
    """Run the pipeline on a list of PDF files. One bad file never stops the rest."""
    return [run_pipeline(p, pr_items, config) for p in sorted(pdf_paths)]
