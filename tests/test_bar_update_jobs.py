from __future__ import annotations

import json
import runpy
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from alpaca_connectors.operations import bar_updates
from alpaca_connectors.operations.bar_updates import (
    ALPACA_BARS_UPDATE_DISPATCHER_EXECUTION_PATH,
    ALPACA_BARS_UPDATE_DISPATCHER_JOB_NAME,
    ALPACA_BARS_UPDATE_EXECUTION_PATH,
    ALPACA_BARS_UPDATE_JOB_NAME,
    ALPACA_BARS_WORKFLOW_PATH,
    get_alpaca_bars_update_job_run,
    launch_alpaca_bars_update,
)
from alpaca_connectors.operations.configuration_jobs import RUN_SCAN_LIMIT

CONFIGURATION_UID = "11111111-1111-4111-8111-111111111111"
JOB_UID = "22222222-2222-4222-8222-222222222222"
JOB_RUN_UID = "33333333-3333-4333-8333-333333333333"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _ready_job() -> SimpleNamespace:
    return SimpleNamespace(
        uid=JOB_UID,
        name=ALPACA_BARS_UPDATE_JOB_NAME,
        execution_path=ALPACA_BARS_UPDATE_EXECUTION_PATH,
        image_status="READY",
        run_job=Mock(return_value={"uid": JOB_RUN_UID, "status": "PENDING"}),
    )


def test_launch_preflights_and_enqueues_only_configuration_uid() -> None:
    job = _ready_job()
    with (
        patch("alpaca_connectors.market_data.resolve_market_data_update") as resolve,
        patch("mainsequence.client.Job.filter", return_value=[job]) as job_filter,
    ):
        submission = launch_alpaca_bars_update(CONFIGURATION_UID)

    resolve.assert_called_once_with(configuration_uid=CONFIGURATION_UID)
    job_filter.assert_called_once_with(name=ALPACA_BARS_UPDATE_JOB_NAME, timeout=60)
    job.run_job.assert_called_once_with(
        timeout=60,
        command_args=["--configuration-uid", CONFIGURATION_UID],
    )
    assert submission.configuration_uid == CONFIGURATION_UID
    assert submission.job_uid == JOB_UID
    assert submission.job_run_uid == JOB_RUN_UID
    assert submission.status == "PENDING"


@pytest.mark.parametrize(
    ("jobs", "message"),
    [
        ([], "has not been deployed"),
        ([_ready_job(), _ready_job()], "More than one branch Job"),
    ],
)
def test_launch_requires_exactly_one_branch_job(jobs: list, message: str) -> None:
    with (
        patch("alpaca_connectors.market_data.resolve_market_data_update"),
        patch("mainsequence.client.Job.filter", return_value=jobs),
        pytest.raises((ValueError, RuntimeError), match=message),
    ):
        launch_alpaca_bars_update(CONFIGURATION_UID)


def test_launch_rejects_non_ready_job() -> None:
    job = _ready_job()
    job.image_status = "BUILDING"
    with (
        patch("alpaca_connectors.market_data.resolve_market_data_update"),
        patch("mainsequence.client.Job.filter", return_value=[job]),
        pytest.raises(ValueError, match="image is not ready"),
    ):
        launch_alpaca_bars_update(CONFIGURATION_UID)

    job.run_job.assert_not_called()


def test_launch_rejects_unreviewed_execution_path() -> None:
    job = _ready_job()
    job.execution_path = "src/jobs/something_else.py"
    with (
        patch("alpaca_connectors.market_data.resolve_market_data_update"),
        patch("mainsequence.client.Job.filter", return_value=[job]),
        pytest.raises(RuntimeError, match="reviewed launcher"),
    ):
        launch_alpaca_bars_update(CONFIGURATION_UID)

    job.run_job.assert_not_called()


