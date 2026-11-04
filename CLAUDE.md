# CLAUDE.md — BIAI Round 2 Task 1: Supplier Document Checker

Read this file at the start of **every** session. Follow it without exception.
If the user asks for something that breaks a rule below, warn them before doing it.

---

## RULE 0 — ASK BEFORE CHANGING *(new — always applies)*

> Before modifying any existing feature, component, or file — even a small change —
> **read this file first**, then tell the user what you plan to change, what it touches,
> and what might break. Wait for their go-ahead before writing any code.

This rule applies to refactors, design tweaks, new flags, dependency changes, and
endpoint modifications. Quick bug fixes that are obviously safe (a typo, a missing
`hidden` attribute) may proceed without asking, but still mention what was changed.

**Why**: the app is a live deployment on Render. A wrong change to `index_premium.html`,
`web/app.py`, or `src/database.py` can break the live site for all users.

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

### Core Concept
**DocCheck** — a single-page compliance checker for procurement teams.
Upload supplier PDFs + a purchase request CSV + a company rules file → get
instant Approve / Reject / Needs-a-person decisions with quoted evidence.

**Auth**: server-side accounts (email + password). JWT stored in `localStorage`
under key `dckyy_token`. No anonymous use — login required to run checks.

The app is a FastAPI backend (`web/app.py`) + SPA frontend
(`web/static/index_premium.html`). The backend serves `index_premium.html` at `/`
with `no-cache` headers so deploys take effect immediately.

> ⚠️ **The production frontend is `index_premium.html`** — not `index_yoyu.html`
> (old prototype, unused) and not `index_demo.html` (scratch file). Never edit the
> wrong file.

### Three Upload Zones
1. **Supplier PDFs** — one or many; also accept a .zip containing PDFs
2. **Purchase Request CSV** — must have the required columns
3. **Company Rules .md** — displayed for reference only (not parsed)

Nothing is loaded silently from disk. Results come only from uploaded files.
The Check button is disabled until all three zones have valid files.
File validation checks real content (PDF starts with `%PDF`; CSV has required columns).
A bad file never crashes: it is rejected with a clear message or escalated.

The uploaded rules file is **displayed for reference only**. The page must state:
> "Rules are applied from the built-in configuration (rules_config.json);
> the uploaded rules file is shown for reference and is not parsed automatically."

### Authentication (server-side, JWT)

| Item | Detail |
|------|--------|
| Registration | POST `/register` — `{name, email, password}` |
| Login | POST `/login` — `{email, password}` → `{access_token, token_type, user}` |
| Token storage | `localStorage.getItem('dckyy_token')` |
| Token sent as | `Authorization: Bearer <token>` header |
| Token expiry | 365 days (python-jose, HS256) |
| Password storage | bcrypt hash in `users` table |
| Secret | `JWT_SECRET` env var (required on Render; falls back to insecure default locally) |
| Current user | `_currentUser` global; populated by `/me` on boot |

Key auth functions in `index_premium.html`:
- `getToken()` / `setToken(t)` / `clearToken()` — read/write/delete `dckyy_token`
- `authHdrs()` — returns `{'Authorization': 'Bearer <token>', 'Content-Type': 'application/json'}`
- `doLogin()` / `doRegister()` — form submit handlers
- `doLogout()` — clears token, resets `_currentUser`, shows auth panel
- `bootApp()` — on page load: calls `/me`, loads sessions if token valid, else shows login

### Session History (server-side database)

Sessions are stored server-side in the `check_sessions` table (SQLite locally,
PostgreSQL on Render). The frontend never writes sessions to `localStorage`.

On login `bootApp()` calls `GET /sessions` (with Bearer token) which returns all
sessions for the logged-in user. These are cached in the global `_sessions` array.
`getHistory()` returns `_sessions` — do NOT use `localStorage.getItem('dckyy_history')`.

Session shape (matches `session_to_dict()` in `src/database.py`):
```
{ id, email, date, summary:{total,approve,reject,review},
  results:[{file, supplier, item, description, quantity, unit, unit_price,
            decision, reasons, evidence, human_override, human_name, human_reason, error}],
  rules_text, rules_name, applied_sources }
```

Key session functions:
- `_loadSessions()` — async; calls `/sessions`, sets `_sessions`, refreshes UI
- `saveCheck(data)` — calls POST `/check`, stores result via API, appends to `_sessions`
- `deleteSession(id)` — calls DELETE `/sessions/{id}`, removes from `_sessions`
- `clearHistory()` — asks for confirm, deletes all sessions for the user via API

### Downloads

Both downloads are **client-side only** — no server endpoint is used.

| Button | Function | Output |
|--------|----------|--------|
| Download Report | `dlReport()` | `doccheck-report.pdf` via jsPDF |
| Download JSON | `dlJSON()` | `results.json` as a Blob download |
| History PDF | `downloadSessionPDF(id)` | `doccheck-report.pdf` via jsPDF |

**jsPDF**: loaded lazily from CDN — `https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js` — only when the user first clicks a report button.

