"""ADR 0013: portfolio configurations run on a workflow-declared Job, whose scheduled run a
dispatcher Job starts; portfolios are the one configuration kind (ADR 0027)."""

from __future__ import annotations

import ast
import datetime as dt
import json
import runpy
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from alpaca_connectors.operations import configuration_jobs
from alpaca_connectors.operations.configuration_jobs import (
    ALPACA_PORTFOLIO_DISPATCHER_EXECUTION_PATH,
    ALPACA_PORTFOLIO_EXECUTION_PATH,
    JOB_SPECS,
    PORTFOLIO_DISPATCHER_JOB_NAME,
    PORTFOLIO_JOB_NAME,
    WORKFLOW_PATH,
    ConfigurationJobState,
    ConfigurationJobSubmission,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS = PROJECT_ROOT / "src" / "jobs"
PORTFOLIO_JOB_UID = "88888888-8888-4888-8888-888888888888"
PORTFOLIO_DISPATCHER_UID = "77777777-7777-4777-8777-777777777772"
A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
NOW = dt.datetime(2026, 10, 5, 9, tzinfo=dt.UTC)


class FakeJob:
    def __init__(
        self,
        *,
        uid: str = PORTFOLIO_JOB_UID,
        name: str = PORTFOLIO_JOB_NAME,
        execution_path: str = ALPACA_PORTFOLIO_EXECUTION_PATH,
        image_status: str = "ready",
        task_schedule: object | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self.uid = uid
        self.name = name
        self.execution_path = execution_path
        self.image_status = image_status
        self.task_schedule = task_schedule
        self.failing = failing or set()
        self.cpu_request = "0.25"
        self.memory_request = "0.5"
        self.max_runtime_seconds = 3600
        self.spot = False
        self.automatic_deployment = True
        self.calls: list[list[str]] = []

    def run_job(self, *, timeout=None, command_args=None):
        if command_args[1] in self.failing:
            raise RuntimeError("Job 'x' did not start.")
        self.calls.append(list(command_args))
        return {"message": "accepted", "job_run_uid": f"run-{len(self.calls)}"}


def _dispatcher(**overrides) -> FakeJob:
    """The portfolio dispatcher Job, which holds the schedule of the portfolio Job."""
    values = {
        "uid": PORTFOLIO_DISPATCHER_UID,
        "name": PORTFOLIO_DISPATCHER_JOB_NAME,
        "execution_path": ALPACA_PORTFOLIO_DISPATCHER_EXECUTION_PATH,
        **overrides,
    }
    return FakeJob(**values)


def _run(
    uid,
    configuration_uid=None,
    *,
    status="SUCCEEDED",
    job_uid=PORTFOLIO_JOB_UID,
    job_name=PORTFOLIO_JOB_NAME,
    command_args=None,
):
    if command_args is None:
        command_args = (
            ["--configuration-uid", configuration_uid] if configuration_uid else ["--scheduled"]
        )
    return SimpleNamespace(
        uid=uid,
        job_uid=job_uid,
        job_name=job_name,
        status=status,
        command_args=command_args,
        execution_start=None,
        execution_end=None,
        commit_hash="abc123",
        runtime_image_uid=None,
        runtime_image_digest=None,
        observability=None,
    )


@pytest.fixture
def platform(monkeypatch):
    """Fake `PlatformJob` and `JobRun` clients: `jobs` by name, `runs` newest first."""

    state = SimpleNamespace(jobs=[], runs=[], job_queries=[], scanned=0)

    class PlatformJob:
        @classmethod
        def filter(cls, **kwargs):
            state.job_queries.append(kwargs)
            return [job for job in state.jobs if job.name == kwargs["name"]]

    class JobRun:
        @classmethod
        def iter_filter(cls, *, job__uid, max_items, timeout):
            assert max_items == configuration_jobs.RUN_SCAN_LIMIT
            for run in state.runs:
                if run.job_uid == job__uid:
                    state.scanned += 1
                    yield run

        @classmethod
        def filter(cls, *, uid, timeout):
            return [run for run in state.runs if run.uid == uid]

    monkeypatch.setattr("alpaca_connectors.operations.platform_jobs.PlatformJob", PlatformJob)
    monkeypatch.setattr("mainsequence.client.JobRun", JobRun)
    return state


def _configuration(monkeypatch, *, uid=A):
    configuration = SimpleNamespace(uid=uuid.UUID(uid))
    monkeypatch.setattr(
        configuration_jobs, "get_portfolio_configuration", lambda value: configuration
    )
    return configuration


def _preflight(monkeypatch, blocked: dict[str, list[str]] | None = None):
    """Replace both preflights; return the configurations each batch preflight was given."""
    blocked = blocked or {}
    batches: list[list[str]] = []

    def answer(configuration_uid):
        blockers = tuple(blocked.get(str(configuration_uid), []))
        return SimpleNamespace(allowed=not blockers, blockers=blockers)

    def preflight(configuration_uid, *, configuration=None, held=None):
        return answer(configuration_uid)

    def preflights(configurations, *, held=None):
        batches.append([str(configuration.uid) for configuration in configurations])
        return {
            str(configuration.uid): answer(configuration.uid) for configuration in configurations
        }

    monkeypatch.setattr(
        "alpaca_connectors.portfolios.execution.preflight_portfolio_configuration", preflight
    )
    monkeypatch.setattr(
        "alpaca_connectors.portfolios.execution.preflight_portfolio_configurations", preflights
    )
    return batches


def test_the_declared_job_is_found_by_name_and_reviewed_launcher(platform) -> None:
    assert configuration_jobs.resolve_configuration_job("portfolio") is None
    assert platform.job_queries == [{"name": PORTFOLIO_JOB_NAME, "timeout": 60}]

    job = FakeJob()
    platform.jobs = [job]
    assert configuration_jobs.resolve_configuration_job("portfolio") is job

    platform.jobs = [FakeJob(execution_path="src/jobs/other.py")]
    with pytest.raises(RuntimeError, match="does not run"):
        configuration_jobs.resolve_configuration_job("portfolio")

    platform.jobs = [FakeJob(), FakeJob()]
    with pytest.raises(RuntimeError, match="More than one branch Job"):
        configuration_jobs.resolve_configuration_job("portfolio")


def test_portfolios_are_the_one_configuration_kind() -> None:
    assert set(JOB_SPECS) == {"portfolio"}
    spec = JOB_SPECS["portfolio"]
    assert (spec.workflow_key, spec.dispatcher_workflow_key) == (
        "alpaca-portfolio",
        "alpaca-portfolio-dispatcher",
    )
    assert (spec.name, spec.dispatcher_name) == ("Alpaca Portfolio", "Alpaca Portfolio Dispatcher")
    assert (spec.execution_path, spec.dispatcher_execution_path) == (
        "src/jobs/run_alpaca_portfolio.py",
        "src/jobs/dispatch_alpaca_portfolio.py",
    )


def test_launch_starts_one_run_that_carries_the_configuration_uid(platform, monkeypatch) -> None:
    job = FakeJob()
    platform.jobs = [job]
    platform.runs = [_run("other", B, status="RUNNING")]
    _configuration(monkeypatch)
    _preflight(monkeypatch)

    submission = configuration_jobs.launch_portfolio_job(A)

    assert job.calls == [["--configuration-uid", A]]
    assert submission == ConfigurationJobSubmission(A, PORTFOLIO_JOB_UID, "run-1", "QUEUED")


def test_launch_refuses_without_a_ready_job_or_while_a_run_is_active(platform, monkeypatch) -> None:
    _configuration(monkeypatch)
    _preflight(monkeypatch)
    with pytest.raises(ValueError, match=WORKFLOW_PATH):
        configuration_jobs.launch_portfolio_job(A)

    platform.jobs = [FakeJob(image_status="building")]
    with pytest.raises(ValueError, match="image is not ready"):
        configuration_jobs.launch_portfolio_job(A)

    job = FakeJob()
    platform.jobs = [job]
    platform.runs = [_run("newest", A, status="RUNNING"), _run("older", A)]
    with pytest.raises(ValueError, match="pending or running"):
        configuration_jobs.launch_portfolio_job(A)
    assert job.calls == []


def test_launch_of_a_missing_configuration_is_not_found(platform, monkeypatch) -> None:
    monkeypatch.setattr(configuration_jobs, "get_portfolio_configuration", lambda value: None)

    with pytest.raises(LookupError, match="Portfolio configuration"):
        configuration_jobs.launch_portfolio_job(A)
    assert platform.job_queries == []


def test_portfolio_launch_requires_an_allowed_preflight(platform, monkeypatch) -> None:
    job = FakeJob()
    platform.jobs = [job]
    _configuration(monkeypatch)
    _preflight(monkeypatch, {A: ["Signal x has no published observations."]})
    with pytest.raises(
        ValueError,
        match="Portfolio preflight is blocked: Signal x has no published observations.",
    ):
        configuration_jobs.launch_portfolio_job(A)
    assert job.calls == []

    _preflight(monkeypatch)
    assert configuration_jobs.launch_portfolio_job(A).job_uid == PORTFOLIO_JOB_UID
    assert job.calls == [["--configuration-uid", A]]


def test_runs_map_back_to_configurations_through_their_arguments(platform) -> None:
    platform.jobs = [FakeJob()]
    platform.runs = [
        _run("a-new", A, status="RUNNING"),
        _run("dispatch"),
        _run("b", B, status="COMPLETED"),
        _run("a-old", A, status="ERROR"),
        _run("bars", A, job_uid="other-job", job_name="Alpaca Bars Update"),
    ]

    runs = configuration_jobs.list_configuration_runs(SimpleNamespace(uid=A), kind="portfolio")
    assert [run.uid for run in runs] == ["a-new", "a-old"]
    assert runs[0].configuration_uid == A
    assert runs[0].command_args == ["--configuration-uid", A]
    assert runs[1].status == "FAILED"
    assert runs[1].failure_message == "The portfolio update failed."
    limited = configuration_jobs.list_configuration_runs(
        SimpleNamespace(uid=A), kind="portfolio", limit=1
    )
    assert [run.uid for run in limited] == ["a-new"]

    platform.scanned = 0
    state = configuration_jobs.configuration_job_state("portfolio", [A, uuid.UUID(B)])
    assert state.job is platform.jobs[0]
    assert {key: run.uid for key, run in state.latest_runs.items()} == {A: "a-new", B: "b"}
    assert state.latest_runs[B].status == "SUCCEEDED"
    assert platform.scanned == 3


def test_configuration_state_before_the_push_has_no_job_and_no_runs(platform) -> None:
    state = configuration_jobs.configuration_job_state("portfolio", [A])
    assert state == ConfigurationJobState(job=None, latest_runs={}, dispatcher=None)
    assert (
        configuration_jobs.list_configuration_runs(SimpleNamespace(uid=A), kind="portfolio") == []
    )


def test_configuration_state_names_the_dispatcher_that_schedules_the_job(platform) -> None:
    job, dispatcher = FakeJob(), _dispatcher()
    platform.jobs = [job, dispatcher]

    state = configuration_jobs.configuration_job_state("portfolio", [A])

    assert (state.job, state.dispatcher) == (job, dispatcher)
    assert configuration_jobs.resolve_dispatcher_job("portfolio") is dispatcher
    assert {"name": PORTFOLIO_DISPATCHER_JOB_NAME, "timeout": 60} in platform.job_queries

    platform.jobs = [job, _dispatcher(execution_path="src/jobs/other.py")]
    with pytest.raises(RuntimeError, match="does not run"):
        configuration_jobs.resolve_dispatcher_job("portfolio")


def test_job_run_lookup_owns_only_runs_of_this_branch_jobs(platform) -> None:
    mine, dispatch, bars, other_branch, missing = (
        f"{index}{index}{index}{index}0000-0000-4000-8000-000000000000" for index in range(1, 6)
    )
    platform.jobs = [FakeJob()]
    platform.runs = [
        _run(mine, A),
        _run(dispatch, status="FAILED"),
        _run(bars, job_uid="bars-job", job_name="Alpaca Bars Update"),
        _run(other_branch, A, job_uid="77777777-7777-4777-8777-777777777777"),
    ]

    run = configuration_jobs.get_configuration_job_run(mine)
    assert (run.configuration_uid, run.job_uid) == (A, PORTFOLIO_JOB_UID)
    assert run.command_args == ["--configuration-uid", A]
    scheduled_run = configuration_jobs.get_configuration_job_run(dispatch)
    assert scheduled_run.configuration_uid is None
    assert scheduled_run.failure_message == "The scheduled portfolio run failed."
    assert configuration_jobs.get_configuration_job_run(bars) is None
    assert configuration_jobs.get_configuration_job_run(other_branch) is None
    assert configuration_jobs.get_configuration_job_run(missing) is None


def test_scheduled_run_starts_one_run_per_scheduled_configuration(platform, monkeypatch) -> None:
    job = FakeJob(failing={C})
    platform.jobs = [job]
    platform.runs = [_run("b-running", B, status="PENDING"), _run("a-done", A)]
    monkeypatch.setattr(
        configuration_jobs,
        "scheduled_portfolio_configurations",
        lambda: [SimpleNamespace(uid=uuid.UUID(value)) for value in (A, B, C)],
    )
    batches = _preflight(monkeypatch)
    monkeypatch.setattr(
        configuration_jobs,
        "get_portfolio_configuration",
        lambda value: pytest.fail("the scheduled rows are read once"),
    )

    summary = configuration_jobs.dispatch_scheduled_configurations("portfolio")

    # One preflight for every configuration without an active run, from the rows already read
    # (issue #46); one JobRun per configuration.
    assert batches == [[A, C]]
    assert job.calls == [["--configuration-uid", A]]
    assert summary["job_uid"] == PORTFOLIO_JOB_UID
    assert summary["scheduled_configurations"] == 3
    assert summary["launched"] == [{"configuration_uid": A, "job_run_uid": "run-1"}]
    assert summary["skipped"] == [{"configuration_uid": B, "reason": "pending or running JobRun"}]
    assert [item["configuration_uid"] for item in summary["failed"]] == [C]


def test_scheduled_portfolio_run_skips_a_blocked_preflight(platform, monkeypatch) -> None:
    job = FakeJob()
    platform.jobs = [job]
    monkeypatch.setattr(
        configuration_jobs,
        "scheduled_portfolio_configurations",
        lambda: [SimpleNamespace(uid=uuid.UUID(value)) for value in (A, B)],
    )
    _preflight(monkeypatch, {B: ["Bars configuration x is disabled."]})

    summary = configuration_jobs.dispatch_scheduled_configurations("portfolio")

    assert job.calls == [["--configuration-uid", A]]
    assert summary["skipped"] == [
        {
            "configuration_uid": B,
            "reason": "Portfolio preflight is blocked: Bars configuration x is disabled.",
        }
    ]
    assert summary["failed"] == []


def test_a_failed_preflight_read_fails_each_configuration_unraised(platform, monkeypatch) -> None:
    job = FakeJob()
    platform.jobs = [job]
    monkeypatch.setattr(
        configuration_jobs,
        "scheduled_portfolio_configurations",
        lambda: [SimpleNamespace(uid=uuid.UUID(value)) for value in (A, B)],
    )

    def unavailable(configurations, *, held=None):
        raise ConnectionError("MetaTables is unavailable")

    monkeypatch.setattr(
        "alpaca_connectors.portfolios.execution.preflight_portfolio_configurations", unavailable
    )

    summary = configuration_jobs.dispatch_scheduled_configurations("portfolio")

    assert job.calls == []
    assert [item["configuration_uid"] for item in summary["failed"]] == [A, B]


def test_scheduled_run_needs_the_declared_job(platform, monkeypatch) -> None:
    monkeypatch.setattr(configuration_jobs, "scheduled_portfolio_configurations", lambda: [])

    with pytest.raises(ValueError, match=WORKFLOW_PATH):
        configuration_jobs.dispatch_scheduled_configurations("portfolio")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--scheduled"],
        ["--configuration-uid", A, "--scheduled"],
        ["--configuration-uid", "not-a-uid"],
        ["--universe-uid", A],
    ],
)
def test_job_entry_points_run_exactly_one_configuration(argv) -> None:
    # The scheduled run belongs to the dispatcher: a Job cannot declare `runner` on itself.
    with pytest.raises(SystemExit):
        configuration_jobs.build_job_argument_parser("portfolio").parse_args(argv)
    parsed = configuration_jobs.build_job_argument_parser("portfolio").parse_args(
        ["--configuration-uid", A.upper()]
    )
    assert parsed.configuration_uid == A


