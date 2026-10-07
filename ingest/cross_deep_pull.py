#!/usr/bin/env python3
"""Deep pull of CBP CROSS apparel rulings (chapters 61/62) — resume-safe.

Two phases, both polite (1 req/sec, stops immediately on 429/403/login wall):
  Phase 1: page every query's search results, collect ruling numbers.
  Phase 2: fetch details only for numbers not already in the local DB,
           parse, keep chapters 61/62, append to JSONL.

State (data/cross_deep_state.json) makes every run resumable: re-running
never re-fetches a detail page and never re-walks a finished query.

Stops with TARGET_HIT once the DB + newly kept rulings reach TARGET_TOTAL.

Usage:
    python3 cross_deep_pull.py            # pull until 10,000 total rulings
    python3 cross_deep_pull.py --target 15000
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

from cross_ingest import (AccessBlocked, PAGE_SIZE, fetch_ruling, make_session,
                          parse_ruling, search_page)

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PIPELINE_ROOT / "argus.db"
STATE_PATH = PIPELINE_ROOT / "data" / "cross_deep_state.json"
OUT_PATH = PIPELINE_ROOT / "data" / f"cross_ch61_62_deep_{date.today():%Y%m%d}.jsonl"

QUERIES = [
    '"Category: Classification" AND apparel',
    '"Category: Classification" AND garment',
    '"Category: Classification" AND knit',
    '"Category: Classification" AND woven',
    '"Category: Classification" AND textile',
    '"Category: Classification" AND "chapter 62"',
]


def db_ruling_numbers() -> set[str]:
    con = sqlite3.connect(DB_PATH)
    try:
        return {r[0] for r in con.execute("SELECT ruling_number FROM cbp_rulings")}
    finally:
        con.close()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"queries_done": [], "numbers": [], "fetched": [], "kept": 0,
            "stats": {"fetched": 0, "kept": 0, "not_chapter": 0, "parse_fail": 0,
                      "skipped_404": 0}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=10_000)
    args = ap.parse_args()

    known = db_ruling_numbers()
    state = load_state()
    seen: set[str] = set(state["numbers"]) | known
    fetched: set[str] = set(state["fetched"])
    total_known = len(known) + state["kept"]
    log(f"DB has {len(known)} rulings; target total {args.target}")

    session = make_session()
    try:
        # Phase 1 — collect ruling numbers, one query at a time.
        for q in QUERIES:
            if q in state["queries_done"]:
                log(f"query done (resumed): {q}")
                continue
            log(f"paging query: {q}")
            page, new_this_query = 1, 0
            while True:
                data = search_page(session, q, page=page, page_size=PAGE_SIZE)
                batch = data.get("rulings", [])
                if not batch:
                    break
                for item in batch:
                    num = item.get("rulingNumber")
                    if num and num not in seen:
                        seen.add(num)
                        state["numbers"].append(num)
                        new_this_query += 1
                page += 1
                if page % 20 == 0:
                    save_state(state)
            state["queries_done"].append(q)
            save_state(state)
            log(f"  -> {new_this_query} new numbers ({len(state['numbers'])} queued)")

        # Phase 2 — fetch details for queued numbers.
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_PATH, "a", encoding="utf-8") as f:
            for num in state["numbers"]:
                if total_known >= args.target:
                    log(f"TARGET_HIT: {total_known} total rulings")
                    save_state(state)
                    return 0
                if num in fetched:
                    continue
                try:
                    d = fetch_ruling(session, num)
                except AccessBlocked:
                    raise
                except Exception as e:
                    log(f"  fetch error {num}: {e}")
                    fetched.add(num)
                    continue
                state["stats"]["fetched"] += 1
                fetched.add(num)
                state["fetched"].append(num)
                if d is None:
                    state["stats"]["skipped_404"] += 1
                    continue
                try:
                    triple = parse_ruling(d)
                except Exception:
                    state["stats"]["parse_fail"] += 1
                    continue
                if triple is None or not triple.get("is_ch61_62"):
                    state["stats"]["not_chapter"] += 1
                    continue
                triple.pop("is_ch61_62", None)
                f.write(json.dumps(triple, ensure_ascii=False) + "\n")
                f.flush()
                state["kept"] += 1
                state["stats"]["kept"] += 1
                total_known += 1
                if state["kept"] % 25 == 0:
                    save_state(state)
                    log(f"kept {state['kept']} new ({total_known} total)")
        save_state(state)
        log(f"EXHAUSTED: {total_known} total rulings, stats={state['stats']}")
        return 0
    except AccessBlocked as e:
        save_state(state)
        log(f"ACCESS BLOCKED: {e} — progress saved, stopping per politeness contract.")
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
