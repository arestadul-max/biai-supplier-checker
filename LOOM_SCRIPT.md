# Loom Script — Supplier Document Checker Demo
**BIAI Round 2 · AI Engineer · Task 1 · ~4 minutes**

---

> **Before recording:** open the web app at http://localhost:8000 in Chrome.
> Have the 10 sample PDFs, purchase-request.csv, and company-rules.md ready.
> Open terminal in D:\BIAI\BIAI_Task1.

---

## [0:00–0:30] Introduction

"Hi BIAI team, I'm [your name], and this is my submission for Round 2, Task 1.

The task was to build a system that reads supplier quotation PDFs and tells a
procurement team whether to Approve them, Reject them, or escalate to a human.

I built this with AI — specifically, Claude wrote the code and I reviewed,
tested, and debugged it. BIAI said that was fine, and I think it's the honest
way to approach an AI engineer role.

Let me show you how it works."

---

## [0:30–1:30] Show the web app

*[Switch to browser at http://localhost:8000]*

"This is the web interface. You can see three upload areas:
- Supplier quotation PDFs — you can drop multiple files or a whole ZIP
- The purchase request CSV from BIAI
- The company rules Markdown file

The system never loads files silently. You always provide all three."

*[Click the three upload areas and select the sample files]*

"I'll upload all 10 sample packs, the purchase request, and the company rules."

*[Click 'Check Documents']*

"While it runs — this is a fixed pipeline, not a free-running AI agent.
It goes: extract fields from the PDF, check every extracted value is
verbatim in the document, compare against the purchase request, apply
the rules, make a decision. The whole thing runs in under a second per file."

---

## [1:30–2:30] Walk through the results

*[Results appear on screen]*

"Here are the results. Let me walk through a few interesting cases.

Pack 01 — Approve. Price is within range, advance is under 20%, penalty clause
is present, TIN and BIN are both there.

Pack 03 — Reject. The unit price is 229 BDT, which is 9% above the maximum.
The evidence is this exact line from the PDF: [quote]. The rule says over 3%
is an automatic reject.

Pack 04 — Needs a person to check. The price is 2.31% above maximum — that's
above the maximum but below the 3% automatic reject threshold. So the system
flags it for a human rather than rejecting it itself.

Pack 09 — Needs a person to check, for two reasons. First, the payment terms
say 'as agreed by both parties', which is too vague. Second, the line total in
the invoice is 322,000 BDT but six-forty times fifty thousand is 320,000 —
there's a math error. The system caught both."

---

## [2:30–3:15] Show a human override

*[Click 'Override' on a NEEDS_PERSON result]*

"For cases that need a person, there's a panel where a reviewer can enter
their name, their decision — Approve or Reject — and their reason. That gets
recorded in the downloaded report."

*[Fill in the override and click Apply]*

"The system updates the summary count. You can then download a full Markdown
report with every decision and its evidence."

---

## [3:15–3:45] Show the CLI

*[Switch to terminal]*

"There's also a command-line interface for bulk processing."

```
python cli.py --pdfs *.pdf --pr purchase-request.csv --rules company-rules.md
```

"Same pipeline, same answers. It writes a Markdown report and a JSON file to
the outputs folder. The JSON is useful if you want to feed these results into
another system."

---

## [3:45–4:00] Closing

"A few things I'd highlight:

The system never guesses. If a value is missing, ambiguous, or a math error
is found, it always escalates to a human rather than making up an answer.

Every decision quotes exact text from the document. You can always verify
the system's reasoning against the original file.

And the whole pipeline is fixed steps — no LLM making decisions, no
randomness, no hallucination risk.

Thank you — I'm happy to walk through any part of the code in the interview."

---

> **Recording tips:**
> - Record at 1080p, browser + terminal side-by-side if possible
> - Keep the upload step in real-time (don't cut it)
> - Aim for 4 minutes; 5 is fine if you linger on an interesting case