@pytest.mark.parametrize(
    "argv",
    [[], ["--configuration-uid", A], ["--scheduled", "--configuration-uid", A]],
)
def test_dispatcher_entry_points_take_only_the_scheduled_argument(argv) -> None:
    with pytest.raises(SystemExit):
        configuration_jobs.build_dispatcher_argument_parser("portfolio").parse_args(argv)
    assert (
        configuration_jobs.build_dispatcher_argument_parser("portfolio")
        .parse_args(["--scheduled"])
        .scheduled
    )


def test_dispatcher_job_starts_the_scheduled_runs_of_its_kind(monkeypatch) -> None:
    dispatched = []
    monkeypatch.setattr(
        configuration_jobs,
        "dispatch_scheduled_configurations",
        lambda kind: dispatched.append(kind) or {"kind": kind, "failed": []},
    )

    assert configuration_jobs.run_dispatcher_job("portfolio", ["--scheduled"])["kind"] == (
        "portfolio"
    )
    assert dispatched == ["portfolio"]
    with pytest.raises(SystemExit):
        configuration_jobs.run_configuration_job("portfolio", ["--scheduled"])
    assert dispatched == ["portfolio"]


def _launcher(name: str):
    return runpy.run_path(str(JOBS / name))["main"]


def test_portfolio_entry_point_executes_the_selected_configuration(monkeypatch, capsys) -> None:
    main = _launcher("run_alpaca_portfolio.py")
    executed = []
    monkeypatch.delenv("JOB_RUN_UID", raising=False)
    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind", lambda label: False
    )
    monkeypatch.setattr(
        "alpaca_connectors.portfolios.execution.execute_portfolio_configuration",
        lambda uid: executed.append(uid) or SimpleNamespace(summary=lambda: {"asset_count": 2}),
    )

    assert main(["--configuration-uid", A]) == 0
    assert executed == [A]
    assert json.loads(capsys.readouterr().out) == {
        "asset_count": 2,
        "configuration_uid": A,
        "job_run_uid": None,
    }


