"""Shared fixtures for the rendering pipeline tests.

Tests are hermetic: they import scripts/render.py as a module, monkeypatch its
AGENTS_DIR (and SETTINGS_PATH when relevant) to a tmp scratch dir, and exercise
the renderer's public functions directly. The real agents/ directory is never
mutated.
"""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def schema() -> dict:
    schema_path = PROJECT_ROOT / "schemas" / "customization.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _load_starter(name: str) -> dict:
    path = PROJECT_ROOT / "templates" / "starters" / name / "customization.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def sme_starter() -> dict:
    return copy.deepcopy(_load_starter("sme"))


@pytest.fixture
def sales_coach_starter() -> dict:
    return copy.deepcopy(_load_starter("sales-coach"))


@pytest.fixture
def render_module():
    import render  # noqa: WPS433 — late import after sys.path insert

    return importlib.reload(render)


@pytest.fixture
def render_fn(tmp_path, monkeypatch, render_module):
    """Returns a callable: render_fn(data, name="test_agent") -> rendered text.

    Stages a per-test agents/<name>/customization.json under tmp_path and
    invokes the renderer via its main() entry point so the full pipeline
    (resolve -> load -> validate -> render -> write -> header) runs.
    """

    def _render(data: dict, name: str = "test_agent") -> str:
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / name
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "customization.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

        monkeypatch.setattr(render_module, "AGENTS_DIR", agents_dir)
        monkeypatch.setattr(render_module, "SETTINGS_PATH", tmp_path / "settings.json")
        # PROJECT_ROOT is referenced by main() for the relative_to() print.
        # Point it at tmp_path so AGENTS_DIR remains a subpath of PROJECT_ROOT.
        monkeypatch.setattr(render_module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["render.py", "--agent", name])

        rc = render_module.main()
        assert rc == 0
        return (agent_dir / "system_prompt.txt").read_text(encoding="utf-8")

    return _render