```javascript
function _loadJsPDF(cb){
  if(window.jspdf){ cb(); return; }
  const s = document.createElement('script');
  s.src = 'https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js';
  s.onload = () => cb();
  s.onerror = () => alert('Could not load PDF library. Check your internet connection.');
  document.head.appendChild(s);
}
function _downloadPDF(results, summary, rulesName, dateStr){ /* builds A4 PDF programmatically, calls doc.save('doccheck-report.pdf') */ }
```

Do NOT revert to print-dialog (`window.print()`) or HTML blob download — the user
explicitly chose jsPDF for a real `.pdf` output.

### Views / Navigation
The SPA has four views, switched via the top nav bar:
- **Dashboard** — recent session summary tiles (last 3 sessions); each row is
  clickable and jumps to that session in History with the row expanded.
- **Check Documents** — the three upload zones + "Run Compliance Check →" button.
  After a check completes a second button "↺ New Check" appears to reset everything.
- **History** — full paginated list of past check sessions from the server.
  Each session row expands to show all result cards for that run.
- *(implicit)* Results appear in-page below the upload zone after a check; no
  separate page navigation needed.

### Result Cards (rcard)
Each supplier document gets one collapsible card:
- **Header** (always visible): coloured badge (Approve=green, Reject=red,
  Needs-a-person=amber), supplier name, Item letter, filename.
- **Body** (expanded on click): 8-cell details grid showing Supplier, Item,
  Description (spans 2 cols), Quantity, Unit, Unit Price (BDT), File, Decision;
  then "Decision Reasons" bulleted list; then "Evidence from Document" blockquotes
  with verbatim text from the PDF.
- **Override controls** (Needs-a-person only): Approve / Reject buttons + name and
  reason text fields so a human reviewer can record their sign-off.

### Design Language (index_premium.html)

| Element | Detail |
|---------|--------|
| Body font | "Plus Jakarta Sans" (Google Fonts) — 400/500/600/700/800 |
| Mono font | "JetBrains Mono" (Google Fonts) — 400/500; used for file names and code |
| Accent | `--accent: #6366F1` (indigo) / `--accent-h: #4F46E5` hover |
| Background | `--bg: #F4F5F7` light / `#0D0F17` dark |
| Surface | `--surface: #FFFFFF` light / `#161825` dark |
| Decision colors | `--green` / `--red` / `--amber` with matching `*-bg` tokens |
| Shadows | `--s1` (subtle), `--s2` (card), `--s3` (modal) |
| Border radius | `--r: 10px` default, `--r-sm: 6px`, `--r-lg: 16px`, `--pill: 9999px` |
| Theme toggle | CSS tokens on `:root`; dark via `[data-theme="dark"]` or `prefers-color-scheme` |
| Layout | Narrow centred column (`max-width: ~860px`) with generous whitespace |
| No CSS framework | All styles inline in the HTML file |

**Badge classes**: `.approve`, `.reject`, `.review` (mapped from decision string by `decClass()`).

**Key grid/layout classes**: `.rcard-details-grid`, `.rcard-detail-cell`,
`.rcard-detail-label`, `.rcard-detail-val`, `.evidence-box`, `.dt` (dashboard table),
`.topbar`, `.nav`, `.topbar-right`.

**Mobile nav (max-width: 540px)**: `.nav` uses `flex: 0 0 100%` — this is intentional.
The base CSS sets `flex: 1` on `.nav` which would override `width: 100%`, so the
mobile block must use `flex: 0 0 100%` to force a full-width second row. Do NOT
change this back to `width: 100%` or the nav overlaps the logo again.

### Key JavaScript Functions (index_premium.html)

| Function | Purpose |
|----------|---------|
| `bootApp()` | On load: verify token via `/me`, load sessions, show auth or app |
| `showAuth(pane)` | Show login/register panel |
| `doLogin()` | POST `/login`, store token, call `bootApp()` |
| `doRegister()` | POST `/register`, then auto-login |
| `doLogout()` | Clear token, reset state, show auth panel |
| `getToken()` / `setToken()` / `clearToken()` | Read/write/delete `dckyy_token` |
| `authHdrs()` | Build auth headers with Bearer token |
| `_loadSessions()` | Fetch sessions from API, populate `_sessions` |
| `getHistory()` | Returns `_sessions` (never localStorage) |
| `saveCheck(data)` | POST to `/check`, append result to `_sessions` |
| `deleteSession(id)` | DELETE `/sessions/{id}`, remove from `_sessions` |
| `clearHistory()` | Confirm + delete all sessions |
| `runCheck()` | POST files to `/check`, store session, call `renderResults()` |
| `resetCheck()` | Clear uploads, hide results, hide "↺ New Check" |
| `renderResults(data)` | Build result cards, show summary, reveal "↺ New Check" |
| `buildRCard(r, uid, readOnly)` | Render one result card HTML string |
| `toggleRCard(head)` | Expand/collapse a card body |
| `renderDashRecent()` | Render last-3-sessions summary table on Dashboard |
| `renderHistory()` | Render full session list in History view |
| `goToSession(id)` | Switch to History view + scroll/expand that session |
| `showView(name)` | Switch between dashboard / check / history views |
| `_loadJsPDF(cb)` | Lazy-load jsPDF from cdnjs CDN |
| `_downloadPDF(results, summary, rulesName, dateStr)` | Build A4 PDF, call `doc.save()` |
| `dlReport()` | Download PDF for the last run check |
| `downloadSessionPDF(id)` | Download PDF for a history session |
| `dlJSON()` | Download `results.json` for the last run check |
| `decClass(d)` / `decLabel(d)` | Decision string → CSS class / display label |
| `applyOv(uid, dec)` | Apply human override to a Needs-a-person card |
| `refreshStats()` | Recompute and re-render dashboard stats |

