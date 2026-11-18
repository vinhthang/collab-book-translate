"""Shared pytest fixtures for isolated CollabConfig test execution."""

from __future__ import annotations

from collections.abc import MutableMapping
import json
import os
from pathlib import Path
import pytest


def maybe_install_fallback_collab_config(
    environ: MutableMapping[str, str],
    repo_root: Path,
    temp_dir: Path,
) -> bool:
    """Set COLLAB_CONFIG_PATH to a temporary valid 14-key config file ONLY when
    COLLAB_CONFIG_PATH is unset and <repo_root>/config.json does not exist on disk.
    """
    env_cfg = environ.get("COLLAB_CONFIG_PATH", "").strip()
    repo_config = repo_root / "config.json"
    if env_cfg or repo_config.is_file():
        return False

    temp_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = temp_dir / "config.json"
    payload = {
        "workspace_dir": str(repo_root),
        "repo_full_name": "vinhthang/collab-book-translate",
        "bot_login": "1quachuoi9vang",
        "bot_git_name": "1quachuoi9vang",
        "bot_git_email": "1quachuoi9vang@users.noreply.github.com",
        "human_approver": "vinhthang",
        "window_hours": 24.0,
        "fallback_hours": 48.0,
        "max_revise_rounds": 3,
        "evaluation_engine": "workbuddy",
        "release_formats": "epub,pdf,docx",
        "max_source_bytes": 52428800,
        "allowed_licenses": [
            "public-domain",
            "CC0-1.0",
            "CC-BY-4.0",
            "CC-BY-SA-4.0",
            "owner-permission",
        ],
        "supported_pairs": [
            ["en", "vi"],
            ["zh", "vi"],
        ],
    }
    cfg_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    environ["COLLAB_CONFIG_PATH"] = str(cfg_file)
    return True


@pytest.fixture(scope="session", autouse=True)
def _isolated_collab_config_fallback(tmp_path_factory: pytest.TempPathFactory):
    """Ensure fresh clones without a local config.json can run unit tests cleanly."""
    repo_root = Path(__file__).resolve().parent.parent
    if os.environ.get("COLLAB_CONFIG_PATH", "").strip() or (repo_root / "config.json").is_file():
        yield
        return

    temp_dir = tmp_path_factory.mktemp("collab_config")
    installed = maybe_install_fallback_collab_config(
        environ=os.environ,
        repo_root=repo_root,
        temp_dir=temp_dir,
    )
    try:
        yield
    finally:
        if installed:
            os.environ.pop("COLLAB_CONFIG_PATH", None)
