# NOTE — Supplier Document Checker
**BIAI Round 2 · AI Engineer · Task 1**

---

## What I built

A deterministic supplier-quotation checker that reads PDFs, extracts fields,
and returns **Approve / Reject / Needs a person to check** — always with a
verbatim quote from the document as evidence.

The system runs a fixed LangGraph pipeline (not a free-running agent):

```
extract → verify_grounding → compare_with_pr → check_rules → decide
                                                                  ↓ (if unsure)
                                                           human_review
```

All thresholds are in `rules_config.json`. All decisions are deterministic
Python — no LLM, no guessing.

Two inviolable rules:
1. **Never guess.** If any value is missing, ambiguous, or unreadable → Needs a person to check.
2. **Every answer has a reason.** The reason always quotes exact text from the document.

There are two interfaces:
- **CLI** (`cli.py`) — bulk-process PDFs, write `outputs/report.md` + `outputs/results.json`
- **Web app** (`web/app.py`) — drag-and-drop UI, human-override panel, download report
  → **Live demo:** https://biai-supplier-checker.onrender.com

---

## Testing results

I ran the system against all 10 sample supplier packs.

| Pack | Supplier | Item | Decision | Reason |
|------|----------|------|----------|--------|
| 01 | Padma Textile Mills | A | **Approve** | All checks passed |
| 02 | Jamuna Trims | B | **Approve** | All checks passed |
| 03 | Karnaphuli Yarn | C | **Reject** | Price 229 BDT is 9.05% above max (threshold: 3%) |
| 04 | Surma Packaging | D | **Needs a person** | Price 6.65 is 2.31% above max — within 3% but above max |
| 05 | Meghna Carton Works | E | **Reject** | Advance 40% exceeds 20% limit |
| 06 | Teesta Fabrics | A | **Reject** | No late-delivery penalty clause in document |
| 07 | Rupsha Zipper | B | **Needs a person** | Quantity 16,500 vs 20,000 requested — 17.5% short |
| 08 | Bhairab Thread | C | **Reject** | No penalty clause; TIN/BIN "available on request" only |
| 09 | Dhaleshwari Poly Pack | D | **Needs a person** | Payment "as agreed" is vague; line total 322,000 ≠ 6.40×50,000=320,000 |
| 10 | Kushiyara Box Ind. | E | **Reject** | Delivery 60 days exceeds 45-day limit |

All 75 automated tests pass (10 integration + 65 unit boundary tests).

**HONESTY:** BIAI provided no official answer key. These are my own readings
of the rules. The system's answer may differ from BIAI's intended answer.

---

## What works well

- **Zero guessing:** any ambiguous or missing value immediately routes to NEEDS_PERSON.
- **Verbatim evidence:** every decision quotes exact text from the PDF.
- **Single config file:** changing a threshold takes one number edit in `rules_config.json`.
- **Tricky cases handled:** penalty clause without "penalty" keyword; delivery in weeks;
  vague "as agreed" payment range; "registration details available on request" TIN/BIN;
  math errors in line totals; VAT-inclusive unit pricing.
- **Web app** works identically to CLI — same pipeline, same answers.

---

## What does not work well / limitations

- **Only extracts item A–E rows:** the extractor looks for a single `[A-E]` row in the
  invoice table. Multi-item quotations covering more than one letter are not handled;
  a document with two rows (e.g. both A and C) would only process one.
- **Supplier name extraction is fragile:** relies on a regex for "Supplier:" or company
  name patterns in the PDF. If the PDF uses a logo or unusual formatting, the name may
  come back as "Unknown" (this escalates to NEEDS_PERSON).
- **PDF text extraction:** uses `pdfplumber`. Scanned PDFs (image-only, no text layer)
  cannot be read — they will return NEEDS_PERSON, which is correct but not very helpful.
- **Demo button in web app** requires the sample files to be in a specific subfolder of
  the project; it doesn't work with files in other locations (this is by design for the
  hosted demo — the user uploads manually).
- **Web app state is client-side:** human overrides are stored in browser memory only.
  Refreshing the page loses all work. A production system would persist to a database.
- **No authentication:** the web app has no login. Anyone who can reach the URL can run
  the checker. Acceptable for a demo; not for production.

---

## What I would do next

1. Add OCR fallback (e.g. `pytesseract`) for scanned PDFs.
2. Support multi-item quotations.
3. Persist human overrides to a database (SQLite for local, PostgreSQL for production).
4. Add a login wall (even basic HTTP auth) before deploying publicly.
5. Write a proper CI pipeline (GitHub Actions: lint, pytest, deploy to Render on green).
