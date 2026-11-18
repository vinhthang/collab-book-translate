import json
from pathlib import Path
import re
import subprocess
import pytest

from scripts.collab_config import (
    ConfigError,
    CollabConfig,
    REQUIRED_KEYS,
    load_collab_config,
)

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


def test_load_collab_config_default_fallback_to_repo_config_json(tmp_path: Path, monkeypatch):
    import scripts.collab_config as collab_config_mod

    monkeypatch.delenv("COLLAB_CONFIG_PATH", raising=False)
    repo_cfg = Path(collab_config_mod.__file__).resolve().parent.parent / "config.json"
    if not repo_cfg.is_file():
        fake_scripts = tmp_path / "scripts"
        fake_scripts.mkdir(parents=True, exist_ok=True)
        (tmp_path / "config.json").write_text(
            json.dumps({**VALID_CONFIG_DICT, "bot_login": "1quachuoi9vang"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(collab_config_mod, "__file__", str(fake_scripts / "collab_config.py"))

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


def test_load_collab_config_missing_default_repo_config_raises(tmp_path: Path, monkeypatch):
    import scripts.collab_config as collab_config_mod

    monkeypatch.delenv("COLLAB_CONFIG_PATH", raising=False)
    cwd_dir = tmp_path / "cwd_with_decoy"
    cwd_dir.mkdir(parents=True, exist_ok=True)
    (cwd_dir / "config.json").write_text(json.dumps(VALID_CONFIG_DICT), encoding="utf-8")
    monkeypatch.chdir(cwd_dir)

    fake_engine = tmp_path / "empty_engine"
    (fake_engine / "scripts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        collab_config_mod,
        "__file__",
        str(fake_engine / "scripts" / "collab_config.py"),
    )

    with pytest.raises(ConfigError, match="Config file does not exist"):
        load_collab_config()


@pytest.mark.parametrize("valid_repo", [
    "owner/collab-repo",
    "Org_1.2-3/Repo_Name.v2-test",
])
def test_load_collab_config_valid_repo_full_name(tmp_path: Path, valid_repo: str):
    cfg_file = tmp_path / "valid_repo.json"
    cfg_file.write_text(
        json.dumps({**VALID_CONFIG_DICT, "repo_full_name": valid_repo}),
        encoding="utf-8",
    )
    cfg = load_collab_config(cfg_file)
    assert cfg.repo_full_name == valid_repo


@pytest.mark.parametrize("bad_repo", [
    "missing-slash",
    "owner/",
    "/repo",
    "/",
    "owner/repo/extra",
    "owner /repo",
    "owner/ repo",
    " owner/repo",
    "owner/repo ",
    "<owner>/collab-book-translate",
])
def test_load_collab_config_invalid_repo_full_name_raises(tmp_path: Path, bad_repo: str):
    cfg_file = tmp_path / "bad_repo.json"
    cfg_file.write_text(
        json.dumps({**VALID_CONFIG_DICT, "repo_full_name": bad_repo}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="repo_full_name"):
        load_collab_config(cfg_file)


@pytest.mark.parametrize("valid_engine", ["workbuddy", "cbc"])
def test_load_collab_config_valid_evaluation_engine(tmp_path: Path, valid_engine: str):
    cfg_file = tmp_path / f"engine_{valid_engine}.json"
    cfg_file.write_text(
        json.dumps({**VALID_CONFIG_DICT, "evaluation_engine": valid_engine}),
        encoding="utf-8",
    )
    cfg = load_collab_config(cfg_file)
    assert cfg.evaluation_engine == valid_engine


@pytest.mark.parametrize("bad_engine", [
    "unknown",
    "openai",
    "mock",
    "fallback",
    "WORKBUDDY",
])
def test_load_collab_config_invalid_evaluation_engine_raises(tmp_path: Path, bad_engine: str):
    cfg_file = tmp_path / "bad_engine.json"
    cfg_file.write_text(
        json.dumps({**VALID_CONFIG_DICT, "evaluation_engine": bad_engine}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="evaluation_engine"):
        load_collab_config(cfg_file)


@pytest.mark.parametrize("valid_formats,expected", [
    ("epub", "epub"),
    ("pdf,docx", "pdf,docx"),
    ("epub,pdf,docx", "epub,pdf,docx"),
    ("docx, epub", "docx,epub"),
])
def test_load_collab_config_valid_release_formats(
    tmp_path: Path, valid_formats: str, expected: str
):
    cfg_file = tmp_path / "valid_formats.json"
    cfg_file.write_text(
        json.dumps({**VALID_CONFIG_DICT, "release_formats": valid_formats}),
        encoding="utf-8",
    )
    cfg = load_collab_config(cfg_file)
    assert cfg.release_formats == expected


@pytest.mark.parametrize("bad_formats", [
    "mobi",
    "epub,mobi",
    "epub,txt",
    "epub,",
    ",epub",
    "epub,,pdf",
    ",",
    "EPUB",
    "epub,epub",
])
def test_load_collab_config_invalid_release_formats_raises(tmp_path: Path, bad_formats: str):
    cfg_file = tmp_path / "bad_formats.json"
    cfg_file.write_text(
        json.dumps({**VALID_CONFIG_DICT, "release_formats": bad_formats}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="release_formats"):
        load_collab_config(cfg_file)


def test_load_collab_config_human_approver_equals_bot_login_raises(tmp_path: Path):
    cfg_file = tmp_path / "same_login.json"
    cfg_file.write_text(
        json.dumps({
            **VALID_CONFIG_DICT,
            "bot_login": "shared-account",
            "human_approver": "shared-account",
        }),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="human_approver|bot_login"):
        load_collab_config(cfg_file)


@pytest.mark.parametrize("numeric_field,bad_val", [
    ("window_hours", True),
    ("window_hours", False),
    ("fallback_hours", True),
    ("fallback_hours", False),
    ("max_revise_rounds", True),
    ("max_revise_rounds", False),
    ("max_source_bytes", True),
    ("max_source_bytes", False),
    ("window_hours", -0.1),
    ("fallback_hours", -5),
    ("max_revise_rounds", 0),
    ("max_revise_rounds", -1),
    ("max_revise_rounds", 2.5),
    ("max_source_bytes", 0),
    ("max_source_bytes", -1),
    ("max_source_bytes", 1024.5),
])
def test_load_collab_config_bool_and_non_positive_numeric_bounds_raise(
    tmp_path: Path, numeric_field: str, bad_val
):
    cfg_file = tmp_path / f"bad_num_{numeric_field}.json"
    cfg_file.write_text(
        json.dumps({**VALID_CONFIG_DICT, numeric_field: bad_val}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=numeric_field):
        load_collab_config(cfg_file)


def test_plugin_json_declares_config_and_config_example():
    repo_root = Path(__file__).resolve().parent.parent
    plugin_path = repo_root / "plugin.json"
    example_path = repo_root / "config.example.json"

    assert plugin_path.is_file()
    assert example_path.is_file()

    plugin_data = json.loads(plugin_path.read_text(encoding="utf-8"))
    assert plugin_data.get("config") == "config.json"
    assert plugin_data.get("config_example") == "config.example.json"

    example_data = json.loads(example_path.read_text(encoding="utf-8"))
    assert set(example_data.keys()) == set(REQUIRED_KEYS)


def test_gitignore_excludes_config_json_and_worktrees_and_untracks_config():
    repo_root = Path(__file__).resolve().parent.parent
    gitignore_lines = {
        line.strip()
        for line in (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "/config.json" in gitignore_lines or "config.json" in gitignore_lines
    assert ".worktrees/" in gitignore_lines or "/.worktrees/" in gitignore_lines

    ls_config = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "config.json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert ls_config.stdout.strip() == ""

    ls_tracked = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "config.example.json", "plugin.json"],
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = set(ls_tracked.stdout.strip().splitlines())
    assert tracked == {"config.example.json", "plugin.json"}


def test_conftest_isolated_config_fixture_behavior(tmp_path: Path):
    from tests.conftest import maybe_install_fallback_collab_config

    # 1. Unset COLLAB_CONFIG_PATH and missing repo config -> installs valid 14-key config
    env_a: dict[str, str] = {}
    missing_repo_root = tmp_path / "fresh_clone"
    missing_repo_root.mkdir()
    installed = maybe_install_fallback_collab_config(
        environ=env_a,
        repo_root=missing_repo_root,
        temp_dir=tmp_path / "tmp_cfg_a",
    )
    assert installed is True
    assert "COLLAB_CONFIG_PATH" in env_a
    cfg = load_collab_config(env_a["COLLAB_CONFIG_PATH"])
    assert isinstance(cfg, CollabConfig)

    # 2. Explicit COLLAB_CONFIG_PATH already set -> left untouched
    explicit_missing = str(tmp_path / "explicit_missing.json")
    env_b = {"COLLAB_CONFIG_PATH": explicit_missing}
    installed_b = maybe_install_fallback_collab_config(
        environ=env_b,
        repo_root=missing_repo_root,
        temp_dir=tmp_path / "tmp_cfg_b",
    )
    assert installed_b is False
    assert env_b["COLLAB_CONFIG_PATH"] == explicit_missing
    with pytest.raises(ConfigError):
        load_collab_config(env_b["COLLAB_CONFIG_PATH"])

    # 3. Repo config.json exists on disk -> COLLAB_CONFIG_PATH remains unset
    repo_with_cfg = tmp_path / "with_cfg"
    repo_with_cfg.mkdir()
    (repo_with_cfg / "config.json").write_text(json.dumps(VALID_CONFIG_DICT), encoding="utf-8")
    env_c: dict[str, str] = {}
    installed_c = maybe_install_fallback_collab_config(
        environ=env_c,
        repo_root=repo_with_cfg,
        temp_dir=tmp_path / "tmp_cfg_c",
    )
    assert installed_c is False
    assert "COLLAB_CONFIG_PATH" not in env_c


def test_contributor_templates_synced_with_spec():
    repo_root = Path(__file__).resolve().parent.parent
    contributing = (repo_root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    book_template = (repo_root / ".github" / "PULL_REQUEST_TEMPLATE" / "book.md").read_text(
        encoding="utf-8"
    )
    example_cfg = json.loads((repo_root / "config.example.json").read_text(encoding="utf-8"))

    # Zero Git LFS instructions in contributor docs/templates
    assert "lfs" not in contributing.lower()
    assert "lfs" not in book_template.lower()

    # Source size and per-chapter caps documented in both files
    for doc in (contributing, book_template):
        assert "max_source_bytes" in doc
        assert "50 MiB" in doc
        assert "2,000" in doc
        assert "2.5 MiB" in doc

    # Allowed licences in CONTRIBUTING.md match config.example.json (including owner-permission)
    for lic in example_cfg["allowed_licenses"]:
        assert f"`{lic}`" in contributing

    # Commit-pinned APPROVED review vs /approve-intake <head_oid> documented
    assert "APPROVED" in contributing
    assert "/approve-intake <head_oid>" in contributing

    # Canonical analyze_additions.json proposals schema documented
    assert "analyze_additions.json" in contributing
    assert '"proposals"' in contributing
    assert '"status": "proposed"' in contributing
    assert '"category"' in contributing
    assert '"note"' in contributing


def test_repository_files_have_no_wrapper_or_host_home_paths():
    repo_root = Path(__file__).resolve().parent.parent
    wrapper_tok = "r" + "t" + "k"
    home_prefix = "/" + "Users" + "/"
    forbidden_re = re.compile(rf"\b{wrapper_tok}\b|{re.escape(home_prefix)}")

    scan_targets = [
        repo_root / "scripts",
        repo_root / "tests",
        repo_root / ".github",
        repo_root / "plugin.json",
        repo_root / "config.example.json",
        repo_root / "CONTRIBUTING.md",
    ]
    offenders: list[str] = []
    for target in scan_targets:
        files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
        for path in files:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            match = forbidden_re.search(text)
            if match:
                rel = path.relative_to(repo_root)
                offenders.append(f"{rel}: {match.group(0)!r}")

    assert offenders == []
