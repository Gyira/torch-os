"""Security and data-boundary checks for the local 薪火OS service.

The first release is intended for a single-user local machine. Keeping the
API on a loopback interface is therefore the default security boundary. A
remote bind is only accepted when it is explicit and accompanied by a token,
so an accidental ``HOST=0.0.0.0`` cannot silently expose private records.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


class SecurityConfigError(ValueError):
    """Raised when the service would start outside its documented boundary."""


@dataclass(frozen=True)
class SecurityConfig:
    """Validated security settings used by startup checks and diagnostics."""

    host: str
    data_dir: Path
    vault_dir: Path
    allow_remote_api: bool
    api_token_configured: bool
    api_token: str = field(default="", repr=False)

    @property
    def is_loopback(self) -> bool:
        return is_loopback_host(self.host)

    @property
    def remote_api_enabled(self) -> bool:
        return not self.is_loopback


def is_loopback_host(host: str) -> bool:
    """Return whether *host* names a loopback-only bind interface."""

    return host.strip().lower() in LOOPBACK_HOSTS


def _env_flag(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise SecurityConfigError(
        "ALLOW_REMOTE_API must be one of: 1, 0, true, false, yes, no, on, off"
    )


def validate_runtime_config(
    host: str,
    data_dir: Path,
    vault_dir: Path,
    *,
    allow_remote_api: bool | None = None,
    api_token: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> SecurityConfig:
    """Validate listener and local-data settings before the app starts.

    ``ALLOW_REMOTE_API`` and ``API_TOKEN`` are intentionally opt-in. The
    token is not persisted or included in diagnostics. This function does
    not access any files outside the supplied paths; it only rejects paths
    that already exist as regular files because they cannot hold app data.
    """

    env = os.environ if environ is None else environ
    normalized_host = (host or "").strip()
    if not normalized_host:
        raise SecurityConfigError("HOST must not be empty")

    remote_allowed = (
        _env_flag(env.get("ALLOW_REMOTE_API"))
        if allow_remote_api is None
        else bool(allow_remote_api)
    )
    configured_token = (env.get("API_TOKEN", "") if api_token is None else api_token).strip()

    if remote_allowed and not configured_token:
        raise SecurityConfigError(
            "ALLOW_REMOTE_API requires a non-empty API_TOKEN; private data would otherwise be exposed"
        )
    if not is_loopback_host(normalized_host) and not remote_allowed:
        raise SecurityConfigError(
            f"HOST={normalized_host!r} is not loopback. Keep HOST=127.0.0.1 or explicitly set "
            "ALLOW_REMOTE_API=true together with API_TOKEN."
        )

    data_path = Path(data_dir).expanduser()
    vault_path = Path(vault_dir).expanduser()
    for label, path in (("DATA_DIR", data_path), ("VAULT_DIR", vault_path)):
        if path.exists() and not path.is_dir():
            raise SecurityConfigError(f"{label} must be a directory: {path}")

    return SecurityConfig(
        host=normalized_host,
        data_dir=data_path,
        vault_dir=vault_path,
        allow_remote_api=remote_allowed,
        api_token_configured=bool(configured_token),
        api_token=configured_token,
    )


def validate_config_from_environment(
    host: str,
    data_dir: Path,
    vault_dir: Path,
) -> SecurityConfig:
    """Convenience wrapper used by the FastAPI application at startup."""

    return validate_runtime_config(host, data_dir, vault_dir)


def redact_secret(value: str | None) -> str:
    """Return a safe diagnostic value without exposing a configured token."""

    return "configured" if value and value.strip() else "not configured"


def token_matches(provided: str | None, expected: str | None) -> bool:
    """Compare API tokens without leaking timing information."""

    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.strip(), expected.strip())
