"""
Deterministic rule checks for supplier quotations.

Every decision is made by plain Python arithmetic — no LLM involved.
Each check returns a RuleResult with triggered=True/False, a human-readable
reason, and the verbatim evidence quote.

Priority (per CLAUDE.md): REJECT > NEEDS_PERSON > APPROVE.
A REJECT is only allowed when the underlying fact is certain.
If the fact behind a Reject rule is ambiguous, it escalates to NEEDS_PERSON instead.
"""

from .models import ExtractedQuote, PRItem, RuleResult, REJECT, NEEDS_PERSON, APPROVE


def _r(rule_id: str, severity: str, triggered: bool, reason: str, evidence: str) -> RuleResult:
    """Shorthand constructor for RuleResult."""
    return RuleResult(rule_id=rule_id, severity=severity, triggered=triggered,
                      reason=reason, evidence=evidence)


# ── REJECT rules (Reject rule 1–5 from company-rules.md) ─────────────────────

def check_reject_1_price(q: ExtractedQuote, pr: PRItem, cfg: dict) -> RuleResult:
    """REJECT: unit price more than X% above maximum."""
    rid = "REJECT_1"
    if q.unit_price is None or q.unit_price.is_ambiguous:
        # Cannot confirm the price — escalate to NEEDS_PERSON, not REJECT
        return _r(rid, NEEDS_PERSON, True,
                  "Unit price not extractable or ambiguous — cannot confirm compliance",
                  q.unit_price.evidence if q.unit_price else "")

    price = q.unit_price.parsed
    max_p = pr.max_unit_price
    threshold = cfg["price_over_max_reject_pct"]
    reject_limit = max_p * (1 + threshold / 100)

    if price > reject_limit:
        over_pct = (price - max_p) / max_p * 100
        return _r(rid, REJECT, True,
                  f"Unit price {price:.2f} BDT is {over_pct:.2f}% above max {max_p:.2f} BDT "
                  f"(threshold: {threshold}%)",
                  q.unit_price.evidence)

    return _r(rid, REJECT, False,
              f"Price {price:.2f} ≤ reject limit {reject_limit:.2f}",
              q.unit_price.evidence)


def check_reject_2_advance(q: ExtractedQuote, cfg: dict) -> RuleResult:
    """REJECT: advance payment more than 20%."""
    rid = "REJECT_2"
    if q.advance_pct is None or q.advance_pct.is_ambiguous:
        return _r(rid, NEEDS_PERSON, True,
                  "Advance payment percentage not found or ambiguous",
                  q.advance_pct.evidence if q.advance_pct else "")

    adv = q.advance_pct.parsed
    limit = cfg["advance_max_pct"]

    if adv > limit:
        return _r(rid, REJECT, True,
                  f"Advance payment {adv:.0f}% exceeds maximum {limit:.0f}%",
                  q.advance_pct.evidence)

    return _r(rid, REJECT, False,
              f"Advance {adv:.0f}% ≤ {limit:.0f}%",
              q.advance_pct.evidence)


def check_reject_3_penalty(q: ExtractedQuote) -> RuleResult:
    """REJECT: no late-delivery penalty clause in the contract."""
    rid = "REJECT_3"
    if not q.penalty_present:
        return _r(rid, REJECT, True,
                  "No late-delivery penalty clause found in the Terms and Conditions",
                  "(no penalty clause in document)")

    return _r(rid, REJECT, False,
              "Penalty clause is present",
              q.penalty_pct.evidence if q.penalty_pct else "")


def check_reject_4_tin_bin(q: ExtractedQuote) -> RuleResult:
    """REJECT: TIN or BIN is missing."""
    rid = "REJECT_4"
    tin_missing  = not q.tin or q.tin.is_ambiguous or not q.tin.parsed
    bin_missing  = not q.bin_number or q.bin_number.is_ambiguous or not q.bin_number.parsed

    if tin_missing or bin_missing:
        missing = []
        if tin_missing:  missing.append("TIN")
        if bin_missing:  missing.append("BIN")
        evidence = q.tin_bin_line or (q.tin.evidence if q.tin else "(not found)")
        return _r(rid, REJECT, True,
                  f"{' and '.join(missing)} missing from document",
                  evidence)

    return _r(rid, REJECT, False,
              f"TIN {q.tin.parsed} and BIN {q.bin_number.parsed} present",
              q.tin_bin_line)


