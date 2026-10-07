"""Redaction execution: true PDF redaction, review previews, audit logs."""
from __future__ import annotations

import base64
import hashlib
import io
from datetime import datetime, timezone

import pymupdf  # PyMuPDF

from .detect import Finding


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def apply_redactions(pdf_bytes: bytes, findings: list[Finding]) -> bytes:
    """Burn redactions into a copy of the PDF.

    Uses native redaction annotations + apply_redactions(), which removes the
    underlying text from the content stream. The result cannot be recovered by
    copy-paste or text extraction.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    for f in findings:
        if not f.selected:
            continue
        page = doc[f.page]
        x0, y0, x1, y1 = f.rect
        page.add_redact_annot(pymupdf.Rect(x0, y0, x1, y1), fill=(0, 0, 0))
    for page in doc:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    out = doc.tobytes()
    doc.close()
    return out


def render_preview(pdf_bytes: bytes, findings: list[Finding], dpi: int = 110) -> list[str]:
    """Render pages as base64 PNGs with proposed redactions drawn as red boxes.

    The underlying PDF is untouched; this is only for the review screen.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    images = []
    for page_no, page in enumerate(doc):
        shape = page.new_shape()
        for f in findings:
            if f.page == page_no and f.selected:
                x0, y0, x1, y1 = f.rect
                shape.draw_rect(pymupdf.Rect(x0, y0, x1, y1))
                shape.finish(color=(0.85, 0.1, 0.1), fill=(0.85, 0.1, 0.1),
                             fill_opacity=0.35, width=1.2)
        shape.commit(overlay=True)
        pix = page.get_pixmap(dpi=dpi)
        buf = io.BytesIO(pix.tobytes("png"))
        images.append(base64.b64encode(buf.getvalue()).decode())
    doc.close()
    return images


def extract_text(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def audit_log(*, job_id: str, filename: str, original: bytes, redacted: bytes,
              findings: list[Finding], contributor_code: str) -> dict:
    """Provenance record. Counts and kinds only — never redacted values."""
    applied = [f for f in findings if f.selected]
    by_kind: dict[str, int] = {}
    for f in applied:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    return {
        "job_id": job_id,
        "tool": "argus-redact/0.1",
        "contributor_code": contributor_code,
        "source_filename": filename,
        "original_sha256": sha256(original),
        "redacted_sha256": sha256(redacted),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entities_detected": len(findings),
        "entities_redacted": len(applied),
        "entities_restored_by_contributor": len(findings) - len(applied),
        "redaction_kinds": by_kind,
        "note": ("True redaction: removed from the PDF content stream. "
                 "Values are never stored in this log."),
    }
