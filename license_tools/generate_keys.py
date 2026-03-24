"""
generate_keys.py — One-time Ed25519 keypair generation.

Run once from the repo root:
    python license_tools/generate_keys.py

Outputs:
    license_tools/private_key.pem  — Keep secret, never commit, used to sign licenses.
    license_tools/public_key.pem   — Embed in the binary (copied into license_check.py).

WARNING: If you regenerate the keys, all previously issued licenses become invalid
and the binary must be rebuilt with the new public key.
"""
import getpass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
)

_HERE = Path(__file__).parent


def main() -> None:
    priv_path = _HERE / "private_key.pem"
    pub_path  = _HERE / "public_key.pem"

    if priv_path.exists():
        print(f"ERROR: {priv_path} already exists. Delete it manually to regenerate.")
        raise SystemExit(1)

    passphrase = getpass.getpass("Enter passphrase for private key: ")
    if not passphrase:
        print("ERROR: Passphrase must not be empty.")
        raise SystemExit(1)
    confirm = getpass.getpass("Confirm passphrase: ")
    if passphrase != confirm:
        print("ERROR: Passphrases do not match.")
        raise SystemExit(1)

    private_key = Ed25519PrivateKey.generate()
    public_key  = private_key.public_key()

    priv_path.write_bytes(
        private_key.private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            BestAvailableEncryption(passphrase.encode()),
        )
    )
    pub_path.write_bytes(
        public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )

    print(f"Private key written to: {priv_path}")
    print(f"Public key  written to: {pub_path}")
    print()
    print("Next steps:")
    print("  1. Add license_tools/private_key.pem to .gitignore — never commit it.")
    print("  2. Run license_tools/embed_public_key.py to update solver_executable/license_check.py.")
    print("  3. The passphrase will be required each time you run issue_license.py.")


if __name__ == "__main__":
    main()
