"""Model provider registry: resolve presets, freeze IDs, validate capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

from deeprhetor.config.settings import AppConfig, ModelPreset
from deeprhetor.models.roles import ROLE_PRESET, RoleName

PresetName = Literal["cheap", "mid", "frontier"]

DEFAULT_PRESET_IDS: dict[str, str] = {
    "cheap": "openai/gpt-4o-mini",
    "mid": "openai/gpt-4o",
    "frontier": "anthropic/claude-opus-4",
}


class ModelCapabilityRequirements(BaseModel):
    """Capabilities that must be declared before a model run."""

    tool_calling: bool = True
    structured_output: bool = True


class DeclaredCapabilities(BaseModel):
    """Capabilities declared for a resolved OpenRouter (or test) model."""

    tool_calling: bool = True
    structured_output: bool = True
    notes: str | None = None


@dataclass(frozen=True)
class ResolvedModel:
    """Frozen preset → exact provider/model ID binding for one run."""

    preset_name: str
    provider: str
    model_id: str
    temperature: float
    capabilities: DeclaredCapabilities

    def as_snapshot_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset_name,
            "provider": self.provider,
            "model_id": self.model_id,
            "temperature": self.temperature,
            "capabilities": self.capabilities.model_dump(),
        }


class ModelCapabilityError(ValueError):
    """Raised when a resolved model lacks a required capability."""


class ModelRegistry:
    """Resolve cheap/mid/frontier presets and build Pydantic AI model instances.

    Alias resolution freezes at run start: call :meth:`resolve_presets` (or
    :meth:`snapshot_for_configuration`) once and reuse the snapshot for the
    entire run. Mid-run substitution is not allowed.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        declare_capabilities: DeclaredCapabilities | None = None,
        use_test_models: bool = False,
    ) -> None:
        self._config = config
        self._declare = declare_capabilities or DeclaredCapabilities()
        self._use_test_models = use_test_models
        self._frozen: dict[str, ResolvedModel] | None = None

    @property
    def uses_test_models(self) -> bool:
        return self._use_test_models

    @property
    def frozen(self) -> dict[str, ResolvedModel] | None:
        return self._frozen

    def resolve_presets(self, *, force: bool = False) -> dict[str, ResolvedModel]:
        """Resolve and freeze preset aliases to exact model IDs.

        Subsequent calls return the same snapshot unless ``force=True``.
        """
        if self._frozen is not None and not force:
            return dict(self._frozen)

        resolved: dict[str, ResolvedModel] = {}
        for name, preset in self._config.models.items():
            resolved[name] = self._resolve_one(name, preset)
        # Ensure canonical tiers exist even if user omitted one.
        for tier, default_id in DEFAULT_PRESET_IDS.items():
            if tier not in resolved:
                resolved[tier] = self._resolve_one(
                    tier,
                    ModelPreset(provider="openrouter", model=default_id),
                )
        self._frozen = resolved
        return dict(resolved)

    def snapshot_for_configuration(self) -> dict[str, Any]:
        """Dict suitable for ``configuration_snapshot.model_presets``."""
        return {
            name: model.as_snapshot_dict()
            for name, model in self.resolve_presets().items()
        }

    def resolve_for_role(self, role: str) -> ResolvedModel:
        presets = self.resolve_presets()
        preset_name = ROLE_PRESET.get(role)
        if preset_name is None:
            raise KeyError(f"Unknown role: {role}")
        if preset_name not in presets:
            raise KeyError(f"Missing preset {preset_name!r} for role {role!r}")
        return presets[preset_name]

    def validate_capabilities(
        self,
        resolved: ResolvedModel,
        *,
        required: ModelCapabilityRequirements | None = None,
    ) -> None:
        """Validate tool calling and structured output before a run."""
        req = required or ModelCapabilityRequirements()
        caps = resolved.capabilities
        missing: list[str] = []
        if req.tool_calling and not caps.tool_calling:
            missing.append("tool_calling")
        if req.structured_output and not caps.structured_output:
            missing.append("structured_output")
        if missing:
            raise ModelCapabilityError(
                f"Model {resolved.provider}:{resolved.model_id} "
                f"(preset={resolved.preset_name}) lacks required capabilities: "
                f"{', '.join(missing)}"
            )

    def validate_all(
        self,
        *,
        required: ModelCapabilityRequirements | None = None,
    ) -> None:
        for resolved in self.resolve_presets().values():
            self.validate_capabilities(resolved, required=required)

    def build_model(
        self,
        preset_name: str,
        *,
        test_output_args: Any | None = None,
    ) -> Model:
        """Build a Pydantic AI model for a frozen preset."""
        presets = self.resolve_presets()
        if preset_name not in presets:
            raise KeyError(f"Unknown preset: {preset_name}")
        resolved = presets[preset_name]
        self.validate_capabilities(resolved)

        if self._use_test_models:
            return TestModel(
                custom_output_args=test_output_args,
                model_name=f"test:{resolved.model_id}",
            )

        if resolved.provider != "openrouter":
            raise ValueError(
                f"Unsupported model provider {resolved.provider!r}; "
                "Stage 4 supports openrouter (and TestModel via use_test_models)."
            )

        api_key = self._config.providers.openrouter.api_key.get_secret_value()
        if not api_key:
            raise ValueError(
                "OpenRouter API key is not configured. "
                "Set providers.openrouter.api_key or DEEPRHETOR_OPENROUTER_API_KEY."
            )
        provider = OpenRouterProvider(api_key=api_key)
        settings = ModelSettings(temperature=resolved.temperature)
        return OpenRouterModel(
            resolved.model_id,
            provider=provider,
            settings=settings,
        )

    def build_model_for_role(
        self,
        role: str,
        *,
        test_output_args: Any | None = None,
    ) -> Model:
        resolved = self.resolve_for_role(role)
        return self.build_model(
            resolved.preset_name,
            test_output_args=test_output_args,
        )

    def _resolve_one(self, name: str, preset: ModelPreset) -> ResolvedModel:
        model_id = (preset.model or "").strip()
        if not model_id:
            raise ValueError(f"Preset {name!r} has an empty model id")
        provider = (preset.provider or "openrouter").strip().lower()
        # Freeze exact OpenRouter id (strip accidental openrouter: prefix).
        if model_id.startswith("openrouter:"):
            model_id = model_id.split(":", 1)[1]
        return ResolvedModel(
            preset_name=name,
            provider=provider,
            model_id=model_id,
            temperature=preset.temperature,
            capabilities=self._declare,
        )


def role_names() -> tuple[str, ...]:
    return tuple(r.value for r in RoleName)
