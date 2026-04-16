"""Tests for :class:`guin.agent.nipype_adapter.NipypeToolAdapter`."""

from __future__ import annotations

import inspect
from typing import Any

from guin.agent.nipype_adapter import NipypeToolAdapter
from guin.mcp_server.tools.fmriprep import run_fmriprep


def _fmriprep_json_schema() -> dict[str, Any]:
    """JSON Schema aligned with :func:`run_fmriprep` parameters."""
    return {
        "type": "object",
        "properties": {
            "bids_dir": {"type": "string", "description": "Path to BIDS dataset root"},
            "output_dir": {"type": "string", "description": "Path for derivatives output"},
            "participant_label": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subject IDs without sub- prefix",
            },
            "task": {"type": "string", "description": "Optional BIDS task id"},
            "space": {
                "type": "string",
                "default": "MNI152NLin2009cAsym",
                "description": "Template space label",
            },
            "output_spaces": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Output spaces list",
            },
            "bold_fwhm": {
                "type": "number",
                "default": 6.0,
                "description": "BOLD FWHM (informational)",
            },
            "fd_threshold": {"type": "number", "default": 0.5},
            "n_cpus": {"type": "integer", "default": 4},
            "mem_gb": {"type": "integer", "default": 16},
            "fs_license_path": {
                "type": "string",
                "default": "~/.freesurfer/license.txt",
            },
            "skip_bids_validation": {"type": "boolean", "default": False},
            "use_aroma": {"type": "boolean", "default": False},
        },
        "required": ["bids_dir", "output_dir", "participant_label"],
    }


def test_fmriprep_interface_maps_all_parameters() -> None:
    """InputSpec traits cover every ``run_fmriprep`` parameter name."""
    iface_cls = NipypeToolAdapter.from_mcp_tool(
        "run_fmriprep",
        run_fmriprep,
        _fmriprep_json_schema(),
    )
    sig = inspect.signature(run_fmriprep)
    expected = set(sig.parameters.keys())

    inst = iface_cls()
    trait_names = set(inst.inputs.copyable_trait_names())

    missing = expected - trait_names
    extra = trait_names - expected
    assert not missing, f"InputSpec missing parameters: {missing}"
    assert not extra, f"InputSpec has unexpected parameters: {extra}"


def test_fmriprep_required_traits_mandatory() -> None:
    """Required JSON Schema fields are mandatory Nipype traits."""
    iface_cls = NipypeToolAdapter.from_mcp_tool(
        "run_fmriprep",
        run_fmriprep,
        _fmriprep_json_schema(),
    )
    inst = iface_cls()
    mandatory = set(inst.inputs.traits(mandatory=True).keys())
    assert {"bids_dir", "output_dir", "participant_label"}.issubset(mandatory)


def test_fmriprep_trait_types_match_schema() -> None:
    """Spot-check trait kinds for fMRIPrep (list, float, int, bool)."""
    iface_cls = NipypeToolAdapter.from_mcp_tool(
        "run_fmriprep",
        run_fmriprep,
        _fmriprep_json_schema(),
    )
    traits_map = iface_cls.input_spec().traits()
    from traits.trait_types import Bool, Float, Int, List, Str

    assert isinstance(traits_map["bids_dir"].trait_type, Str)
    assert isinstance(traits_map["participant_label"].trait_type, List)
    assert isinstance(traits_map["bold_fwhm"].trait_type, Float)
    assert isinstance(traits_map["n_cpus"].trait_type, Int)
    assert isinstance(traits_map["skip_bids_validation"].trait_type, Bool)


def test_fmriprep_tool_is_async() -> None:
    assert inspect.iscoroutinefunction(run_fmriprep)
