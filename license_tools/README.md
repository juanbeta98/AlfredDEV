# alfred_solver — Operator Guide

Complete reference for building, licensing, and delivering the `alfred_solver` binary and the AlfredProd customer environment.

---

## How It Works

```
AlfredDEV (internal)
  solver source code + algorithms
  license_tools/ (private key + issuance scripts)
        │
        ├── pyinstaller ──► dist/alfred_solver  (binary, public key baked in)
        │
        └── scripts/build_prod.sh ──► AlfredProd/  (filtered snapshot, no solver source)
                                           bin/alfred_solver  (copy of binary)
                                           license/           (customer places license here)

Customer runs AlfredProd:
  export ALFRED_LICENSE=./license/alfred_license.json
  python main.py
```

The binary uses **offline Ed25519 signed license files**. You hold a private key (never shared) and sign a small JSON file per customer. The binary has the matching public key baked in at build time. On every run, the binary verifies the signature and checks the expiry date — no internet required.

---

## File Map

| File | Purpose |
|---|---|
| `license_tools/generate_keys.py` | One-time keypair generation (passphrase-protected) |
| `license_tools/embed_public_key.py` | Updates the public key and key ID in the binary source |
| `license_tools/issue_license.py` | Issues a signed license file for a customer |
| `license_tools/list_licenses.py` | Audits issued license files; warns on near-expiry |
| `license_tools/issued_licenses.log` | Append-only audit log of every license issued |
| `license_tools/private_key.pem` | **Your private key — never commit, never share** |
| `license_tools/public_key.pem` | Public key — baked into the binary at build time |
| `solver_executable/license_check.py` | License validation module bundled in the binary |
| `solver_executable/main.py` | Binary entry point (`--mode solve` / `--mode probe`) |
| `alfred_solver.spec` | PyInstaller build specification |
| `scripts/build_prod.sh` | Generates AlfredProd from AlfredDEV |

---

## One-Time Setup (do this once per keypair lifetime)

### 1. Generate the keypair

```bash
python license_tools/generate_keys.py
```

Creates:
- `license_tools/private_key.pem` — **keep this secret and backed up safely**
- `license_tools/public_key.pem` — embedded in the binary at build time

> If `private_key.pem` already exists the script refuses to overwrite it. Delete manually only if rotating keys.

### 2. Embed the public key into the binary source

```bash
python license_tools/embed_public_key.py --key-id v1
```

Writes the public key and key version identifier into `solver_executable/license_check.py`.
Must be run before every binary build if the key has changed.
Increment `--key-id` (e.g. `v2`) when rotating keys so version mismatches are flagged.

### 3. Build the binary

```bash
pip install pyinstaller   # once per environment
pyinstaller alfred_solver.spec --distpath dist/
```

Output: `dist/alfred_solver`

The binary now has the public key baked in and will enforce licenses on every invocation.

> The binary must be built on the same OS/architecture as the target machine.
> A macOS build will not run on Linux, and vice versa.

---

## Issuing a License

Run whenever a new customer needs access or an existing one renews:

```bash
python license_tools/issue_license.py \
    --customer "Acme Corp" \
    --expires 2027-03-31 \
    --key-id v1 \
    --out acme_corp_license.json
```

You will be prompted for the private key passphrase.

| Flag | Required | Description |
|---|---|---|
| `--customer` | Yes | Customer name (2–100 printable chars, included in the signed payload) |
| `--expires` | Yes | Expiry date in `YYYY-MM-DD` format |
| `--out` | Yes | Output path for the license JSON file |
| `--key-id` | No | Key version string to embed (e.g. `v1`). Should match what was passed to `embed_public_key.py`. |

Output file format:

```json
{
  "customer": "Acme Corp",
  "expires": "2027-03-31",
  "issued_at": "2026-03-16",
  "key_id": "v1",
  "signature": "..."
}
```

Every issuance is appended to `license_tools/issued_licenses.log` for audit purposes.

Send the `.json` file to the customer. It is not a secret — it cannot be forged or extended without your private key.

---

## Generating AlfredProd

AlfredProd is a filtered, customer-facing snapshot generated from AlfredDEV. Solver source code and algorithms are excluded; the compiled binary is included instead.

### When to regenerate

| What changed | Rebuild binary? | Regenerate AlfredProd? |
|---|---|---|
| Solver / algorithm logic | Yes | Yes |
| Pipeline code (parsing, validation, formatting, etc.) | No | Yes |
| License system (`serde.py`, `license_check.py`) | Yes | Yes |
| Only `request.json` or config | No | No (copy manually if needed) |

### Steps

**If the solver changed** (rebuild binary first):
```bash
cd /path/to/AlfredDEV
pyinstaller alfred_solver.spec --distpath dist/
./scripts/build_prod.sh --output-dir ../AlfredProd --binary dist/alfred_solver
```

**If only pipeline code changed** (no binary rebuild):
```bash
cd /path/to/AlfredDEV
./scripts/build_prod.sh --output-dir ../AlfredProd --binary dist/alfred_solver
```

The script wipes and rebuilds AlfredProd entirely — no manual sync needed.

### Build script options

```
./scripts/build_prod.sh [--output-dir <path>] [--binary <path>] [--skip-binary]

  --output-dir <path>   Where to write AlfredProd. Default: ../AlfredProd
  --binary <path>       Path to the alfred_solver binary. Default: dist/alfred_solver
  --skip-binary         Omit binary installation (useful for testing the Python layer alone)
```

