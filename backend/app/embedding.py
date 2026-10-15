"""Local embedding providers for memory retrieval.

Everything here is local-first: the default provider is deterministic,
offline and dependency-free; a local Ollama server can be selected for real
semantic vectors. No cloud embedding endpoint exists in this module.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Protocol

import httpx


HASH_PROVIDER_NAME = "hash-local"
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
OLLAMA_DEFAULT_MODEL = "bge-m3"


class EmbeddingError(RuntimeError):
    """Controlled failure that never embeds credentials or personal text."""


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _hash_tokens(text: str) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    lowered = text.casefold()
    word: list[str] = []

    def flush_word() -> None:
        if len(word) >= 2:
            tokens.append(("w:" + "".join(word), 1.0))
        word.clear()

    for character in lowered:
        if character.isascii() and (character.isalnum() or character == "_"):
            word.append(character)
            continue
        flush_word()
        if "\u4e00" <= character <= "\u9fff":
            tokens.append((f"c1:{character}", 0.35))
    flush_word()
    for segment in "".join(ch if ("\u4e00" <= ch <= "\u9fff") else " " for ch in lowered).split():
        for size, weight in ((2, 1.0), (3, 0.6)):
            for index in range(len(segment) - size + 1):
                tokens.append((f"c{size}:{segment[index:index + size]}", weight))
    return tokens


def hash_vector(text: str, dim: int = 256) -> list[float]:
    """Signed hashed character n-gram vector, L2-normalized and deterministic."""
    vector = [0.0] * dim
    for token, weight in _hash_tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


class HashEmbeddingProvider:
    """Offline fallback: fuzzy character-level similarity, zero downloads."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = HASH_PROVIDER_NAME

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_vector(text, self.dim) for text in texts]


class OllamaEmbeddingProvider:
    """Local Ollama /api/embed provider (e.g. bge-m3). Never leaves localhost."""

    def __init__(self, model: str = OLLAMA_DEFAULT_MODEL, base_url: str = OLLAMA_DEFAULT_URL,
                 timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        url = (base_url or OLLAMA_DEFAULT_URL).rstrip("/")
        if not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
            raise EmbeddingError("Ollama embedding base URL must be loopback")
        if not model.strip():
            raise EmbeddingError("Ollama embedding model must not be empty")
        self.model = model.strip()
        self.base_url = url
        self.timeout = timeout
        self._transport = transport
        self.name = f"ollama:{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": texts}
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout,
                              transport=self._transport, trust_env=False) as client:
                response = client.post("/api/embed", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError("本地 Ollama 向量服务不可用") from exc
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError("本地向量服务返回了无法使用的响应")
        result: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or not vector or not all(isinstance(value, (int, float)) for value in vector):
                raise EmbeddingError("本地向量服务返回了无法使用的响应")
            result.append([float(value) for value in vector])
        return result


def provider_from_environment(environ: Any = None) -> EmbeddingProvider | None:
    """Resolve EMBEDDING_PROVIDER: hash (default) | ollama | none."""
    env = os.environ if environ is None else environ
    kind = (env.get("EMBEDDING_PROVIDER") or "hash").strip().lower()
    if kind in {"none", "off", "disabled"}:
        return None
    if kind == "ollama":
        return OllamaEmbeddingProvider(
            model=env.get("OLLAMA_EMBED_MODEL") or OLLAMA_DEFAULT_MODEL,
            base_url=env.get("OLLAMA_BASE_URL") or OLLAMA_DEFAULT_URL,
            timeout=float(env.get("OLLAMA_EMBED_TIMEOUT") or 30.0),
        )
    if kind in {"hash", "hash-local", "auto"}:
        return HashEmbeddingProvider()
    raise EmbeddingError("EMBEDDING_PROVIDER 只支持 hash、ollama 或 none")