def test_job_run_projection_is_pollable_and_sanitized() -> None:
    now = datetime.now(UTC)
    run = SimpleNamespace(
        uid=JOB_RUN_UID,
        job_uid=JOB_UID,
        job_name=ALPACA_BARS_UPDATE_JOB_NAME,
        command_args=["--configuration-uid", CONFIGURATION_UID],
        status="ERRORED",
        execution_start=now,
        execution_end=now,
        commit_hash="abc123",
        runtime_image_uid="44444444-4444-4444-8444-444444444444",
        runtime_image_digest="sha256:123",
        observability=SimpleNamespace(application_logs_url="https://logs.example/run"),
        response_error={"message": "private provider exception"},
    )
    with patch("mainsequence.client.JobRun.filter", return_value=[run]) as job_run_filter:
        result = get_alpaca_bars_update_job_run(JOB_RUN_UID)

    job_run_filter.assert_called_once_with(uid=JOB_RUN_UID, timeout=60)
    assert result is not None
    assert result.configuration_uid == CONFIGURATION_UID
    assert result.status == "FAILED"
    assert result.logs_url == "https://logs.example/run"
    assert result.failure_message == "The Alpaca bars update failed."
    assert "private provider exception" not in repr(result)


def test_job_run_projection_hides_unrelated_jobs() -> None:
    run = SimpleNamespace(job_name="Some Other Job")
    with patch("mainsequence.client.JobRun.filter", return_value=[run]):
        assert get_alpaca_bars_update_job_run(JOB_RUN_UID) is None


# ADR 0021: the dispatcher's scheduled run starts one bars Job run per enabled configuration.


class FakeBarsJob:
    def __init__(self, *, image_status: str = "READY", failing: set[str] | None = None) -> None:
        self.uid = JOB_UID
        self.name = ALPACA_BARS_UPDATE_JOB_NAME
        self.execution_path = ALPACA_BARS_UPDATE_EXECUTION_PATH
        self.image_status = image_status
        self.failing = failing or set()
        self.calls: list[list[str]] = []

    def run_job(self, *, timeout=None, command_args=None):
        if command_args[1] in self.failing:
            raise RuntimeError("Job 'x' did not start.")
        self.calls.append(list(command_args))
        return {"job_run_uid": f"run-{len(self.calls)}", "status": "PENDING"}


def _bars_run(configuration_uid: str | None, *, status: str = "SUCCEEDED") -> SimpleNamespace:
    command_args = ["--configuration-uid", configuration_uid] if configuration_uid else []
    return SimpleNamespace(job_uid=JOB_UID, status=status, command_args=command_args)


@pytest.fixture
def bars_platform(monkeypatch):
    """Fake `Job` and `JobRun` clients and the enabled configurations, newest runs first."""
    state = SimpleNamespace(jobs=[], runs=[], enabled=[], reads=0)

    class Job:
        @classmethod
        def filter(cls, *, name, timeout):
            return [job for job in state.jobs if job.name == name]

    class JobRun:
        @classmethod
        def iter_filter(cls, *, job__uid, max_items, timeout):
            assert max_items == RUN_SCAN_LIMIT
            yield from (run for run in state.runs if run.job_uid == job__uid)

    def enabled_configurations():
        state.reads += 1
        return [uuid.UUID(value) for value in state.enabled]

    monkeypatch.setattr("mainsequence.client.Job", Job)
    monkeypatch.setattr("mainsequence.client.JobRun", JobRun)
    monkeypatch.setattr(
        "alpaca_connectors.market_data.scheduled_bar_configuration_uids", enabled_configurations
    )
    return state


def test_scheduled_run_starts_one_bars_run_per_enabled_configuration(bars_platform) -> None:
    job = FakeBarsJob(failing={C})
    bars_platform.jobs = [job]
    bars_platform.enabled = [A, B, C]
    bars_platform.runs = [_bars_run(B, status="QUEUED"), _bars_run(None), _bars_run(A)]

    summary = bar_updates.dispatch_scheduled_bar_updates()

    # No preflight: every enabled configuration without an active run is started.
    assert job.calls == [["--configuration-uid", A]]
    assert summary["kind"] == "bars"
    assert summary["job_uid"] == JOB_UID
    assert summary["scheduled_configurations"] == 3
    assert summary["launched"] == [{"configuration_uid": A, "job_run_uid": "run-1"}]
    assert summary["skipped"] == [{"configuration_uid": B, "reason": "pending or running JobRun"}]
    assert [item["configuration_uid"] for item in summary["failed"]] == [C]


