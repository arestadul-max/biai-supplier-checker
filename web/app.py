"""
FastAPI web backend for the supplier document checker.

All processing goes through the SAME pipeline as cli.py — guaranteed identical answers.
Uploaded files are processed in a temporary directory and deleted after the response.
"""

import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import APPROVE, REJECT, NEEDS_PERSON
from src.pipeline import load_config, load_pr_items, run_batch
from src.extractor import extract_pdf_text   # used for PDF content validation


# ── Constants ─────────────────────────────────────────────────────────────────
MAX_PDF_SIZE_MB   = 10
MAX_PDF_COUNT     = 20
REQUIRED_CSV_COLS = {
    "PR number", "Item", "Description", "Unit",
    "Quantity requested", "Maximum unit price (BDT)",
}
STATIC_DIR = Path(__file__).parent / "static"


# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Supplier Document Checker", version="1.0.0")


@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/classic") or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the premium frontend (main)."""
    return FileResponse(STATIC_DIR / "index_premium.html")


@app.get("/classic", response_class=HTMLResponse)
async def classic():
    """Serve the original frontend (kept for reference)."""
    return FileResponse(STATIC_DIR / "index.html")


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_pdf_bytes(data: bytes, filename: str) -> Optional[str]:
    """
    Return an error message if the bytes are not a readable PDF,
    or None if they are valid.
    """
    if not data.startswith(b"%PDF"):
        return f"'{filename}' does not appear to be a PDF (missing %PDF header)"
    if len(data) > MAX_PDF_SIZE_MB * 1024 * 1024:
        return f"'{filename}' exceeds {MAX_PDF_SIZE_MB} MB limit"
    return None


def _validate_csv_bytes(data: bytes) -> Optional[str]:
    """Return an error message if the CSV is missing required columns, or None if valid."""
    import csv
    try:
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        cols = set(reader.fieldnames or [])
        missing = REQUIRED_CSV_COLS - cols
        if missing:
            return f"Purchase-request CSV is missing columns: {missing}"
    except Exception as exc:
        return f"Could not parse CSV: {exc}"
    return None


# ── Main check endpoint ───────────────────────────────────────────────────────

@app.post("/check")
async def check_documents(
    pdfs:  list[UploadFile] = File(...),
    pr:    UploadFile        = File(...),
    rules: UploadFile        = File(...),
):
    """
    Receive uploaded files, run the pipeline, return JSON results.
    Processing happens in a temp directory that is deleted after the response.
    """

    # ── 1. Validate upload counts and sizes ───────────────────────────────────
    # Collect all PDFs, potentially extracting from a single .zip
    pdf_files: list[tuple[str, bytes]] = []   # (filename, bytes)

    for upload in pdfs:
        raw = await upload.read()
        fname = upload.filename or "unknown"

        # If a .zip was uploaded, unpack it and treat its PDFs as individual files
        if fname.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for name in zf.namelist():
                        if name.lower().endswith(".pdf") and not name.startswith("__MACOSX"):
                            pdf_files.append((Path(name).name, zf.read(name)))
            except Exception as exc:
                raise HTTPException(400, detail=f"Could not unpack zip '{fname}': {exc}")
        elif fname.lower().endswith(".pdf"):
            pdf_files.append((fname, raw))
        else:
            raise HTTPException(400, detail=f"'{fname}' is not a PDF or ZIP file")

    if not pdf_files:
        raise HTTPException(400, detail="No PDF files found in the upload")

    if len(pdf_files) > MAX_PDF_COUNT:
        raise HTTPException(400, detail=f"Too many PDFs: max {MAX_PDF_COUNT}, got {len(pdf_files)}")

    # Validate each PDF
    for fname, data in pdf_files:
        err = _validate_pdf_bytes(data, fname)
        if err:
            raise HTTPException(400, detail=err)

    # ── 2. Validate CSV ───────────────────────────────────────────────────────
    pr_bytes = await pr.read()
    csv_err = _validate_csv_bytes(pr_bytes)
    if csv_err:
        raise HTTPException(400, detail=csv_err)

    # ── 3. Read rules file (stored for display only; not parsed) ─────────────
    rules_text = (await rules.read()).decode("utf-8", errors="replace")

    # ── 4. Write files to a temp directory and run the pipeline ──────────────
    tmp_dir = Path(tempfile.mkdtemp(prefix="biai_"))
    try:
        # Write PDFs
        pdf_paths = []
        for fname, data in pdf_files:
            p = tmp_dir / fname
            p.write_bytes(data)
            pdf_paths.append(str(p))

        # Write CSV
        pr_path = tmp_dir / "purchase-request.csv"
        pr_path.write_bytes(pr_bytes)

        # Load config and PR items
        config   = load_config()
        pr_items = load_pr_items(str(pr_path))

        # Run the same pipeline as the CLI
        results  = run_batch(pdf_paths, pr_items, config)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)   # always clean up

    # ── 5. Serialise results ──────────────────────────────────────────────────
    output = []
    for r in results:
        output.append({
            "file":           r.pdf_filename,
            "supplier":       r.supplier_name,
            "item":           r.item_letter,
            "description":    r.description,
            "quantity":       r.quantity,
            "unit":           r.unit,
            "unit_price":     r.unit_price,
            "decision":       r.decision,
            "reasons":        r.reasons,
            "evidence":       [e for e in r.evidence if e],
            "human_override": r.human_override,
            "human_name":     r.human_name,
            "human_reason":   r.human_reason,
            "error":          r.error,
        })

    summary = {
        "total":   len(output),
        "approve": sum(1 for r in output if r["decision"] == APPROVE),
        "reject":  sum(1 for r in output if r["decision"] == REJECT),
        "review":  sum(1 for r in output if r["decision"] == NEEDS_PERSON),
    }

    return JSONResponse({
        "results":    output,
        "summary":    summary,
        "rules_text": rules_text,
    })


