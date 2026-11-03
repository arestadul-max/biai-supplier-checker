"""
PDF text extraction and field extraction for supplier quotations.

Each extracted field is paired with a verbatim evidence quote (the full line
from the PDF where the value was found). The grounding step later checks that
every evidence string is a substring of the full PDF text.

If a value cannot be found or parsed, the field is marked is_ambiguous=True
and the pipeline routes the document to "Needs a person to check" (Rule 1).
"""

import re
from typing import Optional, Tuple

import pdfplumber

from .models import ExtractedValue, ExtractedQuote


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_line_at(text: str, match_start: int) -> str:
    """Return the full line from `text` that contains position `match_start`."""
    start = text.rfind("\n", 0, match_start) + 1   # start of the line
    end = text.find("\n", match_start)              # end of the line
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def _parse_number(s: str) -> float:
    """Convert a number string like '12,000' or '5,664,000.00' to float."""
    return float(s.replace(",", ""))


def _ambiguous(reason: str, evidence: str = "") -> ExtractedValue:
    """Shorthand: create an ambiguous ExtractedValue with a reason."""
    return ExtractedValue(raw="", parsed=None, evidence=evidence,
                          is_ambiguous=True, ambiguity_reason=reason)


# ── PDF text extraction ───────────────────────────────────────────────────────

def extract_pdf_text(pdf_path: str) -> str:
    """
    Extract all text from a PDF using pdfplumber.
    Raises ValueError if the PDF is empty, image-only, or password-protected.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    text = "\n".join(pages)
    if not text.strip():
        raise ValueError(
            "PDF contains no extractable text. "
            "It may be a scanned image or password-protected."
        )
    return text


# ── Field extractors ──────────────────────────────────────────────────────────

def _extract_supplier_name(text: str) -> Optional[ExtractedValue]:
    """First non-empty line is the supplier name."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and stripped.upper() != "QUOTATION":
            return ExtractedValue(raw=stripped, parsed=stripped, evidence=stripped)
    return _ambiguous("No text found in document")


