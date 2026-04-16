"""W3C PROV-O tracking for GUIN sessions with JSON-LD serialization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prov.model import ProvDocument  # type: ignore[import-untyped]
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from guin import __version__ as GUIN_VERSION
from guin.rag.indexer import DocChunk

GUIN_NS = Namespace("https://guin.dev/prov#")
PROV_NS = Namespace("http://www.w3.org/ns/prov#")


@dataclass(frozen=True)
class VerificationItem:
    """One file-level verification result."""

    file_path: Path
    expected_sha256: str
    observed_sha256: str | None
    passed: bool
    reason: str


@dataclass(frozen=True)
class VerificationReport:
    """Aggregated integrity verification report."""

    all_passed: bool
    checked_at: datetime
    items: list[VerificationItem]


class ProvenanceTracker:
    """Collect and serialize session provenance as W3C PROV-O JSON-LD."""

    def __init__(
        self,
        output_dir: Path,
        *,
        llm_model_name: str,
        guin_version: str = GUIN_VERSION,
        session_timestamp: datetime | None = None,
    ) -> None:
        ts = session_timestamp or datetime.now(timezone.utc)
        self._session_timestamp = ts
        self._session_id = ts.strftime("%Y%m%dT%H%M%SZ")
        self._output_dir = Path(output_dir).expanduser().resolve()
        self._prov_dir = (
            self._output_dir / "derivatives" / "guin" / "provenance"
        ).resolve()
        self._prov_dir.mkdir(parents=True, exist_ok=True)
        self._output_file = self._prov_dir / f"session_{self._session_id}_provenance.jsonld"

        self._prov = ProvDocument()
        self._prov.add_namespace("guin", str(GUIN_NS))
        self._prov.add_namespace("prov", str(PROV_NS))

        self._rdf = Graph()
        self._rdf.bind("guin", GUIN_NS)
        self._rdf.bind("prov", PROV_NS)

        self._agent_id = f"guin:agent_{self._session_id}"
        self._instruction_id: str | None = None
        self._last_llm_code_id: str | None = None
        self._rag_entity_ids: list[str] = []
        self._output_hashes: dict[Path, str] = {}
        self._entity_counter = 0
        self._activity_counter = 0

        self._register_runtime_agent(
            guin_version=guin_version,
            llm_model_name=llm_model_name,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    @staticmethod
    def _sha256_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _lit(value: Any) -> Literal:
        if isinstance(value, datetime):
            return Literal(value.isoformat(), datatype=XSD.dateTime)
        if isinstance(value, bool):
            return Literal(value, datatype=XSD.boolean)
        if isinstance(value, int):
            return Literal(value, datatype=XSD.integer)
        if isinstance(value, float):
            return Literal(value, datatype=XSD.double)
        return Literal(str(value))

    def _uri(self, compact_id: str) -> URIRef:
        _, local = compact_id.split(":", 1)
        return URIRef(f"{GUIN_NS}{local}")

    def _next_entity_id(self, prefix: str) -> str:
        self._entity_counter += 1
        return f"guin:{prefix}_{self._entity_counter:05d}"

    def _next_activity_id(self, prefix: str) -> str:
        self._activity_counter += 1
        return f"guin:{prefix}_{self._activity_counter:05d}"

    def _add_entity(self, entity_id: str, attributes: dict[str, Any]) -> None:
        self._prov.entity(entity_id, attributes)
        e_uri = self._uri(entity_id)
        self._rdf.add((e_uri, RDF.type, PROV_NS.Entity))
        for key, value in attributes.items():
            if key == "prov:value":
                pred = PROV_NS.value
            elif key.startswith("guin:"):
                pred = GUIN_NS[key.split(":", 1)[1]]
            else:
                pred = URIRef(key)
            self._rdf.add((e_uri, pred, self._lit(value)))

    def _add_activity(
        self,
        activity_id: str,
        *,
        start_time: datetime,
        end_time: datetime,
        attributes: dict[str, Any],
    ) -> None:
        self._prov.activity(activity_id, start_time, end_time, attributes)
        a_uri = self._uri(activity_id)
        self._rdf.add((a_uri, RDF.type, PROV_NS.Activity))
        self._rdf.add((a_uri, PROV_NS.startedAtTime, self._lit(start_time)))
        self._rdf.add((a_uri, PROV_NS.endedAtTime, self._lit(end_time)))
        for key, value in attributes.items():
            if key.startswith("guin:"):
                pred = GUIN_NS[key.split(":", 1)[1]]
            else:
                pred = URIRef(key)
            self._rdf.add((a_uri, pred, self._lit(value)))

    def _register_runtime_agent(self, *, guin_version: str, llm_model_name: str) -> None:
        attrs = {
            "guin:guin_version": guin_version,
            "guin:llm_model_name": llm_model_name,
            "guin:python_version": platform.python_version(),
        }
        self._prov.agent(self._agent_id, attrs)
        a_uri = self._uri(self._agent_id)
        self._rdf.add((a_uri, RDF.type, PROV_NS.Agent))
        for k, v in attrs.items():
            self._rdf.add((a_uri, GUIN_NS[k.split(":", 1)[1]], self._lit(v)))

    def track_instruction(self, instruction_text: str) -> str:
        """Record the user's natural-language instruction verbatim."""
        eid = self._next_entity_id("instruction")
        attrs = {
            "prov:value": instruction_text,
            "guin:content_sha256": self._sha256_text(instruction_text),
            "guin:created_at": self._now(),
        }
        self._add_entity(eid, attrs)
        self._instruction_id = eid
        return eid

    def track_rag_chunk(
        self,
        *,
        chunk_text: str,
        source_url: str,
        tool_name: str | None = None,
        chunk_id: str | None = None,
    ) -> str:
        """Record one retrieved RAG chunk with source metadata."""
        eid = self._next_entity_id("rag_chunk")
        attrs: dict[str, Any] = {
            "prov:value": chunk_text,
            "guin:source_url": source_url,
            "guin:content_sha256": self._sha256_text(chunk_text),
            "guin:created_at": self._now(),
        }
        if chunk_id:
            attrs["guin:chunk_id"] = chunk_id
        if tool_name:
            attrs["guin:tool_name"] = tool_name
        self._add_entity(eid, attrs)
        self._rag_entity_ids.append(eid)
        return eid

    def track_rag_chunks(self, chunks: list[DocChunk]) -> list[str]:
        """Record a batch of retrieved chunks from the RAG indexer."""
        out: list[str] = []
        for c in chunks:
            out.append(
                self.track_rag_chunk(
                    chunk_text=c.text,
                    source_url=c.source_url,
                    tool_name=c.tool_name or None,
                    chunk_id=c.id,
                )
            )
        return out

    def track_llm_code(
        self,
        code_text: str,
        *,
        iteration: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record LLM-generated code; links iterations when self-correction occurs."""
        eid = self._next_entity_id("llm_code")
        attrs: dict[str, Any] = {
            "prov:value": code_text,
            "guin:content_sha256": self._sha256_text(code_text),
            "guin:created_at": self._now(),
        }
        if iteration is not None:
            attrs["guin:iteration"] = iteration
        if metadata:
            for k, v in metadata.items():
                attrs[f"guin:{k}"] = v
        self._add_entity(eid, attrs)
        if self._last_llm_code_id is not None:
            self._prov.wasDerivedFrom(eid, self._last_llm_code_id)
            self._rdf.add(
                (
                    self._uri(eid),
                    PROV_NS.wasDerivedFrom,
                    self._uri(self._last_llm_code_id),
                )
            )
        self._last_llm_code_id = eid
        return eid

    def track_workflow_graph(self, workflow_json: str | dict[str, Any]) -> str:
        """Record the serialized Nipype workflow graph JSON."""
        payload = (
            workflow_json
            if isinstance(workflow_json, str)
            else json.dumps(workflow_json, sort_keys=True)
        )
        eid = self._next_entity_id("workflow")
        self._add_entity(
            eid,
            {
                "prov:value": payload,
                "guin:content_sha256": self._sha256_text(payload),
                "guin:format": "application/json",
                "guin:created_at": self._now(),
            },
        )
        return eid

    async def _inspect_container_digest(self, container_sif: Path | None) -> str:
        if container_sif is None:
            return ""
        sif = Path(container_sif).expanduser().resolve()
        if not sif.is_file():
            return f"(missing sif: {sif})"
        apptainer = os.environ.get("GUIN_APPTAINER_BINARY", "apptainer")
        argv = [apptainer, "inspect", "--json", str(sif)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return f"(apptainer not found: {exc})"
        out_b, err_b = await proc.communicate()
        text = (out_b or b"").decode(errors="replace")
        err = (err_b or b"").decode(errors="replace").strip()
        if proc.returncode != 0:
            return err or f"(inspect failed with exit {proc.returncode})"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return self._sha256_text(text)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        attrs = data.get("attributes", {}) if isinstance(data, dict) else {}
        labels = attrs.get("labels", {}) if isinstance(attrs, dict) else {}
        for key in (
            "org.label-schema.build-date",
            "org.label-schema.vcs-ref",
            "org.label-schema.usage.singularity.deffile.from",
            "org.label-schema.usage.singularity.deffile.bootstrap",
        ):
            if key in labels:
                continue
        # Apptainer output has no universal digest field; record stable digest of inspect JSON.
        return self._sha256_text(json.dumps(payload, sort_keys=True))

    async def arecord_tool_invocation(
        self,
        *,
        tool_name: str,
        tool_version: str,
        input_parameters: dict[str, Any],
        output_files: list[Path],
        container_sif: Path | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        used_entity_ids: list[str] | None = None,
    ) -> str:
        """Record one tool invocation activity and generated output entities."""
        st = start_time or self._now()
        et = end_time or self._now()
        container_digest = await self._inspect_container_digest(container_sif)
        output_hashes: dict[str, str] = {}
        for file_path in output_files:
            p = Path(file_path).expanduser().resolve()
            if p.is_file():
                sha = self._sha256_file(p)
                output_hashes[str(p)] = sha
                self._output_hashes[p] = sha
            else:
                output_hashes[str(p)] = "(missing)"

        aid = self._next_activity_id("tool_invocation")
        activity_attrs = {
            "guin:tool_name": tool_name,
            "guin:tool_version": tool_version,
            "guin:container_digest": container_digest,
            "guin:input_parameters": json.dumps(input_parameters, sort_keys=True, default=str),
            "guin:output_files": json.dumps(output_hashes, sort_keys=True),
        }
        self._add_activity(
            aid,
            start_time=st,
            end_time=et,
            attributes=activity_attrs,
        )
        self._prov.wasAssociatedWith(aid, self._agent_id)
        self._rdf.add(
            (self._uri(aid), PROV_NS.wasAssociatedWith, self._uri(self._agent_id))
        )

        to_use = used_entity_ids[:] if used_entity_ids else []
        if self._instruction_id and self._instruction_id not in to_use:
            to_use.append(self._instruction_id)
        for rid in self._rag_entity_ids:
            if rid not in to_use:
                to_use.append(rid)
        for eid in to_use:
            self._prov.used(aid, eid)
            self._rdf.add((self._uri(aid), PROV_NS.used, self._uri(eid)))

        for out_path, out_hash in output_hashes.items():
            e_out = self._next_entity_id("output")
            self._add_entity(
                e_out,
                {
                    "guin:file_path": out_path,
                    "guin:sha256": out_hash,
                },
            )
            self._prov.wasGeneratedBy(e_out, aid)
            self._rdf.add((self._uri(e_out), PROV_NS.wasGeneratedBy, self._uri(aid)))

        return aid

    def record_tool_invocation(self, **kwargs: Any) -> str:
        """Sync wrapper around :meth:`arecord_tool_invocation`."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arecord_tool_invocation(**kwargs))
        raise RuntimeError(
            "record_tool_invocation() cannot run inside an active event loop; "
            "use arecord_tool_invocation(...) instead."
        )

    def save(self) -> Path:
        """Serialize current provenance graph to JSON-LD."""
        text = self._rdf.serialize(format="json-ld", indent=2)
        self._output_file.write_text(text, encoding="utf-8")
        return self._output_file

    def verify(self) -> VerificationReport:
        """Re-hash tracked output files and compare with recorded hashes."""
        items: list[VerificationItem] = []
        for path, expected in sorted(self._output_hashes.items(), key=lambda x: str(x[0])):
            if not path.is_file():
                items.append(
                    VerificationItem(
                        file_path=path,
                        expected_sha256=expected,
                        observed_sha256=None,
                        passed=False,
                        reason="File missing",
                    )
                )
                continue
            observed = self._sha256_file(path)
            passed = observed == expected
            items.append(
                VerificationItem(
                    file_path=path,
                    expected_sha256=expected,
                    observed_sha256=observed,
                    passed=passed,
                    reason="OK" if passed else "Hash mismatch",
                )
            )
        return VerificationReport(
            all_passed=all(i.passed for i in items),
            checked_at=self._now(),
            items=items,
        )
