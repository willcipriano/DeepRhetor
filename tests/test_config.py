"""Config loader exit-criteria tests."""

from __future__ import annotations

from pathlib import Path

from deeprhetor.config import (
    load_config,
    load_example_config,
    redact_config,
    redact_secrets,
)


def test_example_config_loads(example_config_path: Path) -> None:
    assert example_config_path.is_file()
    cfg = load_example_config()
    assert "cheap" in cfg.models
    assert "mid" in cfg.models
    assert "frontier" in cfg.models
    assert cfg.limits.max_critic_passes >= 1
    assert cfg.providers.openrouter.base_url.startswith("https://")


def test_load_config_defaults_when_missing(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "does-not-exist" / "config.toml"
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("DEEPRHETOR_OPENROUTER_API_KEY", raising=False)
    cfg = load_config(missing)
    assert cfg.models["cheap"].model


def test_env_overrides_credentials(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[providers.openrouter]\napi_key = "file-key"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPRHETOR_OPENROUTER_API_KEY", "env-secret-key")
    cfg = load_config(cfg_path)
    assert cfg.providers.openrouter.api_key.get_secret_value() == "env-secret-key"


def test_redaction_helpers() -> None:
    cfg = load_example_config()
    redacted = redact_config(cfg)
    assert redacted["providers"]["openrouter"]["api_key"] == "***REDACTED***"
    assert "YOUR_OPENROUTER" not in str(redacted)

    text = "api_key=sk-secret-abc Authorization: Bearer tok123"
    out = redact_secrets(text, secrets=["sk-secret-abc"])
    assert "sk-secret-abc" not in out
    assert "***REDACTED***" in out
