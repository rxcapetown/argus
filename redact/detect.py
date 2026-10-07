"""Entity detection for trade-document redaction.

Two complementary strategies, tuned for how trade documents actually look:

1. FIELD LABELS — B/Ls, invoices, packing lists and LCs label their sensitive
   parties ("Shipper:", "Consignee:", "Notify Party:"). The label stays; the
   value block after it is redacted. This is the highest-precision signal.
2. REGEX — container numbers (ISO 6346), seal numbers, emails, phone numbers,
   and B/L / vessel / voyage numbers, wherever they appear on the page.
3. CUSTOM TERMS — names the contributor supplies at upload time (their own
   company name, key client names) are always redacted.

Kept by design: unit prices, totals, freight charges, insured values, weights,
quantities — the commercial facts that carry the discrepancy signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Party / identity fields: label kept, value block redacted.
PARTY_LABELS = [
    "shipper", "consignor", "exporter", "seller", "supplier", "manufacturer",
    "producer", "applicant",
    "consignee", "consigned to", "importer", "buyer", "customer",
    "notify party", "notify", "forwarder", "forwarding agent", "freight forwarder",
    "agent", "broker", "clearing agent",
]

# Single-line labeled values: label kept, same-line value redacted.
VALUE_LABELS = {
    "b/l no": "bl_no", "bl no": "bl_no", "b/l number": "bl_no",
    "bill of lading no": "bl_no", "mbl no": "bl_no", "hbl no": "bl_no",
    "container no": "container_no", "container number": "container_no",
    "cntr no": "container_no",
    "seal no": "seal_no", "seal number": "seal_no",
    "vessel": "vessel_voyage", "voyage": "vessel_voyage", "voy no": "vessel_voyage",
}

STREET_HINTS = re.compile(
    r"\b(street|st\.|road|rd\.|avenue|ave\.|lane|ln\.|floor|fl\.|building|bldg\.|"
    r"plot|house|block|suite|industrial|estate|zone)\b",
    re.IGNORECASE,
)

RX_CONTAINER = re.compile(r"\b[A-Z]{4}\d{7}\b")                      # ISO 6346
RX_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RX_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
RX_SEAL = re.compile(r"\b[A-Z0-9]{2,4}[- ]?\d{4,12}\b")
RX_BL_TOKEN = re.compile(r"\b(?:[A-Z]{3,5}\d{6,}|[A-Z0-9]{4,6}/\d{4}/\d{2,6})\b")

LINE_PATTERNS = [
    ("container_no", RX_CONTAINER),
    ("email", RX_EMAIL),
    ("phone", RX_PHONE),
]


@dataclass
class Word:
    x0: float; y0: float; x1: float; y1: float
    text: str; block: int; line: int


@dataclass
class Finding:
    id: str
    page: int
    rect: tuple  # (x0, y0, x1, y1)
    kind: str
    label: str
    selected: bool = True


def _page_words(page) -> list[Word]:
    return [Word(*w[:4], w[4], w[5], w[6]) for w in page.get_text("words")]


def _lines(words: list[Word]) -> list[list[Word]]:
    groups: dict[tuple, list[Word]] = {}
    for w in words:
        groups.setdefault((w.block, w.line), []).append(w)
    return [sorted(g, key=lambda w: w.x0) for g in groups.values()]


def _union_rect(words: list[Word], pad: float = 1.5) -> tuple:
    x0 = min(w.x0 for w in words) - pad
    y0 = min(w.y0 for w in words) - pad
    x1 = max(w.x1 for w in words) + pad
    y1 = max(w.y1 for w in words) + pad
    return (x0, y0, x1, y1)


def _line_text(line: list[Word]) -> str:
    return " ".join(w.text for w in line)


def _is_label_line(text: str, labels: list[str]) -> bool:
    t = text.strip().lower().rstrip(":")
    return any(t == lb or t.startswith(lb + " ") or t.startswith(lb + ":") for lb in labels)


def detect(doc, extra_terms: list[str] | None = None) -> list[Finding]:
    """Scan every page; return findings with page/pdf-space rects."""
    findings: list[Finding] = []
    fid = 0

    def add(page_no: int, words: list[Word], kind: str, label: str):
        nonlocal fid
        if not words:
            return
        fid += 1
        findings.append(Finding(id=f"f{fid}", page=page_no,
                                rect=_union_rect(words), kind=kind, label=label))

    for page_no, page in enumerate(doc):
        words = _page_words(page)
        if not words:
            continue
        lines = _lines(words)
        consumed: set[int] = set()  # word ids already claimed by a finding

        def claim(ws: list[Word]) -> list[Word]:
            out = [w for w in ws if id(w) not in consumed]
            for w in out:
                consumed.add(id(w))
            return out

        # 1) Party field blocks: label line -> value block follows.
        for i, line in enumerate(lines):
            text = _line_text(line)
            low = text.strip().lower()
            matched = next((lb for lb in PARTY_LABELS
                            if low == lb or low.startswith(lb + ":") or low.startswith(lb + " ")), None)
            if not matched:
                continue
            # Value words: remainder of the label line after the label...
            # One finding per line so the label word itself is never covered.
            label_words = len(matched.split())
            first = claim([w for w in line[label_words:]
                           if w.text.strip(":").strip()])
            if first:
                add(page_no, first, "party_field", f"{matched.title()} values")
            # ...plus continuation lines until a blank line, a new label, or a header.
            total = len(first)
            for nxt in lines[i + 1:]:
                ntext = _line_text(nxt).strip()
                if (not ntext or _is_label_line(ntext, PARTY_LABELS)
                        or _is_label_line(ntext, list(VALUE_LABELS))):
                    break
                if len(ntext) < 2:
                    break
                chunk = claim(list(nxt))
                if chunk:
                    add(page_no, chunk, "party_field", f"{matched.title()} (cont.)")
                    total += len(chunk)
                if total > 40:  # sanity cap
                    break

        # 2) Labeled values (B/L no, container no, seal, vessel/voyage).
        # Multiple labels can share one line ("Vessel: X Voyage: 024E"), so find
        # every label occurrence and take the words up to the next label.
        for line in lines:
            text = _line_text(line)
            low = text.lower()
            hits = []
            for label, kind in VALUE_LABELS.items():
                for m in re.finditer(r"(?<![\w/])" + re.escape(label) + r"(?![\w])", low):
                    hits.append((m.start(), m.end(), label, kind))
            hits.sort()
            wbounds, pos = [], 0
            for w in line:
                wbounds.append((pos, pos + len(w.text), w))
                pos += len(w.text) + 1
            for idx, (s, e, label, kind) in enumerate(hits):
                seg_end = hits[idx + 1][0] if idx + 1 < len(hits) else len(text)
                val_words = [w for (ws, we, w) in wbounds
                             if ws >= e and we <= seg_end
                             and not w.text.endswith(":")
                             and w.text.strip(":").strip()]
                val = claim(val_words)
                if val:
                    add(page_no, val, kind, f"{label.upper()} value")

        # 3) Regex sweep over remaining words, line by line (for offset mapping).
        for line in lines:
            text = _line_text(line)
            for kind, rx in LINE_PATTERNS:
                for m in rx.finditer(text):
                    # Map char span -> words by cumulative offsets.
                    hits, pos = [], 0
                    for w in line:
                        start, end = pos, pos + len(w.text)
                        if end > m.start() and start < m.end() and id(w) not in consumed:
                            hits.append(w)
                        pos = end + 1
                    hits = claim(hits)
                    if hits:
                        add(page_no, hits, kind, f"{kind.replace('_', ' ').title()}")

        # 4) Street-address backup: any line with address hints not yet claimed.
        for line in lines:
            text = _line_text(line)
            if STREET_HINTS.search(text):
                rest = claim([w for w in line if id(w) not in consumed])
                if rest:
                    add(page_no, rest, "address", "Address line")

        # 5) Contributor-supplied custom terms (their company / client names).
        for term in (extra_terms or []):
            term = term.strip()
            if len(term) < 3:
                continue
            tl = term.lower()
            for line in lines:
                text = _line_text(line)
                start = text.lower().find(tl)
                if start < 0:
                    continue
                end = start + len(tl)
                hits, pos = [], 0
                for w in line:
                    wstart, wend = pos, pos + len(w.text)
                    if wend > start and wstart < end and id(w) not in consumed:
                        hits.append(w)
                    pos = wend + 1
                hits = claim(hits)
                if hits:
                    add(page_no, hits, "custom_term", f"Custom term: {term}")

    # Drop findings fully covered by an earlier (larger) finding on the same page.
    findings.sort(key=lambda f: ((f.rect[2] - f.rect[0]) * (f.rect[3] - f.rect[1])),
                  reverse=True)
    kept: list[Finding] = []
    for f in findings:
        x0, y0, x1, y1 = f.rect
        if any(k.page == f.page and k.rect[0] <= x0 and k.rect[1] <= y0
               and k.rect[2] >= x1 and k.rect[3] >= y1 for k in kept):
            continue
        kept.append(f)
    kept.sort(key=lambda f: (f.page, f.rect[1], f.rect[0]))
    for i, f in enumerate(kept, 1):
        f.id = f"f{i}"
    return kept
