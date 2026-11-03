# Supplier Document Checker — BIAI Round 2, Task 1

> Built with AI (Claude · Anthropic). The code was written by Claude; reviewed, verified and explained by the candidate. BIAI said "Use any AI tools you like. We want to see how you build with them."

---

## What it does

A garment company receives supplier quotations as PDFs. This system reads each PDF, extracts key fields, compares them against the purchase request, applies company rules, and returns one of three decisions: **Approve**, **Reject**, or **Needs a person to check** — always with a quoted reason.

The system uses a **fixed LangGraph pipeline** (never a free-running agent):

```
extract → verify_grounding → compare_with_pr → check_rules → decide → [human_review]
```

All decisions are made by deterministic Python code. No LLM guesses.

---

## Run in under 5 minutes

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Command line

```bash
python cli.py \
  --pdfs  "C:/path/to/supplier-pack-01.pdf" ... \
  --pr    "C:/path/to/purchase-request.csv" \
  --rules "C:/path/to/company-rules.md"
```

Outputs: `outputs/report.md` and `outputs/results.json`.

Add `--interactive` to approve or reject uncertain cases from the terminal.

**Run all 10 BIAI sample files at once** (place them in `sample-data/` first — see below):

```bash
python cli.py \
  --pdfs  sample-data/supplier-pack-*.pdf \
  --pr    sample-data/purchase-request.csv \
  --rules sample-data/company-rules.md
```

### 3. Web app (local)

```bash
uvicorn web.app:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in a browser.  
Upload the PDF files, `purchase-request.csv`, and `company-rules.md` using the three upload boxes, then click **Check Documents**.

### 4. Run tests

```bash
pytest tests/ -v
```

---

## Project structure

```
BIAI_Task1/
├── CLAUDE.md           ← rules and architecture (read this first)
├── rules_config.json   ← all numeric thresholds in one place
├── cli.py              ← command-line runner
├── src/
│   ├── models.py       ← data classes
│   ├── extractor.py    ← PDF text extraction + field extraction
│   ├── rules.py        ← deterministic rule checks
│   └── pipeline.py     ← LangGraph pipeline (the heart of the system)
├── web/
│   ├── app.py          ← FastAPI backend
│   └── static/
│       └── index.html  ← frontend (pure HTML/JS)
├── tests/
│   ├── test_rules.py   ← unit tests for every rule + boundary values
│   └── test_pipeline.py ← integration tests for all 10 packs
├── outputs/            ← CLI writes report.md and results.json here
├── requirements.txt
└── render.yaml         ← Render.com deployment config
```

---

## Sample data (BIAI evaluators)

The BIAI sample files are not committed to this repo (they belong to BIAI).
Place them in a `sample-data/` folder at the repo root:

```
BIAI_Task1/
└── sample-data/
    ├── supplier-pack-01.pdf
    ├── supplier-pack-02.pdf
    ...
    ├── supplier-pack-10.pdf
    ├── purchase-request.csv
    └── company-rules.md
```

Integration tests (`test_pipeline.py`) skip automatically when `sample-data/` is absent.
If your files are somewhere else, set the env variable before running tests:

```bash
set BIAI_SAMPLE_DIR=C:\path\to\your\files   # Windows
export BIAI_SAMPLE_DIR=/path/to/your/files  # Mac/Linux
pytest tests/ -v
```

The **web app** needs no local files — just upload through the browser.

---

## Changing a threshold

All numeric thresholds (3%, 20%, 45 days, etc.) live in **`rules_config.json`** only. Change one number there and the whole system changes. No searching through code.

---

## Deploy to Render (free)

1. Push this folder to a **GitHub** repository.
2. Go to [https://dashboard.render.com](https://dashboard.render.com) → **New** → **Web Service**.
3. Connect your GitHub repo and select this folder.
4. Render detects `render.yaml` and fills in the settings automatically.
5. Click **Deploy**. Your URL appears in ~2 minutes.

---

## Honesty

- **No official answer key**: BIAI provided none. My expected answers are my own reading of the rules.
- **AI-built**: The full code was written by Claude. I (the candidate) reviewed it, ran it, and can explain every line.
- **What does not work well**: see `NOTE.md`.
