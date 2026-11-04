"""
Parse a natural-language rules .md file to extract numeric thresholds.

Uses regex to find numbers in known rule phrasings. Falls back to
rules_config.json defaults for any threshold not found in the uploaded text.
Returns a config dict with the same keys as rules_config.json.

No LLM involved — all extraction is deterministic regex.
"""

import re
from typing import Optional


def _pct(text: str, *patterns: str) -> Optional[float]:
    """Find the first % value matching any pattern. Group 1 must be the number."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _days(text: str, *patterns: str) -> Optional[int]:
    """Find the first day count matching any pattern. Group 1 must be the number."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def parse_rules_config(rules_text: str, default_config: dict) -> tuple[dict, list[str]]:
    """
    Parse a rules markdown file and extract numeric thresholds.

    Parameters
    ----------
    rules_text      : full text of the uploaded rules .md
    default_config  : fallback values (from rules_config.json)

    Returns
    -------
    (config, applied_sources)
        config          -- dict with same keys as rules_config.json
        applied_sources -- list of human-readable lines noting which
                           thresholds came from the uploaded file vs defaults
    """
    cfg = dict(default_config)
    src: list[str] = []

    # ── 1. price_over_max_reject_pct ──────────────────────────────────────────
    # "unit price is more than X% above the maximum"
    # "no more than X% above the max" (from review rule — same threshold)
    val = _pct(
        rules_text,
        r"(?:unit\s+)?price[^.\n]*?more\s+than\s+([\d.]+)\s*%\s*above",
        r"above[^.\n]*?max(?:imum)?[^.\n]*?more\s+than\s+([\d.]+)\s*%",
        r"no\s+more\s+than\s+([\d.]+)\s*%\s*above\s+the\s+max",
        r"exceeds[^.\n]*?max(?:imum)?[^.\n]*?by\s+(?:more\s+than\s+)?([\d.]+)\s*%",
    )
    if val is not None:
        cfg["price_over_max_reject_pct"] = val
        src.append(f"Price margin: {val}% (from uploaded rules)")
    else:
        src.append(f"Price margin: {cfg['price_over_max_reject_pct']}% (default)")

    # ── 2. advance_max_pct ────────────────────────────────────────────────────
    # "advance payment is more than X%"
    val = _pct(
        rules_text,
        r"advance\s+payment\s+is\s+more\s+than\s+([\d.]+)\s*%",
        r"advance\s+(?:payment\s+)?(?:exceeds|over|more\s+than)\s+([\d.]+)\s*%",
        r"more\s+than\s+([\d.]+)\s*%\s*advance",
    )
    if val is not None:
        cfg["advance_max_pct"] = val
        src.append(f"Max advance: {val}% (from uploaded rules)")
    else:
        src.append(f"Max advance: {cfg['advance_max_pct']}% (default)")

    # ── 3. delivery_reject_days ───────────────────────────────────────────────
    # "later than X days after the purchase order"
    val = _days(
        rules_text,
        r"later\s+than\s+(\d+)\s+days",
        r"more\s+than\s+(\d+)\s+days\s+(?:after|from)\s+(?:the\s+)?(?:PO|purchase)",
    )
    if val is not None:
        cfg["delivery_reject_days"] = val
        src.append(f"Delivery reject: >{val} days (from uploaded rules)")
    else:
        src.append(f"Delivery reject: >{cfg['delivery_reject_days']} days (default)")

    # ── 4. delivery_review_days_min + _max ────────────────────────────────────
    # "Delivery is promised in X to Y days"
    m = re.search(
        r"[Dd]elivery[^.\n]*?in\s+(\d+)\s+to\s+(\d+)\s+days", rules_text
    )
    if not m:
        m = re.search(
            r"promised\s+in\s+(\d+)\s+to\s+(\d+)\s+days", rules_text, re.IGNORECASE
        )
    if m:
        cfg["delivery_review_days_min"] = int(m.group(1))
        cfg["delivery_review_days_max"] = int(m.group(2))
        src.append(
            f"Delivery review zone: {m.group(1)}–{m.group(2)} days (from uploaded rules)"
        )
    else:
        src.append(
            f"Delivery review zone: {cfg['delivery_review_days_min']}–"
            f"{cfg['delivery_review_days_max']} days (default)"
        )

    # ── 5. payment_days_min + payment_days_max ────────────────────────────────
    # "outside X to Y days after delivery"
    m = re.search(r"outside\s+(\d+)\s+to\s+(\d+)\s+days", rules_text, re.IGNORECASE)
    if not m:
        m = re.search(
            r"(\d+)\s+to\s+(\d+)\s+days\s+after\s+delivery", rules_text, re.IGNORECASE
        )
    if m:
        cfg["payment_days_min"] = int(m.group(1))
        cfg["payment_days_max"] = int(m.group(2))
        src.append(f"Payment window: {m.group(1)}–{m.group(2)} days (from uploaded rules)")
    else:
        src.append(
            f"Payment window: {cfg['payment_days_min']}–{cfg['payment_days_max']} days (default)"
        )

    # ── 6. validity_min_days ──────────────────────────────────────────────────
    # "valid for less than X days"
    val = _days(
        rules_text,
        r"valid(?:ity)?[^.\n]*?less\s+than\s+(\d+)\s+days",
        r"quotation[^.\n]*?valid[^.\n]*?less\s+than\s+(\d+)",
    )
    if val is not None:
        cfg["validity_min_days"] = val
        src.append(f"Min validity: {val} days (from uploaded rules)")
    else:
        src.append(f"Min validity: {cfg['validity_min_days']} days (default)")

    # ── 7. penalty_min_pct ────────────────────────────────────────────────────
    # "at least 0.5% of order value per week of delay"
    val = _pct(
        rules_text,
        r"at\s+least\s+([\d.]+)\s*%\s+of\s+order\s+value\s+per\s+week",
        r"penalty[^.\n]*?at\s+least\s+([\d.]+)\s*%",
        r"minimum[^.\n]*?([\d.]+)\s*%\s+of\s+order\s+value\s+per\s+week",
    )
    if val is not None:
        cfg["penalty_min_pct"] = val
        src.append(f"Min penalty: {val}%/week (from uploaded rules)")
    else:
        src.append(f"Min penalty: {cfg['penalty_min_pct']}%/week (default)")

    # ── 8. qty_tolerance_pct ──────────────────────────────────────────────────
    # "quantity differs from the quantity requested by more than X%"
    val = _pct(
        rules_text,
        r"[Qq]uantity[^.\n]*?(?:differs|difference)[^.\n]*?more\s+than\s+([\d.]+)\s*%",
        r"[Qq]uantity[^.\n]*?by\s+more\s+than\s+([\d.]+)\s*%",
    )
    if val is not None:
        cfg["qty_tolerance_pct"] = val
        src.append(f"Qty tolerance: {val}% (from uploaded rules)")
    else:
        src.append(f"Qty tolerance: {cfg['qty_tolerance_pct']}% (default)")

    return cfg, src
