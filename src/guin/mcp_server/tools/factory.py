"""Load YAML tool specs and register dynamically generated MCP tools."""

from __future__ import annotations

import logging
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml  # type: ignore[import-untyped]
from jinja2 import Environment, StrictUndefined, meta
from pydantic import BaseModel, Field, create_model

from guin.mcp_server.server import _resolve_container_sif, mcp, run_container

logger = logging.getLogger(__name__)

_JSON_TO_PY: dict[str, type[Any]] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


@dataclass(frozen=True)
class ToolSpec:
    """Validated tool definition from ``tool_specs/*.yaml``."""

    name: str
    description: str
    container: str
    cli_template: str
    input_schema: dict[str, Any]
    output_bids_suffix: str
    modality: str
    bind_paths: list[str]


_SPEC_REGISTRY: dict[str, ToolSpec] = {}
_MODEL_REGISTRY: dict[str, type[BaseModel]] = {}


def _json_prop_to_field(name: str, prop: Any) -> tuple[Any, Any]:
    if not isinstance(prop, dict):
        raise TypeError(f"input_schema.{name} must be a mapping, got {type(prop)}")
    jt = prop.get("type")
    if jt not in _JSON_TO_PY:
        raise ValueError(f"Unsupported JSON Schema type for {name!r}: {jt!r}")
    py_t: type[Any] = _JSON_TO_PY[jt]
    desc = str(prop.get("description", ""))
    if "default" in prop:
        return (py_t, Field(default=prop["default"], description=desc))
    return (py_t, Field(..., description=desc))


def _build_input_model(spec: ToolSpec) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for key, prop in spec.input_schema.items():
        fields[key] = _json_prop_to_field(key, prop)
    model_name = "".join(w.title() for w in re.split(r"[^a-zA-Z0-9]+", spec.name) if w) + "Inputs"
    return create_model(model_name, **fields)  # type: ignore[call-overload]


def _validate_template_uses_schema(spec: ToolSpec) -> None:
    """Ensure every Jinja variable in *cli_template* has a key in *input_schema*."""
    env = Environment()
    ast = env.parse(spec.cli_template)
    needed = meta.find_undeclared_variables(ast)
    keys = set(spec.input_schema.keys())
    missing = needed - keys
    if missing:
        raise ValueError(
            f"Tool {spec.name!r}: cli_template references undefined input_schema keys: "
            f"{sorted(missing)}. Declared keys: {sorted(keys)}."
        )


def _resolve_host_for_logical(logical: str, data: dict[str, Any]) -> Path:
    if logical == "input_dir":
        if "input" not in data:
            raise ValueError("bind input_dir requires parameter 'input'")
        return Path(data["input"]).resolve().parent
    if logical == "output_dir":
        if "output" in data:
            return Path(data["output"]).resolve().parent
        if "out" in data:
            return Path(data["out"]).resolve().parent
        if "output_prefix" in data:
            return Path(data["output_prefix"]).resolve().parent
        raise ValueError(
            "bind output_dir requires one of: output, out, output_prefix"
        )
    if logical == "ref_dir":
        if "ref" not in data:
            raise ValueError("bind ref_dir requires parameter 'ref'")
        return Path(data["ref"]).resolve().parent
    raise ValueError(f"Unknown bind_paths entry {logical!r} (supported: input_dir, output_dir, ref_dir)")


def _build_container_paths_and_binds(
    spec: ToolSpec, data: dict[str, Any]
) -> tuple[dict[str, Any], list[tuple[Path, Path]]]:
    """Map validated host paths to container paths and build Apptainer bind list."""
    host_to_mount: dict[Path, Path] = {}
    logical_to_mount: dict[str, Path] = {}
    binds: list[tuple[Path, Path]] = []

    for logical in spec.bind_paths:
        host = _resolve_host_for_logical(logical, data).resolve()
        if host not in host_to_mount:
            mp = Path(f"/data/{logical}")
            host_to_mount[host] = mp
            binds.append((host, mp))
        logical_to_mount[logical] = host_to_mount[host]

    out = dict(data)
    if "input" in data:
        root = logical_to_mount["input_dir"]
        out["input"] = str(root / Path(data["input"]).name)
    if "output" in data:
        root = logical_to_mount["output_dir"]
        out["output"] = str(root / Path(data["output"]).name)
    if "out" in data:
        root = logical_to_mount["output_dir"]
        out["out"] = str(root / Path(data["out"]).name)
    if "ref" in data:
        root = logical_to_mount["ref_dir"]
        out["ref"] = str(root / Path(data["ref"]).name)
    if "output_prefix" in data:
        root = logical_to_mount["output_dir"]
        out["output_prefix"] = str(root / Path(data["output_prefix"]).name)

    return out, binds


def _resolve_container_image(container: str) -> Path:
    raw = Path(container)
    if raw.is_file():
        return raw.resolve()
    if raw.suffix.lower() == ".sif":
        return _resolve_container_sif(raw.stem)
    return _resolve_container_sif(str(raw))


