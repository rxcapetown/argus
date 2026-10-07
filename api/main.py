"""Argus MVP API — Trade Exception Corpus.

SQLite-backed FastAPI app, server-rendered Jinja2 UI, no build step.
Business rule enforced: a verifier must differ from the annotation's contributor.
"""
import csv
import io
import json
import os
import secrets
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Security, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.templating import Jinja2Templates

import pymupdf  # PyMuPDF — redaction flow

from redact.detect import Finding, detect as detect_entities
from redact.engine import apply_redactions, audit_log, render_preview
from redact.terms import (TERMS_PARAGRAPHS, TERMS_TITLE, TERMS_VERSION,
                          terms_sha256)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("ARGUS_DB", str(ROOT / "argus.db"))

# API key auth. When ARGUS_API_KEY is unset the app runs in open demo mode
# (writes + exports unauthenticated) and warns at startup. Set the env var in
# production to gate all state-changing endpoints and data exports.
API_KEY = os.environ.get("ARGUS_API_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(title="Argus — Trade Exception Corpus", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def _auth_warning():
    if not API_KEY:
        print("WARNING: ARGUS_API_KEY is not set — write & export endpoints are OPEN (demo mode).")


async def require_api_key(
    request: Request,
    header_key: str = Security(api_key_header),
) -> bool:
    """Gate write/export endpoints.

    Accepts the key via the X-API-Key header, an `api_key` query parameter,
    or an `api_key` form field (so plain HTML forms keep working). Open demo
    mode when ARGUS_API_KEY is unset.
    """
    if not API_KEY:
        return True
    candidates = [header_key, request.query_params.get("api_key")]
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            form = await request.form()
            candidates.append(form.get("api_key"))
        except Exception:
            pass
    if any(c and secrets.compare_digest(str(c), API_KEY) for c in candidates if c):
        return True
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def _template_ctx(request: Request, **extra):
    """Shared template context, incl. whether the UI must prompt for an API key."""
    ctx = {"request": request, "footer": FOOTER, "api_key_required": bool(API_KEY)}
    ctx.update(extra)
    return ctx

FOOTER = "Draft. Demo built on synthetic dossiers and public CBP rulings."

STRIPPED_FIELDS = [
    "buyer name", "supplier name", "forwarder name",
    "addresses", "phone / email contacts",
    "B/L numbers", "container & seal numbers",
    "vessel & voyage", "cargo marks",
]
KEPT_FIELDS = [
    "unit prices", "totals", "freight charges",
    "insured values", "weights", "quantities",
]


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def rows(q: str, args=()):
    con = db()
    try:
        return [dict(r) for r in con.execute(q, args)]
    finally:
        con.close()


def one(q: str, args=()):
    con = db()
    try:
        r = con.execute(q, args).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def dossier_detail(dossier_id: int):
    d = one("SELECT * FROM dossiers WHERE id = ?", (dossier_id,))
    if not d:
        return None
    d["documents"] = rows("SELECT * FROM documents WHERE dossier_id = ?", (dossier_id,))
    d["annotations"] = rows(
        """SELECT a.*, c.contributor_code FROM annotations a
           JOIN contributors c ON c.id = a.contributor_id
           WHERE a.dossier_id = ? ORDER BY a.id""",
        (dossier_id,),
    )
    for a in d["annotations"]:
        a["verifications"] = rows(
            """SELECT v.*, c.contributor_code AS verifier_code FROM verifications v
               JOIN contributors c ON c.id = v.verifier_contributor_id
               WHERE v.annotation_id = ?""",
            (a["id"],),
        )
    d["tax_codes"] = {r["code"]: r for r in rows("SELECT * FROM taxonomy_codes")}
    return d


# ---------- pages ----------

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    stats = one(
        """SELECT COUNT(*) AS n,
                  SUM(CASE WHEN status='verified' THEN 1 ELSE 0 END) AS verified
           FROM dossiers"""
    )
    rulings = one("SELECT COUNT(*) AS n FROM cbp_rulings")["n"]
    codes = one("SELECT COUNT(*) AS n FROM taxonomy_codes")["n"]
    return templates.TemplateResponse(request, "landing.html",
        _template_ctx(request, stats=stats, rulings=rulings, codes=codes))


@app.get("/dossiers", response_class=HTMLResponse)
def dossiers_page(request: Request, code: str = "", status: str = ""):
    q = "SELECT * FROM dossiers WHERE 1=1"
    args = []
    if code:
        q += " AND id IN (SELECT dossier_id FROM annotations WHERE exception_code = ?)"
        args.append(code)
    if status:
        q += " AND status = ?"
        args.append(status)
    q += " ORDER BY shipment_date DESC"
    dossiers = rows(q, args)
    codes = rows("SELECT code FROM taxonomy_codes ORDER BY code")
    stats = one(
        """SELECT COUNT(*) AS n,
                  SUM(CASE WHEN status='verified' THEN 1 ELSE 0 END) AS verified,
                  SUM(CASE WHEN status='annotated' THEN 1 ELSE 0 END) AS awaiting
           FROM dossiers"""
    )
    return templates.TemplateResponse(request, "dossiers.html", _template_ctx(
        request, dossiers=dossiers, codes=codes, stats=stats,
        q_code=code, q_status=status))


@app.get("/dossiers/{dossier_id}", response_class=HTMLResponse)
def dossier_page(request: Request, dossier_id: int):
    d = dossier_detail(dossier_id)
    if not d:
        raise HTTPException(404, "dossier not found")
    return templates.TemplateResponse(request, "dossier_detail.html", _template_ctx(
        request, d=d, stripped=STRIPPED_FIELDS, kept=KEPT_FIELDS))


@app.get("/taxonomy", response_class=HTMLResponse)
def taxonomy_page(request: Request):
    codes = rows("SELECT * FROM taxonomy_codes ORDER BY status, code")
    return templates.TemplateResponse(request, "taxonomy.html", _template_ctx(request, codes=codes))


@app.get("/rulings", response_class=HTMLResponse)
def rulings_page(request: Request, q: str = ""):
    if q:
        like = f"%{q}%"
        rulings = rows(
            """SELECT * FROM cbp_rulings
               WHERE product_description LIKE ? OR hts_classification LIKE ? OR ruling_number LIKE ?
               ORDER BY date DESC LIMIT 200""",
            (like, like, like),
        )
    else:
        rulings = rows("SELECT * FROM cbp_rulings ORDER BY date DESC LIMIT 200")
    total = one("SELECT COUNT(*) AS n FROM cbp_rulings")["n"]
    return templates.TemplateResponse(request, "rulings.html", _template_ctx(
        request, rulings=rulings, total=total, q=q))


@app.get("/benchmarks", response_class=HTMLResponse)
def benchmarks_page(request: Request):
    bms = rows("SELECT * FROM trade_benchmarks ORDER BY country, hs_code LIMIT 200")
    return templates.TemplateResponse(request, "benchmarks.html", _template_ctx(request, benchmarks=bms))


# ---------- JSON API ----------

@app.get("/api/dossiers")
def api_dossiers():
    return JSONResponse(rows("SELECT * FROM dossiers ORDER BY id"))


@app.get("/api/dossiers/{dossier_id}")
def api_dossier(dossier_id: int):
    d = dossier_detail(dossier_id)
    if not d:
        raise HTTPException(404, "dossier not found")
    return JSONResponse(d)


@app.post("/api/dossiers")
def api_create_dossier(
    _auth: bool = Depends(require_api_key),
    reference: str = Form(...), origin: str = Form(...), destination: str = Form(...),
    commodity: str = Form(""), hs_code: str = Form(""), incoterm: str = Form(""),
    shipment_date: str = Form(""), declared_value_usd: int = Form(0),
    freight_cost_usd: int = Form(0),
):
    con = db()
    try:
        cur = con.execute(
            """INSERT INTO dossiers (reference, origin, destination, commodity, hs_code,
               incoterm, shipment_date, declared_value_usd, freight_cost_usd, synthetic, status)
               VALUES (?,?,?,?,?,?,?,?,?,0,'draft')""",
            (reference, origin, destination, commodity, hs_code, incoterm,
             shipment_date, declared_value_usd, freight_cost_usd),
        )
        con.commit()
        return {"id": cur.lastrowid, "reference": reference}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "reference already exists")
    finally:
        con.close()


