"""Environment-backed configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _int_env(name: str, default: int, low: int, high: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < low or (high is not None and value > high):
        bound = f"between {low} and {high}" if high is not None else f"at least {low}"
        raise ValueError(f"{name} must be {bound}")
    return value


@dataclass(frozen=True)
class Settings:
    model: str
    data_dir: Path
    max_repair_rounds: int
    validation_timeout_seconds: int
    max_file_bytes: int
    beeai_model: str
    provider: str = "openai"
    ollama_base_url: str = "http://127.0.0.1:11434/v1/"
    validator: str = "docker"

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("AGENTFORGE_PROVIDER", "openai").strip().lower()
        model = os.getenv("AGENTFORGE_MODEL", "qwen3:8b" if provider == "ollama" else "gpt-5-mini").strip()
        if not model:
            raise ValueError("AGENTFORGE_MODEL cannot be empty")
        if provider not in {"openai", "ollama"}:
            raise ValueError("AGENTFORGE_PROVIDER must be openai or ollama")
        base_url = os.getenv("AGENTFORGE_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1/").strip()
        from urllib.parse import urlsplit
        endpoint = urlsplit(base_url)
        if endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"} or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path.rstrip("/") != "/v1":
            raise ValueError("Ollama endpoint must be a local http://localhost:port/v1/ URL")
        validator = os.getenv("AGENTFORGE_VALIDATOR", "docker").strip().lower()
        if validator not in {"local", "docker"}:
            raise ValueError("AGENTFORGE_VALIDATOR must be local or docker")
        return cls(
            model=model,
            data_dir=Path(os.getenv("AGENTFORGE_DATA_DIR", ".agentforge-data")).expanduser(),
            max_repair_rounds=_int_env("AGENTFORGE_MAX_REPAIR_ROUNDS", 2, 0, 5),
            validation_timeout_seconds=_int_env(
                "AGENTFORGE_VALIDATION_TIMEOUT_SECONDS", 90, 1, 600
            ),
            max_file_bytes=_int_env("AGENTFORGE_MAX_FILE_BYTES", 500_000, 1),
            beeai_model=os.getenv("AGENTFORGE_BEEAI_MODEL", "ollama:granite4.1:8b").strip(),
            provider=provider,
            ollama_base_url=base_url,
            validator=validator,
        )


def require_openai_key() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and set your key, "
            "or export it in the current shell."
        )
