# CLAUDE.md — BIAI Round 2 Task 1: Supplier Document Checker

Read this file at the start of every session. Follow it without exception.
If the user asks for something that breaks a rule below, warn them before doing it.

---

## TOP TWO RULES (NEVER BREAK, NEVER WEAKEN)

### RULE 1 — NEVER GUESS
> "If you are not sure, the answer is 'Needs a person to check'. Never guess."
> — company-rules.md

If any value is missing, unreadable, ambiguous, contradictory, or any step
fails or is uncertain, the result MUST be "Needs a person to check".
No guessing, no assuming, no filling gaps, no "probably", no default values.
When in doubt, escalate.

### RULE 2 — EVERY ANSWER HAS A REASON
> "Give a final answer for each document: Approve, Reject or Needs a person
> to check, with the reason." — BIAI task brief

Every document gets exactly one final answer: "Approve", "Reject", or
"Needs a person to check" — and nothing else. It ALWAYS comes with a
written reason that names the rule that applied and quotes the exact
supporting text from the document.

**An answer without a reason is a bug. A reason without a quote is a bug.**

If any other instruction conflicts with these two rules, these two rules win.

---

## HARD ARCHITECTURE RULES

- **Fixed steps, in order, every time** (LangGraph):
  `extract → verify_grounding → compare_with_pr → check_rules → decide → [human_review if unsure]`
- The only allowed branch is the final routing to human_review.
- **Decisions are made by plain deterministic Python code, never by an LLM.**
  An LLM may only READ text (optional), never decide.
- Every extracted value stores the EXACT sentence from the PDF as evidence.
  A step verifies each quote appears word-for-word in the PDF text.
- **NEVER-GUESS enforcement**: if a value is missing, unreadable, or ambiguous,
  mark it ambiguous and route to "Needs a person to check".
- **REASON enforcement**: the decide step must return decision AND a non-empty
  reason list. A final safety check raises an error if any result has no reason
  or an invalid decision.
- One bad file must never crash the batch: catch errors and escalate.
- **Priority**: Reject beats Needs-a-person; Needs-a-person beats Approve.
  A Reject is only allowed when the underlying fact is certain. If the fact
  behind a Reject rule is unclear, use "Needs a person to check".
- **One shared pipeline**: CLI and web app call the same code — they can never
  give different answers.

---

## RULES (from company-rules.md — that file is authoritative)

### REJECT if ANY:
1. Unit price > 3% above max unit price in purchase request
2. Advance payment > 20%
3. No late-delivery penalty clause
4. TIN or BIN missing
5. Delivery > 45 days after purchase order

### NEEDS A PERSON if ANY (and no reject applies):
1. Unit price above max by ≤ 3%
2. Quantity differs from requested by > 5%
3. Payment terms unclear or outside 30–60 days after delivery
4. Delivery 31–45 days
5. VAT not shown separately at 15%
6. Numbers do not add up (line total or subtotal wrong)
7. Quotation valid for < 30 days

### APPROVE if none of the above applies.

**Penalty note**: the late-delivery penalty must be ≥ 0.5% of order value per week.
Always quote the exact text from the document that supports the decision.

---

## THRESHOLDS (canonical source: rules_config.json)

All numeric thresholds live in `rules_config.json` in the project root.
Do NOT scatter threshold numbers through the code. Import from that file.
Current values match company-rules.md exactly:
- price_over_max_reject_pct: 3.0
- advance_max_pct: 20.0
- delivery_reject_days: 45
- delivery_review_days_min: 31
- delivery_review_days_max: 45
- payment_days_min: 30
- payment_days_max: 60
- validity_min_days: 30
- penalty_min_pct: 0.5
- qty_tolerance_pct: 5.0

---

## WEB APPLICATION

The app has THREE upload boxes (drag-and-drop + file picker):
1. Supplier PDFs (one or many; also accept .zip containing PDFs)
2. Purchase request CSV
3. Company rules .md file

Nothing is loaded silently from disk. Results come only from uploaded files.
The Check button is disabled until all needed files are uploaded.
File validation checks real content (PDF starts with %PDF; CSV has required columns).
A bad file never crashes: it is rejected with a clear message or escalated.

