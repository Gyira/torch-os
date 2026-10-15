from pathlib import Path

import pytest

from backend.app.security import SecurityConfigError, is_loopback_host, token_matches, validate_runtime_config


def test_loopback_hosts_are_recognized():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("LOCALHOST")
    assert is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0")


def test_remote_bind_requires_explicit_token(tmp_path: Path):
    with pytest.raises(SecurityConfigError, match="not loopback"):
        validate_runtime_config("0.0.0.0", tmp_path / "data", tmp_path / "vault", environ={})

    with pytest.raises(SecurityConfigError, match="API_TOKEN"):
        validate_runtime_config(
            "0.0.0.0",
            tmp_path / "data",
            tmp_path / "vault",
            environ={"ALLOW_REMOTE_API": "true"},
        )

    result = validate_runtime_config(
        "0.0.0.0",
        tmp_path / "data",
        tmp_path / "vault",
        environ={"ALLOW_REMOTE_API": "true", "API_TOKEN": "test-only-token"},
    )
    assert result.remote_api_enabled
    assert result.api_token_configured


def test_data_and_vault_paths_must_not_be_regular_files(tmp_path: Path):
    data_file = tmp_path / "data-file"
    data_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(SecurityConfigError, match="DATA_DIR"):
        validate_runtime_config("127.0.0.1", data_file, tmp_path / "vault", environ={})


def test_api_token_comparison():
    assert token_matches("secret", "secret")
    assert token_matches(" Bearer secret ", "Bearer secret")
    assert not token_matches("secret", "other")
    assert not token_matches("", "secret")
