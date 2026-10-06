#!/usr/bin/env python3
"""
CBP CROSS first-pull ingest — Trade Exception Corpus (silver data).

Pulls tariff-classification rulings for apparel (HTS chapters 61/62) from the
public CBP CROSS API (https://rulings.cbp.gov) and parses each ruling into a
{product -> classification -> reasoning} triple saved as JSONL.

CROSS publishes in two letter formats, both handled here:
  - NY series (N######): letter format — RE line, "Dear X:" greeting, product
    facts, requester's suggested classification, CBP analysis, "Sincerely,".
  - HQ series (H###### / numeric): formal FACTS / ISSUE / LAW AND ANALYSIS /
    HOLDING sections.

All CROSS content is U.S. government public domain.

Politeness contract (feasibility pull only):
  - ~1 request/second between API calls
  - max 100 ruling detail fetches
  - identifying User-Agent
  - raw detail JSON cached locally so re-parses never re-hit the server
  - STOPS IMMEDIATELY on HTTP 429/403 or any login wall (no retries around it)

Usage:
    python3 cross_ingest.py --probe        # inspect API shape (1 search + 1 ruling)
    python3 cross_ingest.py                # run the capped first pull
    python3 cross_ingest.py --max 20        # smaller cap for testing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

BASE_URL = "https://rulings.cbp.gov"
SEARCH_PATH = "/api/search"
RULING_PATH = "/api/ruling/{ruling_number}"
UA = "trade-exception-corpus/0.1 (research feasibility pull)"

RATE_DELAY = 1.0
DEFAULT_MAX = 100
PAGE_SIZE = 50
TIMEOUT = 30

# Candidate search queries, tried in order; first with a sane hit count wins.
CANDIDATE_QUERIES = [
    '"Category: Classification" AND apparel',
    '"Category: Classification" AND garment',
    '"Category: Classification" AND knit',
]

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PIPELINE_ROOT / "data" / "raw" / "cross"
RAW_DIR = OUT_DIR / "raw"


class AccessBlocked(Exception):
    """Raised on 429/403/login wall — caller must stop, not retry."""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    try:
        s.get(BASE_URL + "/", timeout=TIMEOUT)
    except Exception:
        pass
    return s


def polite_get(session: requests.Session, url: str, params: dict | None = None):
    """GET with politeness delay; raises AccessBlocked on 429/403/login wall."""
    time.sleep(RATE_DELAY)
    r = session.get(url, params=params, timeout=TIMEOUT)
    if r.status_code in (429, 403):
        raise AccessBlocked(f"HTTP {r.status_code} for {r.url} — stopping.")
    ctype = r.headers.get("Content-Type", "")
    if "text/html" in ctype and "login" in r.text[:2000].lower():
        raise AccessBlocked(f"possible login wall at {r.url} — stopping.")
    return r


def search_page(session, term: str, page: int, page_size: int = PAGE_SIZE) -> dict:
    r = polite_get(session, BASE_URL + SEARCH_PATH, params={
        "term": term,
        "collection": "ALL",
        "commodityGrouping": "ALL",
        "sortBy": "DATE_DESC",
        "pageSize": page_size,
        "page": page,
    })
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return json.loads(r.content.decode("windows-1252", errors="replace"))


def fetch_ruling(session, ruling_number: str) -> dict | None:
    """Fetch detail JSON, using the local raw cache when present."""
    cached = RAW_DIR / f"{ruling_number}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    r = polite_get(session, BASE_URL + RULING_PATH.format(ruling_number=ruling_number))
    if r.status_code == 404:
        return None
    r.raise_for_status()
    try:
        d = r.json()
    except Exception:
        d = json.loads(r.content.decode("windows-1252", errors="replace"))
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def cut_at_markers(text: str, markers: list[str]) -> str:
    cut = len(text)
    for mk in markers:
        i = text.find(mk)
        if i != -1:
            cut = min(cut, i)
    return text[:cut]


def extract_section(text: str, start: str, ends: list[str]) -> str:
    m = re.search(rf"(?im)^[#\s]*{re.escape(start)}\s*:?\s*$", text)
    if not m:
        m = re.search(rf"(?i){re.escape(start)}\s*:", text)
        if not m:
            return ""
    return clean(cut_at_markers(text[m.end():], ends))


# ---------------- NY letter format (N######) ----------------

NY_ANALYSIS_MARKERS = [
    "In your request, you suggest",
    "You suggest the",
    "We disagree",
    "Accordingly,",
    "The applicable subheading for",
]
NY_END_MARKERS = [
    "This ruling does not address the applicability",
    "Duty rates are provided for your convenience",
    "Sincerely,",
]


def parse_ny_letter(text: str) -> dict:
    re_line, country = "", ""
    m = re.search(r"(?i)\bRE:\s*(.+?)(?=\s*Dear\s+[A-Z])", text)
    if m:
        re_line = clean(m.group(1))
        mc = re.search(r"(?i)\bfrom\s+([A-Za-z][A-Za-z ]*?)\s*$", re_line)
        if mc:
            country = mc.group(1).strip()

    mg = re.search(r"(?i)Dear\s+[^:]{1,60}:", text)
    body = text[mg.end():] if mg else text

    proposed = ""
    ms = re.search(r"In your request, you suggest(.{0,500}?)(\d{4}\.\d{2}(?:\.\d{4})?)",
                   body)
    if ms:
        proposed = ms.group(2)

    facts = clean(cut_at_markers(body, NY_ANALYSIS_MARKERS))
    facts = re.sub(r"(?i)^In your letter dated [^.]+\.\s*", "", facts)
    product = " | ".join(x for x in [re_line, facts[:1000]] if x)

    start = len(body)
    for mk in NY_ANALYSIS_MARKERS:
        i = body.find(mk)
        if i != -1:
            start = min(start, i)
    reasoning = clean(cut_at_markers(body[start:], NY_END_MARKERS))[:2500]

    return {
        "product_description": product[:1500],
        "reasoning_summary": reasoning,
        "requester_proposed_hts": proposed,
        "country_of_origin": country,
    }


# ---------------- HQ formal format (H###### / numeric) ----------------

def parse_hq_letter(text: str) -> dict:
    facts = extract_section(text, "FACTS", ["ISSUE", "LAW AND ANALYSIS", "HOLDING"])
    issue = extract_section(text, "ISSUE", ["LAW AND ANALYSIS", "HOLDING"])
    reasoning = extract_section(text, "LAW AND ANALYSIS", ["HOLDING"])
    holding = extract_section(text, "HOLDING", ["Sincerely"])
    if not reasoning:
        reasoning = clean(" ".join(x for x in [issue, holding] if x))
    product = clean(" ".join(x for x in [facts[:1000], issue[:400]] if x))
    return {
        "product_description": product[:1500],
        "reasoning_summary": reasoning[:2500],
        "requester_proposed_hts": "",
        "country_of_origin": "",
    }


# ---------------- top-level parse ----------------

def parse_ruling(d: dict) -> dict | None:
    num = d.get("rulingNumber") or d.get("ruling_number") or d.get("id")
    if not num:
        return None
    num = str(num)

    text = d.get("fullText") or d.get("body") or d.get("text") or d.get("content") or ""
    if isinstance(text, dict):
        text = text.get("text", "")
    text = str(text)

    tariffs = d.get("tariffs") or []
    if isinstance(tariffs, str):
        tariffs = [tariffs]
    if not tariffs and d.get("tariffNumber"):
        tariffs = [d["tariffNumber"]]
    tnorm = [re.sub(r"[^0-9]", "", str(t)) for t in tariffs if t]
    ch_match = [t for t in tnorm if t.startswith("61") or t.startswith("62")]

    is_hq = num.startswith("H") or num.isdigit() or bool(
        re.search(r"(?im)^[#\s]*FACTS\s*:?\s*$", text))
    parts = parse_hq_letter(text) if is_hq else parse_ny_letter(text)

    hts = ""
    if ch_match:
        t = ch_match[0]
        hts = f"{t[:4]}.{t[4:6]}" if len(t) >= 6 else t
    elif tnorm:
        t = tnorm[0]
        hts = f"{t[:4]}.{t[4:6]}" if len(t) >= 6 else t
    else:
        m2 = re.search(r"subheading\s+(\d{4})\s*\.\s*(\d{2})", text)
        if m2:
            hts = f"{m2.group(1)}.{m2.group(2)}"
            if hts.replace(".", "").startswith(("61", "62")):
                ch_match = [hts.replace(".", "")]

    rdate = str(d.get("date") or d.get("rulingDate") or d.get("issuedDate") or "")

    return {
        "ruling_number": num,
        "date": rdate,
        "product_description": parts["product_description"],
        "hts_classification": hts,
        "reasoning_summary": parts["reasoning_summary"],
        "requester_proposed_hts": parts["requester_proposed_hts"],
        "country_of_origin": parts["country_of_origin"],
        "is_ch61_62": bool(ch_match),
        "source_url": f"{BASE_URL}/ruling/{num}",
        "license": "US public domain (U.S. government work)",
    }


def probe(session: requests.Session) -> None:
    data = search_page(session, CANDIDATE_QUERIES[0], page=1, page_size=5)
    print("search keys:", sorted(data.keys()))
    print("totalHits:", data.get("totalHits"))
    rulings = data.get("rulings", [])
    if rulings:
        num = rulings[0].get("rulingNumber")
        d = fetch_ruling(session, num)
        if d:
            print("detail keys:", sorted(d.keys()))
            t = parse_ruling(d)
            t.pop("is_ch61_62", None)
            print(json.dumps(t, indent=2, ensure_ascii=False)[:2000])


def run_pull(max_rulings: int) -> dict:
    session = make_session()
    stats = {"queries_tried": [], "chosen_query": None, "total_hits": 0,
             "fetched": 0, "kept": 0, "skipped_404": 0, "parse_fail": 0,
             "not_chapter": 0, "hq_format": 0, "ny_format": 0}

    for q in CANDIDATE_QUERIES:
        data = search_page(session, q, page=1, page_size=5)
        hits = int(data.get("totalHits") or 0)
        stats["queries_tried"].append({"query": q, "totalHits": hits})
        print(f"query {q!r} -> {hits} hits", flush=True)
        if 0 < hits <= 200_000:
            stats["chosen_query"] = q
            stats["total_hits"] = hits
            break
    if not stats["chosen_query"]:
        print("No usable query — aborting.")
        return stats

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"cross_ch61_62_{date.today():%Y%m%d}.jsonl"
    seen: set[str] = set()
    page = 1
    with open(out_path, "w", encoding="utf-8") as f:
        while stats["fetched"] < max_rulings:
            data = search_page(session, stats["chosen_query"], page=page,
                               page_size=PAGE_SIZE)
            batch = data.get("rulings", [])
            if not batch:
                break
            for item in batch:
                if stats["fetched"] >= max_rulings:
                    break
                num = item.get("rulingNumber")
                if not num or num in seen:
                    continue
                seen.add(num)
                try:
                    d = fetch_ruling(session, num)
                except AccessBlocked:
                    raise
                except Exception as e:
                    print(f"  fetch error {num}: {e}", flush=True)
                    continue
                stats["fetched"] += 1
                if d is None:
                    stats["skipped_404"] += 1
                    continue
                try:
                    triple = parse_ruling(d)
                except Exception as e:
                    stats["parse_fail"] += 1
                    print(f"  parse error {num}: {e}", flush=True)
                    continue
                if triple is None:
                    stats["parse_fail"] += 1
                    continue
                if str(num).startswith("H") or str(num).isdigit():
                    stats["hq_format"] += 1
                else:
                    stats["ny_format"] += 1
                if not triple["is_ch61_62"]:
                    stats["not_chapter"] += 1
                    continue
                triple.pop("is_ch61_62")
                f.write(json.dumps(triple, ensure_ascii=False) + "\n")
                stats["kept"] += 1
                if stats["kept"] % 10 == 0:
                    print(f"  kept {stats['kept']} (fetched {stats['fetched']})",
                          flush=True)
            page += 1

    stats["out_path"] = str(out_path)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX)
    args = ap.parse_args()

    session = make_session()
    try:
        if args.probe:
            probe(session)
            return
        stats = run_pull(args.max)
    except AccessBlocked as e:
        print(f"\nACCESS BLOCKED: {e}", flush=True)
        print("Stopped immediately per politeness contract — no retries.", flush=True)
        sys.exit(2)
    finally:
        session.close()

    print("\n=== PULL SUMMARY ===")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