### Backend API Endpoints (web/app.py)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | None | Serve `index_premium.html` (no-cache) |
| POST | `/register` | None | Create account: `{name, email, password}` |
| POST | `/login` | None | Authenticate: `{email, password}` → JWT |
| GET | `/me` | Bearer | Return current user info |
| POST | `/check` | Bearer | Run compliance check on uploaded files |
| GET | `/sessions` | Bearer | Return all sessions for current user |
| DELETE | `/sessions/{id}` | Bearer | Delete one session |
| DELETE | `/sessions` | Bearer | Delete all sessions for current user |

---

## DATABASE & INFRASTRUCTURE

### Database (src/database.py)

| Environment | Engine | Config |
|-------------|--------|--------|
| Local dev | SQLite | `doccheck.db` in project root (gitignored) |
| Render (production) | PostgreSQL | `DATABASE_URL` env var |

**Tables**: `users` (id, email, name, password_hash, created_at) and
`check_sessions` (id, user_id, date, summary, results, rules_text, rules_name, applied_sources).

`init_db()` creates tables on startup (safe to call repeatedly).

### CRITICAL: psycopg2 driver fix

SQLAlchemy 2.x tries psycopg3 (`psycopg`) first for a bare `postgresql://` URL.
Only `psycopg2-binary` is installed. The URL **must** be rewritten before
`create_engine()` is called. This is already done in `src/database.py`:

```python
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
```

**Do NOT remove or simplify this URL rewriting.** Render provides `postgres://`;
without the rewrite the app crashes with `ModuleNotFoundError: No module named 'psycopg'`.

### Environment Variables (Render)

| Variable | Purpose | Required |
|----------|---------|----------|
| `DATABASE_URL` | PostgreSQL connection string from Render add-on | Yes (for persistent accounts) |
| `JWT_SECRET` | Secret key for signing JWTs | Yes (security) |

Without `DATABASE_URL` the app falls back to ephemeral SQLite — accounts are wiped
on every deploy. **The user must set these in Render → Environment manually.**
Claude cannot enter these values (they contain credentials).

### Deployment

| Item | Detail |
|------|--------|
| GitHub repo | https://github.com/arestadul-max/biai-supplier-checker |
| Live URL | https://biai-supplier-checker.onrender.com |
| Render service | `srv-dbl394ugekts73fhuptg` |
| PostgreSQL add-on | `doccheck-db` at `dpg-dblhougn74is73a615o0-a` |
| Auto-deploy | **Off** — must trigger manually in Render dashboard after each push |

**Render deploy process**: push to GitHub → Render dashboard → Manual Deploy →
"Deploy latest commit". Claude cannot click the deploy button (browser automation
classifier blocks it). The user must click it themselves.

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
| Models / extractor | DONE | `src/models.py`, `src/extractor.py` |
| LangGraph pipeline | DONE | `src/pipeline.py` — 5-node fixed graph |
| Rules engine | DONE | `src/rules.py` + `rules_config.json` |
| Command-line runner | DONE | `cli.py` |
| Web app — FastAPI backend | DONE | `web/app.py` |
| Web app — SPA frontend | DONE | `web/static/index_premium.html` |
| Server-side auth | DONE | bcrypt + JWT; `/register` `/login` `/me` |
| Session storage (DB) | DONE | SQLite locally; PostgreSQL on Render via DATABASE_URL |
| Supplier detail extraction | DONE | description/quantity/unit/unit_price in results |
| Human override flow | DONE | Needs-a-person cards show Approve/Reject buttons |
| Session history (server) | DONE | `_sessions` from `/sessions` API; History view |
| Dashboard | DONE | Last-3-sessions tiles; click → History |
| PDF report download | DONE | jsPDF client-side; `doccheck-report.pdf` |
| JSON export | DONE | `results.json` Blob download |
| New Check button | DONE | Appears after check; resets all state |
| Mobile nav fix | DONE | `.nav { flex: 0 0 100% }` at max-width:540px |
| psycopg2 driver fix | DONE | Explicit `postgresql+psycopg2://` URL in `src/database.py` |
| GitHub push | DONE | Latest commit: `2e5a5d6` |
| Render deployment | DONE | Live at https://biai-supplier-checker.onrender.com |
| DATABASE_URL on Render | PENDING | User must add env var in Render dashboard |
| JWT_SECRET on Render | PENDING | User must add env var in Render dashboard |
| Tests | PARTIAL | Basic smoke tests only |

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