def check_reject_5_delivery(q: ExtractedQuote, cfg: dict) -> RuleResult:
    """REJECT: delivery promised later than 45 days."""
    rid = "REJECT_5"
    if q.delivery_days is None or q.delivery_days.is_ambiguous:
        return _r(rid, NEEDS_PERSON, True,
                  "Delivery time not found or ambiguous",
                  q.delivery_days.evidence if q.delivery_days else "")

    days = q.delivery_days.parsed
    limit = cfg["delivery_reject_days"]

    if days > limit:
        return _r(rid, REJECT, True,
                  f"Delivery {days} days exceeds maximum {limit} days",
                  q.delivery_days.evidence)

    return _r(rid, REJECT, False,
              f"Delivery {days} days ≤ {limit} days",
              q.delivery_days.evidence)


# ── REVIEW rules (Needs a person — rules 1–7 + penalty rate) ─────────────────

def check_review_1_price(q: ExtractedQuote, pr: PRItem, cfg: dict) -> RuleResult:
    """REVIEW: price above max but within the 3% grace band."""
    rid = "REVIEW_1"
    if q.unit_price is None or q.unit_price.is_ambiguous:
        return _r(rid, NEEDS_PERSON, False, "Handled by REJECT_1", "")

    price = q.unit_price.parsed
    max_p = pr.max_unit_price
    threshold = cfg["price_over_max_reject_pct"]
    reject_limit = max_p * (1 + threshold / 100)

    if max_p < price <= reject_limit:
        over_pct = (price - max_p) / max_p * 100
        return _r(rid, NEEDS_PERSON, True,
                  f"Unit price {price:.2f} BDT is {over_pct:.2f}% above max {max_p:.2f} BDT "
                  f"(within {threshold}% reject threshold — needs human sign-off)",
                  q.unit_price.evidence)

    return _r(rid, NEEDS_PERSON, False, f"Price {price:.2f} ≤ max {max_p:.2f}", q.unit_price.evidence)


def check_review_2_quantity(q: ExtractedQuote, pr: PRItem, cfg: dict) -> RuleResult:
    """REVIEW: offered quantity differs from requested by more than 5%."""
    rid = "REVIEW_2"
    if q.quantity is None or q.quantity.is_ambiguous:
        return _r(rid, NEEDS_PERSON, True,
                  "Quantity not found or ambiguous",
                  q.quantity.evidence if q.quantity else "")

    offered = q.quantity.parsed
    requested = pr.quantity
    tolerance = cfg["qty_tolerance_pct"]
    diff_pct = abs(offered - requested) / requested * 100

    if diff_pct > tolerance:
        return _r(rid, NEEDS_PERSON, True,
                  f"Quantity {offered:.0f} {q.unit.parsed if q.unit else ''} offered vs "
                  f"{requested:.0f} requested — differs by {diff_pct:.1f}% (tolerance: {tolerance}%)",
                  q.quantity.evidence)

    return _r(rid, NEEDS_PERSON, False,
              f"Quantity {offered:.0f} within {tolerance}% of {requested:.0f}",
              q.quantity.evidence)


def check_review_3_payment(q: ExtractedQuote, cfg: dict) -> RuleResult:
    """REVIEW: payment terms unclear or outside the allowed 30–60 day window."""
    rid = "REVIEW_3"
    evidence = q.payment_days_max.evidence if q.payment_days_max else ""

    # Explicitly unclear clause (e.g. "as agreed by both parties")
    if not q.payment_terms_clear:
        return _r(rid, NEEDS_PERSON, True,
                  "Payment terms are unclear or contain a vague range ('as agreed')",
                  evidence)

    if q.payment_days_max is None or q.payment_days_max.is_ambiguous:
        return _r(rid, NEEDS_PERSON, True,
                  "Payment terms not found in Payment clause",
                  "")

    d_min_cfg = cfg["payment_days_min"]
    d_max_cfg = cfg["payment_days_max"]
    days = q.payment_days_max.parsed

    if days < d_min_cfg or days > d_max_cfg:
        return _r(rid, NEEDS_PERSON, True,
                  f"Payment {days} days is outside the allowed {d_min_cfg}–{d_max_cfg} day window",
                  q.payment_days_max.evidence)

    # For a range clause (min != max), also check the upper bound
    if q.payment_days_min and q.payment_days_min.parsed != days:
        if q.payment_days_min.parsed < d_min_cfg:
            return _r(rid, NEEDS_PERSON, True,
                      f"Payment range lower bound {q.payment_days_min.parsed} days < {d_min_cfg} minimum",
                      q.payment_days_min.evidence)

    return _r(rid, NEEDS_PERSON, False,
              f"Payment {days} days within {d_min_cfg}–{d_max_cfg} window",
              q.payment_days_max.evidence)