@app.post("/override")
async def override_decision(payload: dict):
    """
    Accept a human's Approve/Reject override for one NEEDS_PERSON document.
    In this demo the override is only stored client-side (in JS) and included
    in the downloaded report. A production system would persist this to a database.
    """
    decision = payload.get("decision")
    if decision not in (APPROVE, REJECT):
        raise HTTPException(400, detail=f"Invalid decision '{decision}'")
    return {"status": "ok", "saved_decision": decision}


@app.post("/download-report")
async def download_report(payload: dict):
    """
    Accept the full results (including any human overrides) and return
    a Markdown report file for download.
    """
    results    = payload.get("results", [])
    rules_text = payload.get("rules_text", "")

    from datetime import datetime
    lines = [
        "# Supplier Document Check Report",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "> **HONESTY NOTE:** Built with AI (Claude). Decisions are deterministic Python — no LLM.\n"
        "> The rules file below was uploaded for reference only; thresholds come from rules_config.json.",
        "",
        "## Summary",
        "",
        "| Decision | Count |",
        "|---|---|",
    ]
    approve = sum(1 for r in results if (r.get("human_override") or r["decision"]) == APPROVE)
    reject  = sum(1 for r in results if (r.get("human_override") or r["decision"]) == REJECT)
    review  = sum(1 for r in results if not r.get("human_override") and r["decision"] == NEEDS_PERSON)
    lines += [
        f"| ✅ Approve | {approve} |",
        f"| ❌ Reject | {reject} |",
        f"| ⚠️ Needs a person | {review} |",
        f"| **Total** | **{len(results)}** |",
        "",
        "## Results",
        "",
    ]
    for r in results:
        final = r.get("human_override") or r["decision"]
        lines.append(f"### {r['file']}")
        lines.append(f"- **Supplier**: {r['supplier']}")
        lines.append(f"- **Item**: {r['item']}")
        lines.append(f"- **Decision**: {final}")
        lines.append(f"- **Reasons**:")
        for reason in r.get("reasons", []):
            lines.append(f"  - {reason}")
        for ev in r.get("evidence", []):
            if ev:
                lines.append(f'  - Evidence: "{ev}"')
        if r.get("human_override"):
            lines.append(f"- **Human override**: {r['human_override']} "
                         f"(by {r.get('human_name','?')}): {r.get('human_reason','')}")
        lines.append("")

    if rules_text:
        lines += ["---", "## Uploaded Rules File (for reference)", "", "```markdown", rules_text, "```"]

    # Write to a temp file and return it
    tmp = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8")
    tmp.write("\n".join(lines))
    tmp.close()
    return FileResponse(tmp.name, filename="report.md", media_type="text/markdown",
                        background=None)
