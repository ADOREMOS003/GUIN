"""Shared GUIN user configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

CONFIG_PATH = Path.home() / ".guin" / "config.yaml"


def _as_opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


@dataclass(frozen=True)
class GuinCLIConfig:
    """User-level CLI and web config from ``~/.guin/config.yaml``."""

    container_dir: str | None = None
    model: str | None = None
    api_key: str | None = None
    bids_validator_path: str | None = None
    apptainer_binary: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> GuinCLIConfig:
        path = path or CONFIG_PATH
        if not path.is_file():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return cls()
        return cls(
            container_dir=_as_opt_str(data.get("container_dir")),
            model=_as_opt_str(data.get("model")),
            api_key=_as_opt_str(data.get("api_key")),
            bids_validator_path=_as_opt_str(data.get("bids_validator_path")),
            apptainer_binary=_as_opt_str(data.get("apptainer_binary")),
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "container_dir": self.container_dir,
            "model": self.model,
            "api_key": self.api_key,
            "bids_validator_path": self.bids_validator_path,
            "apptainer_binary": self.apptainer_binary,
        }

    def save(self, path: Path | None = None) -> Path:
        path = path or CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False), encoding="utf-8")
        return path


def apply_config_env(cfg: GuinCLIConfig) -> None:
    """Populate GUIN-related env vars from config when unset."""
    if cfg.container_dir and "GUIN_CONTAINER_DIR" not in os.environ:
        os.environ["GUIN_CONTAINER_DIR"] = cfg.container_dir
    if cfg.api_key and "ANTHROPIC_API_KEY" not in os.environ:
        os.environ["ANTHROPIC_API_KEY"] = cfg.api_key
    if cfg.bids_validator_path and "GUIN_BIDS_VALIDATOR_PATH" not in os.environ:
        os.environ["GUIN_BIDS_VALIDATOR_PATH"] = cfg.bids_validator_path
    if cfg.apptainer_binary and "GUIN_APPTAINER_BINARY" not in os.environ:
        os.environ["GUIN_APPTAINER_BINARY"] = cfg.apptainer_binary
