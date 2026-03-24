# AlfredDEV — Releases

This folder tracks all deliveries of AlfredProd to customers.

---

## Folder structure

```
releases/          ← metadata only (committed to git)
  log.csv          ← one row per delivery (source of truth)
  licenses/        ← PROD license files, named by release ID

builds/            ← build artifacts (gitignored — share out-of-band)
  PROD/            ← production zips, e.g. R1_ALFRED.zip
  DEV/             ← dev zips, e.g. RDEV_ALFRED_20260324_1200.zip
```

---

## Release naming convention

| Build type | Format | Example |
|---|---|---|
| PROD | `R{N}_{CODE}` | `R1_CLIENTE` |
| DEV | `RDEV_{CODE}_{YYYYMMDD_HHMM}` | `RDEV_CLIENTE_20260322_1430` |

- `R{N}` is a global sequential counter across all customers. Only PROD builds increment it.
- `{CODE}` is a short uppercase identifier for the customer (no spaces).
- DEV builds use a datetime suffix because multiple can be generated per day.
- PROD builds have no date — the number already orders them unambiguously.

---

## log.csv schema

| Column | Description |
|---|---|
| `type` | `PROD` or `DEV` |
| `release_id` | Full release ID (e.g. `R1_CLIENTE`) |
| `customer` | Customer name as passed to the license issuer |
| `issued_at` | Date the delivery was created (YYYY-MM-DD) |
| `expires` | License expiry date (PROD only; blank for DEV) |
| `binary_hash` | SHA-256 of `bin/alfred_solver` at time of packaging |
| `notes` | Free-text notes |

---

## Generating a delivery

Use `scripts/deliver.sh`. It handles building, licensing, zipping, and logging in one command.

**PROD delivery:**
```bash
./scripts/deliver.sh \
  --type prod \
  --customer "Cliente S.A." \
  --code CLIENTE \
  --expires 2026-12-31 \
  --notes "Initial delivery"
```

**DEV delivery (no license required):**
```bash
./scripts/deliver.sh \
  --type dev \
  --customer "Cliente S.A." \
  --code CLIENTE \
  --notes "Testing build for QA"
```

Run `./scripts/deliver.sh --help` for all options.

---

## Git workflow for PROD deliveries

```
main        ← stable code only; direct commits forbidden
develop     ← ongoing work; default working branch
release/R1  ← short-lived branch cut from develop before each PROD delivery
```

**Steps for a PROD delivery:**

```bash
# 1. Cut release branch
git checkout develop && git pull
git checkout -b release/R1

# 2. Test, fix minor issues (no new features on release branches)

# 3. Generate PROD delivery
./scripts/deliver.sh --type prod --customer "..." --code CLIENTE --expires 2026-12-31

# 4. Merge to main
git checkout main && git merge release/R1

# 5. Merge back to develop (preserve any fixes)
git checkout develop && git merge release/R1

# 6. Delete release branch
git branch -d release/R1
```

DEV builds can be generated from any branch without ceremony.

---

## License files

`releases/licenses/` contains the signed license JSON files for each PROD delivery.
- These files are **not secret** — they cannot be forged without our private key.
- They ARE committed to git as an audit trail.
- The private key lives in `license_tools/private_key.pem` (encrypted, never committed).
