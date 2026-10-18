"""Paid contributor terms — versioned, hashed, logged on acceptance.

Short plain-language terms for the $1,000-per-dossier experiment (first 15
verified dossiers). Shown before registration. Every acceptance is recorded
(contributor, timestamp, terms version + hash).

Payment is released only after the dossier passes verification: a complete
document set, contributor annotation against the exception taxonomy, and a
confirmed cross-verification by a different forwarder.

NOTE: Draft language, pending counsel review.
"""
from __future__ import annotations

import hashlib

PAID_TERMS_VERSION = "2026-10-17.paid1"
PAID_TERMS_TITLE = "Paid dossier terms: $1,000 per verified dossier"

PAID_TERMS_PARAGRAPHS = [
    "By registering you confirm you have the right to share the shipment "
    "documents you submit and that they do not contain information you are "
    "prohibited from disclosing.",
    "You will review the automatic redaction before anything is finalized. "
    "Argus Data Labs applies the redaction you approve. After approval, the "
    "redacted documents, annotations, and verification records become the "
    "property of Argus Data Labs and may be used for training, research, and "
    "product development, including use in machine-learning datasets and models.",
    "A dossier is one complete end-to-end shipment document set: at minimum a "
    "commercial invoice, a packing list, and a transport document (bill of "
    "lading or airway bill), plus your annotation of any exception against "
    "the Argus exception taxonomy.",
    "Payment of $1,000 is due only after your dossier passes verification: "
    "a complete document set, your annotation, and a confirmed "
    "cross-verification by a different forwarder. If verification fails you "
    "may fix and resubmit once.",
    "This offer covers the first 15 verified dossiers, in order of "
    "verification. Verified dossiers are paid within 14 days of passing "
    "verification.",
    "These terms are a draft pending counsel review and may be updated; the "
    "version you accept is recorded with your registration.",
]


def paid_terms_text() -> str:
    return "\n\n".join(PAID_TERMS_PARAGRAPHS)


def paid_terms_sha256() -> str:
    return hashlib.sha256(paid_terms_text().encode("utf-8")).hexdigest()