The uploaded rules file is **displayed for reference only**. The page must state:
> "Rules are applied from the built-in configuration (rules_config.json);
> the uploaded rules file is shown for reference and is not parsed automatically."

Results page shows: file, supplier, item, decision (colour-coded), reason,
exact quoted evidence. Summary: counts of Approve / Reject / Needs a person.
Download buttons: report.md and results.json.
"Needs a person" rows show Approve/Reject override buttons + name/reason fields.

---

## HONESTY RULES

- The answer key in the analysis table is mine (the AI's), since BIAI gave none.
  It is based on my reading of the rules and the PDF text; it may contain errors.
- The entire project — including the web application — was built with AI (Claude).
  BIAI said "Use any AI tools you like. We want to see how you build with them."
- Say clearly what was not tested or run.
- If anything is unclear, ask one short question instead of guessing.

---

## PROJECT STATUS

| Stage | Status | Notes |
|-------|--------|-------|
| CLAUDE.md created | DONE | 2026-11-03 |
| Step 1 — manual analysis | DONE | See analysis table below |
| STEP 1 questions answered | PENDING | Waiting for user |
| Models / extractor | NOT STARTED | |
| LangGraph pipeline | NOT STARTED | |
| Command-line runner | NOT STARTED | |
| Web app (FastAPI + frontend) | NOT STARTED | |
| Tests | NOT STARTED | |
| Deployment | NOT STARTED | |

---

## STEP 1 ANALYSIS TABLE (manual, no code)

*(This is the AI's own reading. No official answer key exists.)*

| Pack | Supplier | Item | Decision | Primary Rule | Key Evidence (quoted) |
|------|----------|------|----------|-------------|----------------------|
| 01 | Padma Textile Mills Ltd | A | **APPROVE** | All checks pass | Price 472.00 ≤ max 480.00; advance 10%; delivery 24 days; penalty "1% of order value for each full week of delay"; validity 45 days; TIN/BIN present; numbers correct |
| 02 | Jamuna Trims and Accessories | B | **APPROVE** | All checks pass | Price 36.00 ≤ max 38.00; no advance; delivery 21 days; penalty "0.75% of the order value per week of delay"; validity 30 days; TIN/BIN present; numbers correct |
| 03 | Karnaphuli Yarn Traders | C | **REJECT** | Reject rule 1: price > 3% above max | "Unit price (BDT): 229.00"; max is 210.00; overage = 9.05% > 3% |
| 04 | Surma Packaging Industries | D | **NEEDS A PERSON** | Review rule 1: price above max ≤ 3% | "Unit price (BDT): 6.65"; max is 6.50; overage = 2.31% |
| 05 | Meghna Carton Works | E | **REJECT** | Reject rule 2: advance > 20% | "40% advance payable with the purchase order. 40% advance with the purchase order, balance 60% within 30 days of delivery." |
| 06 | Teesta Fabrics Ltd | A | **REJECT** | Reject rule 3: no late-delivery penalty clause | T&C has clauses 1 (Payment), 2 (Delivery), 3 (Validity), 4 (Quality), 5 (Disputes) — no penalty clause at all |
| 07 | Rupsha Zipper Factory | B | **NEEDS A PERSON** | Review rule 2: qty differs > 5% | "Metal zipper, 18 cm: 16,500 piece"; requested 20,000; shortfall = 17.5% |
| 08 | Bhairab Thread and Co | C | **REJECT** | Reject rule 4: TIN and BIN missing | "Supplier registration details available on request." |
| 09 | Dhaleshwari Poly Pack | D | **NEEDS A PERSON** | Review rules 3 + 6 | (1) 6.40 × 50,000 = 320,000 but amount shows "322,000.00" — numbers wrong; (2) "Payment within 30 to 90 days as agreed by both parties" — upper limit 90 > 60 |
| 10 | Kushiyara Box Industries | E | **REJECT** | Reject rule 5: delivery > 45 days | "Delivery within 60 days of PO." — 60 > 45 days |

Summary: 2 Approve, 5 Reject, 3 Needs a person.