def _extract_item_row(text: str) -> tuple:
    """
    Parse the main line-item row from the quotation table.
    Returns (item_letter, description, quantity, unit, unit_price, line_amount, vat_inclusive).
    Any field that cannot be parsed comes back as an ambiguous ExtractedValue.

    Also detects if the unit-price column header says 'VAT included'.
    """
    # Check if the column header marks the price as VAT-inclusive
    vat_inclusive = bool(
        re.search(r"unit\s*price\s*\([^)]*vat\s+included", text, re.IGNORECASE)
    )

    # Match a row starting with item letter A–E, e.g.:
    # "A  Cotton twill fabric, 280 gsm  12,000  metre  472.00  5,664,000.00"
    NUM = r"[\d,]+(?:\.\d+)?"
    pattern = re.compile(
        r"^([A-E])\s+(.+?)\s+([\d,]+)\s+(\w+)\s+(" + NUM + r")\s+(" + NUM + r")\s*$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        fail = _ambiguous("Could not find item row (A–E) in document")
        return fail, fail, fail, fail, fail, fail, vat_inclusive

    evidence = _get_line_at(text, m.start())
    item   = ExtractedValue(raw=m.group(1), parsed=m.group(1).strip(), evidence=evidence)
    desc   = ExtractedValue(raw=m.group(2), parsed=m.group(2).strip(), evidence=evidence)
    qty    = ExtractedValue(raw=m.group(3), parsed=_parse_number(m.group(3)), evidence=evidence)
    unit   = ExtractedValue(raw=m.group(4), parsed=m.group(4).strip(), evidence=evidence)
    price  = ExtractedValue(raw=m.group(5), parsed=_parse_number(m.group(5)), evidence=evidence)
    amount = ExtractedValue(raw=m.group(6), parsed=_parse_number(m.group(6)), evidence=evidence)
    return item, desc, qty, unit, price, amount, vat_inclusive


def _extract_totals(
    text: str,
) -> Tuple[Optional[ExtractedValue], Optional[ExtractedValue], Optional[ExtractedValue], Optional[ExtractedValue]]:
    """
    Extract Subtotal, VAT %, VAT amount, and Total.
    Returns None for vat_pct / vat_amount when VAT is not shown as a separate line.
    """
    NUM = r"[\d,]+\.\d+"

    # Subtotal
    subtotal = None
    m = re.search(r"Subtotal\s+(" + NUM + r")", text)
    if m:
        line = _get_line_at(text, m.start())
        subtotal = ExtractedValue(raw=m.group(1), parsed=_parse_number(m.group(1)), evidence=line)

    # VAT line: "VAT 15%  849,600.00"
    vat_pct = vat_amount = None
    m = re.search(r"VAT\s+(\d+(?:\.\d+)?)%\s+(" + NUM + r")", text)
    if m:
        line = _get_line_at(text, m.start())
        vat_pct    = ExtractedValue(raw=m.group(1), parsed=float(m.group(1)), evidence=line)
        vat_amount = ExtractedValue(raw=m.group(2), parsed=_parse_number(m.group(2)), evidence=line)

    # Total line — handles both "Total  X" and "Total (all taxes included)  X"
    total = None
    m = re.search(r"^Total(?:\s*\([^)]+\))?\s+(" + NUM + r")", text, re.MULTILINE)
    if m:
        line = _get_line_at(text, m.start())
        total = ExtractedValue(raw=m.group(1), parsed=_parse_number(m.group(1)), evidence=line)

    return subtotal, vat_pct, vat_amount, total


def _extract_tin_bin(text: str) -> Tuple[Optional[ExtractedValue], Optional[ExtractedValue], str]:
    """
    Extract TIN and BIN registration numbers.
    If the document says 'registration details available on request', both are flagged
    as ambiguous so the REJECT_4 rule fires.
    Returns (tin, bin_number, raw_line).
    """
    # Happy path: "Supplier TIN: 101458013391 BIN: 000170141-0101"
    m = re.search(r"Supplier\s+TIN:\s*(\d+)\s+BIN:\s*([\d\-]+)", text, re.IGNORECASE)
    if m:
        line = _get_line_at(text, m.start())
        tin = ExtractedValue(raw=m.group(1), parsed=m.group(1), evidence=line)
        bin_ = ExtractedValue(raw=m.group(2), parsed=m.group(2), evidence=line)
        return tin, bin_, line

    # Known missing case: "Supplier registration details available on request."
    m = re.search(r"Supplier\s+registration\s+details\s+available", text, re.IGNORECASE)
    if m:
        line = _get_line_at(text, m.start())
        tin  = _ambiguous("TIN not provided — 'registration details available on request'", line)
        bin_ = _ambiguous("BIN not provided — 'registration details available on request'", line)
        return tin, bin_, line

    # Neither pattern found
    tin  = _ambiguous("TIN not found in document")
    bin_ = _ambiguous("BIN not found in document")
    return tin, bin_, ""


def _extract_advance_pct(text: str) -> Optional[ExtractedValue]:
    """Extract the advance payment percentage from the payment clause."""
    # "10% advance payable with the purchase order"
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*advance", text, re.IGNORECASE)
    if m:
        line = _get_line_at(text, m.start())
        return ExtractedValue(raw=m.group(1), parsed=float(m.group(1)), evidence=line)

    # "No advance payment required"
    m = re.search(r"No\s+advance\s+payment", text, re.IGNORECASE)
    if m:
        line = _get_line_at(text, m.start())
        return ExtractedValue(raw="0", parsed=0.0, evidence=line)

    return _ambiguous("Advance payment percentage not found in payment clause")


def _extract_payment_days(
    text: str,
) -> Tuple[Optional[ExtractedValue], Optional[ExtractedValue], bool]:
    """
    Extract payment terms from the Payment clause.
    Returns (days_min, days_max, is_clear).

    is_clear=False when the clause is vague ("as agreed", "as agreed by both parties", etc.)
    or when the range upper-end exceeds the allowed maximum.
    """
    # Vague range: "30 to 90 days as agreed by both parties"
    m = re.search(r"(\d+)\s+to\s+(\d+)\s+days\s+as\s+agreed", text, re.IGNORECASE)
    if m:
        line = _get_line_at(text, m.start())
        d_min = ExtractedValue(raw=m.group(1), parsed=int(m.group(1)), evidence=line)
        d_max = ExtractedValue(raw=m.group(2), parsed=int(m.group(2)), evidence=line)
        return d_min, d_max, False  # "as agreed" = unclear

    # Single clear value: "within 45 days after delivery", "within 30 days of delivery"
    m = re.search(
        r"(?:within|payable\s+within|Balance\s+(?:payable\s+)?within|"
        r"Full\s+payment\s+within|Payment\s+within|Balance\s+within)\s+(\d+)\s+days",
        text, re.IGNORECASE,
    )
    if m:
        line = _get_line_at(text, m.start())
        d = ExtractedValue(raw=m.group(1), parsed=int(m.group(1)), evidence=line)
        return d, d, True

    return None, None, True   # not found — flagged by check_review_3


def _extract_delivery_days(text: str) -> Optional[ExtractedValue]:
    """
    Extract delivery time in days from the Delivery clause.
    Converts weeks to days (1 week = 7 days).
    """
    # "Delivery within 24 days ..." or "Delivery within 4 weeks ..."
    m = re.search(
        r"Delivery\s+within\s+(\d+)\s+(days|weeks)",
        text, re.IGNORECASE,
    )
    if not m:
        # "within 18 days of PO", "within 4 weeks of PO"
        m = re.search(
            r"within\s+(\d+)\s+(days|weeks)\s+(?:of|from)\s+(?:the\s+)?(?:PO|purchase\s+order|date|receiving)",
            text, re.IGNORECASE,
        )
    if m:
        line = _get_line_at(text, m.start())
        n = int(m.group(1))
        unit = m.group(2).lower()
        days = n * 7 if unit == "weeks" else n
        note = f" (converted from {n} weeks)" if unit == "weeks" else ""
        return ExtractedValue(raw=m.group(0), parsed=days, evidence=line,
                              ambiguity_reason=note if note else "")
    return _ambiguous("Delivery time not found in Delivery clause")


def _extract_penalty(text: str) -> Tuple[Optional[ExtractedValue], bool]:
    """
    Find the late-delivery penalty clause and extract the percentage.
    Returns (penalty_pct, is_present).

    is_present=False means NO penalty clause was found at all (→ REJECT_3).

    The word 'penalty' is NOT required — handles phrasing like:
      'the supplier shall pay 0.75% of the order value per week of delay'
    """
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s+)?order\s+value"
        r"\s*(?:per\s+week|for\s+each\s+(?:full\s+)?week)",
        text, re.IGNORECASE,
    )
    if m:
        line = _get_line_at(text, m.start())
        pct = ExtractedValue(raw=m.group(1), parsed=float(m.group(1)), evidence=line)
        return pct, True

    return None, False   # no penalty clause