def check_review_4_delivery(q: ExtractedQuote, cfg: dict) -> RuleResult:
    """REVIEW: delivery promised in the 31–45 day amber zone."""
    rid = "REVIEW_4"
    if q.delivery_days is None or q.delivery_days.is_ambiguous:
        return _r(rid, NEEDS_PERSON, False, "Handled elsewhere", "")

    days = q.delivery_days.parsed
    lo = cfg["delivery_review_days_min"]
    hi = cfg["delivery_review_days_max"]

    if lo <= days <= hi:
        return _r(rid, NEEDS_PERSON, True,
                  f"Delivery {days} days is in the amber review zone ({lo}–{hi} days)",
                  q.delivery_days.evidence)

    return _r(rid, NEEDS_PERSON, False,
              f"Delivery {days} days not in amber zone",
              q.delivery_days.evidence)


def check_review_5_vat(q: ExtractedQuote) -> RuleResult:
    """REVIEW: VAT not shown separately at 15%."""
    rid = "REVIEW_5"

    if q.unit_price_vat_inclusive:
        ev = q.unit_price.evidence if q.unit_price else ""
        return _r(rid, NEEDS_PERSON, True,
                  "Unit price is VAT-inclusive; VAT is not shown as a separate line item",
                  ev)

    if q.vat_pct is None:
        return _r(rid, NEEDS_PERSON, True,
                  "No VAT line found — VAT is not shown separately in the quotation",
                  "(no VAT line in document)")

    if abs(q.vat_pct.parsed - 15.0) > 0.01:
        return _r(rid, NEEDS_PERSON, True,
                  f"VAT shown at {q.vat_pct.parsed}% instead of required 15%",
                  q.vat_pct.evidence)

    return _r(rid, NEEDS_PERSON, False, "VAT shown separately at 15%", q.vat_pct.evidence)


def check_review_6_math(q: ExtractedQuote) -> RuleResult:
    """REVIEW: numbers do not add up (line total or subtotal wrong)."""
    rid = "REVIEW_6"
    issues, evidences = [], []

    # Check: line_amount = quantity × unit_price
    if (q.quantity and q.unit_price and q.line_amount
            and not any(f.is_ambiguous for f in [q.quantity, q.unit_price, q.line_amount])):
        expected = round(q.quantity.parsed * q.unit_price.parsed, 2)
        actual   = q.line_amount.parsed
        if abs(expected - actual) > 0.01:
            issues.append(
                f"Line amount {actual:,.2f} ≠ "
                f"{q.quantity.parsed:,.0f} × {q.unit_price.parsed:.2f} = {expected:,.2f}"
            )
            evidences.append(q.line_amount.evidence)

    # Check: subtotal = line_amount (single-item quotation)
    if (q.subtotal and q.line_amount
            and not any(f.is_ambiguous for f in [q.subtotal, q.line_amount])):
        if abs(q.subtotal.parsed - q.line_amount.parsed) > 0.01:
            issues.append(
                f"Subtotal {q.subtotal.parsed:,.2f} ≠ line amount {q.line_amount.parsed:,.2f}"
            )
            evidences.append(q.subtotal.evidence)

    # Check: vat_amount = subtotal × vat_pct / 100
    if (q.subtotal and q.vat_pct and q.vat_amount
            and not any(f.is_ambiguous for f in [q.subtotal, q.vat_pct, q.vat_amount])):
        expected_vat = round(q.subtotal.parsed * q.vat_pct.parsed / 100, 2)
        if abs(expected_vat - q.vat_amount.parsed) > 0.01:
            issues.append(
                f"VAT {q.vat_amount.parsed:,.2f} ≠ "
                f"{q.subtotal.parsed:,.2f} × {q.vat_pct.parsed}% = {expected_vat:,.2f}"
            )
            evidences.append(q.vat_amount.evidence)

    if issues:
        return _r(rid, NEEDS_PERSON, True,
                  "Numbers do not add up: " + "; ".join(issues),
                  " | ".join(e for e in evidences if e))

    return _r(rid, NEEDS_PERSON, False, "All arithmetic checks pass", "")


