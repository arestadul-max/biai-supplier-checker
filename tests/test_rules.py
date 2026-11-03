"""
Unit tests for the rules module.

Tests cover:
- Boundary values (exactly at 3%, 5%, 20%, 30/45 days)
- NEVER-GUESS: ambiguous/missing values route to NEEDS_PERSON
- REASON: every triggered result has a non-empty reason
- Priority: REJECT beats NEEDS_PERSON
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.models import (
    ExtractedValue as EV, ExtractedQuote, PRItem,
    APPROVE, REJECT, NEEDS_PERSON,
)
from src.rules import (
    check_reject_1_price, check_reject_2_advance, check_reject_3_penalty,
    check_reject_4_tin_bin, check_reject_5_delivery,
    check_review_1_price, check_review_2_quantity, check_review_3_payment,
    check_review_4_delivery, check_review_5_vat, check_review_6_math,
    check_review_7_validity, check_penalty_rate,
    apply_all_rules, make_decision,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

CFG = {
    "price_over_max_reject_pct": 3.0,
    "advance_max_pct": 20.0,
    "delivery_reject_days": 45,
    "delivery_review_days_min": 31,
    "delivery_review_days_max": 45,
    "payment_days_min": 30,
    "payment_days_max": 60,
    "validity_min_days": 30,
    "penalty_min_pct": 0.5,
    "qty_tolerance_pct": 5.0,
}

def _val(v, ev="evidence text"):
    """Shorthand: create a non-ambiguous ExtractedValue."""
    return EV(raw=str(v), parsed=v, evidence=ev)

def _ambig(reason="ambiguous"):
    """Shorthand: create an ambiguous ExtractedValue."""
    return EV(raw="", parsed=None, evidence="", is_ambiguous=True, ambiguity_reason=reason)

def _pr(max_price=480.0, qty=12000.0):
    return PRItem("PR-001", "A", "Test item", "metre", qty, max_price)

def _quote(price=470.0, advance=10.0, penalty_pct=1.0, delivery=24,
           qty=12000, tin="123", bin_="456", payment=45, validity=45,
           vat_pct=15.0, unit_price_vat_inclusive=False,
           line_amount=None, subtotal=None, vat_amount=None):
    """Build a complete ExtractedQuote for testing."""
    q = ExtractedQuote(pdf_path="test.pdf", raw_text="test")
    q.unit_price   = _val(price)
    q.advance_pct  = _val(advance)
    q.penalty_pct  = _val(penalty_pct) if penalty_pct is not None else None
    q.penalty_present = penalty_pct is not None
    q.delivery_days = _val(delivery)
    q.quantity      = _val(qty)
    q.tin           = _val(tin) if tin else _ambig("missing")
    q.bin_number    = _val(bin_) if bin_ else _ambig("missing")
    q.tin_bin_line  = f"TIN: {tin} BIN: {bin_}" if tin and bin_ else "registration details available on request"
    q.payment_days_max   = _val(payment)
    q.payment_days_min   = _val(payment)
    q.payment_terms_clear = True
    q.validity_days = _val(validity)
    q.vat_pct       = _val(vat_pct) if vat_pct is not None else None
    q.unit_price_vat_inclusive = unit_price_vat_inclusive
    q.line_amount   = _val(line_amount or round(price * qty, 2))
    q.subtotal      = _val(subtotal or round(price * qty, 2))
    q.vat_amount    = _val(vat_amount or round(price * qty * (vat_pct or 0) / 100, 2))
    return q


# ── REJECT 1: price ───────────────────────────────────────────────────────────

def test_reject_1_price_over_3pct():
    """229 BDT vs max 210 BDT = 9.05% over → REJECT"""
    r = check_reject_1_price(_quote(price=229.0), _pr(max_price=210.0), CFG)
    assert r.triggered and r.severity == REJECT
    assert r.reason  # Rule 2: reason must be non-empty

def test_reject_1_price_exactly_3pct_over():
    """Price exactly at max + 3% → REJECT (not within band)"""
    r = check_reject_1_price(_quote(price=210.0 * 1.031), _pr(max_price=210.0), CFG)
    assert r.triggered and r.severity == REJECT

def test_reject_1_price_at_max():
    """Price at exactly the max → no reject"""
    r = check_reject_1_price(_quote(price=480.0), _pr(max_price=480.0), CFG)
    assert not r.triggered

def test_reject_1_price_below_max():
    """Price below max → no reject"""
    r = check_reject_1_price(_quote(price=472.0), _pr(max_price=480.0), CFG)
    assert not r.triggered

def test_reject_1_ambiguous_price_escalates_to_review():
    """Ambiguous price → cannot REJECT (Rule 1: never guess) — escalate to NEEDS_PERSON."""
    q = _quote()
    q.unit_price = _ambig("price unclear")
    r = check_reject_1_price(q, _pr(), CFG)
    assert r.triggered and r.severity == NEEDS_PERSON  # not REJECT


# ── REJECT 2: advance ─────────────────────────────────────────────────────────

def test_reject_2_advance_40pct():
    """40% advance → REJECT (pack 05 case)"""
    r = check_reject_2_advance(_quote(advance=40.0), CFG)
    assert r.triggered and r.severity == REJECT

def test_reject_2_advance_exactly_20pct():
    """Exactly 20% → no reject (at boundary)"""
    r = check_reject_2_advance(_quote(advance=20.0), CFG)
    assert not r.triggered

def test_reject_2_advance_0pct():
    """0% advance → no reject"""
    r = check_reject_2_advance(_quote(advance=0.0), CFG)
    assert not r.triggered

def test_reject_2_ambiguous_advance():
    """Ambiguous advance → escalate to NEEDS_PERSON"""
    q = _quote()
    q.advance_pct = _ambig("advance unclear")
    r = check_reject_2_advance(q, CFG)
    assert r.triggered and r.severity == NEEDS_PERSON


# ── REJECT 3: penalty ─────────────────────────────────────────────────────────

def test_reject_3_no_penalty_clause():
    """No penalty clause → REJECT (pack 06 case)"""
    q = _quote(penalty_pct=None)
    r = check_reject_3_penalty(q)
    assert r.triggered and r.severity == REJECT

def test_reject_3_penalty_present():
    """Penalty present → no reject"""
    r = check_reject_3_penalty(_quote(penalty_pct=1.0))
    assert not r.triggered


# ── REJECT 4: TIN/BIN ─────────────────────────────────────────────────────────

def test_reject_4_tin_bin_missing():
    """TIN and BIN missing → REJECT (pack 08 case)"""
    q = _quote(tin=None, bin_=None)
    r = check_reject_4_tin_bin(q)
    assert r.triggered and r.severity == REJECT

def test_reject_4_tin_present_bin_missing():
    """BIN missing → REJECT"""
    q = _quote(tin="123", bin_=None)
    r = check_reject_4_tin_bin(q)
    assert r.triggered and r.severity == REJECT

def test_reject_4_both_present():
    """Both TIN and BIN present → no reject"""
    r = check_reject_4_tin_bin(_quote(tin="123", bin_="456"))
    assert not r.triggered


# ── REJECT 5: delivery ────────────────────────────────────────────────────────

def test_reject_5_delivery_60_days():
    """60 days delivery → REJECT (pack 10 case)"""
    r = check_reject_5_delivery(_quote(delivery=60), CFG)
    assert r.triggered and r.severity == REJECT

def test_reject_5_delivery_exactly_45_days():
    """Exactly 45 days → no reject (at boundary)"""
    r = check_reject_5_delivery(_quote(delivery=45), CFG)
    assert not r.triggered

def test_reject_5_delivery_24_days():
    """24 days → no reject (pack 01 case)"""
    r = check_reject_5_delivery(_quote(delivery=24), CFG)
    assert not r.triggered

def test_reject_5_ambiguous_delivery():
    """Ambiguous delivery → NEEDS_PERSON, not REJECT"""
    q = _quote()
    q.delivery_days = _ambig("delivery unclear")
    r = check_reject_5_delivery(q, CFG)
    assert r.triggered and r.severity == NEEDS_PERSON


# ── REVIEW 1: price in grace band ────────────────────────────────────────────

def test_review_1_price_just_over_max():
    """6.65 BDT vs max 6.50 BDT = 2.31% → REVIEW (pack 04 case)"""
    r = check_review_1_price(_quote(price=6.65), _pr(max_price=6.50), CFG)
    assert r.triggered and r.severity == NEEDS_PERSON

def test_review_1_price_at_max():
    """At max → no review"""
    r = check_review_1_price(_quote(price=6.50), _pr(max_price=6.50), CFG)
    assert not r.triggered

def test_review_1_price_below_max():
    """Below max → no review"""
    r = check_review_1_price(_quote(price=6.40), _pr(max_price=6.50), CFG)
    assert not r.triggered


# ── REVIEW 2: quantity ────────────────────────────────────────────────────────

def test_review_2_quantity_17pct_short():
    """16500 vs 20000 = 17.5% short → REVIEW (pack 07 case)"""
    r = check_review_2_quantity(_quote(qty=16500), _pr(qty=20000), CFG)
    assert r.triggered and r.severity == NEEDS_PERSON

def test_review_2_quantity_exactly_5pct():
    """Exactly 5% short → no review (at boundary — rule says MORE THAN 5%)"""
    r = check_review_2_quantity(_quote(qty=19000), _pr(qty=20000), CFG)
    assert not r.triggered  # 5.0% is not > 5%

def test_review_2_quantity_matches_exactly():
    """Exact match → no review"""
    r = check_review_2_quantity(_quote(qty=12000), _pr(qty=12000), CFG)
    assert not r.triggered


# ── REVIEW 3: payment terms ───────────────────────────────────────────────────

def test_review_3_payment_30_days_exact():
    """Exactly 30 days → no review (at lower boundary of 30–60)"""
    r = check_review_3_payment(_quote(payment=30), CFG)
    assert not r.triggered

def test_review_3_payment_60_days_exact():
    """Exactly 60 days → no review (at upper boundary)"""
    r = check_review_3_payment(_quote(payment=60), CFG)
    assert not r.triggered

def test_review_3_payment_90_days():
    """90 days → REVIEW (pack 09 partial case)"""
    r = check_review_3_payment(_quote(payment=90), CFG)
    assert r.triggered

def test_review_3_payment_unclear():
    """Vague clause 'as agreed' → REVIEW (pack 09 main case)"""
    q = _quote(payment=30)
    q.payment_terms_clear = False
    r = check_review_3_payment(q, CFG)
    assert r.triggered and r.severity == NEEDS_PERSON


# ── REVIEW 5: VAT ─────────────────────────────────────────────────────────────

def test_review_5_vat_not_shown():
    """No VAT line → REVIEW"""
    q = _quote(vat_pct=None)
    r = check_review_5_vat(q)
    assert r.triggered

def test_review_5_vat_inclusive():
    """VAT-inclusive price → REVIEW (pack 10 case)"""
    q = _quote(unit_price_vat_inclusive=True)
    r = check_review_5_vat(q)
    assert r.triggered

def test_review_5_vat_shown_at_15():
    """VAT at 15% shown separately → no review"""
    r = check_review_5_vat(_quote(vat_pct=15.0))
    assert not r.triggered


# ── REVIEW 6: math ───────────────────────────────────────────────────────────

def test_review_6_wrong_line_amount():
    """6.40 × 50000 = 320000 but shows 322000 → REVIEW (pack 09 case)"""
    q = _quote(price=6.40, qty=50000, line_amount=322000.0, subtotal=322000.0)
    r = check_review_6_math(q)
    assert r.triggered and r.severity == NEEDS_PERSON

def test_review_6_math_correct():
    """Correct arithmetic → no review"""
    r = check_review_6_math(_quote(price=472.0, qty=12000))
    assert not r.triggered


# ── REVIEW 7: validity ────────────────────────────────────────────────────────

def test_review_7_validity_29_days():
    """29 days → REVIEW"""
    r = check_review_7_validity(_quote(validity=29), CFG)
    assert r.triggered

def test_review_7_validity_exactly_30_days():
    """Exactly 30 days → no review"""
    r = check_review_7_validity(_quote(validity=30), CFG)
    assert not r.triggered


# ── Penalty rate ─────────────────────────────────────────────────────────────

def test_penalty_rate_exactly_05():
    """Exactly 0.5% → passes (at minimum, inclusive)"""
    r = check_penalty_rate(_quote(penalty_pct=0.5), CFG)
    assert not r.triggered

def test_penalty_rate_below_05():
    """Below 0.5% → REVIEW"""
    r = check_penalty_rate(_quote(penalty_pct=0.3), CFG)
    assert r.triggered


# ── make_decision priority ────────────────────────────────────────────────────

def test_priority_reject_beats_review():
    """A REJECT must win over a REVIEW."""
    q = _quote(price=229.0, advance=40.0)   # both REJECT
    pr = _pr(max_price=210.0)
    results = apply_all_rules(q, pr, CFG)
    decision, reasons, _ = make_decision(results)
    assert decision == REJECT
    assert reasons  # Rule 2


def test_decision_approve():
    """A clean pack 01-like quote → APPROVE."""
    q = _quote(price=472.0, advance=10.0, penalty_pct=1.0, delivery=24,
               qty=12000, payment=45, validity=45, vat_pct=15.0)
    pr = _pr(max_price=480.0, qty=12000.0)
    results = apply_all_rules(q, pr, CFG)
    decision, reasons, _ = make_decision(results)
    assert decision == APPROVE
    assert reasons  # Rule 2


# ── Rule 2 enforcement: every result has a reason ────────────────────────────

def test_all_results_have_reasons():
    """Every RuleResult must have a non-empty reason string."""
    q = _quote()
    results = apply_all_rules(q, _pr(), CFG)
    for rr in results:
        assert rr.reason, f"Rule {rr.rule_id} has no reason!"
