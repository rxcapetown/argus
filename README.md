# Argus — Trade Exception Corpus (MVP)

**Argus** is a verified-human data supply venture. Its wedge product is the **Trade
Exception Corpus**: expert-annotated, cross-verified shipment-dossier intelligence
on the Bangladesh → US/UK/EU apparel lane, packaged as AI training data.

Named for Argus Panoptes, the hundred-eyed watcher of Greek myth — every record
is seen by more than one pair of expert eyes.

## The corpus concept

Each **dossier** is one end-to-end shipment: commercial invoice, packing list,
bill of lading, certificate of origin, export declaration, insurance certificate.
A contributing freight forwarder **annotates** it against a fixed exception
taxonomy (what went wrong, what was decided, what it cost, how it resolved).
A **different** forwarder **verifies** the annotation. The API enforces
`verifier != contributor` and returns HTTP 400 otherwise.

## Exception taxonomy (v0.1)

| Code | Meaning |
|---|---|
| VALUE_MISMATCH | Declared value differs across documents |
| QUANTITY_VARIANCE | Quantity differs between packing list / B/L / invoice |
| HS_MISCLASSIFICATION | Wrong tariff code declared |
| LATE_SHIPMENT | Shipment date past the LC deadline |
| COO_DEFECT | Certificate of origin missing or invalid |
| WEIGHT_DISCREPANCY | Declared vs actual weight differs |
| DOC_INCONSISTENCY | Names, ports, marks differ across documents |
| INSURANCE_GAP | Insured value below declared value |
| LC_TERM_DISCREPANCY | LC terms conflict with documents *(proposed v0.1)* |
| FREIGHT_SURCHARGE_VARIANCE | Freight charges differ from agreed/quoted *(proposed v0.1)* |

## Redaction rule (non-negotiable)

**Stripped:** buyer / supplier / forwarder names, addresses, contacts, B/L numbers,
container & seal numbers, vessel & voyage, cargo marks.
**Kept:** unit prices, totals, freight charges, insured values, weights, quantities.
Prices are the discrepancy signal — a corpus without values is useless.

## Verification workflow

1. Ingest dossier → redact identities at intake
2. Contributor annotates against the taxonomy
3. A *different* contributor cross-verifies (enforced in code)
4. Central spot audits on a sample
5. Bundle into tiers: **Standard** (attestation + spot-checks) / **Verified**
   (double-verified, credentials attached, datasheet)

## Included demo datasets

- `seed/synthetic_dossiers_full.jsonl` holds **1,876 synthetic dossiers** and **1,976 annotations**. The source distribution is preserved: 480 dossiers are fully verified by a different contributor and 232 are audit-sampled. Every dossier remains labeled synthetic.
- `data/cross_ch61_62_final_20261006.jsonl` holds the final user-stopped harvest of **1,132 CBP CROSS rulings** for chapters 61/62 (U.S. public domain). This public reference layer is never counted in dossier metrics.

`python scripts/init_db.py` replaces the local database from those two full snapshots. `ingest/cross_ingest.py` remains available for a later, deliberate refresh and is polite by contract (~1 req/sec, stops on 429/403).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py      # builds argus.db from schema.sql + seeds
uvicorn api.main:app --reload  # http://localhost:8000
```

Pages: `/dossiers`, `/taxonomy`, `/rulings`, `/benchmarks`, `/sold`.
Exports: `/export/dossiers.csv`, `/export/dossiers.jsonl`, `/export/rulings.jsonl`
(filenames carry SYNTHETIC-DEMO-DATA / PUBLIC-RECORDS labels).

## Deploy

**Railway (recommended):** connect this repo, Railway detects the `Dockerfile`
via `railway.json`. The container runs `init_db.py` on start, then uvicorn on
`$PORT`. Note: the SQLite DB is ephemeral on Railway unless you attach a volume —
for production, point `ARGUS_DB` at persistent storage or migrate to Postgres
(the schema avoids PG-only types deliberately).

**Render (alternative):** use a `render.yaml` with a Docker runtime, or a native
Python service: build `pip install -r requirements.txt`, start
`python scripts/init_db.py && uvicorn api.main:app --host 0.0.0.0 --port $PORT`.

## Honesty rules (enforced)

- Every synthetic record and page footer is labeled; CBP rows carry a
  "public record" badge.
- No traction, partners, prices, or metrics are invented anywhere. Bundle
  prices are marked "assumption, to be tested with buyers". The license box is
  "draft, pending counsel".
