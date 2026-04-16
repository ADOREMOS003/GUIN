"""BIDS derivative outputs and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BIDSDerivativeResult:
    """Result payload for neuroimaging tools that write BIDS derivatives."""

    output_path: Path
    """Root folder for this pipeline under the derivatives layout (e.g. ``.../fmriprep``)."""

    provenance_hash: str
    """SHA-256 hex digest of the source ``dataset_description.json`` (or empty if missing)."""

    execution_log: str
    """Captured stdout/stderr plus any GUIN diagnostics."""

    container_digest: str
    """``apptainer inspect`` text for the executed SIF (or error message)."""

    wall_clock_seconds: float
    """Wall-clock runtime for the container invocation."""

    iqm_summary: dict[str, Any] | None = None
    """MRIQC-only: aggregated IQM statistics parsed from group-level TSV/CSV tables."""
