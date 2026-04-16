"""MRIQC MCP tool (Apptainer-backed)."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import logging
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

from guin.models.derivatives import BIDSDerivativeResult
from guin.mcp_server.server import CONFIG, _resolve_container_sif, mcp, run_container, validate_bids

logger = logging.getLogger(__name__)

_BIDS_MOUNT = Path("/data/bids")
_OUTPUT_MOUNT = Path("/data/out")
_TEMPLATEFLOW_MOUNT = Path("/templateflow")
_WORK_MOUNT = Path("/data/out/mriqc_work")

# MRIQC 24.x modality suffixes (nipreps; case-insensitive input, canonical output)
_MODALITY_CANON: dict[str, str] = {
    "t1w": "T1w",
    "t2w": "T2w",
    "bold": "bold",
    "dwi": "dwi",
    "flair": "flair",
    "pet": "pet",
    "asl": "asl",
    "fmap": "fmap",
    "perf": "perf",
    "inv1": "inv1",
    "inv2": "inv2",
}


def _templateflow_home_host() -> Path:
    return Path(
        os.environ.get("TEMPLATEFLOW_HOME", Path.home() / ".cache" / "templateflow")
    ).expanduser()


def _normalize_participant_label(label: str) -> str:
    s = label.strip()
    if s.lower().startswith("sub-"):
        return s[4:]
    return s


def _provenance_hash(bids_dir: Path) -> str:
    desc = bids_dir / "dataset_description.json"
    if not desc.is_file():
        return ""
    return hashlib.sha256(desc.read_bytes()).hexdigest()


async def _apptainer_inspect_digest(container_sif: Path) -> str:
    argv = [str(CONFIG.APPTAINER_BINARY), "inspect", str(container_sif.resolve())]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return f"(apptainer not found for inspect: {exc})"

    out_b, err_b = await proc.communicate()
    text = (out_b or b"").decode(errors="replace").strip()
    err = (err_b or b"").decode(errors="replace").strip()
    if err and not text:
        return err
    return text or err or "(empty inspect output)"


def _mriqc_container_sif() -> Path:
    """Resolve MRIQC SIF; default image tag ``nipreps/mriqc:24.0.2`` → cached ``mriqc-24.0.2.sif``."""
    override = os.environ.get("GUIN_MRIQC_SIF")
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(f"GUIN_MRIQC_SIF is not a file: {p}")
    name = os.environ.get("GUIN_MRIQC_CONTAINER", "mriqc-24.0.2")
    return _resolve_container_sif(name)


def _normalize_modalities(modalities: list[str]) -> list[str]:
    out: list[str] = []
    for m in modalities:
        key = m.strip().lower()
        if key not in _MODALITY_CANON:
            raise ValueError(
                f"Unsupported modality {m!r}; allowed: {sorted(_MODALITY_CANON.keys())}"
            )
        canon = _MODALITY_CANON[key]
        if canon not in out:
            out.append(canon)
    return out


def _parse_float_cell(cell: str) -> float | None:
    s = cell.strip()
    if not s or s.lower() in {"n/a", "nan", "none"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _column_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "n": float(len(values)),
        "mean": float(statistics.mean(values)),
        "std": float(statistics.stdev(values)) if len(values) > 1 else 0.0,
        "min": float(min(values)),
        "max": float(max(values)),
    }


def _summarize_iqm_tables(output_root: Path, *, max_rows: int = 5000) -> dict[str, Any]:
    """Parse MRIQC ``group_*.tsv`` / ``group_*.csv`` and return per-column summary stats."""
    summary: dict[str, Any] = {"tables": {}, "files_found": []}
    if not output_root.is_dir():
        return summary

    patterns = ("group_*.tsv", "group_*.csv", "Group_*.tsv", "Group_*.csv")
    files: list[Path] = []
    for pat in patterns:
        files.extend(sorted(output_root.glob(pat)))
    # de-dupe
    files = sorted({f.resolve() for f in files})

    for fp in files:
        summary["files_found"].append(str(fp.name))
        table_key = fp.name
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            summary["tables"][table_key] = {"error": str(exc)}
            continue

        delimiter = "\t" if fp.suffix.lower() == ".tsv" else ","
        rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
        if not rows:
            summary["tables"][table_key] = {"n_rows": 0, "columns": {}}
            continue

        rows = rows[:max_rows]
        colnames = list(rows[0].keys())
        col_stats: dict[str, Any] = {}
        for col in colnames:
            nums: list[float] = []
            for row in rows:
                v = _parse_float_cell(row.get(col, "") or "")
                if v is not None:
                    nums.append(v)
            if nums:
                col_stats[col] = _column_stats(nums)

        summary["tables"][table_key] = {
            "n_rows": len(rows),
            "numeric_columns_summarized": sorted(col_stats.keys()),
            "column_stats": col_stats,
        }

    return summary


_MRIQC_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)out of memory|oom|cannot allocate|killed process|memory", "Possible OOM / memory limit."),
    (r"(?i)empty result|querying bids.*empty", "BIDS query returned no files (modalities/filters?)."),
    (r"(?i)participant labels were not found", "Participant label not found in BIDS directory."),
    (r"(?i)same as the input bids folder", "Output folder cannot equal BIDS root."),
)


def _diagnose_mriqc_log(combined_log: str) -> str:
    hits: list[str] = []
    for pattern, message in _MRIQC_ERROR_PATTERNS:
        if re.search(pattern, combined_log):
            hits.append(f"- {message}")
    if not hits:
        return ""
    return "\n\n## GUIN error summary (heuristic)\n" + "\n".join(hits) + "\n"


def _build_mriqc_argv(
    *,
    analysis_levels: list[str],
    participant_labels_norm: list[str],
    modalities: list[str],
    n_cpus: int,
    mem_gb: int,
) -> list[str]:
    argv: list[str] = [
        "mriqc",
        str(_BIDS_MOUNT),
        str(_OUTPUT_MOUNT),
        *analysis_levels,
        "--modalities",
        *modalities,
        "--nprocs",
        str(n_cpus),
        "--mem",
        f"{mem_gb}G",
        "--work-dir",
        str(_WORK_MOUNT),
        "--notrack",
    ]
    for lab in participant_labels_norm:
        argv.extend(["--participant-label", lab])
    return argv


@mcp.tool()
async def run_mriqc(
    bids_dir: str,
    output_dir: str,
    participant_label: list[str],
    modalities: list[str] | None = None,
    run_group: bool = False,
    n_cpus: int = 4,
    mem_gb: int = 16,
    skip_bids_validation: bool = False,
) -> BIDSDerivativeResult:
    """Run MRIQC (nipreps/mriqc 24.0.2) inside Apptainer with BIDS and TemplateFlow mounts.

    Produces BIDS-style derivative outputs with per-image and group-level IQMs (TSV/CSV).
    Set ``run_group`` to include the ``group`` analysis level (group reports and aggregate
    IQMs; requires participant-level outputs in ``output_dir`` when running group alone).

    Use ``GUIN_MRIQC_SIF`` or ``GUIN_MRIQC_CONTAINER`` (default ``mriqc-24.0.2``) for the SIF.
    Parsed IQM summary statistics from ``group_*.tsv`` / ``group_*.csv`` are returned in
    ``iqm_summary`` when available.
    """
    if modalities is None:
        modalities = ["T1w", "bold"]

    bids_path = Path(bids_dir).expanduser().resolve()
    out_path = Path(output_dir).expanduser().resolve()
    derivative_root = out_path.resolve()
    prov = _provenance_hash(bids_path)

    try:
        mods_norm = _normalize_modalities(modalities)
    except ValueError as exc:
        return BIDSDerivativeResult(
            output_path=derivative_root,
            provenance_hash=prov,
            execution_log=f"GUIN: {exc}",
            container_digest="",
            wall_clock_seconds=0.0,
            iqm_summary=None,
        )

    def _fail(log: str, digest: str = "") -> BIDSDerivativeResult:
        return BIDSDerivativeResult(
            output_path=derivative_root,
            provenance_hash=prov,
            execution_log=log,
            container_digest=digest,
            wall_clock_seconds=0.0,
            iqm_summary=None,
        )

    if not bids_path.is_dir():
        return _fail(f"GUIN: BIDS directory does not exist: {bids_path}")

    if out_path.resolve() == bids_path.resolve():
        return _fail(
            "GUIN: output_dir must not be the same as bids_dir (MRIQC requirement). "
            f"Use e.g. {bids_path / 'derivatives' / 'mriqc'}."
        )

    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "mriqc_work").mkdir(parents=True, exist_ok=True)

    if not skip_bids_validation:
        report = validate_bids(str(bids_path))
        if not report.get("valid", False):
            err_txt = "\n".join(report.get("errors", [])[:50])
            warn_txt = "\n".join(report.get("warnings", [])[:20])
            return _fail(
                "GUIN: BIDS validation failed; not starting MRIQC.\n"
                f"errors:\n{err_txt}\n\nwarnings:\n{warn_txt}"
            )

    has_participants = bool(participant_label)
    if not has_participants and not run_group:
        return _fail(
            "GUIN: provide at least one participant_label and/or set run_group=True "
            "(group-only requires existing participant-level outputs in output_dir)."
        )

    labels_norm = [_normalize_participant_label(p) for p in participant_label] if has_participants else []

    if has_participants:
        missing_subs = [
            lab
            for lab in labels_norm
            if not (bids_path / f"sub-{lab}").is_dir()
            and not (bids_path / f"sub-{lab}.zip").is_file()
        ]
        if missing_subs:
            return _fail(
                "GUIN: participant_label not found under BIDS root: "
                f"{missing_subs!r}"
            )

    if has_participants and run_group:
        analysis_levels = ["participant", "group"]
    elif has_participants:
        analysis_levels = ["participant"]
    else:
        analysis_levels = ["group"]

    try:
        sif = _mriqc_container_sif()
    except FileNotFoundError as exc:
        return _fail(f"GUIN: {exc}")

    tf_host = _templateflow_home_host()
    tf_host.mkdir(parents=True, exist_ok=True)

    preamble = (
        "GUIN MRIQC parameters:\n"
        f"  modalities={mods_norm!r}, run_group={run_group}, analysis_levels={analysis_levels!r}\n"
        f"  container reference: nipreps/mriqc:24.0.2 (SIF: {sif.name})\n\n"
    )

    argv = _build_mriqc_argv(
        analysis_levels=analysis_levels,
        participant_labels_norm=labels_norm,
        modalities=mods_norm,
        n_cpus=n_cpus,
        mem_gb=mem_gb,
    )

    binds: list[tuple[Path, Path]] = [
        (bids_path, _BIDS_MOUNT),
        (out_path, _OUTPUT_MOUNT),
        (tf_host, _TEMPLATEFLOW_MOUNT),
    ]

    env_inside = {"TEMPLATEFLOW_HOME": str(_TEMPLATEFLOW_MOUNT)}

    logger.info("Starting MRIQC in container %s", sif)
    t0 = time.monotonic()
    stdout, stderr, code = await run_container(
        sif,
        argv,
        bind_paths=binds,
        container_env=env_inside,
        timeout_seconds=None,
    )
    wall = time.monotonic() - t0

    combined_body = (
        f"=== MRIQC exit code: {code} ===\n\n=== stdout ===\n{stdout}\n\n=== stderr ===\n{stderr}\n"
    )
    combined = preamble + combined_body
    combined += _diagnose_mriqc_log(combined)

    digest = await _apptainer_inspect_digest(sif)

    iqm_summary: dict[str, Any] | None = None
    if code == 0:
        try:
            iqm_summary = _summarize_iqm_tables(out_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("IQM summary failed")
            iqm_summary = {"error": f"GUIN could not summarize IQMs: {exc}"}

    if code != 0:
        combined += f"\n\nGUIN: MRIQC exited with non-zero status ({code}).\n"
        iqm_summary = iqm_summary or _summarize_iqm_tables(out_path)

    return BIDSDerivativeResult(
        output_path=derivative_root,
        provenance_hash=prov,
        execution_log=combined,
        container_digest=digest,
        wall_clock_seconds=wall,
        iqm_summary=iqm_summary,
    )
