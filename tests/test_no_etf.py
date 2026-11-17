"""Nothing ETF remains in the connector (ADR 0027).

The connector keeps one kind of portfolio, built from the weights an application provides. The
ETF extractor, Universe Sources, Asset Universes, holdings categories, ETF signals and ETF
portfolios are gone, and no name says ETF. This is the one test module that names them.

Two exemptions: the applied revisions under `migrations/versions/` keep the names of the tables
they made, and the four launchers of the retired ETF Jobs stay at their paths for one release
while the platform may still list a Job bound to them (revision 0028's release deletes them).
"""

from __future__ import annotations

import ast
import json
import re
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
JOBS = REPO_ROOT / "src" / "jobs"

# "ETF" or "Etf" anywhere (AlpacaETFSignal, ETFs), and "etf" where a word starts (etf_signal,
# alpaca-etf, etfhextractor); "getfixturevalue" is no mention.
ETF_MENTION = re.compile(r"ETF|Etf|(?<![A-Za-z])etf")
SCANNED_ROOTS = ("src", "api", "tests", "scripts", ".tau", ".mainsequence")
SCANNED_FILES = (
    ".agents/agent_card.json",
    "pyproject.toml",
    "requirements.txt",
    "Dockerfile",
)
SKIPPED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache"}
# Applied revisions name the tables they made and are never edited.
REVISIONS = "src/alpaca_connectors/migrations/versions/"
# The retired Jobs' launchers: the portfolio pair delegates to the new launchers, the signal pair
# does nothing. Revision 0028's release deletes all four.
RETIRED_PORTFOLIO_LAUNCHERS = {
    "run_alpaca_etf_portfolio.py": ("run_configuration_job", ["--configuration-uid", "uid"]),
    "dispatch_alpaca_etf_portfolio.py": ("run_dispatcher_job", ["--scheduled"]),
}
RETIRED_SIGNAL_LAUNCHERS = ("run_alpaca_etf_signal.py", "dispatch_alpaca_etf_signal.py")
RETIRED_LAUNCHERS = frozenset(
    f"src/jobs/{name}" for name in (*RETIRED_PORTFOLIO_LAUNCHERS, *RETIRED_SIGNAL_LAUNCHERS)
)
REMOVED_ROUTE_PREFIXES = ("/v1/universe-sources", "/v1/universes", "/v1/signal-jobs")
REMOVED_DISTRIBUTIONS = ("etfhextractor", "playwright")
MIGRATION_ONLY_MODULES = (
    "alpaca_connectors.migrations.env",
    "alpaca_connectors.portfolios.interpolated_prices_migration",
)


def _scanned_files() -> list[Path]:
    files = [REPO_ROOT / name for name in SCANNED_FILES]
    for root in SCANNED_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*")):
            parts = path.relative_to(REPO_ROOT).parts
            if not path.is_file() or any(
                part in SKIPPED_PARTS or part.endswith(".egg-info") for part in parts
            ):
                continue
            files.append(path)
    return [path for path in files if path.is_file() and path != Path(__file__).resolve()]


def _python_sources(*roots: str) -> list[Path]:
    return [
        path
        for root in roots
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if not any(part.endswith(".egg-info") for part in path.parts)
    ]


def test_no_file_or_name_mentions_etf() -> None:
    mentions: list[str] = []
    for path in _scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith(REVISIONS) or relative in RETIRED_LAUNCHERS:
            continue
        if ETF_MENTION.search(relative):
            mentions.append(relative)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # images and other binary files
        mentions.extend(
            f"{relative}:{number}"
            for number, line in enumerate(text.splitlines(), start=1)
            if ETF_MENTION.search(line)
        )

    assert mentions == []


def test_no_module_imports_the_etf_extractor() -> None:
    importers: list[str] = []
    for path in _python_sources("src", "api", "scripts"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "etfhextractor" for name in names):
                importers.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert importers == []


