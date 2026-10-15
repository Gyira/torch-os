from .application import create_app
from .embedding import EmbeddingError, provider_from_environment

try:
    _embedding_provider = provider_from_environment()
except EmbeddingError:
    # A misconfigured provider must not stop the local workbench from starting.
    _embedding_provider = None

app = create_app(embedding_provider=_embedding_provider)