def _extract_validity_days(text: str) -> Optional[ExtractedValue]:
    """Extract the quotation validity period in days."""
    m = re.search(r"valid\s+for\s+(\d+)\s+days", text, re.IGNORECASE)
    if m:
        line = _get_line_at(text, m.start())
        return ExtractedValue(raw=m.group(1), parsed=int(m.group(1)), evidence=line)
    return _ambiguous("Validity period not found in Validity clause")


# ── Main entry point ──────────────────────────────────────────────────────────

def extract_from_pdf(pdf_path: str) -> ExtractedQuote:
    """
    Extract all fields from one supplier PDF quotation.

    On a completely unreadable PDF, raises ValueError.
    On partial extraction failures, marks affected fields as ambiguous
    so the pipeline routes the document to 'Needs a person to check'.
    """
    text = extract_pdf_text(pdf_path)   # raises ValueError if unreadable

    q = ExtractedQuote(pdf_path=pdf_path, raw_text=text)

    q.supplier_name = _extract_supplier_name(text)

    (q.item_letter, q.description, q.quantity, q.unit,
     q.unit_price, q.line_amount, q.unit_price_vat_inclusive) = _extract_item_row(text)

    q.subtotal, q.vat_pct, q.vat_amount, q.total = _extract_totals(text)

    q.tin, q.bin_number, q.tin_bin_line = _extract_tin_bin(text)

    q.advance_pct = _extract_advance_pct(text)

    q.payment_days_min, q.payment_days_max, q.payment_terms_clear = _extract_payment_days(text)

    q.delivery_days = _extract_delivery_days(text)

    q.penalty_pct, q.penalty_present = _extract_penalty(text)

    q.validity_days = _extract_validity_days(text)

    return q


def verify_grounding(q: ExtractedQuote) -> list[str]:
    """
    Safety check: verify that every evidence quote is a verbatim substring of the PDF text.
    Returns a list of issues (empty = all good).
    With regex extraction this normally passes; it would catch any LLM hallucination.
    """
    issues = []
    fields = [
        q.supplier_name, q.item_letter, q.unit_price, q.quantity,
        q.line_amount, q.subtotal, q.vat_pct, q.total,
        q.tin, q.bin_number, q.advance_pct, q.payment_days_max,
        q.delivery_days, q.penalty_pct, q.validity_days,
    ]
    for fld in fields:
        if fld and fld.evidence and fld.evidence not in q.raw_text:
            issues.append(f"Evidence not found in PDF text: '{fld.evidence[:80]}'")
    return issues
