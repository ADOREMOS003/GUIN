"""Index documentation and CLI help into ChromaDB for retrieval-augmented generation."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, cast
from urllib.parse import urlparse

import httpx
from chromadb import PersistentClient
from chromadb.api.types import EmbeddingFunction, Metadata, Metadatas, Where
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION_NAME = "guin_neuroimaging_docs"
DEFAULT_PERSIST = Path.home() / ".guin" / "chromadb"
HASHES_FILENAME = "content_hashes.json"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class DocChunk:
    """One retrieved passage with GUIN metadata."""

    id: str
    text: str
    tool_name: str
    modality: str
    doc_type: str
    source_url: str
    tool_version: str
    distance: float | None = None


@dataclass(frozen=True)
class ContainerSource:
    """Neurodesk / Apptainer image plus explicit tool names to capture ``--help``."""

    sif_path: Path
    tools: list[str]
    modality: str = "general"
    tool_version: str = "unknown"
    doc_type: Literal["cli_help"] = "cli_help"


SourceLike = str | Path | ContainerSource


def _default_apptainer() -> str:
    return os.environ.get("GUIN_APPTAINER_BINARY", "apptainer")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(re.sub(r"\s+", " ", raw)).strip()


def _load_text_file(path: Path) -> str:
    suffix = path.suffix.lower()
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        return _strip_html(text)
    return text


def _fetch_url_text(url: str, timeout: float = 60.0) -> str:
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        r = client.get(url, headers={"User-Agent": "GUIN-DocumentationIndexer/0.1"})
        r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    body = r.text
    if "html" in ctype.lower() or body.lstrip().lower().startswith("<!doctype html"):
        return _strip_html(body)
    return body.strip()


class DocumentationIndexer:
    """Crawl docs, CLI help, chunk, embed locally, and persist to ChromaDB."""

    def __init__(
        self,
        persist_directory: Path | None = None,
        *,
        collection_name: str = COLLECTION_NAME,
        embedding_model: str = DEFAULT_MODEL,
    ) -> None:
        self.persist_directory = (
            Path(persist_directory).expanduser()
            if persist_directory is not None
            else DEFAULT_PERSIST.expanduser()
        )
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self._hash_path = self.persist_directory / HASHES_FILENAME
        self._content_hashes: dict[str, str] = self._load_hashes()

        self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model,
        )
        self._client = PersistentClient(path=str(self.persist_directory))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=cast(EmbeddingFunction[Any], self._embedding_fn),
            metadata={"description": "GUIN neuroimaging documentation and CLI help"},
        )
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )

    def _load_hashes(self) -> dict[str, str]:
        if not self._hash_path.is_file():
            return {}
        try:
            data = json.loads(self._hash_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read hash file %s: %s", self._hash_path, exc)
            return {}
        return dict(data) if isinstance(data, dict) else {}

    def _save_hashes(self) -> None:
        self._hash_path.write_text(
            json.dumps(self._content_hashes, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _source_key_for_url_or_path(s: str | Path) -> str:
        p = Path(s)
        if p.exists() and p.is_file():
            return p.resolve().as_uri()
        return str(s)

    @staticmethod
    def _source_key_container(sif: Path, tool: str) -> str:
        return f"container://{sif.resolve()}#{tool}"

    def _capture_cli_help(self, sif: Path, tool: str) -> str:
        apptainer = _default_apptainer()
        argv = [apptainer, "exec", str(sif.resolve()), tool, "--help"]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Apptainer binary not found ({apptainer}). Set GUIN_APPTAINER_BINARY."
            ) from exc
        except subprocess.TimeoutExpired:
            return f"(timeout running {' '.join(argv)})"
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0 and not out.strip():
            return f"(exit {proc.returncode} from {' '.join(argv)})"
        return out.strip()

    def _ingest_text(
        self,
        *,
        text: str,
        source_url: str,
        tool_name: str,
        modality: str,
        doc_type: str,
        tool_version: str,
        content_hash: str,
        force: bool,
    ) -> int:
        """Chunk, embed, and upsert. Returns number of chunks written."""
        prev = self._content_hashes.get(source_url)
        if not force and prev == content_hash:
            logger.debug("Unchanged, skipping: %s", source_url)
            return 0

        self._collection.delete(
            where=cast(Where, {"source_url": {"$eq": source_url}}),
        )
        if prev is not None and prev != content_hash:
            logger.info("Content changed, re-indexing: %s", source_url)

        chunks = self._splitter.split_text(text)
        if not chunks:
            self._content_hashes[source_url] = content_hash
            return 0

        ids: list[str] = []
        documents: list[str] = []
        metadatas: Metadatas = []
        for i, chunk in enumerate(chunks):
            cid = f"{_sha256_text(source_url)[:16]}_{i}"
            ids.append(cid)
            documents.append(chunk)
            meta: Metadata = {
                "tool_name": tool_name,
                "modality": modality,
                "doc_type": doc_type,
                "source_url": source_url,
                "tool_version": tool_version,
                "chunk_index": i,
            }
            metadatas.append(meta)

        self._collection.add(ids=ids, documents=documents, metadatas=metadatas)
        self._content_hashes[source_url] = content_hash
        return len(chunks)

    def index(
        self,
        sources: Iterable[SourceLike],
        *,
        default_modality: str = "general",
        default_doc_type: str = "reference",
        default_tool_name: str = "general",
        default_tool_version: str = "unknown",
        force: bool = False,
    ) -> int:
        """Crawl URLs / files / container CLI help and index into ChromaDB.

        Returns total number of new chunks stored (0 for skipped unchanged sources).
        """
        total_chunks = 0
        for src in sources:
            if isinstance(src, ContainerSource):
                total_chunks += self._index_container(src, force=force)
                continue

            if isinstance(src, str) and urlparse(src).scheme in ("http", "https"):
                text = _fetch_url_text(src)
                key = src
                tool = default_tool_name
                modality = default_modality
                dtype = default_doc_type
                ver = default_tool_version
            else:
                p = Path(src).expanduser().resolve()
                if not p.is_file():
                    logger.warning("Skip missing file: %s", p)
                    continue
                text = _load_text_file(p)
                key = self._source_key_for_url_or_path(p)
                tool = default_tool_name
                modality = default_modality
                dtype = default_doc_type
                ver = default_tool_version

            h = _sha256_text(text)
            total_chunks += self._ingest_text(
                text=text,
                source_url=key,
                tool_name=tool,
                modality=modality,
                doc_type=dtype,
                tool_version=ver,
                content_hash=h,
                force=force,
            )

        self._save_hashes()
        return total_chunks

    def _index_container(self, src: ContainerSource, *, force: bool) -> int:
        sif = Path(src.sif_path).expanduser().resolve()
        if not sif.is_file():
            logger.warning("Skip missing container image: %s", sif)
            return 0

        n = 0
        for tool in src.tools:
            key = self._source_key_container(sif, tool)
            text = self._capture_cli_help(sif, tool)
            h = _sha256_text(text)
            n += self._ingest_text(
                text=text,
                source_url=key,
                tool_name=tool,
                modality=src.modality,
                doc_type=src.doc_type,
                tool_version=src.tool_version,
                content_hash=h,
                force=force,
            )
        return n

    def update(
        self,
        sources: Iterable[SourceLike],
        **kwargs: Any,
    ) -> int:
        """Re-index only sources whose content hash changed (same as :meth:`index` with hashing)."""
        return self.index(sources, force=False, **kwargs)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        modality_filter: str | None = None,
    ) -> list[DocChunk]:
        """Semantic search over indexed chunks."""
        q_kw: dict[str, Any] = {
            "query_texts": [query],
            "n_results": top_k,
            "include": ["metadatas", "documents", "distances"],
        }
        if modality_filter is not None:
            q_kw["where"] = {"modality": {"$eq": modality_filter}}

        raw = self._collection.query(**q_kw)
        ids_list = raw.get("ids") or [[]]
        docs_list = raw.get("documents") or [[]]
        meta_list = raw.get("metadatas") or [[]]
        dist_list = raw.get("distances") or [[]]

        ids = ids_list[0] if ids_list else []
        docs = docs_list[0] if docs_list else []
        metas = meta_list[0] if meta_list else []
        dists = dist_list[0] if dist_list else []

        out: list[DocChunk] = []
        for i, cid in enumerate(ids):
            m = metas[i] if i < len(metas) and metas[i] else {}
            dist = dists[i] if i < len(dists) else None
            out.append(
                DocChunk(
                    id=str(cid),
                    text=docs[i] if i < len(docs) else "",
                    tool_name=str(m.get("tool_name", "")),
                    modality=str(m.get("modality", "")),
                    doc_type=str(m.get("doc_type", "")),
                    source_url=str(m.get("source_url", "")),
                    tool_version=str(m.get("tool_version", "")),
                    distance=float(dist) if dist is not None else None,
                )
            )
        return out
