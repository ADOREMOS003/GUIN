# GUIN Architecture

## Core Components

- **CLI (`guin`)**  
  Entry point for run/validate/replay/diff/serve operations.

- **MCP Server (`guin.mcp_server`)**  
  FastMCP tool registry and execution layer for containerized neuroimaging tools.

- **Planner (`guin.planner`)**  
  Converts natural language instructions into ordered MCP tool-call plans.

- **RAG (`guin.rag`)**  
  Indexes docs and CLI help, retrieves context, and supports tool selection.

- **Provenance (`guin.provenance`)**  
  Captures W3C PROV-O JSON-LD session records and provides diff/verification tools.

- **API + Frontend (`guin.api`, `frontend/`)**  
  REST/WS interface and web UI for status, runs, tools, validation, and provenance.

## Data Flow

1. User submits instruction (`guin run` or `/api/v1/run`)
2. Planner builds executable tool-call plan from:
   - MCP tool schemas
   - BIDS layout context
   - output directory constraints
3. Execution invokes tools sequentially via MCP/Apptainer wrappers
4. Provenance tracker records:
   - instruction
   - planner prompt/response
   - tool invocations + inputs/outputs
   - execution metadata and hashes
5. Outputs + provenance are saved to derivatives paths

## Reproducibility Principles

- No hidden state in planning/execution inputs
- Configuration centralized in `~/.guin/config.yaml`
- Dependency lockfile tracked (`uv.lock`)
- Provenance-first artifact model (JSON-LD + hash verification)
