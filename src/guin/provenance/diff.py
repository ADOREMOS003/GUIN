"""Diff two GUIN provenance JSON-LD records for reproducibility diagnostics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROV_ACTIVITY = "http://www.w3.org/ns/prov#Activity"
GUIN_TOOL_NAME = "https://guin.dev/prov#tool_name"
GUIN_TOOL_VERSION = "https://guin.dev/prov#tool_version"
GUIN_CONTAINER_DIGEST = "https://guin.dev/prov#container_digest"
GUIN_INPUT_PARAMETERS = "https://guin.dev/prov#input_parameters"
GUIN_OUTPUT_FILES = "https://guin.dev/prov#output_files"
GUIN_WORKFLOW_FORMAT = "https://guin.dev/prov#format"
PROV_VALUE = "http://www.w3.org/ns/prov#value"
PROV_START = "http://www.w3.org/ns/prov#startedAtTime"

_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


@dataclass(frozen=True)
class ParameterDifference:
    tool_name: str
    invocation_key: str
    parameter_name: str
    value_a: Any
    value_b: Any


@dataclass(frozen=True)
class ToolVersionDifference:
    tool_name: str
    invocation_key: str
    version_a: str | None
    version_b: str | None


@dataclass(frozen=True)
class ContainerDigestDifference:
    tool_name: str
    invocation_key: str
    digest_a: str | None
    digest_b: str | None


@dataclass(frozen=True)
class FileHashDifference:
    tool_name: str
    invocation_key: str
    file_path: str
    hash_a: str | None
    hash_b: str | None
    file_role: str  # "input" | "output"


@dataclass(frozen=True)
class InvocationDifference:
    tool_name: str
    invocation_key: str
    present_in: str  # "record_a" | "record_b"


@dataclass(frozen=True)
class WorkflowGraphDifference:
    added_nodes: list[str]
    removed_nodes: list[str]


@dataclass(frozen=True)
class ProvenanceDiff:
    """Categorized differences between two provenance records."""

    record_a: Path
    record_b: Path
    parameter_differences: list[ParameterDifference] = field(default_factory=list)
    tool_version_differences: list[ToolVersionDifference] = field(default_factory=list)
    container_digest_differences: list[ContainerDigestDifference] = field(
        default_factory=list
    )
    input_file_hash_differences: list[FileHashDifference] = field(default_factory=list)
    output_file_hash_differences: list[FileHashDifference] = field(default_factory=list)
    invocation_differences: list[InvocationDifference] = field(default_factory=list)
    workflow_graph_difference: WorkflowGraphDifference | None = None

    @property
    def has_differences(self) -> bool:
        return any(
            [
                self.parameter_differences,
                self.tool_version_differences,
                self.container_digest_differences,
                self.input_file_hash_differences,
                self.output_file_hash_differences,
                self.invocation_differences,
                self.workflow_graph_difference
                and (
                    self.workflow_graph_difference.added_nodes
                    or self.workflow_graph_difference.removed_nodes
                ),
            ]
        )

    def to_markdown(self) -> str:
        """Render human-readable report suitable for reproducibility triage."""
        lines: list[str] = [
            "# Provenance Diff",
            "",
            f"- Record A: `{self.record_a}`",
            f"- Record B: `{self.record_b}`",
            "",
        ]
        if not self.has_differences:
            lines.append("No differences found.")
            return "\n".join(lines)

        def add_section(title: str, rows: list[str]) -> None:
            if not rows:
                return
            lines.extend([f"## {title}", ""])
            lines.extend(rows)
            lines.append("")

        add_section(
            "Invocation Differences",
            [
                f"- `{d.invocation_key}` (`{d.tool_name}`) present only in **{d.present_in}**"
                for d in self.invocation_differences
            ],
        )
        add_section(
            "Parameter Differences",
            [
                f"- `{d.invocation_key}` `{d.parameter_name}`: A=`{d.value_a}` vs B=`{d.value_b}`"
                for d in self.parameter_differences
            ],
        )
        add_section(
            "Tool Version Differences",
            [
                f"- `{d.invocation_key}`: A=`{d.version_a}` vs B=`{d.version_b}`"
                for d in self.tool_version_differences
            ],
        )
        add_section(
            "Container Digest Differences",
            [
                f"- `{d.invocation_key}`: A=`{d.digest_a}` vs B=`{d.digest_b}`"
                for d in self.container_digest_differences
            ],
        )
        add_section(
            "Input File Hash Differences",
            [
                f"- `{d.invocation_key}` `{d.file_path}`: A=`{d.hash_a}` vs B=`{d.hash_b}`"
                for d in self.input_file_hash_differences
            ],
        )
        add_section(
            "Output File Hash Differences",
            [
                f"- `{d.invocation_key}` `{d.file_path}`: A=`{d.hash_a}` vs B=`{d.hash_b}`"
                for d in self.output_file_hash_differences
            ],
        )
        if self.workflow_graph_difference is not None:
            add_section(
                "Workflow Graph Structural Differences",
                [
                    *[
                        f"- Added node in B: `{n}`"
                        for n in self.workflow_graph_difference.added_nodes
                    ],
                    *[
                        f"- Removed node from B: `{n}`"
                        for n in self.workflow_graph_difference.removed_nodes
                    ],
                ],
            )
        return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class _ActivitySnapshot:
    invocation_key: str
    tool_name: str
    tool_version: str | None
    container_digest: str | None
    input_parameters: dict[str, Any]
    input_hashes: dict[str, str]
    output_hashes: dict[str, str]


def _load_jsonld(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        graph = payload.get("@graph")
        if isinstance(graph, list):
            return [x for x in graph if isinstance(x, dict)]
    raise ValueError(f"Unsupported JSON-LD shape in {path}")


def _obj_values(node: dict[str, Any], predicate: str) -> list[Any]:
    raw = node.get(predicate)
    if not isinstance(raw, list):
        return []
    out: list[Any] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
            continue
        if isinstance(item, dict):
            if "@value" in item:
                out.append(item["@value"])
            elif "@id" in item:
                out.append(item["@id"])
    return out


def _first_str(node: dict[str, Any], predicate: str) -> str | None:
    vals = _obj_values(node, predicate)
    if not vals:
        return None
    return str(vals[0])


def _parse_json_dict(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def _extract_input_hashes(params: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}

    def visit(prefix: str, value: Any) -> None:
        key_low = prefix.lower()
        if isinstance(value, dict):
            for k, v in value.items():
                child = f"{prefix}.{k}" if prefix else str(k)
                visit(child, v)
            return
        if isinstance(value, list):
            for i, item in enumerate(value):
                visit(f"{prefix}[{i}]", item)
            return
        if _is_sha256(value) and "hash" in key_low:
            out[prefix] = str(value)

    visit("", params)
    return out


def _activity_sort_key(node: dict[str, Any]) -> tuple[str, str]:
    start = _first_str(node, PROV_START) or ""
    ident = str(node.get("@id", ""))
    return (start, ident)


def _collect_activities(nodes: list[dict[str, Any]]) -> dict[str, _ActivitySnapshot]:
    activities = []
    for node in nodes:
        types = _obj_values(node, "@type")
        if PROV_ACTIVITY in {str(t) for t in types}:
            activities.append(node)
    activities.sort(key=_activity_sort_key)

    seq_by_tool: dict[str, int] = {}
    out: dict[str, _ActivitySnapshot] = {}
    for node in activities:
        tool = _first_str(node, GUIN_TOOL_NAME) or "unknown_tool"
        seq = seq_by_tool.get(tool, 0) + 1
        seq_by_tool[tool] = seq
        inv_key = f"{tool}#{seq}"
        params = _parse_json_dict(_first_str(node, GUIN_INPUT_PARAMETERS))
        output_hashes = {
            str(k): str(v)
            for k, v in _parse_json_dict(_first_str(node, GUIN_OUTPUT_FILES)).items()
        }
        out[inv_key] = _ActivitySnapshot(
            invocation_key=inv_key,
            tool_name=tool,
            tool_version=_first_str(node, GUIN_TOOL_VERSION),
            container_digest=_first_str(node, GUIN_CONTAINER_DIGEST),
            input_parameters=params,
            input_hashes=_extract_input_hashes(params),
            output_hashes=output_hashes,
        )
    return out


def _extract_workflow_nodes(nodes: list[dict[str, Any]]) -> set[str]:
    for node in nodes:
        if _first_str(node, GUIN_WORKFLOW_FORMAT) != "application/json":
            continue
        payload = _first_str(node, PROV_VALUE)
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        return _workflow_node_names(data)
    return set()


def _workflow_node_names(data: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(data, dict):
        if isinstance(data.get("nodes"), list):
            for item in data["nodes"]:
                if isinstance(item, str):
                    out.add(item)
                elif isinstance(item, dict):
                    if "name" in item:
                        out.add(str(item["name"]))
                    elif "id" in item:
                        out.add(str(item["id"]))
        if isinstance(data.get("steps"), list):
            for step in data["steps"]:
                if isinstance(step, dict) and "name" in step:
                    out.add(str(step["name"]))
    return out


def _jsonish(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _diff_hash_maps(
    *,
    tool_name: str,
    invocation_key: str,
    map_a: dict[str, str],
    map_b: dict[str, str],
    file_role: str,
) -> list[FileHashDifference]:
    out: list[FileHashDifference] = []
    keys = set(map_a) | set(map_b)
    for fp in sorted(keys):
        ha = map_a.get(fp)
        hb = map_b.get(fp)
        if ha != hb:
            out.append(
                FileHashDifference(
                    tool_name=tool_name,
                    invocation_key=invocation_key,
                    file_path=fp,
                    hash_a=ha,
                    hash_b=hb,
                    file_role=file_role,
                )
            )
    return out


def provenance_diff(record_a: Path, record_b: Path) -> ProvenanceDiff:
    """Compare two GUIN provenance JSON-LD records and classify divergences."""
    a_path = Path(record_a).expanduser().resolve()
    b_path = Path(record_b).expanduser().resolve()
    nodes_a = _load_jsonld(a_path)
    nodes_b = _load_jsonld(b_path)
    act_a = _collect_activities(nodes_a)
    act_b = _collect_activities(nodes_b)

    parameter_diffs: list[ParameterDifference] = []
    version_diffs: list[ToolVersionDifference] = []
    digest_diffs: list[ContainerDigestDifference] = []
    input_hash_diffs: list[FileHashDifference] = []
    output_hash_diffs: list[FileHashDifference] = []
    invocation_diffs: list[InvocationDifference] = []

    keys_all = set(act_a) | set(act_b)
    for inv_key in sorted(keys_all):
        sa = act_a.get(inv_key)
        sb = act_b.get(inv_key)
        if sa is None and sb is not None:
            invocation_diffs.append(
                InvocationDifference(
                    tool_name=sb.tool_name,
                    invocation_key=inv_key,
                    present_in="record_b",
                )
            )
            continue
        if sb is None and sa is not None:
            invocation_diffs.append(
                InvocationDifference(
                    tool_name=sa.tool_name,
                    invocation_key=inv_key,
                    present_in="record_a",
                )
            )
            continue
        if sa is None or sb is None:
            continue

        if sa.tool_version != sb.tool_version:
            version_diffs.append(
                ToolVersionDifference(
                    tool_name=sa.tool_name,
                    invocation_key=inv_key,
                    version_a=sa.tool_version,
                    version_b=sb.tool_version,
                )
            )
        if sa.container_digest != sb.container_digest:
            digest_diffs.append(
                ContainerDigestDifference(
                    tool_name=sa.tool_name,
                    invocation_key=inv_key,
                    digest_a=sa.container_digest,
                    digest_b=sb.container_digest,
                )
            )

        param_names = set(sa.input_parameters) | set(sb.input_parameters)
        for pname in sorted(param_names):
            va = sa.input_parameters.get(pname)
            vb = sb.input_parameters.get(pname)
            if _jsonish(va) != _jsonish(vb):
                parameter_diffs.append(
                    ParameterDifference(
                        tool_name=sa.tool_name,
                        invocation_key=inv_key,
                        parameter_name=pname,
                        value_a=va,
                        value_b=vb,
                    )
                )

        input_hash_diffs.extend(
            _diff_hash_maps(
                tool_name=sa.tool_name,
                invocation_key=inv_key,
                map_a=sa.input_hashes,
                map_b=sb.input_hashes,
                file_role="input",
            )
        )
        output_hash_diffs.extend(
            _diff_hash_maps(
                tool_name=sa.tool_name,
                invocation_key=inv_key,
                map_a=sa.output_hashes,
                map_b=sb.output_hashes,
                file_role="output",
            )
        )

    workflow_a = _extract_workflow_nodes(nodes_a)
    workflow_b = _extract_workflow_nodes(nodes_b)
    workflow_diff: WorkflowGraphDifference | None = None
    if workflow_a or workflow_b:
        workflow_diff = WorkflowGraphDifference(
            added_nodes=sorted(workflow_b - workflow_a),
            removed_nodes=sorted(workflow_a - workflow_b),
        )

    return ProvenanceDiff(
        record_a=a_path,
        record_b=b_path,
        parameter_differences=parameter_diffs,
        tool_version_differences=version_diffs,
        container_digest_differences=digest_diffs,
        input_file_hash_differences=input_hash_diffs,
        output_file_hash_differences=output_hash_diffs,
        invocation_differences=invocation_diffs,
        workflow_graph_difference=workflow_diff,
    )