@pytest.mark.parametrize(
    ("jobs", "message"),
    [([], ALPACA_BARS_WORKFLOW_PATH), ([FakeBarsJob(image_status="BUILDING")], "not ready")],
)
def test_scheduled_run_needs_the_declared_bars_job_before_reading_configurations(
    bars_platform, jobs, message
) -> None:
    bars_platform.jobs = jobs
    bars_platform.enabled = [A]

    with pytest.raises(ValueError, match=message):
        bar_updates.dispatch_scheduled_bar_updates()

    assert bars_platform.reads == 0
    assert all(job.calls == [] for job in jobs)


def test_enabled_and_scheduled_bar_configurations_are_dispatched() -> None:
    from alpaca_connectors.market_data.configurations import (
        AlpacaBarsConfigurationTable,
        scheduled_bar_configuration_uids,
    )

    context = object()
    with (
        patch("alpaca_connectors.runtime.account_runtime_models", return_value=[]),
        patch("alpaca_connectors.runtime.start_markets_engine") as start,
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context=context)),
        patch(
            "msm.repositories.base.compile_markets_statement", return_value="select-enabled"
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": [{"uid": B}, {"uid": A}]},
        ) as execute_operation,
    ):
        assert scheduled_bar_configuration_uids() == [uuid.UUID(B), uuid.UUID(A)]

    start.assert_called_once()
    execute_operation.assert_called_once_with("select-enabled", context=context)
    statement = compile_statement.call_args.args[0]
    table = AlpacaBarsConfigurationTable.__table__.name
    sql = " ".join(str(statement.compile(compile_kwargs={"literal_binds": True})).split())
    # Both flags decide: an unscheduled configuration still runs on demand (revision 0024).
    assert sql == (
        f"SELECT {table}.uid FROM {table} WHERE {table}.enabled IS true "
        f"AND {table}.scheduled IS true "
        f"ORDER BY {table}.name ASC, {table}.uid ASC"
    )


@pytest.mark.parametrize(
    "argv",
    [[], ["--configuration-uid", A], ["--scheduled", "--configuration-uid", A]],
)
def test_bars_dispatcher_takes_only_the_scheduled_argument(argv) -> None:
    with pytest.raises(SystemExit):
        bar_updates.build_bars_dispatcher_argument_parser().parse_args(argv)
    assert bar_updates.build_bars_dispatcher_argument_parser().parse_args(["--scheduled"]).scheduled


def test_bars_dispatcher_job_runs_the_scheduled_dispatch(monkeypatch) -> None:
    dispatched = []
    monkeypatch.setattr(
        bar_updates,
        "dispatch_scheduled_bar_updates",
        lambda: dispatched.append("bars") or {"kind": "bars", "failed": []},
    )

    assert bar_updates.run_bars_dispatcher_job(["--scheduled"])["kind"] == "bars"
    assert dispatched == ["bars"]
    with pytest.raises(SystemExit):
        bar_updates.run_bars_dispatcher_job([])
    assert dispatched == ["bars"]


def _dispatcher_main():
    return runpy.run_path(str(PROJECT_ROOT / ALPACA_BARS_UPDATE_DISPATCHER_EXECUTION_PATH))["main"]


def test_bars_dispatcher_entry_point_checks_the_schema_before_any_work(monkeypatch) -> None:
    labels = []
    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind",
        lambda label: labels.append(label) or True,
    )
    monkeypatch.setattr(
        bar_updates,
        "run_bars_dispatcher_job",
        lambda argv: pytest.fail("dispatched while the schema was behind"),
    )

    assert _dispatcher_main()(["--scheduled"]) == 0
    assert labels == ["Alpaca bars update dispatcher Job"]


