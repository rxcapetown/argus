"""Redaction pipeline for contributor document uploads.

Flow: Upload -> detect sensitive entities -> forwarder reviews side-by-side
(original vs proposed redactions, per-item toggles) -> confirm -> true-redacted
PDF + audit log. Redaction removes text from the PDF content stream; it is not
an overlay. The audit log records *what kinds* of entities were removed and how
many, never their values.
"""
