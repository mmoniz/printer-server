"""requirements.txt (what the Pi installs) and pyproject.toml (what uv.lock is
built from) must declare the same runtime dependencies, or the lockfile would
test a different set of packages than the Pi runs."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _requirements_txt() -> set[str]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _pyproject_dependencies() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return set(project["project"]["dependencies"])


def test_pyproject_declares_the_same_dependencies_as_requirements_txt():
    assert _pyproject_dependencies() == _requirements_txt()


def test_pyproject_requires_the_python_the_pi_runs():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["requires-python"] == ">=3.11"
