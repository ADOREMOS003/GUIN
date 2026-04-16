"""Build Nipype interfaces from MCP tool functions and JSON Schema input specs."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, cast

from nipype.interfaces.base import (  # type: ignore[import-untyped]
    BaseInterfaceInputSpec,
    Directory,
    SimpleInterface,
    TraitedSpec,
    isdefined,
    traits,
)

from guin.models.derivatives import BIDSDerivativeResult

logger = logging.getLogger(__name__)


class NipypeToolAdapter:
    """Factory for :class:`SimpleInterface` subclasses driven by MCP tools and JSON Schema."""

    @staticmethod
    def _split_schema(
        input_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], set[str]]:
        """Return ``(properties, required_field_names)``.

        Supports:

        - JSON Schema objects with ``properties`` and optional ``required``.
        - Flat maps ``{ param: {\"type\": ...}, ... }`` where parameters without
          a ``default`` are treated as required.
        """
        if "properties" in input_schema:
            props = input_schema["properties"]
            if not isinstance(props, dict):
                raise TypeError("input_schema['properties'] must be a mapping")
            req = input_schema.get("required") or []
            if not isinstance(req, list):
                raise TypeError("input_schema['required'] must be a list or omitted")
            return props, {str(x) for x in req}

        meta_keys = {"type", "required", "$schema", "title", "description", "additionalProperties"}
        props = {
            k: v
            for k, v in input_schema.items()
            if k not in meta_keys and isinstance(v, dict) and "type" in v
        }
        if not props:
            raise ValueError(
                "input_schema must include a 'properties' object or flat parameter mappings."
            )
        required = {k for k, p in props.items() if "default" not in p}
        return props, required

    @staticmethod
    def _trait_for_property(
        name: str,
        prop: Any,
        required_fields: set[str],
    ) -> traits.TraitType[Any, Any, Any]:
        """Map a single JSON Schema property to a Nipype trait (``mandatory`` = JSON ``required``)."""
        if not isinstance(prop, dict):
            raise TypeError(f"Property {name!r} must be a mapping, got {type(prop)}")

        mandatory = name in required_fields
        desc = str(prop.get("description", ""))
        jtype = prop.get("type")

        if jtype == "array":
            item_t = prop.get("items", {})
            if isinstance(item_t, dict) and item_t.get("type") != "string":
                raise ValueError(
                    f"Property {name!r}: only array of strings is supported (items.type: string)."
                )
            inner = traits.Str()
            if "default" in prop:
                dv = prop["default"]
                if dv is None:
                    return traits.List(inner, desc=desc)
                if not isinstance(dv, list):
                    raise TypeError(f"Property {name!r}: default must be a list or null")
                return traits.List(inner, default=dv, usedefault=True, desc=desc)
            if mandatory:
                return traits.List(inner, minlen=1, mandatory=True, desc=desc)
            return traits.List(inner, minlen=0, desc=desc)

        if jtype == "string":
            if "default" in prop:
                return traits.Str(prop["default"], usedefault=True, desc=desc)
            return traits.Str(mandatory=mandatory, desc=desc)

        if jtype == "number":
            if "default" in prop:
                return traits.Float(
                    prop["default"],
                    usedefault=True,
                    mandatory=mandatory,
                    desc=desc,
                )
            return traits.Float(mandatory=mandatory, desc=desc)

        if jtype == "integer":
            if "default" in prop:
                return traits.Int(
                    prop["default"],
                    usedefault=True,
                    mandatory=mandatory,
                    desc=desc,
                )
            return traits.Int(mandatory=mandatory, desc=desc)

        if jtype == "boolean":
            if "default" in prop:
                return traits.Bool(
                    prop["default"],
                    usedefault=True,
                    mandatory=mandatory,
                    desc=desc,
                )
            return traits.Bool(mandatory=mandatory, desc=desc)

        raise ValueError(f"Unsupported JSON Schema type for {name!r}: {jtype!r}")

    @classmethod
    def build_input_spec(
        cls,
        name: str,
        input_schema: dict[str, Any],
    ) -> type[BaseInterfaceInputSpec]:
        """Create a :class:`BaseInterfaceInputSpec` subclass from *input_schema*."""
        properties, required = cls._split_schema(input_schema)
        attrs: dict[str, Any] = {"__doc__": f"Input specification for MCP tool {name!r}."}
        for prop_name, prop_spec in properties.items():
            attrs[prop_name] = cls._trait_for_property(prop_name, prop_spec, required)
        return cast(
            type[BaseInterfaceInputSpec],
            type(f"{name}InputSpec", (BaseInterfaceInputSpec,), attrs),
        )

    @staticmethod
    def build_output_spec() -> type[TraitedSpec]:
        """Minimum derivative outputs for MCP-backed tools."""

        class MCPDerivativeOutputSpec(TraitedSpec):
            output_path = Directory(
                exists=True,
                desc="Primary output directory on disk (validated when outputs are set).",
            )
            provenance_hash = traits.Str(
                desc="Provenance digest (e.g. SHA-256 of dataset_description.json).",
            )

        return MCPDerivativeOutputSpec

    @staticmethod
    def _call_tool(
        tool_func: Callable[..., Any],
        kwargs: dict[str, Any],
    ) -> BIDSDerivativeResult | Any:
        """Invoke *tool_func* (sync or async) and return its result."""
        if inspect.iscoroutinefunction(tool_func):

            async def _one() -> Any:
                return await tool_func(**kwargs)

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_one())
            raise RuntimeError(
                "Cannot run async MCP tool from Nipype inside an active event loop; "
                "execute the interface from a non-async context."
            )

        return tool_func(**kwargs)

    @classmethod
    def _run_interface_impl(
        cls,
        tool_func: Callable[..., Any],
    ) -> Callable[[SimpleInterface, Any], Any]:
        def _run_interface(self: SimpleInterface, runtime: Any) -> Any:
            kwargs: dict[str, Any] = {}
            for pname in self.inputs.copyable_trait_names():
                val = getattr(self.inputs, pname)
                if isdefined(val):
                    kwargs[pname] = val

            logger.debug("Nipype MCP adapter calling tool with keys %s", sorted(kwargs))
            result = cls._call_tool(tool_func, kwargs)

            self._results = {}
            if isinstance(result, BIDSDerivativeResult):
                self._results["output_path"] = str(result.output_path)
                self._results["provenance_hash"] = result.provenance_hash
            else:
                out = getattr(result, "output_path", None)
                ph = getattr(result, "provenance_hash", "")
                self._results["output_path"] = str(out) if out is not None else ""
                self._results["provenance_hash"] = str(ph) if ph is not None else ""

            return runtime

        return _run_interface

    @classmethod
    def from_mcp_tool(
        cls,
        tool_name: str,
        tool_func: Callable[..., Any],
        input_schema: dict[str, Any],
    ) -> type[SimpleInterface]:
        """Return a :class:`SimpleInterface` subclass that wraps *tool_func*.

        *input_schema* must be JSON Schema with ``properties`` / ``required``, or a flat
        map of parameter names to property objects (parameters without ``default`` are
        required).
        """
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in tool_name)
        if safe and safe[0].isdigit():
            safe = f"t_{safe}"

        input_spec = cls.build_input_spec(safe, input_schema)
        output_spec = cls.build_output_spec()

        run_meth = cls._run_interface_impl(tool_func)

        iface_attrs: dict[str, Any] = {
            "input_spec": input_spec,
            "output_spec": output_spec,
            "_run_interface": run_meth,
        }

        return cast(
            type[SimpleInterface],
            type(f"{safe}MCPInterface", (SimpleInterface,), iface_attrs),
        )
