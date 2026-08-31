from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

_LLM_ENV_PREFIXES = (
    "TENCENT_HY3_",
    "NOUS_PORTAL_",
    "OPENAI_",
    "ANTHROPIC_",
    "RAZORPAY_AGENT_LLM_PROVIDER",
)


def load_dotenv_into_env(path: str | Path = ".env") -> None:
    """Best-effort export of ``.env`` LLM keys into ``os.environ`` (existing vars win).

    Only reasoning-related keys are exported (see ``_LLM_ENV_PREFIXES``) — Razorpay
    credentials in ``.env`` are intentionally left alone, since the server reads them
    through its own loader. This makes a local ``.env`` (already gitignored) visible
    to the reasoning backends without a hard dependency on python-dotenv. Never
    raises — a missing/unreadable file is ignored.
    """
    try:
        p = Path(path)
        if not p.exists():
            return
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if not key or key in os.environ:
                continue
            if not any(key.startswith(prefix) for prefix in _LLM_ENV_PREFIXES):
                continue
            os.environ[key] = val
    except OSError:
        return


class LLMBackend(ABC):
    """Provider-agnostic LLM reasoner. Returns text only — never executes tools or settlement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Run one reasoning step. May emit a tool call in ``<<tool:name {json}>>`` form."""


_PROVIDERS: dict[str, type[LLMBackend]] = {}


def register_provider(name: str, backend_cls: type[LLMBackend]) -> None:
    _PROVIDERS[name] = backend_cls


def get_provider(name: str) -> type[LLMBackend]:
    if name not in _PROVIDERS:
        raise KeyError(f"unknown LLM provider {name!r}; registered: {sorted(_PROVIDERS)}")
    return _PROVIDERS[name]


class StubBackend(LLMBackend):
    """Keyless, deterministic, scripted reasoner — the default so the demo runs with no keys.

    Emits one tool call on the first turn (to inspect the catalog item), then a
    scripted final recommendation. Behaves as a stand-in for a real LLM so the whole
    Hermes loop (prompt -> tool_calls -> loop -> persist) is exercised keylessly.
    """

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model(self) -> str:
        return "stub-scripted"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    def complete(self, prompt: str) -> str:
        if "TOOL_RESULT" not in prompt:
            import re

            sku = "sku-hoodie"
            match = re.search(r"target_sku:\s*(\S+)", prompt, re.IGNORECASE)
            if match:
                sku = match.group(1).strip().strip(".,")
            return (
                "I should ground this recommendation in the actual catalog before "
                "judging the proposal.\n"
                f"<<tool:get_catalog_item {{\"sku\": \"{sku}\"}}>>"
            )
        return (
            "The bandit's proposal is a bounded action (a discount or bundle upsell) "
            "that the rule gate still validates before it reaches the buyer. The "
            "clearance path for stagnant stock is a legitimate inventory tactic, not a "
            "desperate one. I recommend proceeding with the proposed offer as gated. "
            "No settlement is performed by this module."
        )


class OpenAIBackend(LLMBackend):
    """OpenAI-backed reasoner. Reads only OPENAI_API_KEY / OPENAI_BASE_URL (scoped, no leakage)."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        from openai import OpenAI  # imported lazily; optional dependency

        self._client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        self._model = str(config.get("model", "gpt-4o-mini"))

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


class AnthropicBackend(LLMBackend):
    """Anthropic-backed reasoner. Reads only ANTHROPIC_API_KEY (scoped, no leakage)."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        import anthropic  # imported lazily; optional dependency

        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self._model = str(config.get("model", "claude-3-5-haiku-latest"))

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if hasattr(block, "text"))


class OpenAICompatibleBackend(LLMBackend):
    """OpenAI-compatible reasoner (e.g. Tencent HY3, Nous Portal).

    Reads ``<PREFIX>_API_KEY`` / ``<PREFIX>_BASE_URL`` from the environment only —
    scoped, no leakage. Subclasses set the prefix and defaults. Optional
    ``<PREFIX>_TAGS`` env var (JSON list or single string) is forwarded as
    ``extra_body.tags``; subclasses can also set ``default_tags``.
    """

    env_prefix: str = ""
    default_base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4o-mini"
    default_tags: list[str] | None = None
    provider_name: str = "openai-compatible"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        from openai import OpenAI  # imported lazily; optional dependency

        api_key = os.environ.get(f"{self.env_prefix}_API_KEY")
        base_url = os.environ.get(f"{self.env_prefix}_BASE_URL", self.default_base_url)
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = str(
            config.get("model")
            or os.environ.get(f"{self.env_prefix}_MODEL")
            or self.default_model
        )

    @property
    def name(self) -> str:
        return self.provider_name

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str) -> str:
        tags: list[str] | None = None
        tags_raw = os.environ.get(f"{self.env_prefix}_TAGS")
        if tags_raw is not None:
            try:
                parsed = json.loads(tags_raw)
                tags = parsed if isinstance(parsed, list) and parsed else [str(parsed)]
            except json.JSONDecodeError:
                tags = [tags_raw]
            if tags is not None and not all("=" in str(t) for t in tags):
                tags = None
        if tags is None and getattr(self, "default_tags", None):
            tags = list(self.default_tags)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if tags is not None:
            body["tags"] = tags

        api_key = os.environ.get(f"{self.env_prefix}_API_KEY", "")
        base_url = os.environ.get(
            f"{self.env_prefix}_BASE_URL", self.default_base_url
        ).rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        timeout = float(
            os.environ.get(f"{self.env_prefix}_TIMEOUT", "120")
        )
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content") or ""
            return str(content)
        return ""


class TencentBackend(OpenAICompatibleBackend):
    env_prefix = "TENCENT_HY3"
    default_base_url = "https://api.hunyuan.tencentcloudapi.com/v1"
    default_model = "hy3"
    provider_name = "tencent"


class NousBackend(OpenAICompatibleBackend):
    env_prefix = "NOUS_PORTAL"
    default_base_url = "https://inference-api.nousresearch.com/v1"
    default_model = "stepfun/step-3.7-flash:free"
    default_tags = ["product=opencode", "user=opencode"]
    provider_name = "nous"


register_provider("stub", StubBackend)
register_provider("openai", OpenAIBackend)
register_provider("anthropic", AnthropicBackend)
register_provider("tencent", TencentBackend)
register_provider("nous", NousBackend)


def resolve_provider(
    name: str | None = None,
    config: dict[str, Any] | None = None,
) -> LLMBackend:
    """Resolve an LLM backend with precedence: explicit -> config -> env -> default (stub).

    Any failure constructing the requested backend (missing key, missing SDK, network
    error at call time) safely falls back to the keyless ``StubBackend`` so the demo
    always runs and the decision layer is never blocked on the reasoner.
    """
    config = config or {}
    load_dotenv_into_env()
    resolved = (
        name
        or config.get("provider")
        or os.environ.get("RAZORPAY_AGENT_LLM_PROVIDER")
        or "stub"
    )
    try:
        backend_cls = get_provider(resolved)
        return backend_cls(config=config)
    except Exception:
        return StubBackend(config=config)
