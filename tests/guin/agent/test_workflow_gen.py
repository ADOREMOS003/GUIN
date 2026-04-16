"""Tests for :mod:`guin.agent.workflow_gen`."""

from __future__ import annotations

import pytest

from guin.agent.workflow_gen import (
    WorkflowGenerator,
    WorkflowStep,
    load_workflow_from_json,
    plan_to_workflow,
    validate_workflow_dag,
)


def test_validate_dag_cycle() -> None:
    steps = [
        WorkflowStep(name="a", tool="run_mriqc", params={}, depends_on=["b"]),
        WorkflowStep(name="b", tool="run_mriqc", params={}, depends_on=["a"]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        validate_workflow_dag(steps)


def test_validate_output_mapping_requires_dep() -> None:
    steps = [
        WorkflowStep(
            name="b",
            tool="run_mriqc",
            params={},
            depends_on=[],
            output_mapping={"bids_dir": "a.output_path"},
        ),
        WorkflowStep(name="a", tool="run_mriqc", params={}),
    ]
    with pytest.raises(ValueError, match="depends_on"):
        validate_workflow_dag(steps)


def test_validate_output_mapping_ok_when_dep_listed() -> None:
    steps = [
        WorkflowStep(name="a", tool="run_mriqc", params={}),
        WorkflowStep(
            name="b",
            tool="run_mriqc",
            params={},
            depends_on=["a"],
            output_mapping={"bids_dir": "a.output_path"},
        ),
    ]
    validate_workflow_dag(steps)


def test_topological_order() -> None:
    steps = [
        WorkflowStep(name="first", tool="run_mriqc", params={}),
        WorkflowStep(
            name="second",
            tool="run_mriqc",
            params={},
            depends_on=["first"],
            output_mapping={"bids_dir": "first.output_path"},
        ),
    ]
    order = validate_workflow_dag(steps)
    assert order.index("first") < order.index("second")


def test_json_roundtrip() -> None:
    steps = [
        WorkflowStep(
            name="qc",
            tool="run_mriqc",
            params={"bids_dir": "/b", "output_dir": "/o", "participant_label": ["01"]},
        )
    ]
    gen = WorkflowGenerator(steps, workflow_name="t", base_dir="/tmp")
    s = gen.to_json()
    gen2 = WorkflowGenerator.from_json(s)
    assert len(gen2.steps) == 1
    assert gen2.steps[0].name == "qc"
    assert gen2.workflow_name == "t"


def test_load_workflow_from_json_dict() -> None:
    payload = {
        "schema_version": 1,
        "workflow_name": "w",
        "base_dir": None,
        "steps": [
            {
                "name": "only",
                "tool": "run_mriqc",
                "params": {
                    "bids_dir": "/b",
                    "output_dir": "/o",
                    "participant_label": [],
                    "run_group": True,
                },
                "depends_on": [],
                "output_mapping": {},
            }
        ],
    }
    wf = load_workflow_from_json(payload)
    assert wf.name == "w"


def test_plan_to_workflow_builds() -> None:
    """Smoke test: build a single-node workflow (no execution)."""
    steps = [
        WorkflowStep(
            name="mriqc_only",
            tool="run_mriqc",
            params={
                "bids_dir": "/data/bids",
                "output_dir": "/data/out",
                "participant_label": [],
                "run_group": True,
            },
        )
    ]
    wf = plan_to_workflow(steps, workflow_name="smoke", base_dir=None)
    assert wf is not None
    assert wf.name == "smoke"
