"""Load, write, and redact DeepRhetor user configuration."""

from __future__ import annotations

import os
import re
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from .settings import AppConfig, ModelPreset

CONFIG_ENV_PREFIX = "DEEPRHETOR_"

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([^\s'\"]+)"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*"),
)

_REDACTED = "***REDACTED***"


def default_config_path() -> Path:
    """Return the OS-standard user config path for DeepRhetor."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "deeprhetor" / "config.toml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "deeprhetor" / "config.toml"
    return Path.home() / ".config" / "deeprhetor" / "config.toml"


def example_config_path() -> Path:
    """Locate config.example.toml shipped with the repository / package."""
    # repo root when editable: .../DeepRhetor/config.example.toml
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "config.example.toml",  # src/deeprhetor/config -> repo
        here.parents[2] / "config.example.toml",
        Path.cwd() / "config.example.toml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("config.example.toml not found")


def _parse_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _normalize_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested TOML shape into AppConfig fields."""
    out: dict[str, Any] = {}
    if "providers" in raw:
        out["providers"] = raw["providers"]
    if "limits" in raw:
        out["limits"] = raw["limits"]
    models = raw.get("models", {})
    presets = models.get("presets", models) if isinstance(models, dict) else {}
    if presets:
        out["models"] = {
            name: ModelPreset.model_validate(value).model_dump()
            for name, value in presets.items()
        }
    return out


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Overlay credential env vars onto a config dict."""
    providers = data.setdefault("providers", {})
    openrouter = providers.setdefault("openrouter", {})
    tavily = providers.setdefault("tavily", {})

    or_key = os.environ.get(f"{CONFIG_ENV_PREFIX}OPENROUTER_API_KEY")
    if or_key:
        openrouter["api_key"] = or_key
    or_url = os.environ.get(f"{CONFIG_ENV_PREFIX}OPENROUTER_BASE_URL")
    if or_url:
        openrouter["base_url"] = or_url
    tav_key = os.environ.get(f"{CONFIG_ENV_PREFIX}TAVILY_API_KEY")
    if tav_key:
        tavily["api_key"] = tav_key
    return data


def load_config(path: Path | None = None, *, apply_env: bool = True) -> AppConfig:
    """Load configuration from path (default user location) or return defaults."""
    config_path = path or default_config_path()
    if config_path.is_file():
        raw = _normalize_raw(_parse_toml(config_path))
    else:
        raw = {}
    if apply_env:
        raw = _apply_env_overrides(raw)
    return AppConfig.model_validate(raw) if raw else AppConfig()


def load_example_config(*, apply_env: bool = False) -> AppConfig:
    """Load the repository example config (placeholders only by default)."""
    raw = _normalize_raw(_parse_toml(example_config_path()))
    if apply_env:
        raw = _apply_env_overrides(raw)
    return AppConfig.model_validate(raw)


def _restrictive_perms(path: Path) -> None:
    """Best-effort owner-only permissions (POSIX). No-op where unsupported."""
    if os.name != "posix":
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        if path.parent.is_dir():
            os.chmod(path.parent, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass


def write_config(config: AppConfig, path: Path | None = None) -> Path:
    """Write config TOML with restrictive permissions where supported."""
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    _restrictive_perms(target.parent)

    payload = config.model_dump(mode="python")
    # Serialize SecretStr values for file storage.
    providers = payload.get("providers", {})
    for name, block in list(providers.items()):
        if isinstance(block, dict):
            for key, value in list(block.items()):
                if isinstance(value, SecretStr):
                    block[key] = value.get_secret_value()
            providers[name] = block

    text = _to_toml(payload)
    target.write_text(text, encoding="utf-8")
    _restrictive_perms(target)
    return target


def _to_toml(data: dict[str, Any]) -> str:
    """Minimal TOML emitter sufficient for AppConfig round-trips."""
    lines: list[str] = []

    providers = data.get("providers") or {}
    for name, block in providers.items():
        lines.append(f"[providers.{name}]")
        for key, value in block.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")

    models = data.get("models") or {}
    for name, preset in models.items():
        lines.append(f"[models.presets.{name}]")
        for key, value in preset.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")

    limits = data.get("limits") or {}
    if limits:
        lines.append("[limits]")
        for key, value in limits.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, SecretStr):
        return _toml_value(value.get_secret_value())
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def redact_secrets(text: str, secrets: list[str] | None = None) -> str:
    """Redact known secret substrings and common credential patterns."""
    result = text
    for secret in secrets or []:
        if secret:
            result = result.replace(secret, _REDACTED)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(
            lambda m: (
                f"{m.group(1)}={_REDACTED}"
                if m.lastindex and m.lastindex >= 2
                else _REDACTED
            ),
            result,
        )
    return result


def redact_config(config: AppConfig) -> dict[str, Any]:
    """Return a JSON-safe dict with credentials replaced by placeholders."""
    data = config.model_dump(mode="json")
    providers = data.get("providers", {})
    for block in providers.values():
        if isinstance(block, dict) and "api_key" in block:
            raw = block["api_key"]
            block["api_key"] = _REDACTED if raw else ""
    return data