def _contributor_id(code: str) -> int:
    con = db()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO contributors (contributor_code, role) VALUES (?, 'forwarder')",
            (code,),
        )
        con.commit()
        r = con.execute(
            "SELECT id FROM contributors WHERE contributor_code = ?", (code,)).fetchone()
        return r[0]
    finally:
        con.close()


@app.post("/api/dossiers/{dossier_id}/annotate")
def api_annotate(
    dossier_id: int,
    _auth: bool = Depends(require_api_key),
    contributor_code: str = Form(...),
    exception_code: str = Form(...),
    documents_compared: str = Form(""),
    decision: str = Form(""),
    cost_impact_usd: int = Form(0),
    resolution: str = Form(""),
):
    if not one("SELECT id FROM dossiers WHERE id = ?", (dossier_id,)):
        raise HTTPException(404, "dossier not found")
    if not one("SELECT code FROM taxonomy_codes WHERE code = ?", (exception_code,)):
        raise HTTPException(400, "unknown exception code")
    cid = _contributor_id(contributor_code)
    con = db()
    try:
        cur = con.execute(
            """INSERT INTO annotations (dossier_id, contributor_id, exception_code,
               documents_compared, decision, cost_impact_usd, resolution)
               VALUES (?,?,?,?,?,?,?)""",
            (dossier_id, cid, exception_code, documents_compared, decision,
             cost_impact_usd, resolution),
        )
        con.execute("UPDATE dossiers SET status='annotated' WHERE id=?", (dossier_id,))
        con.commit()
        return {"annotation_id": cur.lastrowid}
    finally:
        con.close()


