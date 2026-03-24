"""
embed_public_key.py — Update the public key and key ID constants in license_check.py.

Run this after generate_keys.py (or after re-generating keys):
    python license_tools/embed_public_key.py [--key-id v1]

It reads license_tools/public_key.pem, writes the base64-encoded DER bytes
into _PUBLIC_KEY_B64 and the provided key ID into _KEY_ID inside
solver_executable/license_check.py.
Then rebuild the binary with PyInstaller to bake the new values in.
"""
import argparse
import base64
import re
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.primitives.serialization import load_pem_public_key

_HERE       = Path(__file__).parent
_REPO_ROOT  = _HERE.parent
_PUB_PEM    = _HERE / "public_key.pem"
_CHECK_PY   = _REPO_ROOT / "src" / "solver_executable" / "license_check.py"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed the public key (and optional key ID) into license_check.py."
    )
    parser.add_argument(
        "--key-id", default="v1",
        help="Key version identifier to embed as _KEY_ID (default: 'v1').",
    )
    args = parser.parse_args()

    if not _PUB_PEM.exists():
        print(f"ERROR: {_PUB_PEM} not found. Run generate_keys.py first.")
        raise SystemExit(1)

    pub_key = load_pem_public_key(_PUB_PEM.read_bytes())
    der_b64 = base64.b64encode(
        pub_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode()

    source = _CHECK_PY.read_text()

    # Update _PUBLIC_KEY_B64
    new_source = re.sub(
        r'_PUBLIC_KEY_B64\s*=\s*"[^"]*"',
        f'_PUBLIC_KEY_B64 = "{der_b64}"',
        source,
    )
    if new_source == source:
        print('ERROR: Could not find _PUBLIC_KEY_B64 = "..." in license_check.py')
        raise SystemExit(1)

    # Update _KEY_ID
    new_source2 = re.sub(
        r'_KEY_ID\s*=\s*"[^"]*"',
        f'_KEY_ID = "{args.key_id}"',
        new_source,
    )
    if new_source2 == new_source and not re.search(r'_KEY_ID\s*=\s*"[^"]*"', new_source):
        print('ERROR: Could not find _KEY_ID = "..." in license_check.py')
        raise SystemExit(1)

    _CHECK_PY.write_text(new_source2)
    print(f"Updated _PUBLIC_KEY_B64 and _KEY_ID={args.key_id!r} in {_CHECK_PY}")
    print("Rebuild the binary with:  pyinstaller alfred_solver.spec --distpath dist/")


if __name__ == "__main__":
    main()
