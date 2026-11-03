# Interview Q&A — Supplier Document Checker
**BIAI Round 2 · AI Engineer · Task 1**

---

## Likely questions and answers

---

### 1. "Why did you use LangGraph? You're not using any LLM."

LangGraph is a graph-based workflow library. I used it because the task said "use LangGraph",
and because a StateGraph with fixed edges is the right structure for a fixed pipeline:
it makes the step order visible, lets each node be tested independently, and the
conditional edge to `human_review` documents the only branch in the system.

The alternative — plain Python function calls — would work too. LangGraph adds structure
and makes the pipeline's topology clear at a glance. You could remove it and the
logic would be identical.

---

### 2. "Could you replace the rule checks with an LLM?"

Technically yes, but I would not want to. Rules like "is the price more than 3% above
the maximum?" have a definitive answer. An LLM might get it right most of the time
but it would occasionally hallucinate or interpret "maximum" differently.

The requirement said "decisions are made by deterministic Python code, never by an LLM."
That's a good requirement. Procurement decisions have financial consequences. You want
to be able to audit exactly why something was rejected — and a Python comparison
gives you that; a language model does not.

---

### 3. "What happens if a supplier formats their PDF differently?"

The extractor uses regex patterns on the raw text that pdfplumber extracts.
If a supplier uses a completely different layout — for example, the item table
is in a different position, or the column headers use different words — the
extraction will fail or return None for that field.

That's fine: any missing or ambiguous field triggers "Needs a person to check"
(Rule 1). The system never guesses. If the PDF format changes enough that
extraction stops working, a human reviews it.

In a production system I would add:
- A confidence score per field
- A separate extraction test suite with examples of each supplier's format
- An LLM-assisted fallback for unusual formats (flagged as lower confidence)

---

### 4. "How did you handle the 'penalty clause without the word penalty' case?"

One of the sample PDFs (pack-05/06) describes a late-delivery deduction without
ever using the word "penalty". My regex matches the pattern
`"N% of order value per week"` directly, regardless of surrounding words.

That pattern is the economic substance of a penalty clause. If it's present,
the clause is present, whatever the supplier calls it.

---

### 5. "Pack 09 has a math error in the invoice. How did you catch it?"

The extractor reads the unit price, quantity, and line total separately from
the PDF. The `check_review_6_math` function then recomputes `unit_price × quantity`
and compares it to the stated line total with a small tolerance (0.01).

If they don't match, the document goes to "Needs a person to check" with the
evidence quoting the exact line from the invoice. In the live demo you can see
the reason says "322,000 ≠ 6.40 × 50,000 = 320,000".

---

### 6. "What is grounding verification?"

After extracting each field, the `verify_grounding` node checks that the
evidence string I stored for that field is a verbatim substring of the PDF
text. This is a safeguard against regex bugs — if my pattern accidentally
matched something that isn't in the document, the grounding check would catch it
and escalate to human review.

In practice, during development it caught two cases where my regex was matching
text I had constructed, not text from the PDF.

---

### 7. "Why is the company-rules.md file displayed but not parsed?"

Two reasons.

First, the rules in the Markdown file are written in natural language for human
readers. Parsing them reliably would require an LLM — and I do not want an LLM
making decisions (see Q2).

Second, the thresholds in the rules are already encoded in `rules_config.json`,
which is the single source of truth. The rules file and the config file agree
because I read the rules file and transcribed the numbers by hand. If BIAI
updates the rules, the config file gets updated — not the code.

---

### 8. "What would you do differently with more time?"

1. **Multi-item quotations.** The current extractor handles one item letter per PDF.
   Some quotations might cover multiple items; I would extend the extractor to
   return a list and run the pipeline once per item.

2. **OCR fallback.** Scanned PDFs (image-only, no text layer) return NEEDS_PERSON
   because pdfplumber can't read them. I would add a pytesseract fallback.

3. **Persistent overrides.** Human review decisions are currently client-side only.
   A real system would write them to a database and track who approved what.

4. **CI/CD.** GitHub Actions: lint → test → deploy to Render on green.

---

### 9. "The system gives two different Needs-a-person reasons for pack 09. Is that intentional?"

Yes. The priority rule is: REJECT beats NEEDS_PERSON beats APPROVE. But if
multiple "needs human" rules fire, all their reasons are reported. A human
reviewer needs to know about both the vague payment terms and the math error.

---

### 10. "You said 'built with AI'. How much of this did you actually write?"

Claude (Anthropic's model) wrote essentially all the code. I:
- Specified the architecture and all business rules in detail
- Read and reviewed every file it produced
- Ran the pipeline against the actual PDFs and found the bugs (BOM encoding, emoji encoding)
- Fixed the bugs (in cli.py and pipeline.py)
- Verified the test results

The BOM bug (CSV column matching failure) and the Windows cp1252 encoding bug for
special characters were both found and fixed by me during the run. The code Claude
produced was correct in logic but those two environmental issues only appeared at runtime.

I can explain every line and design decision in the codebase.

---

## Live change scenarios

If BIAI asks you to change something during the interview:

### "Change the price tolerance to 5% instead of 3%"

```bash
# Edit rules_config.json — one line:
# "price_over_max_reject_pct": 5.0
# That's it. No code changes. Restart the server.
```

### "Add a new rule: reject if the validity period is less than 14 days"

1. Change `validity_min_days` in `rules_config.json` from 30 to 14.
   (This changes the REVIEW threshold, not REJECT — explain the difference.)

   OR if they want a hard REJECT rule:
2. Add `check_reject_6_validity(q, cfg)` in `src/rules.py` analogous to `check_reject_5_delivery`.
3. Call it in `apply_all_rules()`.
4. Add a test in `tests/test_rules.py`.

### "What if a supplier submits multiple items in one PDF?"

Walk through the extractor logic. Explain that `extract_from_pdf` currently
returns one `ExtractedQuote`. To support multiple items:
1. Return `list[ExtractedQuote]` from `extract_from_pdf`.
2. In `pipeline.py`, split the state into one sub-pipeline per item and
   aggregate the results.
3. The decision per item is independent; the overall document decision would
   be the most severe of all item decisions.