def test_dispatcher_entry_point_fails_when_a_start_fails(monkeypatch, capsys) -> None:
    main = _launcher("dispatch_alpaca_portfolio.py")
    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind", lambda label: False
    )
    monkeypatch.setattr(
        configuration_jobs,
        "dispatch_scheduled_configurations",
        lambda value: {"kind": value, "launched": [], "skipped": [], "failed": [{"x": 1}]},
    )

    assert main(["--scheduled"]) == 1
    assert json.loads(capsys.readouterr().out)["kind"] == "portfolio"


@pytest.mark.parametrize(
    ("launcher", "argv"),
    [
        ("run_alpaca_portfolio.py", ["--configuration-uid", A]),
        ("dispatch_alpaca_portfolio.py", ["--scheduled"]),
    ],
)
def test_entry_points_check_the_schema_before_any_work(monkeypatch, launcher, argv) -> None:
    main = _launcher(launcher)
    calls = []
    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind",
        lambda label: calls.append(label) or True,
    )
    for work in ("run_configuration_job", "run_dispatcher_job"):
        monkeypatch.setattr(
            configuration_jobs,
            work,
            lambda *args: pytest.fail("ran work while the schema was behind"),
        )

    assert main(argv) == 0
    assert calls and calls[0].startswith("Alpaca portfolio")


