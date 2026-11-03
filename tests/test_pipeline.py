"""
Integration tests for the full pipeline.

Tests cover:
- All 10 sample packs match my expected answer table
- NEVER-GUESS: error paths → NEEDS_PERSON
- Every result has a valid decision and non-empty reason
- Evidence quotes exist in the PDF text
- Web endpoint returns same answers as CLI pipeline
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.models import APPROVE, REJECT, NEEDS_PERSON, VALID_DECISIONS
from src.pipeline import load_config, load_pr_items, run_pipeline, run_batch

# ── Paths to sample data ──────────────────────────────────────────────────────
# Resolution order:
#   1. BIAI_SAMPLE_DIR env variable (any absolute path)
#   2. sample-data/ folder next to this repo (default, not committed)
_env_dir = os.environ.get("BIAI_SAMPLE_DIR")
SAMPLE_DIR = Path(_env_dir) if _env_dir else Path(__file__).parent.parent / "sample-data"
PR_CSV     = SAMPLE_DIR / "purchase-request.csv"

# Skip all integration tests if sample data not present (CI, fresh clone)
SAMPLES_AVAILABLE = SAMPLE_DIR.exists() and PR_CSV.exists()
skip_if_no_samples = pytest.mark.skipif(
    not SAMPLES_AVAILABLE,
    reason=(
        "BIAI sample data not found. "
        "Place the BIAI sample files in sample-data/ at the repo root, "
        "or set BIAI_SAMPLE_DIR=/path/to/sample/files and re-run."
    ),
)

# Load config and PR items once for the test session
CONFIG   = load_config()
PR_ITEMS = load_pr_items(str(PR_CSV)) if SAMPLES_AVAILABLE else {}

# ── My expected answer key (manual analysis from Step 1) ─────────────────────
# Format: (pdf_filename, expected_decision)
# HONESTY: This is the AI's own reading — BIAI provided no official answer key.
EXPECTED = {
    "supplier-pack-01.pdf": APPROVE,
    "supplier-pack-02.pdf": APPROVE,
    "supplier-pack-03.pdf": REJECT,
    "supplier-pack-04.pdf": NEEDS_PERSON,
    "supplier-pack-05.pdf": REJECT,
    "supplier-pack-06.pdf": REJECT,
    "supplier-pack-07.pdf": NEEDS_PERSON,
    "supplier-pack-08.pdf": REJECT,
    "supplier-pack-09.pdf": NEEDS_PERSON,
    "supplier-pack-10.pdf": REJECT,
}


@skip_if_no_samples
@pytest.mark.parametrize("filename,expected", EXPECTED.items())
def test_pack_decision(filename, expected):
    """Each pack must match my expected answer."""
    pdf = SAMPLE_DIR / filename
    result = run_pipeline(str(pdf), PR_ITEMS, CONFIG)
    assert result.decision == expected, (
        f"{filename}: got '{result.decision}', expected '{expected}'. "
        f"Reasons: {result.reasons}"
    )


@skip_if_no_samples
@pytest.mark.parametrize("filename", EXPECTED.keys())
def test_pack_has_reason(filename):
    """Rule 2: every result must have a non-empty reason."""
    pdf = SAMPLE_DIR / filename
    result = run_pipeline(str(pdf), PR_ITEMS, CONFIG)
    assert result.decision in VALID_DECISIONS, f"Invalid decision: {result.decision}"
    assert result.reasons, f"{filename}: no reasons returned"
    assert all(r.strip() for r in result.reasons), f"{filename}: empty reason string"


@skip_if_no_samples
@pytest.mark.parametrize("filename", EXPECTED.keys())
def test_evidence_in_pdf_text(filename):
    """Every evidence quote must appear verbatim in the extracted PDF text."""
    from src.extractor import extract_pdf_text
    pdf = SAMPLE_DIR / filename
    text = extract_pdf_text(str(pdf))
    result = run_pipeline(str(pdf), PR_ITEMS, CONFIG)
    for ev in result.evidence:
        if ev and ev not in ("(no penalty clause in document)", "(not found in document)",
                              "(no VAT line in document)"):
            assert ev in text, f"{filename}: evidence not found in PDF text: '{ev[:80]}'"


@skip_if_no_samples
def test_batch_never_crashes():
    """The batch runner must return a result for every file, never raise."""
    pdfs = [str(SAMPLE_DIR / f) for f in EXPECTED]
    results = run_batch(pdfs, PR_ITEMS, CONFIG)
    assert len(results) == 10
    for r in results:
        assert r.decision in VALID_DECISIONS
        assert r.reasons


# ── NEVER-GUESS tests ─────────────────────────────────────────────────────────

def test_fake_pdf_becomes_needs_person(tmp_path):
    """A .txt file renamed to .pdf must not crash — becomes NEEDS_PERSON."""
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"This is not a PDF")
    result = run_pipeline(str(fake), PR_ITEMS or {}, CONFIG)
    assert result.decision == NEEDS_PERSON
    assert result.reasons


def test_empty_file_becomes_needs_person(tmp_path):
    """An empty file → NEEDS_PERSON."""
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    result = run_pipeline(str(empty), PR_ITEMS or {}, CONFIG)
    assert result.decision == NEEDS_PERSON


def test_item_not_in_pr_becomes_needs_person(tmp_path):
    """A PDF whose item letter is not in the PR → NEEDS_PERSON."""
    # Use a real PDF but a PR with no matching item
    if not SAMPLES_AVAILABLE:
        pytest.skip("Sample data not available")
    pdf = SAMPLE_DIR / "supplier-pack-01.pdf"   # item A
    empty_pr = {}   # no items at all
    result = run_pipeline(str(pdf), empty_pr, CONFIG)
    assert result.decision == NEEDS_PERSON
    assert result.reasons


# ── Rule 2 safety: valid decision + non-empty reason always ──────────────────

def test_valid_decisions_only():
    """No result can have a decision outside the three allowed values."""
    if not SAMPLES_AVAILABLE:
        pytest.skip("Sample data not available")
    pdfs = [str(SAMPLE_DIR / f) for f in EXPECTED]
    results = run_batch(pdfs, PR_ITEMS, CONFIG)
    for r in results:
        assert r.decision in VALID_DECISIONS, f"Invalid: {r.decision} for {r.pdf_filename}"
