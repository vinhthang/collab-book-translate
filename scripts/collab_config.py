"""collab_config.py - Plugin configuration loader for collab-book-translate.

Zero-fallback configuration module. Validates all required keys and ensures
types and constraints are strictly met.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Optional, Union


class ConfigError(ValueError):
    """Raised when configuration file is missing, malformed, or missing required keys."""
    pass


@dataclass(frozen=True)
class CollabConfig:
    workspace_dir: Path
    repo_full_name: str
    bot_login: str
    bot_git_name: str
    bot_git_email: str
    human_approver: str
    window_hours: float
    fallback_hours: float
    max_revise_rounds: int
    evaluation_engine: str
    release_formats: str
    max_source_bytes: int
    allowed_licenses: frozenset[str]
    supported_pairs: frozenset[tuple[str, str]]


REQUIRED_KEYS = (
    "workspace_dir",
    "repo_full_name",
    "bot_login",
    "bot_git_name",
    "bot_git_email",
    "human_approver",
    "window_hours",
    "fallback_hours",
    "max_revise_rounds",
    "evaluation_engine",
    "release_formats",
    "max_source_bytes",
    "allowed_licenses",
    "supported_pairs",
)


def load_collab_config(config_path: Optional[Union[str, Path]] = None) -> CollabConfig:
    """Load and strictly validate CollabConfig from file.

    Resolution order:
      1. Explicit config_path parameter
      2. COLLAB_CONFIG_PATH environment variable
      3. config.json in repository root (two directories above this file)

    Raises ConfigError if the file is missing, invalid JSON, or missing/invalid keys.
    """
    if config_path is not None:
        target_path = Path(config_path)
    elif "COLLAB_CONFIG_PATH" in os.environ and os.environ["COLLAB_CONFIG_PATH"].strip():
        target_path = Path(os.environ["COLLAB_CONFIG_PATH"].strip())
    else:
        target_path = Path(__file__).resolve().parent.parent / "config.json"

    if not target_path.is_file():
        raise ConfigError(f"Config file does not exist: {target_path}")

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Failed to parse config file as JSON: {target_path} ({exc})") from exc
    except Exception as exc:
        raise ConfigError(f"Error reading config file {target_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Config file must be a JSON object: {target_path}")

    for key in REQUIRED_KEYS:
        if key not in data:
            raise ConfigError(f"Missing or invalid required config key: {key}")

    str_fields = (
        "workspace_dir",
        "repo_full_name",
        "bot_login",
        "bot_git_name",
        "bot_git_email",
        "human_approver",
        "evaluation_engine",
        "release_formats",
    )
    for f in str_fields:
        val = data[f]
        if not isinstance(val, str) or not val.strip():
            raise ConfigError(f"Missing or invalid required config key: {f}")

    for f in ("window_hours", "fallback_hours"):
        val = data[f]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
            raise ConfigError(f"Missing or invalid required config key: {f}")

    for f in ("max_revise_rounds", "max_source_bytes"):
        val = data[f]
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ConfigError(f"Missing or invalid required config key: {f}")

    raw_licenses = data["allowed_licenses"]
    if not isinstance(raw_licenses, (list, tuple, set, frozenset)) or len(raw_licenses) == 0:
        raise ConfigError("Missing or invalid required config key: allowed_licenses")
    clean_licenses = []
    for lic in raw_licenses:
        if not isinstance(lic, str) or not lic.strip():
            raise ConfigError("Missing or invalid required config key: allowed_licenses")
        clean_licenses.append(lic.strip())

    raw_pairs = data["supported_pairs"]
    if not isinstance(raw_pairs, (list, tuple, set, frozenset)) or len(raw_pairs) == 0:
        raise ConfigError("Missing or invalid required config key: supported_pairs")
    clean_pairs = []
    for p in raw_pairs:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ConfigError("Missing or invalid required config key: supported_pairs")
        src, tgt = p
        if not isinstance(src, str) or not src.strip() or not isinstance(tgt, str) or not tgt.strip():
            raise ConfigError("Missing or invalid required config key: supported_pairs")
        clean_pairs.append((src.strip(), tgt.strip()))

    return CollabConfig(
        workspace_dir=Path(data["workspace_dir"].strip()),
        repo_full_name=data["repo_full_name"].strip(),
        bot_login=data["bot_login"].strip(),
        bot_git_name=data["bot_git_name"].strip(),
        bot_git_email=data["bot_git_email"].strip(),
        human_approver=data["human_approver"].strip(),
        window_hours=float(data["window_hours"]),
        fallback_hours=float(data["fallback_hours"]),
        max_revise_rounds=int(data["max_revise_rounds"]),
        evaluation_engine=data["evaluation_engine"].strip(),
        release_formats=data["release_formats"].strip(),
        max_source_bytes=int(data["max_source_bytes"]),
        allowed_licenses=frozenset(clean_licenses),
        supported_pairs=frozenset(clean_pairs),
    )
