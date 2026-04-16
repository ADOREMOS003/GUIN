"""GUIN MCP server built on FastMCP (SSE default, stdio fallback)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    if raw:
        return Path(raw).expanduser()
    return default


@dataclass(frozen=True)
class GuinMCPConfig:
    """Paths used by the GUIN MCP server (override via environment variables)."""

    CONTAINER_DIR: Path
    """Local Apptainer/Singularity image cache and Neurodesk manifest cache."""

    BIDS_VALIDATOR_PATH: Path
    """Executable or script that runs the BIDS validator (e.g. ``bids-validator`` or ``npx``)."""

    APPTAINER_BINARY: Path
    """Apptainer/Singularity client used for ``exec`` (never raw subprocess in tools)."""


CONFIG: Final[GuinMCPConfig] = GuinMCPConfig(
    CONTAINER_DIR=_env_path(
        "GUIN_CONTAINER_DIR",
        Path.home() / ".cache" / "guin" / "containers",
    ),
    BIDS_VALIDATOR_PATH=_env_path("GUIN_BIDS_VALIDATOR_PATH", Path("bids-validator")),
    APPTAINER_BINARY=_env_path("GUIN_APPTAINER_BINARY", Path("apptainer")),
)

# Typical Neurodesk / CVMFS layout (try in order alongside local manifests).
_CVMFS_MANIFEST_CANDIDATES: Final[tuple[Path, ...]] = (
    Path("/cvmfs/neurodesk.ardc.edu.au/neurodesktop/neurodesk/containers.json"),
    Path("/cvmfs/neurodesk.ardc.edu.au/neurodesktop/containers/vnm/containers.json"),
)

_LOCAL_MANIFEST_NAMES: Final[tuple[str, ...]] = (
    "neurodesk_containers.json",
    "containers.json",
    "manifest.json",
)


# ---------------------------------------------------------------------------
# Apptainer execution (no raw subprocess in container tools — use this helper)
# ---------------------------------------------------------------------------


async def run_container(
    container_sif: Path,
    command: list[str],
    bind_paths: list[tuple[Path, Path]] | None = None,
    *,
    timeout_seconds: float | None = 300.0,
    container_env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    """Run ``apptainer exec`` with optional bind mounts; returns (stdout, stderr, exit_code).

    Uses :func:`asyncio.create_subprocess_exec` (not :func:`subprocess.run`) for container runs.
    If *timeout_seconds* is ``None``, wait indefinitely for the process (for long jobs like fMRIPrep).
    *container_env* entries are passed as ``--env KEY=VAL`` to Apptainer so they apply inside the image.
    """
    if not container_sif.is_file():
        raise FileNotFoundError(f"Container image not found: {container_sif}")

    apptainer = str(CONFIG.APPTAINER_BINARY)
    argv: list[str] = [apptainer, "exec"]
    if container_env:
        for key, val in container_env.items():
            argv.extend(["--env", f"{key}={val}"])
    if bind_paths:
        for host_path, container_path in bind_paths:
            argv.extend(
                [
                    "--bind",
                    f"{host_path.resolve()}:{container_path}",
                ]
            )
    argv.append(str(container_sif.resolve()))
    argv.extend(command)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Apptainer binary not found or not executable: {apptainer}"
        ) from exc

    try:
        if timeout_seconds is None:
            stdout_b, stderr_b = await proc.communicate()
        else:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"apptainer exec exceeded {timeout_seconds}s for {container_sif}"
        ) from exc

    out = stdout_b.decode(errors="replace")
    err = stderr_b.decode(errors="replace")
    code = int(proc.returncode) if proc.returncode is not None else -1
    return out, err, code


def _resolve_container_sif(container_name: str) -> Path:
    """Resolve *container_name* to a ``.sif`` path under :attr:`CONFIG.CONTAINER_DIR`."""
    name = container_name.strip()
    if not name:
        raise ValueError("container_name must be non-empty")

    base = CONFIG.CONTAINER_DIR.expanduser().resolve()
    if name.endswith(".sif"):
        candidate = Path(name) if Path(name).is_absolute() else base / name
    else:
        candidate = base / f"{name}.sif"

    if candidate.is_file():
        return candidate

    # Fuzzy: stem match (e.g. ``fsl_6.0.5``)
    matches = sorted(base.glob(f"*{name}*.sif"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise FileNotFoundError(
            f"Ambiguous container name {container_name!r}; matches: "
            + ", ".join(m.name for m in matches[:10])
        )

    raise FileNotFoundError(
        f"No .sif found for {container_name!r} under {base}. "
        "Place images in CONTAINER_DIR or set GUIN_CONTAINER_DIR."
    )


def _parse_manifest_payload(data: Any) -> list[dict[str, Any]]:
    """Normalize JSON manifest into ``[{name, version, modalities}]`` entries."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "containers" in data:
        items = data["containers"]
    elif isinstance(data, dict) and "images" in data:
        items = data["images"]
    else:
        return []

    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", row.get("container", row.get("id", ""))))
        version = str(row.get("version", row.get("tag", "unknown")))
        modalities = row.get("modalities", row.get("modalities_supported", []))
        if not isinstance(modalities, list):
            modalities = []
        modalities_str = [str(m) for m in modalities]
        if name:
            out.append(
                {
                    "name": name,
                    "version": version,
                    "modalities": modalities_str,
                }
            )
    return out


