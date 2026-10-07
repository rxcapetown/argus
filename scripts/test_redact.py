"""End-to-end test: synthetic commercial invoice -> detect -> redact -> verify.

Asserts that identifying values are gone from the redacted PDF's text layer
while commercial facts (prices, quantities, weights) survive.
"""
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redact.detect import detect
from redact.engine import apply_redactions, extract_text

PII = [
    "Meridian Freight Ltd",          # forwarder (custom term)
    "Globex Apparel Inc",            # consignee (custom term)
    "12/A Industrial Estate",        # address
    "TEL: +880-1712-345678",         # phone
    "ops@meridianfreight.com",       # email
    "MSKU 4829173",                  # container -> normalized below
    "MAEU123456789",                 # B/L token
    "SEAL 88412",                    # seal
]
KEEP = ["48,500.00", "12,000 pcs", "8,400 kg", "1,250.00"]  # commercial facts


def build_invoice() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [
        "COMMERCIAL INVOICE",
        "Shipper: Meridian Freight Ltd",
        "12/A Industrial Estate, Gazipur, Dhaka",
        "TEL: +880-1712-345678 | ops@meridianfreight.com",
        "",
        "Consignee: Globex Apparel Inc",
        "44 Harbor Road, Rotterdam",
        "",
        "B/L No: MAEU123456789",
        "Container No: MSKU4829173",
        "Seal No: SEAL 88412",
        "Vessel: Ever Given Voyage: 024E",
        "",
        "Description: Men's cotton shirts, HS 6105.10",
        "Quantity: 12,000 pcs",
        "Unit price: USD 4.04",
        "Total value: USD 48,500.00",
        "Gross weight: 8,400 kg",
        "Freight charges: USD 1,250.00",
        "Incoterm: FOB Chattogram",
    ]
    y = 60
    for ln in lines:
        page.insert_text((60, y), ln, fontsize=11)
        y += 22
    data = doc.tobytes()
    doc.close()
    return data


def main() -> None:
    pdf = build_invoice()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    findings = detect(doc, extra_terms=["Meridian Freight Ltd", "Globex Apparel Inc"])
    doc.close()

    kinds = {}
    for f in findings:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    print(f"detected {len(findings)} findings: {kinds}")
    # Phone + email ride inside the shipper party block, so 7 findings cover 9+ entities.
    assert len(findings) >= 6, f"expected >= 6 findings, got {len(findings)}"

    redacted = apply_redactions(pdf, findings)
    before, after = extract_text(pdf), extract_text(redacted)

    failures = []
    for secret in ["Meridian Freight Ltd", "Globex Apparel Inc", "Gazipur",
                   "1712-345678", "ops@meridianfreight.com", "MAEU123456789",
                   "MSKU4829173", "88412", "Ever Given"]:
        if secret.replace(" ", "") in after.replace(" ", ""):
            failures.append(secret)
    for keep in KEEP:
        if keep not in after:
            failures.append(f"LOST commercial fact: {keep}")
    # Labels themselves should survive (structure preserved).
    for label in ["Shipper:", "Consignee:", "B/L No:"]:
        if label not in after:
            failures.append(f"LOST label: {label}")

    assert not failures, f"redaction failures: {failures}"
    assert "Meridian Freight Ltd" in before, "sanity: PII present before redaction"
    print("OK: PII removed from text layer; commercial facts and labels preserved.")
    print(f"original {len(pdf)} bytes -> redacted {len(redacted)} bytes")


if __name__ == "__main__":
    main()
