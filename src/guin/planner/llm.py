"""Claude-powered tool-call planning for natural-language GUIN instructions."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from guin.mcp_server.server import mcp
from guin.provenance.tracker import ProvenanceTracker

DEFAULT_MODEL = "claude-sonnet-4-20250514"
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.MULTILINE)


@dataclass(frozen=True)
class ToolCall:
    """One planned MCP tool invocation."""

    tool_name: str
    arguments: dict[str, Any]
    reasoning: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    """LLM-derived (or fallback) plan."""

    instruction: str
    model: str
    tool_calls: list[ToolCall]
    prompt_text: str
    response_text: str
    usage: dict[str, int] | None = None
    used_fallback: bool = False


def _load_tool_schemas() -> list[dict[str, Any]]:
    rows = asyncio.run(mcp.list_tools())
    out: list[dict[str, Any]] = []
    for t in rows:
        out.append(
            {
                "name": str(getattr(t, "name", "")),
                "description": str(getattr(t, "description", "")).strip(),
                "input_schema": getattr(t, "inputSchema", {}),
            }
        )
    return out


def _scan_bids_layout(bids_dir: Path) -> dict[str, Any]:
    root = Path(bids_dir).expanduser().resolve()
    subjects: dict[str, dict[str, list[str]]] = {}
    for sub_dir in sorted(root.glob("sub-*")):
        if not sub_dir.is_dir():
            continue
        sid = sub_dir.name
        anat = [str(p.relative_to(root)) for p in sorted((sub_dir / "anat").glob("*")) if p.is_file()]
        func = [str(p.relative_to(root)) for p in sorted((sub_dir / "func").glob("*")) if p.is_file()]
        dwi = [str(p.relative_to(root)) for p in sorted((sub_dir / "dwi").glob("*")) if p.is_file()]
        subjects[sid] = {"anat": anat[:30], "func": func[:30], "dwi": dwi[:30]}
    return {
        "root": str(root),
        "subjects": subjects,
    }


def _build_prompt(
    *,
    instruction: str,
    tools: list[dict[str, Any]],
    bids_layout: dict[str, Any],
    output_dir: Path,
) -> tuple[str, str]:
    system = (
        "You are GUIN, an MCP neuroimaging planning engine. "
        "Generate ONLY valid JSON with key `tool_calls` (array). "
        "Each item: {\"tool_name\": str, \"arguments\": object, \"reasoning\": str}. "
        "Use only provided tools. Choose concrete paths from BIDS layout. "
        "Output paths must be inside output_dir."
    )
    user = json.dumps(
        {
            "instruction": instruction,
            "output_dir": str(output_dir),
            "available_tools": tools,
            "bids_layout": bids_layout,
        },
        indent=2,
    )
    return system, user


def _parse_plan_json(text: str) -> list[ToolCall]:
    body = text.strip()
    m = _FENCE_RE.search(body)
    if m:
        body = m.group(1).strip()
    data = json.loads(body)
    calls_raw = data.get("tool_calls", []) if isinstance(data, dict) else []
    calls: list[ToolCall] = []
    if not isinstance(calls_raw, list):
        return calls
    for item in calls_raw:
        if not isinstance(item, dict):
            continue
        name = item.get("tool_name")
        args = item.get("arguments", {})
        reasoning = item.get("reasoning", "")
        if isinstance(name, str) and isinstance(args, dict):
            calls.append(ToolCall(tool_name=name, arguments=args, reasoning=str(reasoning)))
    return calls


def _prototype_fallback_plan(
    *,
    instruction: str,
    tools: list[dict[str, Any]],
    bids_layout: dict[str, Any],
    output_dir: Path,
    model: str,
) -> ExecutionPlan:
    names = {t["name"] for t in tools}
    subj = None
    m = re.search(r"(sub-\d+)", instruction, flags=re.IGNORECASE)
    if m:
        subj = m.group(1).lower()
    chosen_t1 = ""
    if subj and subj in bids_layout.get("subjects", {}):
        anat_files = bids_layout["subjects"][subj].get("anat", [])
        for f in anat_files:
            if "T1w" in f:
                chosen_t1 = f
                break
    calls: list[ToolCall] = []
    lower = instruction.lower()
    if "skull" in lower and "strip" in lower and "skull_strip_fsl" in names and chosen_t1:
        inp = str(Path(bids_layout["root"]) / chosen_t1)
        out = str((output_dir / chosen_t1).with_name(Path(chosen_t1).name.replace("_T1w", "_T1w_brain")))
        calls.append(
            ToolCall(
                tool_name="skull_strip_fsl",
                arguments={"input": inp, "output": out, "frac": 0.5},
                reasoning="Keyword + BIDS T1w match fallback planner.",
            )
        )
    elif "run_mriqc" in names:
        calls.append(
            ToolCall(
                tool_name="run_mriqc",
                arguments={
                    "bids_dir": bids_layout["root"],
                    "output_dir": str(output_dir),
                    "participant_label": [subj[4:]] if subj else [],
                    "run_group": True,
                },
                reasoning="Generic QC fallback.",
            )
        )
    prompt = json.dumps(
        {
            "fallback": True,
            "instruction": instruction,
            "tools": tools,
            "bids_layout": bids_layout,
            "output_dir": str(output_dir),
        },
        indent=2,
    )
    return ExecutionPlan(
        instruction=instruction,
        model=model,
        tool_calls=calls,
        prompt_text=prompt,
        response_text=json.dumps({"tool_calls": [c.__dict__ for c in calls]}, indent=2),
        usage=None,
        used_fallback=True,
    )


def generate_plan(
    *,
    instruction: str,
    bids_dir: Path,
    output_dir: Path,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> ExecutionPlan:
    """Generate a tool-call plan from NL instruction."""
    tools = _load_tool_schemas()
    bids_layout = _scan_bids_layout(bids_dir)
    system, user = _build_prompt(
        instruction=instruction,
        tools=tools,
        bids_layout=bids_layout,
        output_dir=output_dir,
    )
    key = api_key or ""
    if not key.strip():
        return _prototype_fallback_plan(
            instruction=instruction,
            tools=tools,
            bids_layout=bids_layout,
            output_dir=output_dir,
            model=model,
        )

    from anthropic import Anthropic  # type: ignore[import-untyped]

    client = Anthropic(api_key=key)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = ""
    for block in msg.content:
        if hasattr(block, "text"):
            text += str(block.text)
    calls = _parse_plan_json(text)
    usage = None
    u = getattr(msg, "usage", None)
    if u is not None:
        usage = {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        }
    if not calls:
        return _prototype_fallback_plan(
            instruction=instruction,
            tools=tools,
            bids_layout=bids_layout,
            output_dir=output_dir,
            model=model,
        )
    return ExecutionPlan(
        instruction=instruction,
        model=model,
        tool_calls=calls,
        prompt_text=f"SYSTEM:\n{system}\n\nUSER:\n{user}",
        response_text=text,
        usage=usage,
        used_fallback=False,
    )


def render_plan_python(plan: ExecutionPlan) -> str:
    """Render plan as readable Python preview."""
    lines = [
        "# GUIN Generated Plan",
        f"# Instruction: {plan.instruction}",
        f"# Model: {plan.model}",
        "",
        "from guin.mcp import call_tool",
        "",
    ]
    for i, call in enumerate(plan.tool_calls, start=1):
        lines.append(f"# Step {i}: {call.tool_name}")
        lines.append(
            f"result_{i} = call_tool({call.tool_name!r}, {json.dumps(call.arguments, indent=4, sort_keys=True)})"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _extract_output_paths(result: Any) -> list[Path]:
    out: list[Path] = []

    def visit(v: Any) -> None:
        if isinstance(v, dict):
            for k, iv in v.items():
                k_low = str(k).lower()
                if k_low in {"output", "output_path", "out"} and isinstance(iv, str):
                    p = Path(iv).expanduser()
                    if p.is_file():
                        out.append(p.resolve())
                else:
                    visit(iv)
        elif isinstance(v, list):
            for iv in v:
                visit(iv)

    visit(result)
    uniq = []
    seen: set[str] = set()
    for p in out:
        s = str(p)
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq


def execute_plan(
    plan: ExecutionPlan,
    *,
    console: Console,
    tracker: ProvenanceTracker | None = None,
) -> list[dict[str, Any]]:
    """Execute tool calls sequentially via MCP with rich logging."""
    rows: list[dict[str, Any]] = []
    for idx, call in enumerate(plan.tool_calls, start=1):
        console.print(f"[cyan]Step {idx}/{len(plan.tool_calls)}[/cyan] [bold]{call.tool_name}[/bold]")
        console.print(f"Arguments: {json.dumps(call.arguments, sort_keys=True)}")
        result = asyncio.run(mcp.call_tool(call.tool_name, call.arguments))
        normalized: dict[str, Any]
        if isinstance(result, tuple) and len(result) == 2:
            _content, structured = result
            normalized = {"result": structured}
        else:
            normalized = {"result": result}
        rows.append(
            {
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "result": normalized,
            }
        )
        console.print("[green]Done[/green]")

        if tracker is not None:
            tracker.record_tool_invocation(
                tool_name=call.tool_name,
                tool_version="unknown",
                input_parameters=call.arguments,
                output_files=_extract_output_paths(normalized),
            )
    return rows
