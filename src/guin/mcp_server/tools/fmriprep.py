"""fMRIPrep MCP tool (Apptainer-backed)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path

from guin.models.derivatives import BIDSDerivativeResult
from guin.mcp_server.server import (
    CONFIG,
    _resolve_container_sif,
    mcp,
    run_container,
    validate_bids,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths inside the Apptainer image (official-style layout)
# ---------------------------------------------------------------------------

_BIDS_MOUNT = Path("/data/bids")
_OUTPUT_MOUNT = Path("/data/out")
_LICENSE_MOUNT = Path("/licenses/license.txt")
_TEMPLATEFLOW_MOUNT = Path("/templateflow")


def _templateflow_home_host() -> Path:
    return Path(
        os.environ.get("TEMPLATEFLOW_HOME", Path.home() / ".cache" / "templateflow")
    ).expanduser()


def _normalize_participant_label(label: str) -> str:
    """Return subject label without a ``sub-`` prefix (fMRIPrep CLI style)."""
    s = label.strip()
    if s.lower().startswith("sub-"):
        return s[4:]
    return s


def _merge_output_spaces(space: str, output_spaces: list[str]) -> list[str]:
    """Place *space* first if missing, then dedupe while preserving order."""
    merged: list[str] = []
    if space and space not in output_spaces:
        merged.append(space)
    merged.extend(output_spaces)
    seen: set[str] = set()
    out: list[str] = []
    for item in merged:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _provenance_hash(bids_dir: Path) -> str:
    """SHA-256 hex digest of ``dataset_description.json`` if present."""
    desc = bids_dir / "dataset_description.json"
    if not desc.is_file():
        return ""
    data = desc.read_bytes()
    return hashlib.sha256(data).hexdigest()


async def _apptainer_inspect_digest(container_sif: Path) -> str:
    """Return ``apptainer inspect`` text for *container_sif* (async, not subprocess.run)."""
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


def _fmriprep_container_sif() -> Path:
    """Resolve fMRIPrep ``.sif`` from ``GUIN_FMRIPREP_SIF`` or named image in cache."""
    override = os.environ.get("GUIN_FMRIPREP_SIF")
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(f"GUIN_FMRIPREP_SIF is not a file: {p}")
    name = os.environ.get("GUIN_FMRIPREP_CONTAINER", "fmriprep")
    return _resolve_container_sif(name)


def _build_fmriprep_argv(
    *,
    output_space_list: list[str],
    participant_labels_norm: list[str],
    task: str | None,
    n_cpus: int,
    mem_mb: int,
    fd_threshold: float,
    skip_bids_validation: bool,
    use_aroma: bool,
) -> list[str]:
    """Build ``fmriprep`` CLI arguments (nipreps 24.x-style).

    Note: BOLD volumetric smoothing FWHM is not exposed as a standalone CLI flag in
    recent fMRIPrep releases; callers pass ``bold_fwhm`` for logging and future mapping.
    """
    argv: list[str] = [
        "fmriprep",
        str(_BIDS_MOUNT),
        str(_OUTPUT_MOUNT),
        "participant",
        "--output-spaces",
        " ".join(output_space_list),
        "--nthreads",
        str(n_cpus),
        "--mem-mb",
        str(mem_mb),
        "--fs-license-file",
        str(_LICENSE_MOUNT),
        "--fd-spike-threshold",
        str(fd_threshold),
    ]
    for lab in participant_labels_norm:
        argv.extend(["--participant-label", lab])
    if task:
        argv.extend(["--task-id", task.strip()])
    if skip_bids_validation:
        argv.append("--skip-bids-validation")
    if use_aroma:
        argv.append("--use-aroma")
    return argv


_FMRI_PREP_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)no.*t1w|missing.*t1|t1w.*not found", "Missing or unusable T1w (anatomical) data."),
    (r"(?i)license|freesurfer.*license|fs_license", "FreeSurfer license problem (path, mount, or expiry)."),
    (r"(?i)out of memory|oom|cannot allocate|killed process|memory", "Possible OOM / memory limit hit."),
    (r"(?i)bold.*too short|insufficient.*length|too few volumes|length.*bold", "BOLD run may be too short for confounds / processing."),
)


def _diagnose_fmriprep_log(combined_log: str) -> str:
    """Append a short GUIN diagnostic section for common fMRIPrep failure modes."""
    hits: list[str] = []
    for pattern, message in _FMRI_PREP_ERROR_PATTERNS:
        if re.search(pattern, combined_log):
            hits.append(f"- {message}")
    if not hits:
        return ""
    return "\n\n## GUIN error summary (heuristic)\n" + "\n".join(hits) + "\n"


@mcp.tool()
async def run_fmriprep(
    bids_dir: str,
    output_dir: str,
    participant_label: list[str],
    task: str | None = None,
    space: str = "MNI152NLin2009cAsym",
    output_spaces: list[str] | None = None,
    bold_fwhm: float = 6.0,
    fd_threshold: float = 0.5,
    n_cpus: int = 4,
    mem_gb: int = 16,
    fs_license_path: str = "~/.freesurfer/license.txt",
    skip_bids_validation: bool = False,
    use_aroma: bool = False,
) -> BIDSDerivativeResult:
    """Run fMRIPrep inside Apptainer with BIDS I/O and TemplateFlow bind mounts.

    Validates the BIDS root (unless skipped), checks that each participant has a ``sub-*``
    folder, then runs the official-style CLI: ``fmriprep <bids> <out> participant`` with
    threading, memory, output spaces, smoothing, FD spike threshold, optional task and AROMA.

    Configure the SIF via ``GUIN_FMRIPREP_SIF`` or ``GUIN_FMRIPREP_CONTAINER`` (image name
    under the container cache). Set ``TEMPLATEFLOW_HOME`` on the host for TemplateFlow data.
    """
    if output_spaces is None:
        output_spaces = ["MNI152NLin2009cAsym", "fsaverage5"]

    bids_path = Path(bids_dir).expanduser().resolve()
    out_path = Path(output_dir).expanduser().resolve()
    license_host = Path(fs_license_path).expanduser().resolve()
    derivative_root = out_path / "fmriprep"
    prov = _provenance_hash(bids_path)

    def _result(
        execution_log: str,
        digest: str = "",
        wall_s: float = 0.0,
    ) -> BIDSDerivativeResult:
        log = execution_log + _diagnose_fmriprep_log(execution_log)
        return BIDSDerivativeResult(
            output_path=derivative_root,
            provenance_hash=prov,
            execution_log=log,
            container_digest=digest,
            wall_clock_seconds=wall_s,
        )

    if not bids_path.is_dir():
        return _result(f"GUIN: BIDS directory does not exist: {bids_path}")

    if not license_host.is_file():
        return _result(
            f"GUIN: FreeSurfer license file not found: {license_host}\n"
            "Set fs_license_path or place a license at the default location."
        )

    out_path.mkdir(parents=True, exist_ok=True)

    if not skip_bids_validation:
        report = validate_bids(str(bids_path))
        if not report.get("valid", False):
            err_txt = "\n".join(report.get("errors", [])[:50])
            warn_txt = "\n".join(report.get("warnings", [])[:20])
            return _result(
                "GUIN: BIDS validation failed; not starting fMRIPrep.\n"
                f"errors:\n{err_txt}\n\nwarnings:\n{warn_txt}"
            )

    if not participant_label:
        return _result("GUIN: participant_label must contain at least one subject.")

    labels_norm = [_normalize_participant_label(p) for p in participant_label]
    missing_subs = [
        lab
        for lab in labels_norm
        if not (bids_path / f"sub-{lab}").is_dir()
        and not (bids_path / f"sub-{lab}.zip").is_file()
    ]
    if missing_subs:
        return _result(
            "GUIN: participant_label not found under BIDS root "
            f"(expected sub-<label> directories): {missing_subs!r}"
        )

    try:
        sif = _fmriprep_container_sif()
    except FileNotFoundError as exc:
        return _result(f"GUIN: {exc}")

    tf_host = _templateflow_home_host()
    tf_host.mkdir(parents=True, exist_ok=True)

    spaces_merged = _merge_output_spaces(space, output_spaces)
    mem_mb = mem_gb * 1024

    preamble = (
        "GUIN run parameters:\n"
        f"  bold_fwhm={bold_fwhm} (informational; not a separate fMRIPrep 24.x CLI flag)\n"
        f"  space={space!r}, output_spaces={spaces_merged!r}\n"
        f"  task={task!r}, skip_bids_validation={skip_bids_validation}, use_aroma={use_aroma}\n\n"
    )

    argv = _build_fmriprep_argv(
        output_space_list=spaces_merged,
        participant_labels_norm=labels_norm,
        task=task,
        n_cpus=n_cpus,
        mem_mb=mem_mb,
        fd_threshold=fd_threshold,
        skip_bids_validation=skip_bids_validation,
        use_aroma=use_aroma,
    )

    binds: list[tuple[Path, Path]] = [
        (bids_path, _BIDS_MOUNT),
        (out_path, _OUTPUT_MOUNT),
        (license_host, _LICENSE_MOUNT),
        (tf_host, _TEMPLATEFLOW_MOUNT),
    ]

    env_inside = {"TEMPLATEFLOW_HOME": str(_TEMPLATEFLOW_MOUNT)}

    logger.info("Starting fMRIPrep in container %s", sif)
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
        f"=== fMRIPrep exit code: {code} ===\n\n=== stdout ===\n{stdout}\n\n=== stderr ===\n{stderr}\n"
    )
    combined = preamble + combined_body
    combined += _diagnose_fmriprep_log(combined)
    digest = await _apptainer_inspect_digest(sif)

    if code != 0:
        combined += f"\n\nGUIN: fMRIPrep exited with non-zero status ({code}).\n"

    return BIDSDerivativeResult(
        output_path=derivative_root,
        provenance_hash=prov,
        execution_log=combined,
        container_digest=digest,
        wall_clock_seconds=wall,
    )
