"""Classify an uploaded trade document into one of the 8 canonical dossier
document types, so uploads land in the right slot of a shipment workspace
no matter what order (or mess) they arrive in.

Heuristic keyword scoring on extracted text — fast, dependency-free, and the
contributor always confirms or corrects the guess in the UI.
"""

DOC_TYPES = [
    ("commercial_invoice", "Commercial invoice"),
    ("packing_list", "Packing list"),
    ("bill_of_lading", "Bill of lading"),
    ("certificate_of_origin", "Certificate of origin"),
    ("letter_of_credit", "Letter of credit"),
    ("insurance_certificate", "Insurance certificate"),
    ("inspection_certificate", "Inspection certificate"),
    ("export_declaration", "Export customs declaration"),
]

_KEYWORDS = {
    "commercial_invoice": [
        "commercial invoice", "invoice no", "invoice number", "unit price",
        "total value", "total amount", "terms of payment",
    ],
    "packing_list": [
        "packing list", "packing slip", "carton no", "gross weight",
        "net weight", "number of packages", "package count", "dimensions",
    ],
    "bill_of_lading": [
        "bill of lading", "b/l no", "port of loading", "port of discharge",
        "vessel", "voyage", "consignee", "notify party", "container no",
        "on board date",
    ],
    "certificate_of_origin": [
        "certificate of origin", "country of origin", "chamber of commerce",
        "preferential origin", "origin criterion",
    ],
    "letter_of_credit": [
        "letter of credit", "documentary credit", "l/c no", "issuing bank",
        "advising bank", "field 45a", "latest shipment date",
        "tolerance",
    ],
    "insurance_certificate": [
        "insurance certificate", "insurance policy", "insured value",
        "institute cargo clauses", "coverage", "premium",
    ],
    "inspection_certificate": [
        "inspection certificate", "inspection report", "inspected quantity",
        "defect", "aql", "inspection date", "inspector",
    ],
    "export_declaration": [
        "customs declaration", "export declaration", "declared value",
        "hs code", "tariff code", "export license",
    ],
}


def classify_document(text: str) -> tuple[str, float]:
    """Return (doc_type, confidence 0..1) for extracted document text."""
    t = (text or "").lower()
    scores = {}
    for dtype, phrases in _KEYWORDS.items():
        # longer phrases weigh more — they are more distinctive
        scores[dtype] = sum(len(p.split()) for p in phrases if p in t)
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = (scores[best] / total) if total else 0.0
    return best, round(confidence, 2)
