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

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session as DBSession

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import APPROVE, REJECT, NEEDS_PERSON
from src.pipeline import load_config, load_pr_items, run_batch
from src.extractor import extract_pdf_text   # used for PDF content validation
from src.rules_parser import parse_rules_config
from src.database import CheckSession as DBCheckSession, User as DBUser, get_db, init_db, session_to_dict
from src.auth import create_token, decode_token, hash_password, verify_password


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


@app.on_event("startup")
def on_startup():
    init_db()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: DBSession = Depends(get_db),
) -> DBUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(authorization[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(DBUser).filter(DBUser.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/auth/register")
async def register(payload: dict, db: DBSession = Depends(get_db)):
    email    = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    name     = (payload.get("name") or email).strip()
    if not email or not password:
        raise HTTPException(400, "Email and password required")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if db.query(DBUser).filter(DBUser.email == email).first():
        raise HTTPException(400, "An account with this email already exists")
    user = DBUser(email=email, name=name, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"token": create_token(user.id, user.email), "id": user.id,
            "email": user.email, "name": user.name}


@app.post("/auth/login")
async def login(payload: dict, db: DBSession = Depends(get_db)):
    email    = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    return {"token": create_token(user.id, user.email), "id": user.id,
            "email": user.email, "name": user.name}


@app.get("/auth/me")
async def me(current_user: DBUser = Depends(_get_current_user)):
    return {"id": current_user.id, "email": current_user.email, "name": current_user.name}


# ── Session history endpoints ─────────────────────────────────────────────────

@app.get("/sessions")
async def list_sessions(
    current_user: DBUser = Depends(_get_current_user),
    db: DBSession = Depends(get_db),
):
    rows = (
        db.query(DBCheckSession)
        .filter(DBCheckSession.user_id == current_user.id)
        .order_by(DBCheckSession.date.desc())
        .all()
    )
    return [session_to_dict(r, current_user.email) for r in rows]


@app.post("/sessions/{session_id}")
async def save_session(
    session_id: str,
    payload: dict,
    current_user: DBUser = Depends(_get_current_user),
    db: DBSession = Depends(get_db),
):
    if db.query(DBCheckSession).filter(
        DBCheckSession.id == session_id,
        DBCheckSession.user_id == current_user.id,
    ).first():
        return {"ok": True}   # already saved — idempotent

    sess = DBCheckSession(
        id              = session_id,
        user_id         = current_user.id,
        date            = payload.get("date") or "",
        summary         = json.dumps(payload.get("summary") or {}),
        results         = json.dumps(payload.get("results") or []),
        rules_text      = payload.get("rules_text") or "",
        rules_name      = payload.get("rules_name") or "",
        applied_sources = json.dumps(payload.get("applied_sources") or []),
    )
    db.add(sess)
    db.commit()
    return {"ok": True}


@app.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    current_user: DBUser = Depends(_get_current_user),
    db: DBSession = Depends(get_db),
):
    db.query(DBCheckSession).filter(
        DBCheckSession.id == session_id,
        DBCheckSession.user_id == current_user.id,
    ).delete()
    db.commit()
    return {"ok": True}


@app.delete("/sessions")
async def clear_sessions(
    current_user: DBUser = Depends(_get_current_user),
    db: DBSession = Depends(get_db),
):
    db.query(DBCheckSession).filter(
        DBCheckSession.user_id == current_user.id,
    ).delete()
    db.commit()
    return {"ok": True}


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

    # ── 3. Read and parse rules file ─────────────────────────────────────────
    rules_text = (await rules.read()).decode("utf-8", errors="replace")
    default_config = load_config()
    parsed_config, applied_sources = parse_rules_config(rules_text, default_config)

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

        # Load PR items (config comes from the parsed rules file)
        config   = parsed_config
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
        "results":         output,
        "summary":         summary,
        "rules_text":      rules_text,
        "applied_sources": applied_sources,
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
        "> Thresholds are extracted from the uploaded rules file; defaults from rules_config.json fill any gaps.",
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
