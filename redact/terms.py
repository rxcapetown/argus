"""Contributor upload terms — versioned, hashed, logged on acceptance.

Short plain-language terms shown in a popup before upload. Every acceptance is
recorded (timestamp, contributor, terms version + hash) in the job folder, and
the version is stamped into the redaction audit log.

NOTE: Draft language, pending counsel review — same standing as the
contribution agreement (v0.1).
"""
from __future__ import annotations

import hashlib

TERMS_VERSION = "2026-10-07.v1"
TERMS_TITLE = "Document contribution terms"

TERMS_PARAGRAPHS = [
    "By uploading a document you confirm you have the right to share it and "
    "that it does not contain information you are prohibited from disclosing.",
    "Any document you upload — including its redacted derivatives, annotations, "
    "and verification records — becomes the property of Argus Logistics AI and "
    "its subsidiaries, and may be used for training, research, and product "
    "development, including use in machine-learning datasets and models.",
    "You will have the chance to review the automatic redaction before anything "
    "is finalized. Argus Logistics AI applies the redaction you approve, but "
    "you remain responsible for checking that no identifying information you "
    "wish to withhold remains in the approved version.",
    "These terms are a draft pending counsel review and may be updated; the "
    "version you accept is recorded with your upload.",
]


def terms_text() -> str:
    return "\n\n".join(TERMS_PARAGRAPHS)


def terms_sha256() -> str:
    return hashlib.sha256(terms_text().encode("utf-8")).hexdigest()