def check_review_7_validity(q: ExtractedQuote, cfg: dict) -> RuleResult:
    """REVIEW: quotation valid for less than 30 days."""
    rid = "REVIEW_7"
    if q.validity_days is None or q.validity_days.is_ambiguous:
        return _r(rid, NEEDS_PERSON, True,
                  "Validity period not found in Validity clause",
                  q.validity_days.evidence if q.validity_days else "")

    days = q.validity_days.parsed
    minimum = cfg["validity_min_days"]

    if days < minimum:
        return _r(rid, NEEDS_PERSON, True,
                  f"Quotation valid for {days} days (minimum: {minimum} days)",
                  q.validity_days.evidence)

    return _r(rid, NEEDS_PERSON, False,
              f"Quotation valid for {days} days ≥ {minimum} days",
              q.validity_days.evidence)


def check_penalty_rate(q: ExtractedQuote, cfg: dict) -> RuleResult:
    """REVIEW: penalty clause present but percentage below the minimum rate."""
    rid = "REVIEW_PENALTY_RATE"
    if not q.penalty_present:
        return _r(rid, NEEDS_PERSON, False, "No penalty clause — handled by REJECT_3", "")

    if q.penalty_pct is None or q.penalty_pct.is_ambiguous:
        return _r(rid, NEEDS_PERSON, True,
                  "Penalty clause found but percentage not extractable",
                  q.penalty_pct.evidence if q.penalty_pct else "")

    pct     = q.penalty_pct.parsed
    minimum = cfg["penalty_min_pct"]

    if pct < minimum:
        return _r(rid, NEEDS_PERSON, True,
                  f"Penalty {pct}% per week is below the required minimum {minimum}%",
                  q.penalty_pct.evidence)

    return _r(rid, NEEDS_PERSON, False,
              f"Penalty {pct}% ≥ minimum {minimum}%",
              q.penalty_pct.evidence)


# ── Run all rules ─────────────────────────────────────────────────────────────

def apply_all_rules(q: ExtractedQuote, pr: PRItem, cfg: dict) -> list[RuleResult]:
    """Apply every rule and return the full list of RuleResult objects."""
    return [
        # Reject rules
        check_reject_1_price(q, pr, cfg),
        check_reject_2_advance(q, cfg),
        check_reject_3_penalty(q),
        check_reject_4_tin_bin(q),
        check_reject_5_delivery(q, cfg),
        # Review rules
        check_review_1_price(q, pr, cfg),
        check_review_2_quantity(q, pr, cfg),
        check_review_3_payment(q, cfg),
        check_review_4_delivery(q, cfg),
        check_review_5_vat(q),
        check_review_6_math(q),
        check_review_7_validity(q, cfg),
        check_penalty_rate(q, cfg),
    ]


def make_decision(
    rule_results: list[RuleResult],
) -> tuple[str, list[str], list[str]]:
    """
    Derive the final decision from the rule results.
    Priority: REJECT > NEEDS_PERSON > APPROVE.
    Returns (decision, [reasons], [evidence_quotes]).
    """
    rejects = [r for r in rule_results if r.severity == REJECT and r.triggered]
    reviews = [r for r in rule_results if r.severity == NEEDS_PERSON and r.triggered]

    if rejects:
        return REJECT, [r.reason for r in rejects], [r.evidence for r in rejects if r.evidence]

    if reviews:
        return NEEDS_PERSON, [r.reason for r in reviews], [r.evidence for r in reviews if r.evidence]

    # Nothing triggered → Approve; list what passed as the reason
    passing = [r for r in rule_results if not r.triggered and r.reason and "Handled" not in r.reason]
    reasons = ["All checks passed: " + "; ".join(r.reason for r in passing[:5])]
    return APPROVE, reasons, []