@app.post("/api/annotations/{annotation_id}/verify")
def api_verify(
    annotation_id: int,
    _auth: bool = Depends(require_api_key),
    verifier_code: str = Form(...),
    verdict: str = Form(...),
    notes: str = Form(""),
):
    ann = one("SELECT * FROM annotations WHERE id = ?", (annotation_id,))
    if not ann:
        raise HTTPException(404, "annotation not found")
    if verdict not in ("confirmed", "rejected", "needs_work"):
        raise HTTPException(400, "verdict must be confirmed | rejected | needs_work")
    vid = _contributor_id(verifier_code)
    # ENFORCE: verifier must differ from the annotation's contributor
    if vid == ann["contributor_id"]:
        raise HTTPException(
            400, "verifier must differ from the contributing forwarder (cross-verification rule)")
    con = db()
    try:
        cur = con.execute(
            """INSERT INTO verifications (annotation_id, verifier_contributor_id, verdict, notes)
               VALUES (?,?,?,?)""",
            (annotation_id, vid, verdict, notes),
        )
        if verdict == "confirmed":
            con.execute("UPDATE dossiers SET status='verified' WHERE id=?",
                        (ann["dossier_id"],))
        con.commit()
        return {"verification_id": cur.lastrowid, "verdict": verdict}
    finally:
        con.close()


@app.post("/api/agreements/accept")
def api_accept_agreement(
    _auth: bool = Depends(require_api_key),
    contributor_code: str = Form(...),
    signer_name: str = Form(...),
    signer_firm: str = Form(...),
    agreement_version: str = Form("v0.1"),
):
    cid = _contributor_id(contributor_code)
    con = db()
    try:
        cur = con.execute(
            """INSERT INTO agreement_acceptances
               (contributor_id, agreement_version, signer_name, signer_firm)
               VALUES (?,?,?,?)""",
            (cid, agreement_version, signer_name, signer_firm),
        )
        con.commit()
        return {"acceptance_id": cur.lastrowid, "agreement_version": agreement_version}
    finally:
        con.close()


