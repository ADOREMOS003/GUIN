"""Tests for :mod:`guin.provenance.tracker`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from guin.provenance.tracker import ProvenanceTracker


def test_tracker_writes_jsonld_and_verifies_outputs(tmp_path: Path) -> None:
    tracker = ProvenanceTracker(
        output_dir=tmp_path,
        llm_model_name="test-model",
        session_timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )
    tracker.track_instruction("denoise my diffusion data")
    tracker.track_rag_chunk(
        chunk_text="dwidenoise --extent 5,5,5 --noise noise.mif in.mif out.mif",
        source_url="https://example.org/docs/dwidenoise",
        tool_name="dwidenoise",
        chunk_id="chunk-1",
    )
    tracker.track_llm_code("dwidenoise in.mif out.mif")
    tracker.track_workflow_graph({"nodes": ["denoise"], "edges": []})

    out_file = tmp_path / "derivatives" / "denoise.mif"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("synthetic", encoding="utf-8")

    tracker.record_tool_invocation(
        tool_name="dwidenoise",
        tool_version="3.0.4",
        input_parameters={"extent": "5,5,5"},
        output_files=[out_file],
        container_sif=None,
    )

    prov_path = tracker.save()
    assert prov_path.name == "session_20260102T030405Z_provenance.jsonld"
    text = prov_path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload

    report = tracker.verify()
    assert report.all_passed is True
    assert len(report.items) == 1
    assert report.items[0].passed is True


def test_verify_detects_hash_mismatch(tmp_path: Path) -> None:
    tracker = ProvenanceTracker(output_dir=tmp_path, llm_model_name="test-model")
    out_file = tmp_path / "derivatives" / "out.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("first", encoding="utf-8")

    tracker.record_tool_invocation(
        tool_name="mock_tool",
        tool_version="1.0",
        input_parameters={},
        output_files=[out_file],
        container_sif=None,
    )
    out_file.write_text("modified", encoding="utf-8")

    report = tracker.verify()
    assert report.all_passed is False
    assert len(report.items) == 1
    assert report.items[0].passed is False
    assert report.items[0].reason == "Hash mismatch"
