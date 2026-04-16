"""Map natural-language queries to ranked tool suggestions using indexed documentation."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from guin.rag.indexer import DocChunk, DocumentationIndexer

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.MULTILINE)
_CLI_FLAG = re.compile(
    r"(?:^|\s)(--[a-zA-Z][a-zA-Z0-9_-]*)(?:\s+|=|\[)([^\s\[\],]+)?",
)
_SHORT_FLAG = re.compile(r"(?:^|\s)(-[a-zA-Z])(?:\s+|=)([^\s,]+)")


@dataclass
class ToolSuggestion:
    """One ranked neuroimaging tool recommendation derived from retrieved docs."""

    tool_name: str
    confidence: float  # 0–1
    relevant_doc_chunk: str
    suggested_parameters: dict[str, Any]
    reasoning: str


def _confidence_from_distance(distance: float | None) -> float:
    if distance is None:
        return 0.55
    # Lower distance ⇒ stronger match (Chroma default metric is L2 on embeddings).
    return max(0.05, min(1.0, 1.0 / (1.0 + float(distance))))


def _parse_cli_flags(text: str, max_flags: int = 16) -> dict[str, Any]:
    """Pull common CLI flags from help-style text for heuristic parameter hints."""
    out: dict[str, Any] = {}
    for pattern in (_CLI_FLAG, _SHORT_FLAG):
        for m in pattern.finditer(text):
            raw = m.group(1)
            key = raw.lstrip("-").replace("-", "_")
            if not key or key in out:
                continue
            val: Any = True
            if len(m.groups()) >= 2 and m.lastindex and m.lastindex >= 2:
                g2 = m.group(2)
                if g2:
                    val = g2.strip("\"'")
            out[key] = val
            if len(out) >= max_flags:
                return out
    return out


def _format_chunks_for_prompt(chunks: list[DocChunk]) -> str:
    parts: list[str] = []
    for i, c in enumerate(chunks, start=1):
        parts.append(
            f"### Chunk {i}\n"
            f"- tool_name (metadata): {c.tool_name}\n"
            f"- modality: {c.modality}\n"
            f"- doc_type: {c.doc_type}\n"
            f"- distance: {c.distance}\n"
            f"```\n{c.text.strip()}\n```\n"
        )
    return "\n".join(parts)


def _extract_json_array(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1).strip()
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Expected JSON array")
    return [x for x in data if isinstance(x, dict)]


def _suggestion_from_dict(d: dict[str, Any], fallback_chunk: str) -> ToolSuggestion | None:
    name = d.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        return None
    conf = d.get("confidence", 0.5)
    if isinstance(conf, (int, float)):
        confidence = max(0.0, min(1.0, float(conf)))
    else:
        confidence = 0.5
    chunk = d.get("relevant_doc_chunk")
    if not isinstance(chunk, str) or not chunk.strip():
        chunk = fallback_chunk
    params = d.get("suggested_parameters")
    if not isinstance(params, dict):
        params = {}
    reasoning = d.get("reasoning")
    if not isinstance(reasoning, str):
        reasoning = "Selected from retrieved documentation."
    return ToolSuggestion(
        tool_name=name.strip(),
        confidence=confidence,
        relevant_doc_chunk=chunk.strip(),
        suggested_parameters=dict(params),
        reasoning=reasoning.strip(),
    )


class ToolSelector:
    """Retrieve doc chunks for a query and rank tool suggestions for CodeAct planning."""

    def __init__(
        self,
        indexer: DocumentationIndexer,
        *,
        retrieve_top_k: int = 15,
        anthropic_model: str = "claude-3-5-haiku-20241022",
        anthropic_client: Any | None = None,
        use_llm: bool | None = None,
    ) -> None:
        self._indexer = indexer
        self._retrieve_top_k = retrieve_top_k
        self._anthropic_model = anthropic_model
        self._use_llm = (
            use_llm
            if use_llm is not None
            else bool(os.environ.get("ANTHROPIC_API_KEY"))
        )
        self._anthropic: Any | None = anthropic_client
        if self._use_llm and self._anthropic is None:
            try:
                from anthropic import Anthropic  # noqa: PLC0415

                self._anthropic = Anthropic()
            except Exception as exc:  # pragma: no cover - import/env guard
                logger.warning(
                    "LLM tool extraction disabled (Anthropic client unavailable): %s",
                    exc,
                )
                self._use_llm = False

    def select(
        self,
        query: str,
        *,
        top_k: int = 5,
        modality_filter: str | None = None,
    ) -> list[ToolSuggestion]:
        """Return ranked tool suggestions for *query* using retrieval + extraction."""
        chunks = self._indexer.retrieve(
            query,
            top_k=self._retrieve_top_k,
            modality_filter=modality_filter,
        )
        if not chunks:
            return []

        if self._use_llm and self._anthropic is not None:
            try:
                return self._select_with_llm(query, chunks, top_k=top_k)
            except Exception as exc:
                logger.warning("LLM tool extraction failed, using heuristic: %s", exc)

        return self._select_heuristic(chunks, top_k=top_k)

    def _select_with_llm(
        self,
        query: str,
        chunks: list[DocChunk],
        *,
        top_k: int,
    ) -> list[ToolSuggestion]:
        client = self._anthropic
        assert client is not None
        system = (
            "You are a neuroimaging workflow assistant. Given retrieved documentation "
            "chunks, identify distinct command-line tools that best address the user's "
            "goal. For each tool, propose typical CLI parameters as a JSON object "
            "(flag names without leading dashes as keys, string values when known). "
            "Respond with ONLY a JSON array (no prose outside JSON). Each element must "
            "have: tool_name (string), confidence (0-1 number), relevant_doc_chunk "
            "(verbatim or lightly trimmed excerpt from the chunks), "
            "suggested_parameters (object), reasoning (short string)."
        )
        user = (
            f"User query:\n{query}\n\nRetrieved documentation:\n\n"
            f"{_format_chunks_for_prompt(chunks)}"
        )
        msg = client.messages.create(
            model=self._anthropic_model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = ""
        for block in msg.content:
            if hasattr(block, "text"):
                text += block.text
        items = _extract_json_array(text)
        seen: set[str] = set()
        out: list[ToolSuggestion] = []
        for item in items:
            fb = chunks[0].text if chunks else ""
            sug = _suggestion_from_dict(item, fb)
            if sug is None:
                continue
            key = sug.tool_name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(sug)
        out.sort(key=lambda s: s.confidence, reverse=True)
        return out[:top_k]

    def _select_heuristic(self, chunks: list[DocChunk], *, top_k: int) -> list[ToolSuggestion]:
        """Rank by retrieval score per metadata tool_name; parse flags from chunk text."""
        best: dict[str, tuple[DocChunk, float]] = {}
        for c in chunks:
            name = (c.tool_name or "").strip() or "unknown"
            if name.lower() in {"", "general", "unknown"}:
                continue
            conf = _confidence_from_distance(c.distance)
            prev = best.get(name.lower())
            if prev is None or conf > prev[1]:
                best[name.lower()] = (c, conf)

        out: list[ToolSuggestion] = []
        for _k, (chunk, conf) in sorted(best.items(), key=lambda x: -x[1][1]):
            params = _parse_cli_flags(chunk.text)
            out.append(
                ToolSuggestion(
                    tool_name=chunk.tool_name,
                    confidence=round(conf, 4),
                    relevant_doc_chunk=chunk.text.strip()[:2000],
                    suggested_parameters=params,
                    reasoning=(
                        f"Matched query via vector retrieval (modality={chunk.modality}, "
                        f"doc_type={chunk.doc_type}). Primary CLI flags parsed from help text."
                    ),
                )
            )

        if not out:
            # Fall back to raw chunks when metadata tool_name is missing or generic.
            for c in chunks[:top_k]:
                conf = _confidence_from_distance(c.distance)
                tn = (c.tool_name or "unknown").strip() or "unknown"
                out.append(
                    ToolSuggestion(
                        tool_name=tn,
                        confidence=round(conf, 4),
                        relevant_doc_chunk=c.text.strip()[:2000],
                        suggested_parameters=_parse_cli_flags(c.text),
                        reasoning=(
                            "Retrieval match; tool_name may be generic — confirm against "
                            "container or CLI entrypoint."
                        ),
                    )
                )

        out.sort(key=lambda s: s.confidence, reverse=True)
        return out[:top_k]
