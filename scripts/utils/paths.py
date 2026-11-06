"""
scripts/utils/paths.py — Workspace & Engine Path Resolution (REQ-007 / AC-007.1).
"""

import json
import os
from pathlib import Path
from typing import Optional, Union


def get_engine_root() -> Path:
    """Returns the root directory of the agent-translator engine."""
    return Path(__file__).resolve().parent.parent.parent


def get_workspace_root(override: Optional[Union[str, Path]] = None) -> Path:
    """
    Resolves the books workspace root directory according to precedence:
    1. Explicit override argument.
    2. AGENT_TRANSLATOR_WORKSPACE environment variable.
    3. 'workspace_dir' in <engine_root>/config.json.
    4. Path.cwd() if it contains a 'runs' or 'books' directory.
    5. Fallback to get_engine_root().
    """
    if override:
        return Path(override).expanduser().resolve()

    env_val = os.environ.get("AGENT_TRANSLATOR_WORKSPACE")
    if env_val and env_val.strip():
        return Path(env_val).expanduser().resolve()

    cfg_file = get_engine_root() / "config.json"
    if cfg_file.is_file():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            ws = cfg.get("workspace_dir")
            if ws and str(ws).strip():
                return Path(ws).expanduser().resolve()
        except Exception:
            pass

    cwd = Path.cwd().resolve()
    if (cwd / "runs").is_dir() or (cwd / "books").is_dir():
        return cwd

    return get_engine_root()


def get_runs_dir(override: Optional[Union[str, Path]] = None) -> Path:
    """Returns the runs directory within the active workspace."""
    return get_workspace_root(override) / "runs"


def get_books_dir(override: Optional[Union[str, Path]] = None) -> Path:
    """Returns the books directory within the active workspace."""
    return get_workspace_root(override) / "books"


def get_inputs_dir(override: Optional[Union[str, Path]] = None) -> Path:
    """Returns the inputs directory within the active workspace."""
    return get_workspace_root(override) / "inputs"


def resolve_run_dir(
    run_dir_or_slug: Union[str, Path],
    workspace_override: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Resolves a run directory given a Path, relative path, or book slug.
    Checks:
    1. Direct absolute path or existing relative path.
    2. Path starting with 'runs/'.
    3. Slug inside get_runs_dir().
    4. Fallback to get_workspace_root() / run_dir_or_slug.
    """
    p = Path(run_dir_or_slug)
    if p.is_absolute() or p.exists():
        return p.resolve()

    if str(run_dir_or_slug).startswith("runs/"):
        cand = get_workspace_root(workspace_override) / p
        return cand.resolve()

    cand_runs = get_runs_dir(workspace_override) / p
    if cand_runs.exists():
        return cand_runs.resolve()

    return (get_workspace_root(workspace_override) / p).resolve()