def _load_manifest_from_path(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read manifest %s: %s", path, exc)
        return None
    parsed = _parse_manifest_payload(data)
    return parsed if parsed else None


def _containers_from_sif_scan(container_dir: Path) -> list[dict[str, Any]]:
    """Derive minimal metadata from ``*.sif`` filenames when no manifest exists."""
    out: list[dict[str, Any]] = []
    for sif in sorted(container_dir.glob("*.sif")):
        stem = sif.stem
        if "_" in stem:
            name, version = stem.split("_", 1)
        else:
            name, version = stem, "unknown"
        out.append({"name": name, "version": version, "modalities": []})
    return out


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp = FastMCP("guin-neuroimaging")


@mcp.tool()
def validate_bids(dataset_path: str) -> dict[str, Any]:
    """Validate a BIDS dataset directory using the bids-validator CLI (subprocess).

    Runs the validator with JSON output and returns validity plus error and warning messages.
    Configure the executable via ``GUIN_BIDS_VALIDATOR_PATH`` / :attr:`CONFIG.BIDS_VALIDATOR_PATH`.
    """
    root = Path(dataset_path).expanduser().resolve()
    if not root.is_dir():
        return {
            "valid": False,
            "errors": [f"Not a directory: {root}"],
            "warnings": [],
        }

    validator = CONFIG.BIDS_VALIDATOR_PATH
    cmd: list[str]
    exe = str(validator)
    if exe.endswith("npx") or Path(exe).name == "npx":
        cmd = [exe, "--yes", "@bids/validator", str(root), "--json"]
    else:
        cmd = [exe, str(root), "--json"]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError:
        return {
            "valid": False,
            "errors": [
                f"bids-validator not found: {validator}. "
                "Install it or set GUIN_BIDS_VALIDATOR_PATH."
            ],
            "warnings": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "valid": False,
            "errors": ["bids-validator timed out"],
            "warnings": [],
        }

    raw_out = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    try:
        payload = json.loads(completed.stdout or raw_out)
    except json.JSONDecodeError:
        return {
            "valid": False,
            "errors": [
                "bids-validator did not return JSON. "
                f"exit={completed.returncode} stdout/stderr snippet: {raw_out[:2000]!r}"
            ],
            "warnings": [],
        }

    issues = payload.get("issues", [])
    if not isinstance(issues, list):
        issues = []

    errors: list[str] = []
    warnings: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        sev = str(issue.get("severity", "")).lower()
        msg = issue.get("message") or issue.get("reason") or json.dumps(issue)
        line = f"{issue.get('path', '')}: {msg}".strip(": ")
        if sev == "error":
            errors.append(line)
        elif sev == "warning":
            warnings.append(line)

    valid = completed.returncode == 0 and not errors
    return {"valid": valid, "errors": errors, "warnings": warnings}


@mcp.tool()
def list_containers() -> list[dict[str, Any]]:
    """List Neurodesk-style containers from CVMFS manifest, local cache, or ``*.sif`` scan.

    Tries, in order: CVMFS Neurodesk JSON (if mounted), then JSON files in
    :attr:`CONFIG.CONTAINER_DIR`, then infers entries from ``*.sif`` files in that directory.
    """
    # 1) CVMFS
    for candidate in _CVMFS_MANIFEST_CANDIDATES:
        loaded = _load_manifest_from_path(candidate)
        if loaded:
            logger.info("Loaded container manifest from CVMFS: %s", candidate)
            return loaded

    # 2) Local manifest files next to cached images
    base = CONFIG.CONTAINER_DIR.expanduser()
    base.mkdir(parents=True, exist_ok=True)
    for fname in _LOCAL_MANIFEST_NAMES:
        loaded = _load_manifest_from_path(base / fname)
        if loaded:
            logger.info("Loaded container manifest from %s", base / fname)
            return loaded

    # 3) Scan SIF files
    scanned = _containers_from_sif_scan(base)
    if scanned:
        logger.info("Derived %d container(s) from *.sif in %s", len(scanned), base)
    else:
        logger.warning(
            "No Neurodesk manifest or .sif files found under %s; returning empty list.",
            base,
        )
    return scanned


@mcp.tool()
async def get_tool_help(tool_name: str, container_name: str) -> str:
    """Run ``<tool> --help`` inside the given Apptainer image and return help text.

    Uses :func:`run_container` (async subprocess) — not :func:`subprocess.run`.
    """
    sif = _resolve_container_sif(container_name)
    stdout, stderr, code = await run_container(
        sif,
        [tool_name, "--help"],
        bind_paths=None,
        timeout_seconds=120.0,
    )
    text = stdout if stdout.strip() else stderr
    if not text.strip():
        return (
            f"(no stdout/stderr; exit code {code}) "
            f"stdout={stdout!r} stderr={stderr!r}"
        )
    if code != 0 and not stdout.strip():
        return f"{text}\n(exit code {code})"
    return text.rstrip()


import guin.mcp_server.tools  # noqa: E402, F401 — register tools (e.g. run_fmriprep)


def main() -> None:
    """Run the MCP server: SSE by default; stdio if ``GUIN_MCP_TRANSPORT=stdio`` or SSE fails."""
    logging.basicConfig(level=os.environ.get("GUIN_LOG_LEVEL", "INFO"))

    transport = os.environ.get("GUIN_MCP_TRANSPORT", "sse").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if transport != "sse":
        logger.warning("Unknown GUIN_MCP_TRANSPORT=%r; using sse", transport)

    try:
        mcp.run(transport="sse")
    except OSError as exc:
        logger.warning("SSE transport failed (%s); falling back to stdio", exc)
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
