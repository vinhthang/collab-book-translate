"""collab_config.py - Plugin configuration loader for collab-book-translate.

Zero-fallback configuration module. Validates all 12 required keys and ensures
types and constraints are strictly met while ignoring extra unknown keys.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from typing import Optional, Union


class ConfigError(ValueError):
    """Raised when configuration file is missing, malformed, or missing required keys."""


@dataclass(frozen=True)
class CollabConfig:
    workspace_dir: Path
    repo_full_name: str
    bot_login: str
    bot_git_name: str
    bot_git_email: str
    human_approver: str
    fallback_hours: float
    max_revise_rounds: int
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
    "fallback_hours",
    "max_revise_rounds",
    "release_formats",
    "max_source_bytes",
    "allowed_licenses",
    "supported_pairs",
)

REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
VALID_RELEASE_FORMATS = frozenset({"epub", "pdf", "docx"})


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
        "release_formats",
    )
    for f in str_fields:
        val = data[f]
        if not isinstance(val, str) or not val.strip():
            raise ConfigError(f"Missing or invalid required config key: {f}")

    raw_repo_full_name = data["repo_full_name"]
    if not REPO_FULL_NAME_RE.match(raw_repo_full_name):
        raise ConfigError(
            "Invalid required config key repo_full_name: must match 'owner/repo' "
            "(^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$)"
        )

    bot_login = data["bot_login"].strip()
    human_approver = data["human_approver"].strip()
    if human_approver == bot_login:
        raise ConfigError(
            "Invalid config: human_approver must not equal bot_login"
        )

    fallback_val = data["fallback_hours"]
    if (
        isinstance(fallback_val, bool)
        or not isinstance(fallback_val, (int, float))
        or not math.isfinite(fallback_val)
        or fallback_val <= 0
    ):
        raise ConfigError("Missing or invalid required config key: fallback_hours")

    for f in ("max_revise_rounds", "max_source_bytes"):
        val = data[f]
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ConfigError(f"Missing or invalid required config key: {f}")

    raw_format_tokens = [tok.strip() for tok in data["release_formats"].split(",")]
    if not raw_format_tokens or any(not tok for tok in raw_format_tokens):
        raise ConfigError(
            "Invalid required config key release_formats: empty format token is not allowed"
        )
    unknown_formats = [tok for tok in raw_format_tokens if tok not in VALID_RELEASE_FORMATS]
    if unknown_formats:
        raise ConfigError(
            f"Invalid required config key release_formats: unsupported format(s) {unknown_formats}"
        )
    if len(set(raw_format_tokens)) != len(raw_format_tokens):
        raise ConfigError(
            "Invalid required config key release_formats: duplicate format tokens are not allowed"
        )
    clean_release_formats = ",".join(raw_format_tokens)

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
        repo_full_name=raw_repo_full_name,
        bot_login=bot_login,
        bot_git_name=data["bot_git_name"].strip(),
        bot_git_email=data["bot_git_email"].strip(),
        human_approver=human_approver,
        fallback_hours=float(fallback_val),
        max_revise_rounds=int(data["max_revise_rounds"]),
        release_formats=clean_release_formats,
        max_source_bytes=int(data["max_source_bytes"]),
        allowed_licenses=frozenset(clean_licenses),
        supported_pairs=frozenset(clean_pairs),
    )
