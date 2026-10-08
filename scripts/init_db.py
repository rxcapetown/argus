"""Initialize the Argus SQLite DB from the full synthetic corpus and final CBP snapshot."""
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Honor ARGUS_DB so the DB can live on a persistent Railway volume.
DB = Path(os.environ.get("ARGUS_DB", str(ROOT / "argus.db")))
DOSSIERS_PATH = ROOT / "seed" / "synthetic_dossiers_full.jsonl"
RULINGS_PATH = ROOT / "data" / "cross_ch61_62_final_20261006.jsonl"


def jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _saved_leads() -> list:
    """User-submitted expert leads that must survive a seed rebuild."""
    if not DB.exists():
        return []
    try:
        con = sqlite3.connect(DB)
        rows = con.execute(
            "SELECT first_name, last_name, company, phone, email, source, created_at"
            " FROM expert_leads"
        ).fetchall()
        con.close()
        return rows
    except sqlite3.Error:
        return []


def main() -> None:
    leads = _saved_leads()  # preserve across rebuilds (volume-backed DBs)
    if DB.exists():
        DB.unlink()
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript((ROOT / "schema.sql").read_text())
    if leads:
        cur.executemany(
            """INSERT INTO expert_leads
               (first_name, last_name, company, phone, email, source, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            leads,
        )

    dossiers = list(jsonl(DOSSIERS_PATH))
    contributor_codes = sorted({
        code
        for dossier in dossiers
        for code in [
            dossier["provenance"].get("contributor_id"),
            dossier["provenance"].get("verifier_id"),
            *[a.get("annotator_id") for a in dossier.get("annotations", [])],
            *[a.get("verifier_id") for a in dossier.get("annotations", [])],
        ]
        if code
    })
    for code in contributor_codes:
        cur.execute(
            "INSERT OR IGNORE INTO contributors (contributor_code, role) VALUES (?, 'forwarder')",
            (code,),
        )
    id_of = {row[0]: row[1] for row in cur.execute("SELECT contributor_code, id FROM contributors")}

    for dossier in dossiers:
        provenance = dossier["provenance"]
        annotations = dossier.get("annotations", [])
        status = "verified" if provenance.get("verifier_id") else ("annotated" if annotations else "draft")
        cur.execute(
            """INSERT INTO dossiers (
                reference, origin, destination, commodity, hs_code, incoterm,
                shipment_date, declared_value_usd, freight_cost_usd, insured_value_usd,
                gross_weight_kg, quantity_pcs, unit_price_usd, vintage_year, regime_note,
                synthetic, status, ingested_date, redacted, audit_sampled
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                dossier["reference"], dossier["origin"], dossier["destination"],
                dossier["commodity"], dossier["hs_code"], dossier["incoterm"],
                dossier["shipment_date"], dossier["declared_value_usd"],
                dossier["freight_cost_usd"], dossier["insured_value_usd"],
                dossier["gross_weight_kg"], dossier["quantity"], dossier["unit_price_usd"],
                int(dossier["shipment_date"][:4]), dossier["regulatory_regime"], 1,
                status, provenance["ingested"], 1 if provenance["redacted"] else 0,
                1 if provenance["audit_sampled"] else 0,
            ),
        )
        dossier_id = cur.lastrowid
        for annotation in annotations:
            contributor_id = id_of[annotation["annotator_id"]]
            cur.execute(
                """INSERT INTO annotations (
                    annotation_ref, dossier_id, contributor_id, exception_code, definition,
                    documents_compared, decision, cost_impact_usd, resolution, annotation_status
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    annotation["annotation_id"], dossier_id, contributor_id,
                    annotation["exception_code"], annotation["definition"],
                    json.dumps(annotation["documents_compared"]), annotation["decision"],
                    annotation["cost_impact_usd"], annotation["status"], annotation["status"],
                ),
            )
            annotation_id = cur.lastrowid
            verifier_code = annotation.get("verifier_id")
            if verifier_code:
                verifier_id = id_of[verifier_code]
                assert verifier_id != contributor_id, "seed violates verifier != contributor"
                cur.execute(
                    """INSERT INTO verifications (annotation_id, verifier_contributor_id, verdict, notes)
                       VALUES (?, ?, 'confirmed', 'Synthetic cross-verification from source seed.')""",
                    (annotation_id, verifier_id),
                )

    n_rulings = 0
    for ruling in jsonl(RULINGS_PATH):
        cur.execute(
            """INSERT OR IGNORE INTO cbp_rulings
               (ruling_number, date, product_description, hts_classification,
                requester_proposed_hts, reasoning_summary, country_of_origin, source_url, license)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                ruling.get("ruling_number"), ruling.get("date"), ruling.get("product_description"),
                ruling.get("hts_classification"), ruling.get("requester_proposed_hts"),
                ruling.get("reasoning_summary"), ruling.get("country_of_origin"),
                ruling.get("source_url"), ruling.get("license", "public-domain"),
            ),
        )
        n_rulings += 1

    con.commit()
    n_dossiers = cur.execute("SELECT COUNT(*) FROM dossiers").fetchone()[0]
    n_annotations = cur.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    n_verifications = cur.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]
    n_audited = cur.execute("SELECT COUNT(*) FROM dossiers WHERE audit_sampled = 1").fetchone()[0]
    print(
        f"OK: {n_dossiers} dossiers, {n_annotations} annotations, "
        f"{n_verifications} verifications, {n_audited} audit-sampled, "
        f"{n_rulings} CBP rulings -> {DB}"
    )
    con.close()


if __name__ == "__main__":
    main()
