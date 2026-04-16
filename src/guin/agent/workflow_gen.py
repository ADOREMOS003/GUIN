"""Build Nipype workflows from structured GUIN plans (DAG of MCP tool steps)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import networkx as nx  # type: ignore[import-untyped]
from nipype.pipeline.engine import MapNode, Node, Workflow  # type: ignore[import-untyped]

from guin.agent.nipype_adapter import NipypeToolAdapter

logger = logging.getLogger(__name__)

JSON_SCHEMA_VERSION = 1


@dataclass
class WorkflowStep:
    """One node in a GUIN workflow plan."""

    name: str
    """Stable step id (must be unique within the plan)."""

    tool: str
    """Registered MCP tool name (e.g. ``run_fmriprep``)."""

    params: dict[str, Any]
    """Static parameters passed to the tool interface."""

    depends_on: list[str] = field(default_factory=list)
    """Upstream step :attr:`name` values that must complete before this step."""

    output_mapping: dict[str, str] = field(default_factory=dict)
    """Maps **this step's input trait names** to ``\"upstream_step.output_field\"``.

    Example: ``{\"bids_dir\": \"anat_prep.output_path\"}`` connects the upstream
    node's ``output_path`` output to this step's ``bids_dir`` input.
    Only :class:`~nipype.interfaces.base.SimpleInterface` outputs from
    :class:`NipypeToolAdapter` are available: ``output_path``, ``provenance_hash``.
    """


# --- JSON Schemas for built-in MCP tools (aligned with NipypeToolAdapter tests) ---


def _fmriprep_input_schema() -> dict[str, Any]:
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
            "bold_fwhm": {"type": "number", "default": 6.0, "description": "BOLD FWHM"},
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


def _mriqc_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "bids_dir": {"type": "string"},
            "output_dir": {"type": "string"},
            "participant_label": {"type": "array", "items": {"type": "string"}},
            "modalities": {"type": "array", "items": {"type": "string"}},
            "run_group": {"type": "boolean", "default": False},
            "n_cpus": {"type": "integer", "default": 4},
            "mem_gb": {"type": "integer", "default": 16},
            "skip_bids_validation": {"type": "boolean", "default": False},
        },
        "required": ["bids_dir", "output_dir", "participant_label"],
    }


def _default_tool_registry() -> dict[str, tuple[Callable[..., Any], dict[str, Any]]]:
    from guin.mcp_server.tools.fmriprep import run_fmriprep
    from guin.mcp_server.tools.mriqc import run_mriqc

    return {
        "run_fmriprep": (run_fmriprep, _fmriprep_input_schema()),
        "run_mriqc": (run_mriqc, _mriqc_input_schema()),
    }


def _sanitize_node_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if safe and safe[0].isdigit():
        safe = f"s_{safe}"
    return safe or "step"


def validate_workflow_dag(steps: list[WorkflowStep]) -> list[str]:
    """Validate step names, dependencies, and acyclicity; return topological order.

    Raises ``ValueError`` on duplicate names, missing deps, invalid mappings, or cycles.
    """
    names = [s.name for s in steps]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate WorkflowStep.name values: {names}")

    name_set = set(names)
    for s in steps:
        for d in s.depends_on:
            if d not in name_set:
                raise ValueError(f"Step {s.name!r} depends_on unknown step {d!r}")
        for dest_in, src_ref in s.output_mapping.items():
            if "." not in src_ref:
                raise ValueError(
                    f"Step {s.name!r} output_mapping[{dest_in!r}] must be "
                    f"'upstream_step.output_field', got {src_ref!r}"
                )
            up, outf = src_ref.split(".", 1)
            if up not in name_set:
                raise ValueError(f"output_mapping references unknown step {up!r}")
            if outf not in ("output_path", "provenance_hash"):
                raise ValueError(
                    f"Unknown output field {outf!r}; use output_path or provenance_hash"
                )
            if up not in s.depends_on:
                raise ValueError(
                    f"Step {s.name!r} maps from {up!r} but {up!r} is not in depends_on"
                )

    g = nx.DiGraph()
    g.add_nodes_from(names)
    for s in steps:
        for d in s.depends_on:
            g.add_edge(d, s.name)

    if not nx.is_directed_acyclic_graph(g):
        raise ValueError("Workflow DAG contains a cycle")

    return list(nx.topological_sort(g))


class WorkflowGenerator:
    """Construct a :class:`nipype.pipeline.engine.Workflow` from :class:`WorkflowStep` plans."""

    _extra_tools: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {}

    def __init__(
        self,
        steps: list[WorkflowStep],
        *,
        workflow_name: str = "guin_workflow",
        base_dir: str | Path | None = None,
    ) -> None:
        self.steps = steps
        self.workflow_name = _sanitize_node_name(workflow_name)
        self.base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self._workflow: Workflow | None = None
        self._topo_order: list[str] | None = None

    @classmethod
    def register_tool(
        cls,
        tool_name: str,
        tool_func: Callable[..., Any],
        input_schema: dict[str, Any],
    ) -> None:
        """Register an MCP tool for use in :attr:`WorkflowStep.tool`."""
        cls._extra_tools[tool_name] = (tool_func, input_schema)

    def _resolve_tool(self, tool_name: str) -> tuple[Callable[..., Any], dict[str, Any]]:
        if tool_name in WorkflowGenerator._extra_tools:
            return WorkflowGenerator._extra_tools[tool_name]
        reg = _default_tool_registry()
        if tool_name not in reg:
            raise KeyError(
                f"Unknown MCP tool {tool_name!r}. Register with "
                f"WorkflowGenerator.register_tool or use a built-in name."
            )
        return reg[tool_name]

    def validate(self) -> list[str]:
        """Validate the plan and return a topological order of step names."""
        order = validate_workflow_dag(self.steps)
        self._topo_order = order
        return order

    def _make_node(self, step: WorkflowStep) -> Node | MapNode:
        func, schema = self._resolve_tool(step.tool)
        iface_cls = NipypeToolAdapter.from_mcp_tool(step.tool, func, schema)
        iface = iface_cls()
        name = _sanitize_node_name(step.name)
        params = dict(step.params)

        pl = params.pop("participant_label", None)
        use_map = isinstance(pl, list)

        if use_map:
            node = MapNode(iface, "participant_label", name)
            node.inputs.participant_label = pl
        else:
            node = Node(iface, name)

        for key, val in params.items():
            if hasattr(node.inputs, key):
                setattr(node.inputs, key, val)
            else:
                logger.warning("Step %s: ignoring unknown param %s", step.name, key)

        return node

    def build(self) -> Workflow:
        """Build and return a :class:`Workflow` with nodes and connections."""
        order = self.validate()
        wf = Workflow(name=self.workflow_name, base_dir=str(self.base_dir) if self.base_dir else None)

        step_by_name = {s.name: s for s in self.steps}
        nodes: dict[str, Node | MapNode] = {}

        for step_name in order:
            st = step_by_name[step_name]
            nodes[step_name] = self._make_node(st)

        wf.add_nodes(list(nodes.values()))

        for st in self.steps:
            dest = nodes[st.name]
            for dest_in, src_ref in st.output_mapping.items():
                up, outf = src_ref.split(".", 1)
                src = nodes[up]
                wf.connect(src, outf, dest, dest_in)

        self._workflow = wf
        return wf

    @property
    def workflow(self) -> Workflow | None:
        """Last workflow built by :meth:`build`, if any."""
        return self._workflow

    def write_graph(
        self,
        dotfilename: str = "workflow.dot",
        graph2use: str = "flat",
        **kwargs: Any,
    ) -> str:
        """Write a Graphviz file via :meth:`nipype.pipeline.engine.Workflow.write_graph`."""
        wf = self._workflow or self.build()
        return wf.write_graph(dotfilename=dotfilename, graph2use=graph2use, **kwargs)

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the plan and metadata for provenance (re-executable without an LLM)."""
        payload = {
            "schema_version": JSON_SCHEMA_VERSION,
            "workflow_name": self.workflow_name,
            "base_dir": str(self.base_dir) if self.base_dir else None,
            "steps": [asdict(s) for s in self.steps],
        }
        return json.dumps(payload, indent=indent)

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> WorkflowGenerator:
        """Deserialize a plan produced by :meth:`to_json`."""
        if isinstance(data, str):
            obj = json.loads(data)
        else:
            obj = data
        ver = obj.get("schema_version", JSON_SCHEMA_VERSION)
        if ver != JSON_SCHEMA_VERSION:
            logger.warning("Unknown workflow JSON schema_version %s", ver)
        steps_raw = obj["steps"]
        steps = [
            WorkflowStep(
                name=s["name"],
                tool=s["tool"],
                params=s.get("params", {}),
                depends_on=list(s.get("depends_on", [])),
                output_mapping=dict(s.get("output_mapping", {})),
            )
            for s in steps_raw
        ]
        return cls(
            steps,
            workflow_name=str(obj.get("workflow_name", "guin_workflow")),
            base_dir=obj.get("base_dir"),
        )

    def to_json_file(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_json_file(cls, path: str | Path) -> WorkflowGenerator:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def run(
        self,
        plugin: str = "Linear",
        plugin_args: dict[str, Any] | None = None,
    ) -> Any:
        """Build (if needed) and run the workflow with the given Nipype execution plugin."""
        wf = self._workflow or self.build()
        return wf.run(plugin=plugin, plugin_args=plugin_args or {})


def plan_to_workflow(
    steps: list[WorkflowStep],
    *,
    workflow_name: str = "guin_workflow",
    base_dir: str | Path | None = None,
) -> Workflow:
    """Main entry point: validate the DAG, wrap MCP tools, and return a Nipype :class:`Workflow`."""
    gen = WorkflowGenerator(steps, workflow_name=workflow_name, base_dir=base_dir)
    return gen.build()


def load_workflow_from_json(
    data: str | dict[str, Any] | Path,
) -> Workflow:
    """Deserialize a saved JSON plan and build a workflow (re-execute without the LLM)."""
    if isinstance(data, Path):
        gen = WorkflowGenerator.from_json_file(data)
    else:
        gen = WorkflowGenerator.from_json(data)
    return gen.build()
