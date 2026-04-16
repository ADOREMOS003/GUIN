"""GUIN CLI entry point."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table

from guin import __version__
from guin.api import create_app
from guin.core.config import GuinCLIConfig, apply_config_env
from guin.planner.llm import execute_plan, generate_plan, render_plan_python
from guin.provenance.diff import provenance_diff
from guin.provenance.tracker import ProvenanceTracker

console = Console()


def _load_workflow_json_from_provenance(path: Path) -> dict[str, Any] | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("@graph"), list):
        nodes = payload["@graph"]
    elif isinstance(payload, list):
        nodes = payload
    else:
        return None

    for node in nodes:
        if not isinstance(node, dict):
            continue
        fmt_raw = node.get("https://guin.dev/prov#format")
        if not isinstance(fmt_raw, list):
            continue
        fmt_val = None
        if fmt_raw and isinstance(fmt_raw[0], dict):
            fmt_val = fmt_raw[0].get("@value")
        if fmt_val != "application/json":
            continue
        pval_raw = node.get("http://www.w3.org/ns/prov#value")
        if not isinstance(pval_raw, list) or not pval_raw:
            continue
        pval = pval_raw[0].get("@value") if isinstance(pval_raw[0], dict) else None
        if not isinstance(pval, str):
            continue
        try:
            obj = json.loads(pval)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


@click.group()
@click.version_option(version=__version__, prog_name="guin")
def cli() -> None:
    """GUIN: MCP-based neuroimaging workflow platform."""


@cli.command()
@click.argument("instruction")
@click.option("--bids-dir", required=True, type=click.Path(exists=True))
@click.option("--output-dir", required=True)
@click.option("--model", default="claude-sonnet-4-20250514")
@click.option("--dry-run", is_flag=True, help="Show generated code without executing")
@click.option(
    "--skip-bids-validation",
    is_flag=True,
    help="Skip BIDS validation before planning/execution.",
)
def run(
    instruction: str,
    bids_dir: str,
    output_dir: str,
    model: str,
    dry_run: bool,
    skip_bids_validation: bool,
) -> None:
    """Execute a natural language neuroimaging instruction."""
    cfg = GuinCLIConfig.load()
    apply_config_env(cfg)
    selected_model = cfg.model or model
    api_key = cfg.api_key or os.environ.get("ANTHROPIC_API_KEY")

    from guin.mcp_server.server import validate_bids

    should_validate = not dry_run and not skip_bids_validation
    if should_validate:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            progress.add_task("Validating BIDS dataset...", total=None)
            validation = validate_bids(
                dataset_path=str(Path(bids_dir).expanduser().resolve())
            )

        if not bool(validation.get("valid", False)):
            errors = validation.get("errors", [])
            pretty = "\n".join(f"- {e}" for e in errors) if errors else "- Unknown issue"
            raise click.ClickException(f"BIDS validation failed:\n{pretty}")
    else:
        reason = "--dry-run" if dry_run else "--skip-bids-validation"
        console.print(f"[yellow]Skipping BIDS validation due to {reason}.[/yellow]")

    bids_root = Path(bids_dir).expanduser().resolve()
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    tracker = ProvenanceTracker(out_root, llm_model_name=selected_model)
    tracker.track_instruction(instruction)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task("Planning tool sequence with LLM...", total=None)
        plan = generate_plan(
            instruction=instruction,
            bids_dir=bids_root,
            output_dir=out_root,
            model=selected_model,
            api_key=api_key,
        )

    if not plan.tool_calls:
        raise click.ClickException(
            "Planner returned no executable tool calls. Rephrase instruction or "
            "ensure required tools are registered."
        )

    tracker.track_llm_code(
        plan.prompt_text,
        iteration=1,
        metadata={
            "llm_record_type": "prompt",
            "model": plan.model,
            "used_fallback": plan.used_fallback,
        },
    )
    response_meta: dict[str, Any] = {
        "llm_record_type": "response",
        "model": plan.model,
        "used_fallback": plan.used_fallback,
    }
    if plan.usage:
        response_meta["usage_input_tokens"] = plan.usage.get("input_tokens", 0)
        response_meta["usage_output_tokens"] = plan.usage.get("output_tokens", 0)
    tracker.track_llm_code(plan.response_text, iteration=2, metadata=response_meta)

    code = render_plan_python(plan)
    tracker.track_llm_code(
        code,
        iteration=3,
        metadata={"llm_record_type": "generated_plan_python", "model": plan.model},
    )
    console.print(
        Panel.fit(
            f"[bold]Model:[/bold] {selected_model}\n"
            f"[bold]Instruction:[/bold] {instruction}\n"
            f"[bold]BIDS:[/bold] {bids_root}\n"
            f"[bold]Output:[/bold] {out_root}\n"
            f"[bold]Planner:[/bold] {'fallback' if plan.used_fallback else 'anthropic'}\n"
            f"[bold]Tool Calls:[/bold] {len(plan.tool_calls)}",
            title="GUIN Run",
            border_style="green",
        )
    )
    console.print(Syntax(code, "python", line_numbers=True, theme="monokai"))

    if dry_run:
        prov_path = tracker.save()
        console.print(f"[green]Provenance saved:[/green] {prov_path}")
        console.print("[yellow]Dry run enabled: no execution performed.[/yellow]")
        return

    script_path = out_root / "guin_generated_plan.py"
    script_path.write_text(code, encoding="utf-8")
    results = execute_plan(plan, console=console, tracker=tracker)
    results_path = out_root / "guin_execution_results.json"
    results_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    prov_path = tracker.save()
    console.print(
        f"[green]Generated plan saved:[/green] {script_path}\n"
        f"[green]Execution results saved:[/green] {results_path}\n"
        f"[green]Provenance saved:[/green] {prov_path}"
    )


@cli.command()
def tools() -> None:
    """List all registered MCP tools with their schemas."""
    cfg = GuinCLIConfig.load()
    apply_config_env(cfg)

    from guin.mcp_server.server import mcp

    rows = asyncio.run(mcp.list_tools())
    table = Table(title="Registered MCP Tools")
    table.add_column("Name", style="bold cyan")
    table.add_column("Description", overflow="fold")
    table.add_column("Input Schema", overflow="fold")

    for t in rows:
        schema = json.dumps(getattr(t, "inputSchema", {}), indent=2, sort_keys=True)
        table.add_row(
            str(getattr(t, "name", "")),
            str(getattr(t, "description", "")).strip(),
            schema,
        )
    console.print(table)


@cli.command()
@click.argument("dataset_path")
def validate(dataset_path: str) -> None:
    """Run BIDS validation on a dataset."""
    cfg = GuinCLIConfig.load()
    apply_config_env(cfg)
    from guin.mcp_server.server import validate_bids

    result = validate_bids(dataset_path=dataset_path)
    ok = bool(result.get("valid", False))
    if ok:
        console.print("[green]BIDS validation passed.[/green]")
    else:
        console.print("[red]BIDS validation failed.[/red]")
    for err in result.get("errors", []):
        console.print(f"[red]- {err}[/red]")
    for warn in result.get("warnings", []):
        console.print(f"[yellow]- {warn}[/yellow]")
    if not ok:
        raise click.ClickException("Dataset is not valid BIDS.")


@cli.command()
@click.argument("provenance_file", type=click.Path(exists=True))
def replay(provenance_file: str) -> None:
    """Re-execute a recorded workflow without the LLM."""
    path = Path(provenance_file).expanduser().resolve()
    workflow_json = _load_workflow_json_from_provenance(path)
    if workflow_json is None:
        raise click.ClickException(
            "No serialized workflow JSON found in provenance record."
        )

    from guin.agent.workflow_gen import WorkflowGenerator

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task("Loading workflow from provenance...", total=None)
        gen = WorkflowGenerator.from_json(workflow_json)
        progress.add_task("Executing workflow...", total=None)
        gen.run(plugin="Linear")

    console.print("[green]Replay completed successfully.[/green]")


@cli.command()
@click.option("--port", default=8080)
def serve(port: int) -> None:
    """Start the GUIN web interface."""
    import uvicorn

    cfg = GuinCLIConfig.load()
    app = create_app(config=cfg)

    console.print(f"[green]Starting GUIN web interface on port {port}[/green]")
    uvicorn.run(app, host="0.0.0.0", port=int(port))


@cli.command()
@click.argument("record_a", type=click.Path(exists=True))
@click.argument("record_b", type=click.Path(exists=True))
def diff(record_a: str, record_b: str) -> None:
    """Compare two provenance records."""
    pdiff = provenance_diff(Path(record_a), Path(record_b))
    md = pdiff.to_markdown()
    console.print(Panel.fit(md, title="Provenance Diff", border_style="blue"))


if __name__ == "__main__":
    cli()
