"""Argus MVP API — Trade Exception Corpus.

SQLite-backed FastAPI app, server-rendered Jinja2 UI, no build step.
Business rule enforced: a verifier must differ from the annotation's contributor.
"""
import csv
import io
import json
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("ARGUS_DB", str(ROOT / "argus.db"))

app = FastAPI(title="Argus — Trade Exception Corpus", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
    return RedirectResponse("/dossiers", status_code=302)


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
    return templates.TemplateResponse("dossiers.html", {
        "request": request, "dossiers": dossiers, "codes": codes,
        "stats": stats, "footer": FOOTER, "q_code": code, "q_status": status,
    })


@app.get("/dossiers/{dossier_id}", response_class=HTMLResponse)
def dossier_page(request: Request, dossier_id: int):
    d = dossier_detail(dossier_id)
    if not d:
        raise HTTPException(404, "dossier not found")
    return templates.TemplateResponse("dossier_detail.html", {
        "request": request, "d": d, "footer": FOOTER,
        "stripped": STRIPPED_FIELDS, "kept": KEPT_FIELDS,
    })


@app.get("/taxonomy", response_class=HTMLResponse)
def taxonomy_page(request: Request):
    codes = rows("SELECT * FROM taxonomy_codes ORDER BY status, code")
    return templates.TemplateResponse("taxonomy.html",
        {"request": request, "codes": codes, "footer": FOOTER})


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
    return templates.TemplateResponse("rulings.html", {
        "request": request, "rulings": rulings, "total": total,
        "footer": FOOTER, "q": q,
    })


@app.get("/benchmarks", response_class=HTMLResponse)
def benchmarks_page(request: Request):
    bms = rows("SELECT * FROM trade_benchmarks ORDER BY country, hs_code LIMIT 200")
    return templates.TemplateResponse("benchmarks.html",
        {"request": request, "benchmarks": bms, "footer": FOOTER})


@app.get("/sold", response_class=HTMLResponse)
def sold_page(request: Request):
    standard_n = one(
        "SELECT COUNT(DISTINCT dossier_id) AS n FROM annotations")["n"]
    verified_n = one(
        """SELECT COUNT(DISTINCT a.dossier_id) AS n FROM annotations a
           JOIN verifications v ON v.annotation_id = a.id AND v.verdict = 'confirmed'""")["n"]
    sample = rows(
        """SELECT a.*, d.reference, d.commodity, d.hs_code, d.unit_price_usd,
                  c.contributor_code
           FROM annotations a JOIN dossiers d ON d.id = a.dossier_id
           JOIN contributors c ON c.id = a.contributor_id
           ORDER BY a.id LIMIT 3"""
    )
    for s in sample:
        s["verifications"] = rows(
            """SELECT v.*, c.contributor_code AS verifier_code FROM verifications v
               JOIN contributors c ON c.id = v.verifier_contributor_id
               WHERE v.annotation_id = ?""",
            (s["id"],),
        )
    return templates.TemplateResponse("sold.html", {
        "request": request, "footer": FOOTER,
        "standard_n": standard_n, "verified_n": verified_n, "sample": sample,
    })


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


# ---------- exports (filenames carry the synthetic label) ----------

@app.get("/export/dossiers.csv")
def export_csv():
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
def export_jsonl():
    return StreamingResponse(_dossier_jsonl(), media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=argus_dossiers_SYNTHETIC-DEMO-DATA.jsonl"})


def _rulings_jsonl():
    for r in rows("SELECT * FROM cbp_rulings ORDER BY date DESC"):
        yield json.dumps(r) + "\n"


@app.get("/export/rulings.jsonl")
def export_rulings_jsonl():
    return StreamingResponse(_rulings_jsonl(), media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=argus_cbp_rulings_PUBLIC-RECORDS.jsonl"})
