# GUIN
GUIN is a neuroimaging pipeline that processes OpenNeuro data to produce skull-stripped NIfTI outputs with integrated PROV-O provenance, enabling reproducible, transparent, and portable analysis workflows.

GUIN is an MCP-based, provenance-aware neuroimaging workflow platform for BIDS/OpenNeuro datasets.

It combines:
- natural-language planning for neuroimaging workflows,
- executable MCP tool orchestration,
- and W3C PROV-O JSON-LD provenance tracking for reproducibility audits.

## Current Status

GUIN is in active development.

- ✅ MCP server with registered neuroimaging tools
- ✅ LLM-backed planning with deterministic fallback
- ✅ provenance tracking + diff utilities
- ✅ web API + frontend scaffold
- 🔄 production-hardening and benchmarking

## Repository Layout

```text
guin/
├── pyproject.toml
├── uv.lock
├── src/guin/
│   ├── cli/
│   ├── mcp_server/
│   ├── planner/
│   ├── provenance/
│   ├── rag/
│   └── api/
├── tests/
├── examples/
└── docs/
```

## Installation

```bash
uv sync
```

Or editable install:

```bash
pip install -e .
```

## CLI Quick Start

```bash
guin run "skull strip the T1w image for sub-01 using FSL BET" \
  --bids-dir /path/to/bids_dataset \
  --output-dir /path/to/output \
  --dry-run
```

Useful commands:

- `guin tools` - list registered MCP tools and schemas
- `guin validate <dataset_path>` - run BIDS validation
- `guin replay <provenance.jsonld>` - replay recorded workflow
- `guin diff <record_a.jsonld> <record_b.jsonld>` - compare provenance
- `guin serve --port 8080` - run FastAPI + frontend host

## Configuration

User config lives at:

`~/.guin/config.yaml`

Supported keys:
- `container_dir`
- `model`
- `api_key`
- `bids_validator_path`
- `apptainer_binary`

## Reproducibility

GUIN emphasizes reproducibility through:
- pinned dependency lockfile (`uv.lock`),
- config-driven execution,
- container-based tool invocation,
- provenance capture in PROV-O JSON-LD.

See `docs/architecture.md` for the system overview.