async def _guin_dynamic_tool_dispatch(name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    spec = _SPEC_REGISTRY[name]
    model = _MODEL_REGISTRY[name]
    validated = model(**kwargs)
    data = validated.model_dump()
    container_data, binds = _build_container_paths_and_binds(spec, data)

    env = Environment(undefined=StrictUndefined)
    try:
        rendered = env.from_string(spec.cli_template).render(**container_data)
    except Exception as exc:
        raise ValueError(f"Template render failed for {name!r}: {exc}") from exc

    posix = os.name != "nt"
    argv = shlex.split(rendered, posix=posix)
    if not argv:
        raise ValueError(f"Rendered CLI is empty for {name!r}")

    sif = _resolve_container_image(spec.container)
    stdout, stderr, code = await run_container(
        sif,
        argv,
        bind_paths=binds,
        timeout_seconds=None,
    )
    return {
        "tool": spec.name,
        "command": rendered,
        "argv": argv,
        "exit_code": code,
        "stdout": stdout,
        "stderr": stderr,
        "output_bids_suffix": spec.output_bids_suffix,
        "modality": spec.modality,
    }


def _sanitize_identifier(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if safe and safe[0].isdigit():
        safe = f"t_{safe}"
    return safe or "dynamic_tool"


def _compile_tool_function(spec: ToolSpec) -> Callable[..., Any]:
    """Build an ``async def`` with one parameter per *input_schema* key for FastMCP schemas."""
    safe = _sanitize_identifier(spec.name)
    params: list[str] = []
    for key, prop in spec.input_schema.items():
        if not isinstance(prop, dict):
            raise TypeError(f"{spec.name}: input_schema.{key} must be a mapping")
        jt = prop.get("type")
        if jt not in _JSON_TO_PY:
            raise ValueError(f"{spec.name}: bad type for {key}: {jt!r}")
        ann = _JSON_TO_PY[jt].__name__
        if "default" in prop:
            params.append(f"{key}: {ann} = {repr(prop['default'])}")
        else:
            params.append(f"{key}: {ann}")
    sig = ", ".join(params)
    pairs = ", ".join(f"{k!r}: {k}" for k in spec.input_schema)
    code = (
        f"async def {safe}({sig}) -> dict[str, Any]:\n"
        f"    return await _guin_dynamic_tool_dispatch({spec.name!r}, {{{pairs}}})\n"
    )
    ns: dict[str, Any] = {
        "_guin_dynamic_tool_dispatch": _guin_dynamic_tool_dispatch,
        "Any": Any,
    }
    exec(code, ns)  # noqa: S102 — intentional codegen from trusted YAML
    fn = ns[safe]
    fn.__name__ = safe
    fn.__doc__ = spec.description
    fn.__qualname__ = safe
    return fn


class ToolFactory:
    """Load ``*.yaml`` tool specs and register generated tools on a :class:`FastMCP` app."""

    def __init__(
        self,
        specs_dir: Path | None = None,
        mcp_app: Any | None = None,
    ) -> None:
        self.specs_dir = (
            specs_dir if specs_dir is not None else Path(__file__).resolve().parent / "tool_specs"
        )
        self.mcp = mcp_app if mcp_app is not None else mcp

    def _load_spec_file(self, path: Path) -> ToolSpec:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: root must be a mapping")
        required = (
            "name",
            "description",
            "container",
            "cli_template",
            "input_schema",
            "output_bids_suffix",
            "modality",
            "bind_paths",
        )
        for key in required:
            if key not in raw:
                raise ValueError(f"{path}: missing required key {key!r}")
        name = str(raw["name"])
        if not name.isidentifier():
            raise ValueError(f"{path}: name {name!r} must be a valid Python identifier")
        bind_paths = raw["bind_paths"]
        if not isinstance(bind_paths, list) or not all(isinstance(x, str) for x in bind_paths):
            raise ValueError(f"{path}: bind_paths must be a list of strings")
        input_schema = raw["input_schema"]
        if not isinstance(input_schema, dict):
            raise ValueError(f"{path}: input_schema must be a mapping")

        return ToolSpec(
            name=name,
            description=str(raw["description"]),
            container=str(raw["container"]),
            cli_template=str(raw["cli_template"]),
            input_schema=input_schema,
            output_bids_suffix=str(raw["output_bids_suffix"]),
            modality=str(raw["modality"]),
            bind_paths=bind_paths,
        )

    def load_and_register(self) -> list[str]:
        """Load all ``tool_specs/*.yaml`` files and :meth:`FastMCP.add_tool` for each."""
        if not self.specs_dir.is_dir():
            logger.warning("Tool specs directory missing: %s", self.specs_dir)
            return []

        registered: list[str] = []
        seen: set[str] = set()
        for path in sorted(self.specs_dir.glob("*.yaml")):
            spec = self._load_spec_file(path)
            if spec.name in seen:
                raise ValueError(f"Duplicate tool name {spec.name!r} in {path}")
            seen.add(spec.name)
            _validate_template_uses_schema(spec)
            model = _build_input_model(spec)
            _SPEC_REGISTRY[spec.name] = spec
            _MODEL_REGISTRY[spec.name] = model
            fn = _compile_tool_function(spec)
            self.mcp.add_tool(fn, name=spec.name, description=spec.description)
            registered.append(spec.name)
            logger.info("Registered dynamic tool %s from %s", spec.name, path.name)
        return registered


def _register_factory_tools() -> None:
    ToolFactory().load_and_register()


_register_factory_tools()
