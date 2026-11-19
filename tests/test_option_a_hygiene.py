"""Tests for REQ-037: Option A corpus separation, relocated pilot fixture, and mcp_config.json."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

from scripts.collab_completeness import verify_book_completeness
from scripts.collab_config import REQUIRED_KEYS, load_collab_config
from scripts.collab_ledger import verify_ledger_integrity


def scan_option_a_violations(repo_root: Path, tracked_paths: list[str]) -> list[str]:
    """Return deterministic violation strings for Option A corpus & token hygiene."""
    violations: list[str] = []
    for rel in tracked_paths:
        if rel.startswith(("books/", "runs/")):
            violations.append(f"tracked_corpus:{rel}")

    wrapper_tok = "r" + "t" + "k"
    home_prefix = "/" + "Users" + "/"
    vendor_tok = "work" + "buddy"
    forbidden_re = re.compile(
        rf"\b{wrapper_tok}\b|{re.escape(home_prefix)}|{vendor_tok}",
        re.IGNORECASE,
    )

    targets = [
        repo_root / "scripts",
        repo_root / "plugin.json",
        repo_root / "mcp_config.json",
        repo_root / "config.example.json",
        repo_root / "CONTRIBUTING.md",
        repo_root / "README.md",
    ]
    for target in targets:
        if not target.exists():
            violations.append(f"missing_required_file:{target.name}")
            continue
        files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
        for path in files:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            match = forbidden_re.search(text)
            if match:
                violations.append(f"forbidden_token:{path.relative_to(repo_root)}:{match.group(0)}")
    return violations


def test_ac_037_1_untracked_corpus_mcp_config_and_relocated_fixture():
    repo_root = Path(__file__).resolve().parent.parent

    # 1. Zero tracked files under books/ or runs/
    res = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "books", "runs"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert res.stdout.strip() == "", f"Expected zero tracked corpus files, got:\n{res.stdout}"

    # 2. mcp_config.json declares Streamable HTTP collab-books endpoint
    mcp_cfg_path = repo_root / "mcp_config.json"
    assert mcp_cfg_path.is_file(), "mcp_config.json must exist at plugin root"
    mcp_cfg = json.loads(mcp_cfg_path.read_text(encoding="utf-8"))
    assert mcp_cfg == {
        "mcpServers": {
            "collab-books": {
                "url": "https://collab.vinhthang.dev/mcp",
            }
        }
    }

    # 3. config.example.json preserves exact 12 keys and targets collab-books
    example_path = repo_root / "config.example.json"
    raw_example = json.loads(example_path.read_text(encoding="utf-8"))
    assert set(raw_example.keys()) == set(REQUIRED_KEYS)
    assert raw_example["repo_full_name"] == "<owner>/collab-books"
    assert raw_example["workspace_dir"] == "/path/to/collab-books"

    # 4. Relocated pilot fixture satisfies completeness and ledger integrity
    fixture_dir = repo_root / "tests" / "fixtures" / "runs" / "scandal-in-bohemia"
    assert fixture_dir.is_dir()
    comp = verify_book_completeness(fixture_dir)
    assert comp["ready_to_publish"] is True
    ledger = json.loads((fixture_dir / "collab_ledger.json").read_text(encoding="utf-8"))
    ledger_res = verify_ledger_integrity(ledger, ["ch01", "ch02", "ch03"])
    assert ledger_res["valid"] is True


def test_ac_037_2_hygiene_scanner_passes_repo_and_catches_synthetic_violations(tmp_path: Path):
    repo_root = Path(__file__).resolve().parent.parent
    ls_all = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = [line.strip() for line in ls_all.stdout.splitlines() if line.strip()]
    assert scan_option_a_violations(repo_root, tracked) == []

    # Boundary/failure: synthetic repo with tracked books/ path and forbidden token fails
    fake_root = tmp_path / "repo"
    (fake_root / "scripts").mkdir(parents=True)
    bad_word = "work" + "buddy"
    (fake_root / "scripts" / "bad.py").write_text(f"# {bad_word}\n", encoding="utf-8")
    for fname in ("plugin.json", "mcp_config.json", "config.example.json", "CONTRIBUTING.md", "README.md"):
        (fake_root / fname).write_text("{}\n", encoding="utf-8")

    found = scan_option_a_violations(fake_root, ["books/demo/manifest.json"])
    assert "tracked_corpus:books/demo/manifest.json" in found
    assert any(item.startswith("forbidden_token:scripts/bad.py:") for item in found)
