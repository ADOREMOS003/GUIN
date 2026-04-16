"""Tests for LLM planner fallback and rendering."""

from __future__ import annotations

from pathlib import Path

from guin.planner import llm


def test_generate_plan_fallback_no_api_key(monkeypatch, tmp_path: Path) -> None:
    bids = tmp_path / "bids"
    (bids / "sub-01" / "anat").mkdir(parents=True)
    (bids / "sub-01" / "anat" / "sub-01_T1w.nii.gz").write_text("x", encoding="utf-8")
    out = tmp_path / "out"

    monkeypatch.setattr(
        llm,
        "_load_tool_schemas",
        lambda: [
            {"name": "skull_strip_fsl", "description": "BET", "input_schema": {}},
            {"name": "run_mriqc", "description": "MRIQC", "input_schema": {}},
        ],
    )

    plan = llm.generate_plan(
        instruction="skull strip sub-01 T1w with FSL BET",
        bids_dir=bids,
        output_dir=out,
        model="claude-sonnet-4-20250514",
        api_key=None,
    )
    assert plan.used_fallback is True
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0].tool_name == "skull_strip_fsl"
    assert "input" in plan.tool_calls[0].arguments


def test_render_plan_python_contains_call_tool() -> None:
    plan = llm.ExecutionPlan(
        instruction="demo",
        model="m",
        tool_calls=[llm.ToolCall(tool_name="run_mriqc", arguments={"bids_dir": "/b"})],
        prompt_text="p",
        response_text="r",
    )
    code = llm.render_plan_python(plan)
    assert "from guin.mcp import call_tool" in code
    assert "run_mriqc" in code
