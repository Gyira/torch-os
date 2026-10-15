import math

import httpx
import pytest

from backend.app.embedding import (
    EmbeddingError,
    HashEmbeddingProvider,
    OllamaEmbeddingProvider,
    cosine,
    hash_vector,
    provider_from_environment,
)


def test_hash_vectors_are_deterministic_normalized_and_local():
    first = hash_vector("睡眠质量与运动习惯")
    second = hash_vector("睡眠质量与运动习惯")
    assert first == second
    assert len(first) == 256
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, rel_tol=1e-9)
    assert cosine(first, second) == pytest.approx(1.0)
    other = hash_vector("番茄工作法")
    assert -1.0 <= cosine(first, other) <= 1.0
    assert cosine(first, [0.0] * 256) == 0.0
    assert cosine([1.0, 0.0], [1.0]) == 0.0
    assert hash_vector("") == [0.0] * 256


def test_hash_provider_matches_vector_function_and_stays_offline(monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("hash provider must never touch the network")

    monkeypatch.setattr(httpx, "Client", explode)
    provider = HashEmbeddingProvider()
    assert provider.name == "hash-local"
    assert provider.embed(["阅读一页"])[0] == hash_vector("阅读一页")


def test_provider_factory_defaults_and_explicit_modes():
    assert provider_from_environment({}).name == "hash-local"
    assert provider_from_environment({"EMBEDDING_PROVIDER": "hash"}).name == "hash-local"
    assert provider_from_environment({"EMBEDDING_PROVIDER": "none"}) is None
    ollama = provider_from_environment({"EMBEDDING_PROVIDER": "ollama", "OLLAMA_EMBED_MODEL": "bge-m3"})
    assert ollama.name == "ollama:bge-m3"
    with pytest.raises(EmbeddingError):
        provider_from_environment({"EMBEDDING_PROVIDER": "cloud"})
    with pytest.raises(EmbeddingError):
        OllamaEmbeddingProvider(base_url="http://192.168.1.5:11434")


def test_ollama_provider_parses_embed_response_via_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        assert b"bge-m3" in request.content
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]})

    provider = OllamaEmbeddingProvider(model="bge-m3", transport=httpx.MockTransport(handler))
    vectors = provider.embed(["一", "二"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert provider.name == "ollama:bge-m3"


def test_ollama_provider_rejects_bad_responses_and_errors():
    def bad_shape(request):
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    provider = OllamaEmbeddingProvider(transport=httpx.MockTransport(bad_shape))
    with pytest.raises(EmbeddingError):
        provider.embed(["a", "b"])

    def failure(request):
        return httpx.Response(500, json={"error": "boom"})

    provider = OllamaEmbeddingProvider(transport=httpx.MockTransport(failure))
    with pytest.raises(EmbeddingError):
        provider.embed(["a"])