---

## Delivering to the Customer

The customer receives:
1. The AlfredProd directory (zip, shared drive, or git snapshot)
2. The `alfred_solver` binary (already inside `bin/` if generated by the script)
3. A license file issued specifically for them

### Customer setup

```bash
# Place the license file
cp acme_corp_license.json AlfredProd/license/alfred_license.json

# Set the environment variable (add to .env or shell profile)
export ALFRED_LICENSE=./license/alfred_license.json

# Run
cd AlfredProd
python main.py
```

The `license/` directory is gitignored in AlfredProd — license files should never be committed.

---

## Renewing a License

Issue a new license file with a later expiry. No binary rebuild needed.

```bash
python license_tools/issue_license.py \
    --customer "Acme Corp" \
    --expires 2028-03-31 \
    --out acme_corp_license_2028.json
```

Send the new file. The customer replaces their old license file and updates `ALFRED_LICENSE` if the path changed.

---

## Revoking a License

Do not renew. Once the `expires` date passes, the binary refuses to run:

```
LICENSE ERROR: License expired on 2027-03-31. Please contact support to renew.
```

There is no revocation list — expiry is the enforcement mechanism. To revoke before expiry, rotate the keypair (see below), rebuild the binary, and regenerate AlfredProd for all customers.

---

## Key Rotation (if private key is compromised)

Old license files will not work with a binary built against a new key, so all customers must receive a new binary and new license file.

```bash
# 1. Delete old keys
rm license_tools/private_key.pem license_tools/public_key.pem

# 2. Generate new keypair (will prompt for a new passphrase)
python license_tools/generate_keys.py

# 3. Embed new public key with incremented key ID
python license_tools/embed_public_key.py --key-id v2

# 4. Rebuild binary
pyinstaller alfred_solver.spec --distpath dist/

# 5. Re-issue licenses for all active customers
python license_tools/issue_license.py --customer "Acme Corp" --expires 2028-03-31 --key-id v2 --out acme_corp_new.json

# 6. Regenerate and deliver AlfredProd to all customers
./scripts/build_prod.sh --output-dir ../AlfredProd --binary dist/alfred_solver
```

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Bad arguments or missing input/output paths |
| `2` | Input deserialization error |
| `3` | Solver execution error |
| `4` | Output serialization error |
| `5` | **License invalid, expired, or not provided** |

---

## Protecting the Private Key

- `license_tools/private_key.pem` is already in `.gitignore` — never remove this
- The key is passphrase-encrypted on disk. Store both the file and the passphrase in a secure location (password manager, encrypted storage)
- Never share it, never commit it, never put it on a customer's machine
- If in doubt about whether it was exposed, rotate immediately

---

## Auditing Issued Licenses

Every call to `issue_license.py` appends a line to `license_tools/issued_licenses.log`.

To see which licenses are active and check for upcoming expiries:

```bash
# Scan the directory where you keep issued license JSON files
python license_tools/list_licenses.py --dir /path/to/licenses/

# Exit code 0 = all OK, exit code 2 = at least one expires within 30 days
```

This is also useful in a cron job to send renewal reminders.

---

## Emergency Procedures

### If the private key is lost

The private key **cannot be recovered**. There is no backup mechanism in the binary.

Action:
1. Generate a new keypair: `python license_tools/generate_keys.py`
2. Embed new public key with a new key ID: `python license_tools/embed_public_key.py --key-id v2`
3. Rebuild the binary: `pyinstaller alfred_solver.spec --distpath dist/`
4. Re-issue licenses for all active customers with the new `--key-id v2`
5. Regenerate AlfredProd and deliver the new binary + new licenses to all customers

Existing license files signed under the old key stop working the moment customers receive the new binary. Coordinate delivery to avoid downtime.

### If a license file is leaked or a customer must be cut off before expiry

The license system is **expiry-based** — there is no per-license revocation list. This is a deliberate trade-off for offline simplicity (a revocation list embedded in the binary would be stale by the next issuance; one fetched at runtime would break the offline model).

Options in ascending order of disruption:

1. **Near-term expiry (preferred for planned cut-offs):** Issue new licenses for all other customers with short expiries when you have advance notice. The leaking customer's license expires naturally.
2. **Key rotation (immediate, affects everyone):** The only way to invalidate all existing licenses immediately. Follow the Key Rotation procedure above. All customers must receive a new binary and new license simultaneously — plan the delivery window carefully.

Document the trade-off clearly in internal runbooks: key rotation is the offline revocation mechanism.

### If the passphrase is forgotten

The private key PEM file is encrypted with the passphrase. Without the passphrase, the key is inaccessible and cannot be used to sign new licenses. Treat this the same as a lost key — rotate immediately.

---

## Post-Delivery Checklist

Before considering a customer delivery complete:

- [ ] License issued with the correct customer name and expiry
- [ ] `--key-id` matches the key ID embedded in the binary (`_KEY_ID` in `license_check.py`)
- [ ] License file sent to the customer (not committed to any repository)
- [ ] Issuance entry visible in `license_tools/issued_licenses.log`
- [ ] AlfredProd generated from the correct binary version
- [ ] Customer confirmed `ALFRED_LICENSE` environment variable points to the license file
- [ ] Deployment validated: `python validate_deployment.py` exits 0 on the customer's machine
- [ ] Expiry date calendared for a renewal reminder (~30 days before)
