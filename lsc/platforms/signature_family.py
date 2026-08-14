"""Signature-family fingerprints for single-connect signed CDNs."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlparse


def signature_family_id(url: str) -> str:
    """Stable non-secret id shared by CDN lines that reuse one anti-code."""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        query = parse_qs(urlparse(text).query, keep_blank_values=False)
    except Exception:
        return ""
    secret = ""
    ws_time = ""
    for key, values in query.items():
        lowered = key.lower()
        if not values:
            continue
        if lowered == "wssecret":
            secret = str(values[0])
        elif lowered == "wstime":
            ws_time = str(values[0])
    if secret or ws_time:
        material = f"{secret}|{ws_time}"
    else:
        material = "&".join(
            f"{key}={values[0]}"
            for key, values in sorted(query.items())
            if key.lower() not in {"codec", "ctype", "fs"}
        )
    if not material:
        return ""
    return hashlib.sha256(material.encode("utf-8", "ignore")).hexdigest()[:16]
