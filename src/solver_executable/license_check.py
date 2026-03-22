"""
license_check.py — Offline license validation for the alfred_solver binary.

The public key is embedded as a base64-encoded DER constant (_PUBLIC_KEY_B64).
It is updated by running:  python license_tools/embed_public_key.py
and baked into the binary at PyInstaller build time.

License file format (JSON):
    {
      "customer":  "Acme Corp",
      "expires":   "2026-12-31",
      "issued_at": "2026-03-16",
      "signature": "<base64-ed25519-signature>",
      "key_id":    "v1"           (optional — used to detect key version mismatches)
    }

The signature covers the canonical payload:
    {"customer":"...","expires":"...","issued_at":"..."}
    (sorted keys, no whitespace — key_id is NOT part of the signed payload)

Exit code 5 is used for all license failures, keeping it distinct from the
solver's own exit codes (1–4).
"""
from __future__ import annotations

import base64
import json
import logging as _logging
import sys
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Embedded constants — updated by license_tools/embed_public_key.py
# ---------------------------------------------------------------------------
_PUBLIC_KEY_B64 = "MCowBQYDK2VwAyEAK23XYx2IxYyqS8oDlAKA8Z9wqH1PX1mRnDPMtfOsyxE="
_KEY_ID = "v1"  # updated alongside _PUBLIC_KEY_B64 by embed_public_key.py


def _load_public_key():
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    der = base64.b64decode(_PUBLIC_KEY_B64)
    return load_der_public_key(der)


def _canonical_payload(customer: str, expires: str, issued_at: str) -> bytes:
    return json.dumps(
        {"customer": customer, "expires": expires, "issued_at": issued_at},
        sort_keys=True, separators=(",", ":"),
    ).encode()


def check_license(license_path: str) -> None:
    """
    Validate the license file at license_path.

    Exits with code 5 and a human-readable message on any failure:
      - File not found or unreadable
      - Invalid JSON or missing fields
      - Signature verification failure (tampered or wrong key)
      - License expired
    """
    def _fail(msg: str) -> None:
        print(f"LICENSE ERROR: {msg}", file=sys.stderr)
        sys.exit(5)

    # 1. Read file
    path = Path(license_path)
    if not path.exists():
        _fail(f"License file not found: {license_path}")

    try:
        data = json.loads(path.read_text())
    except Exception:
        _fail(f"License file is not valid JSON: {license_path}")

    # 2. Extract fields
    customer  = data.get("customer")
    expires   = data.get("expires")
    issued_at = data.get("issued_at")
    signature = data.get("signature")

    if not all([customer, expires, issued_at, signature]):
        _fail("License file is missing required fields (customer, expires, issued_at, signature).")

    # 2b. Optional key_id check (non-fatal — warn only; signature will confirm)
    key_id = data.get("key_id")
    if key_id is not None and _KEY_ID and key_id != _KEY_ID:
        print(
            f"LICENSE WARNING: key_id mismatch "
            f"(license={key_id!r}, binary={_KEY_ID!r}). "
            "This license may have been issued for a different key version.",
            file=sys.stderr,
        )

    # 3. Verify signature
    if not _PUBLIC_KEY_B64:
        _fail("No public key embedded in this binary. Rebuild with embed_public_key.py.")

    try:
        pub_key = _load_public_key()
    except Exception as exc:
        _logging.debug("license_check: failed to load embedded public key: %s", exc)
        _fail("No valid public key embedded in this binary. Rebuild with embed_public_key.py.")

    try:
        sig_bytes = base64.b64decode(signature, validate=True)
    except Exception as exc:
        _logging.debug("license_check: base64 decode of signature failed: %s", exc)
        _fail("License signature is invalid (not valid base64). The file may have been tampered with.")

    try:
        payload = _canonical_payload(customer, expires, issued_at)
        pub_key.verify(sig_bytes, payload)
    except Exception as exc:
        _logging.debug("license_check: Ed25519 verification failed: %s", exc)
        _fail("License signature is invalid. The file may have been tampered with.")

    # 4. Check expiry
    try:
        expiry_date = datetime.strptime(expires, "%Y-%m-%d").date()
    except ValueError:
        _fail(f"License expiry date is malformed: {expires!r}")

    if date.today() > expiry_date:
        _fail(f"License expired on {expires}. Please contact support to renew.")
