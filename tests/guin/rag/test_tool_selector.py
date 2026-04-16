"""Tests for ToolSelector (heuristic path; no Anthropic calls)."""

from __future__ import annotations

from unittest.mock import MagicMock

from guin.rag.indexer import DocChunk
from guin.rag.tool_selector import ToolSelector, ToolSuggestion


def _chunk(
    *,
    tid: str,
    text: str,
    tool_name: str,
    distance: float,
) -> DocChunk:
    return DocChunk(
        id=tid,
        text=text,
        tool_name=tool_name,
        modality="diffusion",
        doc_type="cli_help",
        source_url=f"container://test#{tool_name}",
        tool_version="1",
        distance=distance,
    )


def test_select_heuristic_orders_by_retrieval_score() -> None:
    indexer = MagicMock()
    indexer.retrieve.return_value = [
        _chunk(
            tid="1",
            text="dwidenoise --input in.nii --output out.nii",
            tool_name="dwidenoise",
            distance=1.2,
        ),
        _chunk(
            tid="2",
            text="mrdegibbs -axis 0 in out",
            tool_name="mrdegibbs",
            distance=0.4,
        ),
    ]
    ts = ToolSelector(indexer, use_llm=False)
    out = ts.select("denoise my diffusion data", top_k=5)
    assert len(out) == 2
    assert isinstance(out[0], ToolSuggestion)
    # Lower Chroma distance ⇒ higher confidence; mrdegibbs should rank first.
    assert out[0].tool_name == "mrdegibbs"
    assert out[1].tool_name == "dwidenoise"
    assert out[0].confidence >= out[1].confidence


def test_select_empty_chunks() -> None:
    indexer = MagicMock()
    indexer.retrieve.return_value = []
    ts = ToolSelector(indexer, use_llm=False)
    assert ts.select("anything") == []


def test_select_fallback_when_tool_name_generic() -> None:
    indexer = MagicMock()
    indexer.retrieve.return_value = [
        DocChunk(
            id="1",
            text="Some CLI --verbose --output out.nii.gz",
            tool_name="general",
            modality="diffusion",
            doc_type="reference",
            source_url="https://example.com",
            tool_version="x",
            distance=0.2,
        ),
    ]
    ts = ToolSelector(indexer, use_llm=False)
    out = ts.select("denoise", top_k=3)
    assert len(out) == 1
    assert out[0].tool_name == "general"
    assert "verbose" in out[0].suggested_parameters or "output" in out[0].suggested_parameters