@pytest.mark.parametrize(("failed", "exit_code"), [([], 0), ([{"configuration_uid": C}], 1)])
def test_bars_dispatcher_entry_point_prints_the_summary_and_fails_on_a_failed_start(
    monkeypatch, capsys, failed, exit_code
) -> None:
    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind", lambda label: False
    )
    monkeypatch.setattr(
        bar_updates,
        "run_bars_dispatcher_job",
        lambda argv: {"kind": "bars", "argv": list(argv), "launched": [], "failed": failed},
    )

    assert _dispatcher_main()(["--scheduled"]) == exit_code
    printed = json.loads(capsys.readouterr().out)
    assert (printed["kind"], printed["argv"]) == ("bars", ["--scheduled"])


def _crontab_time(resource: dict) -> tuple[int, int]:
    schedule = resource["spec"]["task_schedule"]["schedule"]
    assert (schedule["type"], schedule["timezone"]) == ("crontab", "UTC")
    minute, hour, *days = schedule["expression"].split()
    assert days == ["*", "*", "*"]
    return int(hour), int(minute)


def test_workflow_declares_the_bars_dispatcher_beside_the_bars_job() -> None:
    workflow = yaml.safe_load((PROJECT_ROOT / ALPACA_BARS_WORKFLOW_PATH).read_text())
    resources = {resource["key"]: resource for resource in workflow["resources"]}
    job = resources["alpaca-bars-update"]
    dispatcher = resources["alpaca-bars-update-dispatcher"]

    # `access.resources` names only this file's resources, so the dispatcher lives beside the Job.
    assert set(resources) == {"alpaca-bars-update", "alpaca-bars-update-dispatcher"}
    assert "execution" not in workflow
    assert dispatcher["kind"] == "job"
    assert dispatcher["spec"]["name"] == ALPACA_BARS_UPDATE_DISPATCHER_JOB_NAME
    assert dispatcher["spec"]["execution_path"] == ALPACA_BARS_UPDATE_DISPATCHER_EXECUTION_PATH
    assert (PROJECT_ROOT / ALPACA_BARS_UPDATE_DISPATCHER_EXECUTION_PATH).is_file()
    for key in ("cpu_request", "memory_request", "max_runtime_seconds", "spot"):
        assert dispatcher["spec"][key] == job["spec"][key]
    assert dispatcher["spec"]["automatic_redeployment"] == {"enabled": True, "tag_regex": None}
    assert dispatcher["spec"]["scheduled_command_args"] == ["--scheduled"]
    # It may run only the bars Job, and it opens no account keys.
    assert dispatcher["access"] == {
        "branches": [
            {"repository": "MetaTables", "policy": "reader"},
            {"repository": "MainSequenceMarkets", "policy": "reader"},
        ],
        "resources": [{"key": "alpaca-bars-update", "policy": "runner"}],
    }
    # The bars Job keeps manual runs only.
    assert job["spec"].get("task_schedule") is None
    assert not job["spec"].get("scheduled_command_args")


def test_bars_dispatch_runs_after_utc_midnight_and_before_the_signal_and_portfolio() -> None:
    bars = yaml.safe_load((PROJECT_ROOT / ALPACA_BARS_WORKFLOW_PATH).read_text())
    api = yaml.safe_load(
        (PROJECT_ROOT / ".mainsequence/workflows/alpaca-connectors-api.yaml").read_text()
    )
    by_key = {resource["key"]: resource for resource in [*bars["resources"], *api["resources"]]}

    # The daily updater takes only bars that start before the current UTC midnight, so 00:00 UTC
    # is the first run that includes the session closed at 20:00 or 21:00 UTC.
    assert _crontab_time(by_key["alpaca-bars-update-dispatcher"]) == (0, 0)
    assert (
        _crontab_time(by_key["alpaca-bars-update-dispatcher"])
        < _crontab_time(by_key["alpaca-etf-signal-dispatcher"])
        < _crontab_time(by_key["alpaca-etf-portfolio-dispatcher"])
    )