@app.get("/api/rulings")
def api_rulings(q: str = Query("")):
    if q:
        like = f"%{q}%"
        return JSONResponse(rows(
            "SELECT * FROM cbp_rulings WHERE product_description LIKE ? OR hts_classification LIKE ? LIMIT 200",
            (like, like)))
    return JSONResponse(rows("SELECT * FROM cbp_rulings ORDER BY date DESC LIMIT 200"))


@app.get("/api/benchmarks")
def api_benchmarks():
    return JSONResponse(rows("SELECT * FROM trade_benchmarks ORDER BY country, hs_code"))


# ---------- redaction flow: upload -> review -> confirm ----------

REDACT_DIR = ROOT / "data" / "redact_jobs"
REDACT_DIR.mkdir(parents=True, exist_ok=True)


def _finding_to_dict(f: Finding) -> dict:
    return {"id": f.id, "page": f.page, "rect": list(f.rect),
            "kind": f.kind, "label": f.label, "selected": f.selected}


def _finding_from_dict(d: dict) -> Finding:
    return Finding(id=d["id"], page=d["page"], rect=tuple(d["rect"]),
                   kind=d["kind"], label=d["label"], selected=d.get("selected", True))


def _job_dir(job_id: str) -> "Path":
    p = REDACT_DIR / job_id
    if not p.is_dir():
        raise HTTPException(404, "redaction job not found")
    return p


@app.get("/redact", response_class=HTMLResponse)
def redact_upload_page(request: Request):
    return templates.TemplateResponse(request, "redact_upload.html", _template_ctx(request, stripped=STRIPPED_FIELDS, kept=KEPT_FIELDS,
                      terms_title=TERMS_TITLE, terms_paragraphs=TERMS_PARAGRAPHS,
                      terms_version=TERMS_VERSION))


@app.post("/redact/upload")
async def redact_upload(
    request: Request,
    _auth: bool = Depends(require_api_key),
    contributor_code: str = Form(""),
    extra_terms: str = Form(""),
    terms_accepted: str = Form(""),
    terms_version: str = Form(""),
    file: UploadFile = File(...),
):
    if terms_accepted != "1" or terms_version != TERMS_VERSION:
        raise HTTPException(400, "terms must be accepted before uploading")
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "only PDF uploads are supported in this version")
    job_id = secrets.token_hex(8)
    jobdir = REDACT_DIR / job_id
    jobdir.mkdir(parents=True)
    (jobdir / "original.pdf").write_bytes(data)
    # Consent trail: what was accepted, when, by whom — never the doc values.
    from datetime import datetime, timezone
    (jobdir / "consent.json").write_text(json.dumps({
        "job_id": job_id,
        "contributor_code": contributor_code,
        "terms_title": TERMS_TITLE,
        "terms_version": TERMS_VERSION,
        "terms_sha256": terms_sha256(),
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "user_agent": request.headers.get("user-agent", ""),
        "client": request.client.host if request.client else "",
    }, indent=2))
    terms = [t.strip() for t in extra_terms.replace(",", "\n").splitlines() if t.strip()]
    doc = pymupdf.open(stream=data, filetype="pdf")
    findings = detect_entities(doc, extra_terms=terms)
    doc.close()
    (jobdir / "findings.json").write_text(
        json.dumps([_finding_to_dict(f) for f in findings], indent=2))
    (jobdir / "meta.json").write_text(json.dumps({
        "filename": file.filename, "contributor_code": contributor_code,
        "extra_terms": terms}))
    return RedirectResponse(f"/redact/review/{job_id}", status_code=303)


