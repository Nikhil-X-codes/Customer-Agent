"""Document ingestion and semantic retrieval using Qdrant."""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 250_000


class KnowledgeError(Exception):
    pass


@dataclass(frozen=True)
class Passage:
    chunk_id: str
    document_id: str
    title: str
    text: str
    score: float


def _extract(name: str, raw: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in (".txt", ".md"):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise KnowledgeError("Text documents must be UTF-8") from exc
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise KnowledgeError("PDF support requires pip install 'customer-agent[pdf]'") from exc
        try:
            reader = PdfReader(io.BytesIO(raw), strict=False)
            if len(reader.pages) > 100:
                raise KnowledgeError("PDF exceeds 100 pages")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeError("PDF could not be read") from exc
    else:
        raise KnowledgeError("Upload a .txt, .md, or text-based .pdf file")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise KnowledgeError("Document has no extractable text")
    if len(text) > MAX_TEXT_CHARS:
        raise KnowledgeError("Extracted text is too large")
    return text


def _chunks(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    size, overlap = 150, 30
    chunks = []
    for start in range(0, len(words), size - overlap):
        part = " ".join(words[start:start + size])
        if part:
            chunks.append(part)
        if start + size >= len(words):
            break
    return chunks


class KnowledgeBase:
    """Document points and semantic passage vectors in Qdrant."""

    COLLECTION = "support_knowledge"
    DIMENSION = 384  # BAAI/bge-small-en-v1.5

    def __init__(self, *, url: str | None = None,
                 api_key: str | None = None, embedder=None, client=None,
                 collection: str = COLLECTION) -> None:
        from qdrant_client import QdrantClient, models

        if client is None:
            if not url or not (url.startswith("https://") or
                               url.startswith("http://127.0.0.1:") or
                               url.startswith("http://localhost:")):
                raise ValueError("QDRANT_URL must use HTTPS or local loopback HTTP")
            client = QdrantClient(url=url, api_key=api_key, timeout=5)
        self.client = client
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", collection):
            raise ValueError("Invalid Qdrant collection name")
        self.collection = collection
        if embedder is None:
            cache_dir = os.getenv("EMBEDDING_CACHE_PATH", "var/embedding-cache")
            os.environ.setdefault("HF_HOME", str(Path(cache_dir).resolve() / "hf"))
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from fastembed import TextEmbedding
            embedder = TextEmbedding(
                model_name="BAAI/bge-small-en-v1.5",
                cache_dir=cache_dir)
        self.embedder = embedder
        self.models = models
        try:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(size=self.DIMENSION,
                                                       distance=models.Distance.COSINE))
        except Exception as exc:
            raise KnowledgeError("Qdrant collection unavailable") from exc

    def _vectors(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors = [list(map(float, vector)) for vector in self.embedder.embed(texts)]
            if len(vectors) != len(texts) or any(len(v) != self.DIMENSION for v in vectors):
                raise ValueError("Embedding model returned the wrong dimensions")
            return vectors
        except Exception as exc:
            raise KnowledgeError("Embedding model unavailable") from exc

    def _scroll(self, condition) -> list:
        points, offset = [], None
        while True:
            batch, offset = self.client.scroll(
                self.collection, scroll_filter=self.models.Filter(must=[condition]),
                limit=100, offset=offset, with_payload=True, with_vectors=False)
            points.extend(batch)
            if offset is None:
                return points

    def _field(self, key: str, value: str):
        return self.models.FieldCondition(key=key,
                                          match=self.models.MatchValue(value=value))

    def add(self, name: str, raw: bytes) -> dict:
        name = Path(name).name.strip()
        if not name or len(name) > 160 or name.startswith("."):
            raise KnowledgeError("Invalid document name")
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            raise KnowledgeError("Document must be 1 byte to 5 MB")
        text = _extract(name, raw)
        chunks = _chunks(text)
        vectors = self._vectors(chunks)
        document_id = uuid.uuid4().hex
        checksum = hashlib.sha256(raw).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        document = {"id": document_id, "name": name, "sha256": checksum,
                    "uploaded_at": now, "chunk_count": len(chunks)}
        try:
            old = self._scroll(self._field("name", name))
            points = [self.models.PointStruct(
                id=str(uuid.uuid4()), vector=[0.0] * self.DIMENSION,
                payload={"kind": "document", "document_id": document_id, **document})]
            points += [self.models.PointStruct(
                id=str(uuid.uuid4()), vector=vector,
                payload={"kind": "chunk", "document_id": document_id,
                         "name": name, "text": part, "ordinal": ordinal})
                       for ordinal, (part, vector) in enumerate(zip(chunks, vectors))]
            self.client.upsert(self.collection, points=points, wait=True)
            if old:
                self.client.delete(self.collection,
                                   points_selector=self.models.PointIdsList(
                                       points=[point.id for point in old]), wait=True)
        except Exception as exc:
            raise KnowledgeError("Qdrant upload unavailable") from exc
        return document

    def list_documents(self) -> list[dict]:
        try:
            points = self._scroll(self._field("kind", "document"))
        except Exception as exc:
            raise KnowledgeError("Qdrant document list unavailable") from exc
        keys = ("id", "name", "sha256", "uploaded_at", "chunk_count")
        return sorted([{key: point.payload[key] for key in keys} for point in points],
                      key=lambda item: item["uploaded_at"], reverse=True)

    def delete(self, document_id: str) -> bool:
        try:
            points = self._scroll(self._field("document_id", document_id))
            if not points:
                return False
            self.client.delete(self.collection,
                               points_selector=self.models.PointIdsList(
                                   points=[point.id for point in points]), wait=True)
            return True
        except Exception as exc:
            raise KnowledgeError("Qdrant delete unavailable") from exc

    def search(self, question: str, limit: int = 6) -> list[Passage]:
        if not question.strip():
            return []
        vector = self._vectors([question])[0]
        try:
            result = self.client.query_points(
                self.collection, query=vector,
                query_filter=self.models.Filter(must=[self._field("kind", "chunk")]),
                limit=limit, score_threshold=0.35, with_payload=True)
        except Exception as exc:
            raise KnowledgeError("Qdrant search unavailable") from exc
        return [Passage(str(point.id), point.payload["document_id"],
                        point.payload["name"], point.payload["text"], point.score)
                for point in result.points]

    def ready(self) -> bool:
        try:
            return self.client.collection_exists(self.collection)
        except Exception:
            return False
