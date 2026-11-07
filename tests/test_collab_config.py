import json
from pathlib import Path
import pytest

from scripts.collab_config import ConfigError, CollabConfig, load_collab_config

VALID_CONFIG_DICT = {
    "workspace_dir": "/custom/workspace",
    "repo_full_name": "owner/collab-repo",
    "bot_login": "my-bot",
    "bot_git_name": "MyBot",
    "bot_git_email": "bot@example.com",
    "human_approver": "approver-user",
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


def test_load_collab_config_happy_path(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(VALID_CONFIG_DICT), encoding="utf-8")

    cfg = load_collab_config(cfg_file)
    assert isinstance(cfg, CollabConfig)
    assert cfg.workspace_dir == Path("/custom/workspace")
    assert cfg.repo_full_name == "owner/collab-repo"
    assert cfg.bot_login == "my-bot"
    assert cfg.bot_git_name == "MyBot"
    assert cfg.bot_git_email == "bot@example.com"
    assert cfg.human_approver == "approver-user"
    assert cfg.window_hours == 24.0
    assert cfg.fallback_hours == 48.0
    assert cfg.max_revise_rounds == 3
    assert cfg.evaluation_engine == "workbuddy"
    assert cfg.release_formats == "epub,pdf,docx"
    assert cfg.max_source_bytes == 52428800
    assert cfg.allowed_licenses == frozenset([
        "public-domain",
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "owner-permission",
    ])
    assert cfg.supported_pairs == frozenset([("en", "vi"), ("zh", "vi")])


def test_load_collab_config_missing_file_raises(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.json"
    with pytest.raises(ConfigError, match="Config file does not exist|Missing or invalid"):
        load_collab_config(non_existent)


def test_load_collab_config_invalid_json_raises(tmp_path: Path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json content!", encoding="utf-8")
    with pytest.raises(ConfigError, match="Failed to parse|not valid JSON"):
        load_collab_config(bad_file)


def test_load_collab_config_not_a_dict_raises(tmp_path: Path):
    bad_file = tmp_path / "list.json"
    bad_file.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a JSON object|dictionary"):
        load_collab_config(bad_file)


@pytest.mark.parametrize("missing_key", [
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
])
def test_load_collab_config_missing_key_raises(tmp_path: Path, missing_key: str):
    data = dict(VALID_CONFIG_DICT)
    del data[missing_key]
    cfg_file = tmp_path / f"missing_{missing_key}.json"
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_collab_config(cfg_file)
    assert missing_key in str(exc_info.value)


@pytest.mark.parametrize("bad_field,bad_value", [
    ("workspace_dir", ""),
    ("repo_full_name", ""),
    ("bot_login", "   "),
    ("bot_git_name", ""),
    ("bot_git_email", ""),
    ("human_approver", ""),
    ("window_hours", 0),
    ("window_hours", -1.0),
    ("fallback_hours", 0),
    ("fallback_hours", "not_a_number"),
    ("max_revise_rounds", 0),
    ("max_revise_rounds", -5),
    ("evaluation_engine", ""),
    ("release_formats", ""),
    ("max_source_bytes", 0),
    ("max_source_bytes", -100),
    ("allowed_licenses", []),
    ("allowed_licenses", [123]),
    ("supported_pairs", []),
    ("supported_pairs", [["en"]]),
    ("supported_pairs", [["en", "vi", "fr"]]),
    ("supported_pairs", ["en-vi"]),
])
def test_load_collab_config_invalid_values_raises(tmp_path: Path, bad_field: str, bad_value):
    data = dict(VALID_CONFIG_DICT)
    data[bad_field] = bad_value
    cfg_file = tmp_path / f"invalid_{bad_field}.json"
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_collab_config(cfg_file)
    assert bad_field in str(exc_info.value) or "invalid" in str(exc_info.value).lower()


def test_load_collab_config_env_var_override(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "env_config.json"
    data = dict(VALID_CONFIG_DICT)
    data["repo_full_name"] = "env-owner/env-repo"
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setenv("COLLAB_CONFIG_PATH", str(cfg_file))
    cfg = load_collab_config()
    assert cfg.repo_full_name == "env-owner/env-repo"


def test_load_collab_config_explicit_arg_overrides_env_var(tmp_path: Path, monkeypatch):
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps({**VALID_CONFIG_DICT, "bot_login": "env-bot"}), encoding="utf-8")

    arg_file = tmp_path / "arg.json"
    arg_file.write_text(json.dumps({**VALID_CONFIG_DICT, "bot_login": "arg-bot"}), encoding="utf-8")

    monkeypatch.setenv("COLLAB_CONFIG_PATH", str(env_file))
    cfg = load_collab_config(arg_file)
    assert cfg.bot_login == "arg-bot"


def test_load_collab_config_default_fallback_to_repo_config_json(monkeypatch):
    monkeypatch.delenv("COLLAB_CONFIG_PATH", raising=False)
    cfg = load_collab_config()
    assert isinstance(cfg, CollabConfig)
    assert cfg.bot_login == "1quachuoi9vang"


def test_get_workspace_root_resolution(tmp_path: Path, monkeypatch):
    from scripts.utils.paths import get_workspace_root

    # 1. Override takes precedence
    assert get_workspace_root(tmp_path / "custom") == (tmp_path / "custom").resolve()

    # 2. AGENT_TRANSLATOR_WORKSPACE takes second precedence
    monkeypatch.setenv("AGENT_TRANSLATOR_WORKSPACE", str(tmp_path / "env_ws"))
    assert get_workspace_root() == (tmp_path / "env_ws").resolve()

    # 3. Falls back to load_collab_config().workspace_dir
    monkeypatch.delenv("AGENT_TRANSLATOR_WORKSPACE", raising=False)
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({**VALID_CONFIG_DICT, "workspace_dir": str(tmp_path / "cfg_ws")}), encoding="utf-8")
    monkeypatch.setenv("COLLAB_CONFIG_PATH", str(cfg_file))
    assert get_workspace_root() == (tmp_path / "cfg_ws").resolve()

    # 4. Raises ConfigError if config is missing and no env var/override
    monkeypatch.setenv("COLLAB_CONFIG_PATH", str(tmp_path / "nonexistent.json"))
    with pytest.raises(ConfigError):
        get_workspace_root()

