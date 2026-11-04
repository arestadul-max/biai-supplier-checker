"""
Data models for the supplier document checker.

Every extracted value carries its verbatim evidence quote from the PDF.
This makes it easy to show a person exactly which sentence led to each decision.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# ── Decision constants ────────────────────────────────────────────────────────
APPROVE = "Approve"
REJECT = "Reject"
NEEDS_PERSON = "Needs a person to check"
VALID_DECISIONS = {APPROVE, REJECT, NEEDS_PERSON}


@dataclass
class ExtractedValue:
    """
    One extracted field paired with the verbatim sentence from the PDF.
    If we could not find or parse the value, is_ambiguous=True and
    the pipeline routes the document to NEEDS_PERSON (Rule 1: never guess).
    """
    raw: str            # the text string found in the PDF (before parsing)
    parsed: Any         # Python-typed value: float, int, str, or None
    evidence: str       # the full line from the PDF that contains this value
    is_ambiguous: bool = False
    ambiguity_reason: str = ""


@dataclass
class PRItem:
    """One row from the purchase-request CSV (one item the company wants to buy)."""
    pr_number: str
    item: str           # letter A–E
    description: str
    unit: str
    quantity: float
    max_unit_price: float   # maximum price the company is willing to pay per unit


@dataclass
class ExtractedQuote:
    """All data extracted from one supplier PDF quotation."""
    pdf_path: str
    raw_text: str       # full text from pdfplumber (used for grounding checks)

    # ── Header ───────────────────────────────────────────────────────────────
    supplier_name:     Optional[ExtractedValue] = None
    quotation_number:  Optional[ExtractedValue] = None
    pr_reference:      Optional[ExtractedValue] = None

    # ── Line-item table ───────────────────────────────────────────────────────
    item_letter:           Optional[ExtractedValue] = None
    description:           Optional[ExtractedValue] = None
    quantity:              Optional[ExtractedValue] = None
    unit:                  Optional[ExtractedValue] = None
    unit_price:            Optional[ExtractedValue] = None
    unit_price_vat_inclusive: bool = False  # True when column header says "VAT included"
    line_amount:           Optional[ExtractedValue] = None

    # ── Totals block ──────────────────────────────────────────────────────────
    subtotal:    Optional[ExtractedValue] = None
    vat_pct:     Optional[ExtractedValue] = None    # None = VAT not shown separately
    vat_amount:  Optional[ExtractedValue] = None
    total:       Optional[ExtractedValue] = None

    # ── Registration numbers ──────────────────────────────────────────────────
    tin:          Optional[ExtractedValue] = None
    bin_number:   Optional[ExtractedValue] = None
    tin_bin_line: str = ""  # raw line for reference in reports

    # ── Payment terms ─────────────────────────────────────────────────────────
    advance_pct:         Optional[ExtractedValue] = None
    payment_days_max:    Optional[ExtractedValue] = None  # max days to pay after delivery
    payment_days_min:    Optional[ExtractedValue] = None  # only set for range clauses
    payment_terms_clear: bool = True   # False when clause is vague ("as agreed", etc.)

    # ── Delivery ──────────────────────────────────────────────────────────────
    delivery_days: Optional[ExtractedValue] = None  # already converted to days if given in weeks

    # ── Penalty clause ────────────────────────────────────────────────────────
    penalty_pct:     Optional[ExtractedValue] = None
    penalty_present: bool = False   # False = no penalty clause at all → REJECT

    # ── Validity ──────────────────────────────────────────────────────────────
    validity_days: Optional[ExtractedValue] = None


@dataclass
class RuleResult:
    """
    The outcome of applying one specific rule to one document.
    triggered=True means this rule flags a problem.
    """
    rule_id:   str    # e.g. "REJECT_1", "REVIEW_5"
    severity:  str    # REJECT or NEEDS_PERSON
    triggered: bool
    reason:    str    # human-readable explanation
    evidence:  str    # verbatim quote from the document


@dataclass
class DocumentResult:
    """
    The final result for one supplier document after the full pipeline.
    Rule 2: every result has a non-empty reason and exactly one of the three allowed decisions.
    """
    pdf_filename:   str
    supplier_name:  str
    item_letter:    str
    decision:       str       # APPROVE, REJECT, or NEEDS_PERSON
    reasons:        list[str] = field(default_factory=list)
    evidence:       list[str] = field(default_factory=list)
    rule_results:   list[RuleResult] = field(default_factory=list)

    # Extracted line-item details from the supplier PDF
    description:    Optional[str]   = None
    quantity:       Optional[float] = None
    unit:           Optional[str]   = None
    unit_price:     Optional[float] = None

    # Set when a human overrides a NEEDS_PERSON result
    human_override: Optional[str] = None
    human_name:     Optional[str] = None
    human_reason:   Optional[str] = None

    # Set when something goes wrong at any stage
    error: Optional[str] = None