def test_portfolio_responses_report_the_declared_job(monkeypatch) -> None:
    from alpaca_connectors.api.app.services import portfolio_configurations as service
    from alpaca_connectors.portfolios import AlpacaPortfolioConfiguration

    row = AlpacaPortfolioConfiguration.model_validate(
        {
            "uid": A,
            "name": "Daily analytical portfolio",
            "description": None,
            "owner_gateway_uid": "owner-uid",
            "owner_user_uid": "owner-uid",
            "bars_configuration_uid": C,
            "rebalance_configuration_uid": C,
            "portfolio_uid": None,
            "scheduled": False,
            "upsample_frequency_id": "1d",
            "intraday_bar_interpolation_rule": "ffill",
            "valuation_column": "close",
            "valuation_maximum_staleness_seconds": 86_400,
            "fail_on_missing_prices": True,
            "commission_fee": 0.00018,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    job = FakeJob()
    dispatcher = _dispatcher(
        task_schedule={"schedule": {"type": "crontab", "expression": "0 2 * * *"}}
    )
    monkeypatch.setattr(
        service,
        "configuration_job_state",
        lambda kind, uids: ConfigurationJobState(job=job, latest_runs={}, dispatcher=dispatcher),
    )
    monkeypatch.setattr(
        service,
        "rebalance_configurations_by_uids",
        lambda uids: {C: SimpleNamespace(strategy="calendar_event_signal")},
    )
    # A portfolio not yet adopted: no definition to read, and its former bars stay named.
    monkeypatch.setattr(
        "alpaca_connectors.portfolios.signal_definitions.signal_definitions_by_uids",
        lambda uids: {},
    )
    monkeypatch.setattr(
        "alpaca_connectors.market_data.configurations.portfolio_bar_configurations",
        lambda uids: {},
    )

    (response,) = service._responses([row])

    assert response.scheduled is False
    assert response.job_uid == PORTFOLIO_JOB_UID
    assert response.job.schedule_expression == "0 2 * * *"
    assert response.latest_run_status is None


PROVIDED_WEIGHTS_BODY = {
    "name": "x",
    "bars_configuration_uid": B,
    "rebalance_configuration_uid": C,
    "observations": [{"observed_at": "2026-11-16T14:00:00+00:00", "weights": {"ALPACA::x": 1}}],
}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({**PROVIDED_WEIGHTS_BODY, "schedule_type": "interval"}, "schedule_type"),
        ({**PROVIDED_WEIGHTS_BODY, "spot": False}, "spot"),
        ({**PROVIDED_WEIGHTS_BODY, "job": {"schedule_type": "interval"}}, "job"),
    ],
)
def test_create_requests_reject_job_settings_naming_the_workflow_file(
    body, field, api_client
) -> None:
    # The only create is a provided-weights portfolio's (ADR 0024, ADR 0027).
    response = api_client.post("/v1/portfolio-configurations/provided-weights", json=body)

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert field in error["msg"]
    assert WORKFLOW_PATH in error["msg"]


