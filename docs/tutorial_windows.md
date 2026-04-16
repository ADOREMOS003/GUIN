# GUIN Windows Tutorial

This tutorial is validated for PowerShell on Windows and uses `uv` for dependency management.

## Prerequisites

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Node.js (for BIDS Validator CLI)

## 1) Clone and install

```powershell
git clone https://github.com/ADOREMOS003/GUIN.git
cd GUIN
uv sync
```

## 2) Configure a valid BIDS validator path

On this Windows setup, a valid executable path is:

`C:\Users\iador\AppData\Roaming\npm\bids-validator.cmd`

Create/update your GUIN user config:

```powershell
mkdir $HOME\.guin -Force
@"
bids_validator_path: C:/Users/iador/AppData/Roaming/npm/bids-validator.cmd
"@ | Set-Content $HOME\.guin\config.yaml
```

Optional check:

```powershell
uv run guin --help
uv run guin tools
```

## 3) Run a safe dry-run smoke test

This verifies planning + provenance generation without executing containers:

```powershell
uv run guin run "skull strip the T1w image for sub-01 using FSL BET" `
  --bids-dir . `
  --output-dir .\.guin_smoketest_out `
  --dry-run `
  --skip-bids-validation
```

Expected result:

- command exits successfully
- generated plan is printed
- provenance JSON-LD appears under:
  - `.guin_smoketest_out\derivatives\guin\provenance\`

## 4) Validate a real BIDS dataset

Use an actual BIDS dataset directory:

```powershell
uv run guin validate D:\path\to\bids_dataset
```

## 5) Execute a real run

```powershell
uv run guin run "skull strip the T1w image for sub-01 using FSL BET" `
  --bids-dir D:\path\to\bids_dataset `
  --output-dir D:\path\to\guin_outputs
```

## 6) Start the web interface (optional)

```powershell
uv run guin serve --port 8080
```

Then open: <http://localhost:8080>
