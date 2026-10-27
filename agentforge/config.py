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

    @classmethod
    def from_env(cls) -> "Settings":
        model = os.getenv("AGENTFORGE_MODEL", "gpt-5-mini").strip()
        if not model:
            raise ValueError("AGENTFORGE_MODEL cannot be empty")
        return cls(
            model=model,
            data_dir=Path(os.getenv("AGENTFORGE_DATA_DIR", ".agentforge-data")).expanduser(),
            max_repair_rounds=_int_env("AGENTFORGE_MAX_REPAIR_ROUNDS", 2, 0, 5),
            validation_timeout_seconds=_int_env(
                "AGENTFORGE_VALIDATION_TIMEOUT_SECONDS", 90, 1, 600
            ),
            max_file_bytes=_int_env("AGENTFORGE_MAX_FILE_BYTES", 500_000, 1),
            beeai_model=os.getenv("AGENTFORGE_BEEAI_MODEL", "ollama:granite4.1:8b").strip(),
        )


def require_openai_key() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and set your key, "
            "or export it in the current shell."
        )