@pytest.mark.parametrize(
    "body",
    [{"cpu_request": "1"}, {"enabled": False}, {"job": {"schedule_type": "interval"}}],
)
def test_update_requests_reject_job_settings_naming_the_workflow_file(body, api_client) -> None:
    response = api_client.patch(f"/v1/portfolio-configurations/{A}", json=body)

    assert response.status_code == 422
    assert WORKFLOW_PATH in response.json()["detail"][0]["msg"]


def test_portfolio_patch_sets_only_the_scheduled_flag(api_client, local_caller) -> None:
    services = "alpaca_connectors.api.app.services.portfolio_configurations"
    owned = SimpleNamespace(owner_gateway_uid=local_caller.uid, owner_user_uid=local_caller.uid)
    with (
        # Only the portfolio's owner pauses it (ADR 0024, decision 6).
        patch(f"{services}.get_portfolio_configuration", return_value=owned),
        patch(
            f"{services}.update_portfolio_configuration",
            side_effect=LookupError("stop"),
        ) as update,
    ):
        response = api_client.patch(f"/v1/portfolio-configurations/{A}", json={"scheduled": False})

    assert response.status_code == 404
    update.assert_called_once_with(A, scheduled=False)


def test_job_run_status_returns_the_run_arguments(monkeypatch) -> None:
    from alpaca_connectors.api.app.services import operations

    shaped = configuration_jobs._shape_run(_run("run-uid", A, status="ERROR"), "portfolio")
    monkeypatch.setattr(operations, "get_alpaca_bars_update_job_run", lambda uid: None)
    monkeypatch.setattr(operations, "get_configuration_job_run", lambda uid: shaped)

    response = operations.get_job_run("run-uid")

    assert response.configuration_uid == A
    assert response.command_args == ["--configuration-uid", A]
    assert response.error.code == "job_run_failed"
    assert response.error.message == "The portfolio update failed."