def test_the_extractor_and_its_browser_are_no_dependency() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = [
        *project["project"]["dependencies"],
        *(
            requirement
            for extra in project["project"].get("optional-dependencies", {}).values()
            for requirement in extra
        ),
    ]
    for distribution in REMOVED_DISTRIBUTIONS:
        assert not [item for item in declared if distribution in item.lower()], distribution
        for locked in ("uv.lock", "requirements.txt"):
            text = (REPO_ROOT / locked).read_text(encoding="utf-8").lower()
            assert distribution not in text, (locked, distribution)


def test_no_route_serves_a_source_universe_or_signal() -> None:
    from alpaca_connectors.api.app.main import create_app

    paths = sorted(getattr(route, "path", "") for route in create_app().routes)

    assert paths
    assert [path for path in paths if path.startswith(REMOVED_ROUTE_PREFIXES)] == []


def test_no_tool_or_skill_is_named_for_etf() -> None:
    from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOL_NAMES

    card = json.loads((REPO_ROOT / ".agents" / "agent_card.json").read_text(encoding="utf-8"))
    names = [
        *PROJECT_AGENT_TOOL_NAMES,
        *(path.name for path in (REPO_ROOT / ".tau" / "skills").iterdir()),
        *(skill["id"] for skill in card["skills"]),
        *(skill["name"] for skill in card["skills"]),
    ]

    assert names
    assert [name for name in names if "etf" in name.lower()] == []


def test_no_application_module_loads_the_etf_extractor() -> None:
    """Every module of the package and both API entry points, in a fresh interpreter.

    Two modules run only inside a migration: Alembic's environment script and the interpolated
    prices provider, which reads the sources its preparing command sets.
    """
    code = (
        "import importlib, json, pkgutil, sys\n"
        "import alpaca_connectors\n"
        "names = [m.name for m in pkgutil.walk_packages(alpaca_connectors.__path__, "
        "'alpaca_connectors.') if m.name not in set(sys.argv[1:])]\n"
        "for name in [*names, 'api.app.main', 'api.agent.main']:\n"
        "    importlib.import_module(name)\n"
        "print(json.dumps({'imported': names, 'loaded': sorted(sys.modules)}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, *MIGRATION_ONLY_MODULES],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
        timeout=300,
    )
    report = json.loads(result.stdout.splitlines()[-1])

    assert "alpaca_connectors.migrations.schema_gate" in report["imported"]
    assert [name for name in report["loaded"] if name.split(".")[0] == "etfhextractor"] == []


@pytest.mark.parametrize("launcher", sorted(RETIRED_PORTFOLIO_LAUNCHERS))
def test_a_retired_portfolio_launcher_runs_the_portfolio_launcher(
    monkeypatch, capsys, launcher
) -> None:
    from alpaca_connectors.operations import configuration_jobs

    work, argv = RETIRED_PORTFOLIO_LAUNCHERS[launcher]
    gate: list[str] = []
    runs: list[tuple[str, str, list[str]]] = []
    behind = True
    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind",
        lambda label: gate.append(label) or behind,
    )
    for name in ("run_configuration_job", "run_dispatcher_job"):
        monkeypatch.setattr(
            configuration_jobs,
            name,
            lambda kind, given, name=name: runs.append((name, kind, list(given))) or {},
        )
    main = runpy.run_path(str(JOBS / launcher))["main"]

    # The schema gate of the portfolio launcher comes first.
    assert main(argv) == 0
    assert runs == [] and gate[0].startswith("Alpaca portfolio")

    behind = False
    assert main(argv) == 0
    capsys.readouterr()
    assert runs == [(work, "portfolio", argv)]


@pytest.mark.parametrize("launcher", RETIRED_SIGNAL_LAUNCHERS)
def test_a_retired_signal_launcher_does_nothing(monkeypatch, capsys, launcher) -> None:
    from alpaca_connectors.operations import configuration_jobs

    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind",
        lambda label: pytest.fail("a launcher that does nothing needs no schema check"),
    )
    for work in ("run_configuration_job", "run_dispatcher_job"):
        monkeypatch.setattr(configuration_jobs, work, lambda *args: pytest.fail("ran work"))

    assert runpy.run_path(str(JOBS / launcher))["main"](["--scheduled"]) == 0
    assert json.loads(capsys.readouterr().out)["retired"] is True
