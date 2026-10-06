"""Initialize the Argus SQLite DB: schema + taxonomy seeds + synthetic dossiers + CBP sample."""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "argus.db"


def main() -> None:
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript((ROOT / "schema.sql").read_text())

    # Contributors referenced by the synthetic seed (anonymized codes)
    codes = ["FWD-A", "FWD-B", "FWD-C", "FWD-D"]
    for c in codes:
        cur.execute(
            "INSERT OR IGNORE INTO contributors (contributor_code, role) VALUES (?, 'forwarder')",
            (c,),
        )
    con.commit()
    id_of = {r[0]: r[1] for r in cur.execute("SELECT contributor_code, id FROM contributors")}

    dossiers = json.loads((ROOT / "seed" / "synthetic_dossiers.json").read_text())
    for d in dossiers:
        cur.execute(
            """INSERT INTO dossiers (reference, origin, destination, commodity, hs_code, incoterm,
               shipment_date, declared_value_usd, freight_cost_usd, gross_weight_kg, quantity_pcs,
               unit_price_usd, vintage_year, regime_note, synthetic, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'verified')""",
            (
                d["reference"], d["origin"], d["destination"], d["commodity"], d["hs_code"],
                d["incoterm"], d["shipment_date"], d["declared_value_usd"], d["freight_cost_usd"],
                d["gross_weight_kg"], d["quantity_pcs"], d["unit_price_usd"], d["vintage_year"],
                d["regime_note"], 1 if d.get("synthetic") else 0,
            ),
        )
        dossier_id = cur.lastrowid
        for dt in d.get("documents", []):
            cur.execute(
                "INSERT INTO documents (dossier_id, doc_type, redacted) VALUES (?, ?, 1)",
                (dossier_id, dt),
            )
        contrib_id = id_of[d["contributor_code"]]
        for a in d.get("annotations", []):
            cur.execute(
                """INSERT INTO annotations (dossier_id, contributor_id, exception_code,
                   documents_compared, decision, cost_impact_usd, resolution)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    dossier_id, contrib_id, a["exception_code"], a["documents_compared"],
                    a["decision"], a["cost_impact_usd"], a["resolution"],
                ),
            )
            ann_id = cur.lastrowid
            verifier_id = id_of[a["verifier_code"]]
            assert verifier_id != contrib_id, "seed violates verifier != contributor"
            cur.execute(
                """INSERT INTO verifications (annotation_id, verifier_contributor_id, verdict, notes)
                   VALUES (?,?,?,?)""",
                (ann_id, verifier_id, a["verdict"], a["verifier_notes"]),
            )

    # CBP rulings sample (US public domain)
    n_rulings = 0
    for line in (ROOT / "seed" / "cross_sample.jsonl").read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        cur.execute(
            """INSERT OR IGNORE INTO cbp_rulings
               (ruling_number, date, product_description, hts_classification,
                requester_proposed_hts, reasoning_summary, country_of_origin, source_url, license)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                r.get("ruling_number"), r.get("date"), r.get("product_description"),
                r.get("hts_classification"), r.get("requester_proposed_hts"),
                r.get("reasoning_summary"), r.get("country_of_origin"),
                r.get("source_url"), r.get("license", "public-domain"),
            ),
        )
        n_rulings += 1

    con.commit()
    n_d = cur.execute("SELECT COUNT(*) FROM dossiers").fetchone()[0]
    n_a = cur.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    n_v = cur.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]
    print(f"OK: {n_d} dossiers, {n_a} annotations, {n_v} verifications, {n_rulings} CBP rulings -> {DB}")
    con.close()


if __name__ == "__main__":
    main()