def test_configurations_store_the_scheduled_flag_but_no_job_link() -> None:
    from alpaca_connectors.portfolios.configurations import (
        AlpacaPortfolioConfiguration,
        AlpacaPortfolioConfigurationTable,
    )

    columns = AlpacaPortfolioConfigurationTable.__table__.columns
    scheduled = columns["scheduled"]
    assert scheduled.nullable is False
    assert scheduled.default.arg is True
    assert str(scheduled.server_default.arg) == "true"
    # The retired per-configuration Job link did not move to the one table (ADR 0027).
    assert "job_uid" not in columns
    assert "job_uid" not in AlpacaPortfolioConfiguration.model_fields
    assert "scheduled" in AlpacaPortfolioConfiguration.model_fields

    revision = PROJECT_ROOT.joinpath(
        "src/alpaca_connectors/migrations/versions/alpaca_connectors/"
        "0016_add_portfolio_scheduled_flag.py"
    ).read_text()
    assert 'down_revision: Union[str, Sequence[str], None] = "0015"' in revision
    assert "server_default=sa.true()" in revision


def _job_calls(path: Path) -> list[str]:
    """Calls that create a Job or write its settings: `<...Job>.create(...)`, `<...job>.patch(...)`."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        name = (
            receiver.id
            if isinstance(receiver, ast.Name)
            else receiver.attr
            if isinstance(receiver, ast.Attribute)
            else ""
        )
        creates = node.func.attr == "create" and name.endswith("Job")
        patches = node.func.attr in {"patch", "patch_by_uid"} and name.lower().endswith("job")
        if creates or patches:
            found.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    return found


def test_no_code_creates_jobs_or_writes_their_schedules() -> None:
    sources = [
        path for root in ("src", "api") for path in PROJECT_ROOT.joinpath(root).rglob("*.py")
    ]
    assert sources
    assert [call for path in sources for call in _job_calls(path)] == []
    for path in sources:
        assert "create_schedule" not in path.read_text(encoding="utf-8"), path


def test_workflow_declares_one_job_and_one_dispatcher_per_kind_outside_the_steps() -> None:
    workflow = yaml.safe_load((PROJECT_ROOT / WORKFLOW_PATH).read_text(encoding="utf-8"))
    resources = {resource["key"]: resource for resource in workflow["resources"]}
    referenced = {
        value
        for step in workflow["execution"]["steps"].values()
        for key, value in step.items()
        if key in {"prepare_image", "run_job", "deploy"}
    }
    # Every Job reads MetaTables and reaches Markets through the Markets API (ms-markets ADR 0047).
    metatables = [
        {"repository": "MetaTables", "policy": "reader"},
        {"repository": "MainSequenceMarkets", "policy": "reader"},
    ]
    api_entries = resources["alpaca"]["access"]["resources"]

    for kind, expression in (("portfolio", "0 2 * * *"),):
        spec = JOB_SPECS[kind]
        resource = resources[spec.workflow_key]
        dispatcher = resources[spec.dispatcher_workflow_key]
        for key, declared, name, path in (
            (spec.workflow_key, resource, spec.name, spec.execution_path),
            (
                spec.dispatcher_workflow_key,
                dispatcher,
                spec.dispatcher_name,
                spec.dispatcher_execution_path,
            ),
        ):
            assert declared["kind"] == "job"
            assert key not in referenced
            assert declared["spec"]["name"] == name
            assert declared["spec"]["execution_path"] == path
            assert (PROJECT_ROOT / path).is_file()
            assert declared["spec"]["automatic_redeployment"] == {
                "enabled": True,
                "tag_regex": None,
            }
            assert (
                declared["spec"]["cpu_request"],
                declared["spec"]["memory_request"],
                declared["spec"]["max_runtime_seconds"],
                declared["spec"]["spot"],
            ) == ("0.25", "0.5", 3600, False)
        # The Job runs only when started; a push changes only the fields a declaration states,
        # so the explicit null and empty list remove the schedule earlier pushes set.
        assert resource["spec"]["task_schedule"] is None
        assert resource["spec"]["scheduled_command_args"] == []
        # A portfolio run first fetches its bars with the account's keys (issue #39).
        assert resource["access"] == {
            "branches": metatables,
            "secrets": [{"name": "ALPACA_CONNECTORS_DATA_KEY", "policy": "value_reader"}],
        }
        # The dispatcher holds the schedule and may only run (and see) the kind's Job.
        assert dispatcher["spec"]["task_schedule"] == {
            "schedule": {"type": "crontab", "expression": expression, "timezone": "UTC"}
        }
        assert dispatcher["spec"]["scheduled_command_args"] == ["--scheduled"]
        assert dispatcher["access"] == {
            "branches": metatables,
            "resources": [{"key": spec.workflow_key, "policy": "runner"}],
        }
        # The API starts and lists the kind's runs and reports the dispatcher's schedule.
        assert {"key": spec.workflow_key, "policy": "runner"} in api_entries
        assert {"key": spec.dispatcher_workflow_key, "policy": "viewer"} in api_entries
    # No other configuration Job is declared any more (ADR 0027); the lifecycle check is an
    # on-demand Job, not a configuration kind (ADR 0032).
    assert {key for key in resources if key not in {"alpaca", "alpaca-connectors-agent"}} == {
        "alpaca-connectors-migrate",
        "alpaca-portfolio",
        "alpaca-portfolio-dispatcher",
        "alpaca-lifecycle-check",
    }