@app.get("/redact/review/{job_id}", response_class=HTMLResponse)
def redact_review_page(request: Request, job_id: str):
    jobdir = _job_dir(job_id)
    original = (jobdir / "original.pdf").read_bytes()
    findings = [_finding_from_dict(d)
                for d in json.loads((jobdir / "findings.json").read_text())]
    proposed = render_preview(original, findings)
    clean = render_preview(original, [])
    pages = list(zip(clean, proposed))
    return templates.TemplateResponse(request, "redact_review.html", _template_ctx(request, job_id=job_id, pages=pages, findings=findings))


@app.post("/redact/confirm/{job_id}", response_class=HTMLResponse)
async def redact_confirm(request: Request, job_id: str,
                         _auth: bool = Depends(require_api_key)):
    jobdir = _job_dir(job_id)
    form = await request.form()
    keep = set(form.getlist("keep"))
    findings = [_finding_from_dict(d)
                for d in json.loads((jobdir / "findings.json").read_text())]
    for f in findings:
        f.selected = f.id in keep
    (jobdir / "findings.json").write_text(
        json.dumps([_finding_to_dict(f) for f in findings], indent=2))
    original = (jobdir / "original.pdf").read_bytes()
    redacted = apply_redactions(original, findings)
    (jobdir / "redacted.pdf").write_bytes(redacted)
    meta = json.loads((jobdir / "meta.json").read_text())
    consent_path = jobdir / "consent.json"
    terms_v = json.loads(consent_path.read_text()).get("terms_version") if consent_path.is_file() else None
    audit = audit_log(job_id=job_id, filename=meta["filename"], original=original,
                      redacted=redacted, findings=findings,
                      contributor_code=meta.get("contributor_code", ""),
                      terms_version=terms_v)
    (jobdir / "audit.json").write_text(json.dumps(audit, indent=2))
    return templates.TemplateResponse(request, "redact_done.html", _template_ctx(request, job_id=job_id, audit=audit))


@app.get("/redact/download/{job_id}/{name}")
def redact_download(job_id: str, name: str, _auth: bool = Depends(require_api_key)):
    if name not in ("redacted.pdf", "audit.json"):
        raise HTTPException(404)
    p = _job_dir(job_id) / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=f"argus_{job_id}_{name}")


# ---------- exports (filenames carry the synthetic label) ----------

@app.get("/export/dossiers.csv")
def export_csv(_auth: bool = Depends(require_api_key)):
    ds = rows("SELECT * FROM dossiers ORDER BY id")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["reference", "origin", "destination", "commodity", "hs_code", "incoterm",
                "shipment_date", "declared_value_usd", "freight_cost_usd", "gross_weight_kg",
                "quantity_pcs", "unit_price_usd", "vintage_year", "synthetic", "status"])
    for d in ds:
        w.writerow([d["reference"], d["origin"], d["destination"], d["commodity"], d["hs_code"],
                    d["incoterm"], d["shipment_date"], d["declared_value_usd"], d["freight_cost_usd"],
                    d["gross_weight_kg"], d["quantity_pcs"], d["unit_price_usd"],
                    d["vintage_year"], d["synthetic"], d["status"]])
    buf.seek(0)
    return StreamingResponse(iter([buf.read()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=argus_dossiers_SYNTHETIC-DEMO-DATA.csv"})


def _dossier_jsonl():
    for d in rows("SELECT * FROM dossiers ORDER BY id"):
        yield json.dumps(d) + "\n"


@app.get("/export/dossiers.jsonl")
def export_jsonl(_auth: bool = Depends(require_api_key)):
    return StreamingResponse(_dossier_jsonl(), media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=argus_dossiers_SYNTHETIC-DEMO-DATA.jsonl"})


def _rulings_jsonl():
    for r in rows("SELECT * FROM cbp_rulings ORDER BY date DESC"):
        yield json.dumps(r) + "\n"


@app.get("/export/rulings.jsonl")
def export_rulings_jsonl(_auth: bool = Depends(require_api_key)):
    return StreamingResponse(_rulings_jsonl(), media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=argus_cbp_rulings_PUBLIC-RECORDS.jsonl"})
