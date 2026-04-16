"""Tests for :mod:`guin.provenance.diff`."""

from __future__ import annotations

import json
from pathlib import Path

from guin.provenance.diff import provenance_diff


def _make_record(
    path: Path,
    *,
    extent: str,
    tool_version: str,
    container_digest: str,
    input_hash: str,
    output_hash: str,
    nodes: list[str],
) -> None:
    payload = [
        {
            "@id": "https://guin.dev/prov#tool_invocation_00001",
            "@type": ["http://www.w3.org/ns/prov#Activity"],
            "https://guin.dev/prov#tool_name": [{"@value": "dwidenoise"}],
            "https://guin.dev/prov#tool_version": [{"@value": tool_version}],
            "https://guin.dev/prov#container_digest": [{"@value": container_digest}],
            "http://www.w3.org/ns/prov#startedAtTime": [
                {"@value": "2026-01-01T00:00:00+00:00"}
            ],
            "https://guin.dev/prov#input_parameters": [
                {
                    "@value": json.dumps(
                        {
                            "extent": extent,
                            "input_hashes": {"sub-01_dwi.nii.gz": input_hash},
                        },
                        sort_keys=True,
                    )
                }
            ],
            "https://guin.dev/prov#output_files": [
                {
                    "@value": json.dumps(
                        {"derivatives/sub-01_dwi_denoised.nii.gz": output_hash},
                        sort_keys=True,
                    )
                }
            ],
        },
        {
            "@id": "https://guin.dev/prov#workflow_00002",
            "@type": ["http://www.w3.org/ns/prov#Entity"],
            "https://guin.dev/prov#format": [{"@value": "application/json"}],
            "http://www.w3.org/ns/prov#value": [
                {"@value": json.dumps({"nodes": nodes, "edges": []}, sort_keys=True)}
            ],
        },
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_provenance_diff_detects_all_requested_categories(tmp_path: Path) -> None:
    rec_a = tmp_path / "a.jsonld"
    rec_b = tmp_path / "b.jsonld"
    _make_record(
        rec_a,
        extent="5,5,5",
        tool_version="3.0.4",
        container_digest="digest-A",
        input_hash="a" * 64,
        output_hash="b" * 64,
        nodes=["dwidenoise"],
    )
    _make_record(
        rec_b,
        extent="7,7,7",
        tool_version="3.0.5",
        container_digest="digest-B",
        input_hash="c" * 64,
        output_hash="d" * 64,
        nodes=["dwidenoise", "mrdegibbs"],
    )

    diff = provenance_diff(rec_a, rec_b)

    assert diff.has_differences is True
    assert any(d.parameter_name == "extent" for d in diff.parameter_differences)
    assert len(diff.tool_version_differences) == 1
    assert len(diff.container_digest_differences) == 1
    assert len(diff.input_file_hash_differences) == 1
    assert len(diff.output_file_hash_differences) == 1
    assert diff.workflow_graph_difference is not None
    assert diff.workflow_graph_difference.added_nodes == ["mrdegibbs"]
    md = diff.to_markdown()
    assert "Parameter Differences" in md
    assert "Tool Version Differences" in md
    assert "Container Digest Differences" in md
    assert "Input File Hash Differences" in md
    assert "Output File Hash Differences" in md
    assert "Workflow Graph Structural Differences" in md


def test_provenance_diff_no_differences(tmp_path: Path) -> None:
    rec_a = tmp_path / "a.jsonld"
    rec_b = tmp_path / "b.jsonld"
    _make_record(
        rec_a,
        extent="5,5,5",
        tool_version="3.0.4",
        container_digest="digest-A",
        input_hash="a" * 64,
        output_hash="b" * 64,
        nodes=["dwidenoise"],
    )
    _make_record(
        rec_b,
        extent="5,5,5",
        tool_version="3.0.4",
        container_digest="digest-A",
        input_hash="a" * 64,
        output_hash="b" * 64,
        nodes=["dwidenoise"],
    )
    diff = provenance_diff(rec_a, rec_b)
    assert diff.has_differences is False
    assert "No differences found." in diff.to_markdown()
