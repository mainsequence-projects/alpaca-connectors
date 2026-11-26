"""Workflow-declared Jobs for portfolio configurations (ADR 0013).

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares one Job per configuration kind and
one dispatcher Job per kind; portfolios are the one kind left (ADR 0027). A run of the kind's Job carries ``--configuration-uid <uid>`` and
executes that configuration; the API and the CLI start such runs. The dispatcher holds the daily
schedule: its scheduled run carries ``--scheduled`` and starts one run of the kind's Job for the
whole tick, carrying ``--configuration-uid`` once per scheduled configuration, which runs them all
in one ms-markets ``run_portfolios`` call (ADR 0013, as amended on 2026-11-26). The scheduled run
cannot live on the kind's Job, because starting runs of a Job needs ``runner`` on it and a Job
cannot declare access to itself; the dispatcher declares ``runner`` on the kind's Job. The backend
cannot filter JobRuns by their arguments, so runs map back to every configuration they carry
through ``JobRun.command_args`` over a bounded scan of the Job's newest runs. No code
here creates a Job or changes its schedule: since mainsequence 9.0.2 only a workflow declaration
creates a Job, and a push re-applies its schedule.
"""

from __future__ import annotations

import argparse
import os
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from alpaca_connectors.portfolios.configurations import (
    get_portfolio_configuration,
    scheduled_portfolio_configurations,
)

ConfigurationKind = Literal["portfolio"]

WORKFLOW_PATH = ".mainsequence/workflows/alpaca-connectors-api.yaml"
PORTFOLIO_JOB_NAME = "Alpaca Portfolio"
ALPACA_PORTFOLIO_EXECUTION_PATH = "src/jobs/run_alpaca_portfolio.py"
PORTFOLIO_DISPATCHER_JOB_NAME = "Alpaca Portfolio Dispatcher"
ALPACA_PORTFOLIO_DISPATCHER_EXECUTION_PATH = "src/jobs/dispatch_alpaca_portfolio.py"
CONFIGURATION_UID_ARGUMENT = "--configuration-uid"
SCHEDULED_ARGUMENT = "--scheduled"
RUNNING_JOB_STATUSES = frozenset({"PENDING", "QUEUED", "RUNNING", "STARTED"})
# Newest runs read when mapping runs to configurations; the backend lists newest first.
RUN_SCAN_LIMIT = 500


@dataclass(frozen=True, slots=True)
class ConfigurationJobSpec:
    """The kind's Job, which runs one configuration, and the dispatcher that schedules it."""

    kind: ConfigurationKind
    workflow_key: str
    name: str
    execution_path: str
    dispatcher_workflow_key: str
    dispatcher_name: str
    dispatcher_execution_path: str


JOB_SPECS: dict[str, ConfigurationJobSpec] = {
    "portfolio": ConfigurationJobSpec(
        "portfolio",
        "alpaca-portfolio",
        PORTFOLIO_JOB_NAME,
        ALPACA_PORTFOLIO_EXECUTION_PATH,
        "alpaca-portfolio-dispatcher",
        PORTFOLIO_DISPATCHER_JOB_NAME,
        ALPACA_PORTFOLIO_DISPATCHER_EXECUTION_PATH,
    ),
}


@dataclass(frozen=True, slots=True)
class ConfigurationJobSubmission:
    configuration_uid: str
    job_uid: str
    job_run_uid: str
    status: str


@dataclass(frozen=True, slots=True)
class ConfigurationJobRun:
    uid: str
    job_uid: str
    job_name: str
    # The configuration the run is read for; a tick's run read by its UID alone carries several
    # (``configuration_uids``) and names none here.
    configuration_uid: str | None
    status: str
    execution_start: Any | None
    execution_end: Any | None
    commit_hash: str | None
    runtime_image_uid: str | None
    runtime_image_digest: str | None
    command_args: list[str]
    logs_url: str | None
    failure_message: str | None
    configuration_uids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfigurationJobState:
    """The kind's declared Job and dispatcher (None before a push creates them) and the latest
    run per configuration."""

    job: Any | None
    latest_runs: dict[str, ConfigurationJobRun]
    dispatcher: Any | None = None


def _canonical_uid(value: Any, *, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid UUID.") from exc


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    status = str(raw or "QUEUED").strip().upper()
    return {
        "COMPLETE": "SUCCEEDED",
        "COMPLETED": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "SUCCESSFUL": "SUCCEEDED",
        "ERROR": "FAILED",
        "ERRORED": "FAILED",
    }.get(status, status)


def _image_ready(job: Any) -> bool:
    value = getattr(job.image_status, "value", job.image_status)
    return str(value or "").strip().lower() == "ready"


def _resolve_declared_job(name: str, execution_path: str) -> Any | None:
    from alpaca_connectors.operations.platform_jobs import PlatformJob

    jobs = list(PlatformJob.filter(name=name, timeout=60))
    if not jobs:
        return None
    if len(jobs) > 1:
        raise RuntimeError(f"More than one branch Job is named {name!r}.")
    job = jobs[0]
    if job.execution_path != execution_path:
        raise RuntimeError(f"The {name!r} Job does not run {execution_path!r}.")
    return job


def resolve_configuration_job(kind: ConfigurationKind) -> Any | None:
    """Return this branch's declared Job for ``kind``, or None until a push creates it."""
    spec = JOB_SPECS[kind]
    return _resolve_declared_job(spec.name, spec.execution_path)


def resolve_dispatcher_job(kind: ConfigurationKind) -> Any | None:
    """Return the dispatcher that holds the schedule of ``kind``, or None until a push creates it."""
    spec = JOB_SPECS[kind]
    return _resolve_declared_job(spec.dispatcher_name, spec.dispatcher_execution_path)


def require_configuration_job(kind: ConfigurationKind) -> Any:
    """Return the declared Job for ``kind`` once it exists and its image is ready."""
    spec = JOB_SPECS[kind]
    job = resolve_configuration_job(kind)
    if job is None:
        raise ValueError(
            f"The {spec.name!r} Job does not exist on this branch yet; a push of "
            f"{WORKFLOW_PATH} creates it."
        )
    if not _image_ready(job):
        raise ValueError(f"The {spec.name!r} Job image is not ready.")
    return job


def configuration_command_args(*configuration_uids: Any) -> list[str]:
    """Return the per-run arguments that select these configurations, one flag each."""
    if not configuration_uids:
        raise ValueError("A run selects at least one configuration.")
    arguments: list[str] = []
    for configuration_uid in configuration_uids:
        arguments += [
            CONFIGURATION_UID_ARGUMENT,
            _canonical_uid(configuration_uid, label="Configuration UID"),
        ]
    return arguments


def configuration_uids_from_command_args(command_args: Sequence[Any] | None) -> tuple[str, ...]:
    """Return every configuration a JobRun's arguments select, in order."""
    values = [str(value) for value in (command_args or [])]
    selected: list[str] = []
    for position, value in enumerate(values[:-1]):
        if value != CONFIGURATION_UID_ARGUMENT:
            continue
        try:
            selected.append(str(uuid.UUID(values[position + 1])))
        except ValueError:
            continue
    return tuple(dict.fromkeys(selected))


def configuration_uid_from_command_args(command_args: Sequence[Any] | None) -> str | None:
    """Return the one configuration a JobRun's arguments select, or None: a tick's run selects
    several."""
    selected = configuration_uids_from_command_args(command_args)
    return selected[0] if len(selected) == 1 else None


def _shape_run(
    run: Any, kind: ConfigurationKind, *, for_configuration: str | None = None
) -> ConfigurationJobRun:
    command_args = [str(value) for value in (run.command_args or [])]
    configuration_uids = configuration_uids_from_command_args(command_args)
    configuration_uid = for_configuration or (
        configuration_uids[0] if len(configuration_uids) == 1 else None
    )
    observability = run.observability
    status = _status(run.status)
    failure_message = None
    if status == "FAILED":
        if len(configuration_uids) > 1:
            # The tick's run fails when any of its portfolios does; its log names which.
            failure_message = f"The scheduled {kind} run failed for at least one {kind}."
        elif configuration_uids:
            failure_message = f"The {kind} update failed."
        else:
            failure_message = f"The scheduled {kind} run failed."
    return ConfigurationJobRun(
        uid=str(run.uid),
        job_uid=str(run.job_uid),
        job_name=str(run.job_name or ""),
        configuration_uid=configuration_uid,
        status=status,
        execution_start=run.execution_start,
        execution_end=run.execution_end,
        commit_hash=run.commit_hash,
        runtime_image_uid=str(run.runtime_image_uid) if run.runtime_image_uid else None,
        runtime_image_digest=run.runtime_image_digest,
        command_args=command_args,
        logs_url=observability.application_logs_url if observability else None,
        failure_message=failure_message,
        configuration_uids=configuration_uids,
    )


def _recent_job_runs(job_uid: Any) -> Iterator[Any]:
    from mainsequence.client import JobRun

    return JobRun.iter_filter(job__uid=str(job_uid), max_items=RUN_SCAN_LIMIT, timeout=60)


def _latest_runs(
    job: Any, wanted: set[str], kind: ConfigurationKind
) -> dict[str, ConfigurationJobRun]:
    latest: dict[str, ConfigurationJobRun] = {}
    if not wanted:
        return latest
    for run in _recent_job_runs(job.uid):
        for configuration_uid in configuration_uids_from_command_args(run.command_args):
            if configuration_uid in wanted and configuration_uid not in latest:
                latest[configuration_uid] = _shape_run(
                    run, kind, for_configuration=configuration_uid
                )
        if len(latest) == len(wanted):
            break
    return latest


def configuration_job_state(
    kind: ConfigurationKind, configuration_uids: Iterable[Any]
) -> ConfigurationJobState:
    """Resolve the kind's Job, its dispatcher and the newest run of each listed configuration."""
    wanted = {_canonical_uid(value, label="Configuration UID") for value in configuration_uids}
    job = resolve_configuration_job(kind)
    latest = _latest_runs(job, wanted, kind) if job is not None else {}
    return ConfigurationJobState(
        job=job, latest_runs=latest, dispatcher=resolve_dispatcher_job(kind)
    )


def list_configuration_runs(
    configuration: Any, *, kind: ConfigurationKind, limit: int = 25
) -> list[ConfigurationJobRun]:
    """Return the configuration's newest runs, newest first."""
    job = resolve_configuration_job(kind)
    if job is None:
        return []
    target = _canonical_uid(configuration.uid, label="Configuration UID")
    result: list[ConfigurationJobRun] = []
    for run in _recent_job_runs(job.uid):
        if target in configuration_uids_from_command_args(run.command_args):
            result.append(_shape_run(run, kind, for_configuration=target))
            if len(result) >= limit:
                break
    return result


def list_portfolio_job_runs(
    configuration_uid: Any, *, limit: int = 25
) -> list[ConfigurationJobRun]:
    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    return list_configuration_runs(configuration, kind="portfolio", limit=limit)


def get_configuration_job_run(job_run_uid: Any) -> ConfigurationJobRun | None:
    """Return a run of this branch's portfolio Job, or None for any other run."""
    from mainsequence.client import JobRun

    rows = list(JobRun.filter(uid=_canonical_uid(job_run_uid, label="JobRun UID"), timeout=60))
    if not rows:
        return None
    if len(rows) > 1:
        raise RuntimeError(f"JobRun {job_run_uid!s} resolved more than once.")
    run = rows[0]
    spec = next((value for value in JOB_SPECS.values() if value.name == run.job_name), None)
    if spec is None or not run.job_uid:
        return None
    job = resolve_configuration_job(spec.kind)
    if job is None or str(job.uid) != str(run.job_uid):
        return None
    return _shape_run(run, spec.kind)


def _start_job_run(job: Any, configuration_uids: Sequence[str]) -> tuple[str, str]:
    """Start one run of ``job`` for these configurations; answer its UID and status."""
    payload = job.run_job(timeout=60, command_args=configuration_command_args(*configuration_uids))
    run_uid = payload.get("uid") or payload.get("job_run_uid")
    if not run_uid:
        raise RuntimeError("Main Sequence accepted the Job without returning a JobRun UID.")
    return str(run_uid), _status(payload.get("status"))


def _start_run(job: Any, configuration_uid: str) -> ConfigurationJobSubmission:
    run_uid, status = _start_job_run(job, [configuration_uid])
    return ConfigurationJobSubmission(configuration_uid, str(job.uid), run_uid, status)


def launch_configuration_job(
    configuration: Any, *, kind: ConfigurationKind
) -> ConfigurationJobSubmission:
    """Start one run of the kind's Job for ``configuration``."""
    job = require_configuration_job(kind)
    configuration_uid = _canonical_uid(configuration.uid, label="Configuration UID")
    latest = _latest_runs(job, {configuration_uid}, kind).get(configuration_uid)
    if latest is not None and latest.status in RUNNING_JOB_STATUSES:
        raise ValueError(f"{kind.title()} configuration already has a pending or running JobRun.")
    return _start_run(job, configuration_uid)


def _blocked(preflight: Any) -> ValueError:
    return ValueError("Portfolio preflight is blocked: " + "; ".join(preflight.blockers))


def require_portfolio_preflight(
    configuration_uid: Any,
    *,
    configuration: Any = None,
    held: Any = None,
) -> None:
    """Raise ValueError naming the blockers when the portfolio cannot run.

    A caller that holds the configuration, or inputs it just wrote (``HeldPortfolioInputs``),
    passes them and they are not read again (issue #46).
    """
    from alpaca_connectors.portfolios.execution import preflight_portfolio_configuration

    preflight = preflight_portfolio_configuration(
        configuration_uid, configuration=configuration, held=held
    )
    if not preflight.allowed:
        raise _blocked(preflight)


def launch_portfolio_job(
    configuration_uid: Any,
    *,
    configuration: Any = None,
    held: Any = None,
) -> ConfigurationJobSubmission:
    """Start one portfolio run once its preflight allows it.

    The configuration is read once, unless the caller holds it (a create that starts its first
    run passes it and what it wrote, ``held``).
    """
    if configuration is None:
        configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    require_portfolio_preflight(configuration.uid, configuration=configuration, held=held)
    return launch_configuration_job(configuration, kind="portfolio")


def _active_configuration_uids(job: Any) -> set[str]:
    active: set[str] = set()
    for run in _recent_job_runs(job.uid):
        if _status(run.status) in RUNNING_JOB_STATUSES:
            active.update(configuration_uids_from_command_args(run.command_args))
    return active


def dispatch_scheduled_configurations(kind: ConfigurationKind) -> dict[str, Any]:
    """Start one run of the kind's Job for the whole tick: all the dispatcher does.

    The dispatcher's scheduled run calls this; it holds ``runner`` on the kind's Job. A
    configuration with a pending or running run is skipped, and so is a portfolio whose
    preflight is blocked. The preflights of all the others read their inputs together, one read
    per kind, and every configuration they allow goes in one JobRun, which runs them in one
    ``run_portfolios`` call (ADR 0013, as amended): one start and one drain for the tick, and no
    run waits for another's update. When that start fails, every one of them is failed.
    """
    from alpaca_connectors.portfolios.execution import preflight_portfolio_configurations

    job = require_configuration_job(kind)
    configurations = scheduled_portfolio_configurations()
    active = _active_configuration_uids(job)
    launched: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    candidates = [
        configuration
        for configuration in configurations
        if _canonical_uid(configuration.uid, label="Configuration UID") not in active
    ]
    # Every candidate's preflight inputs at once, one read per kind (issue #46); the scheduled
    # rows above are not read again. A failed read fails each candidate, as before, unraised.
    preflights: dict[str, Any] = {}
    if candidates:
        try:
            preflights = preflight_portfolio_configurations(candidates)
        except Exception as exc:
            preflights = {str(configuration.uid): exc for configuration in candidates}
    allowed: list[str] = []
    for configuration in configurations:
        configuration_uid = _canonical_uid(configuration.uid, label="Configuration UID")
        if configuration_uid in active:
            skipped.append(
                {"configuration_uid": configuration_uid, "reason": "pending or running JobRun"}
            )
            continue
        # One configuration's failure must not stop the others.
        preflight = preflights[str(configuration.uid)]
        if isinstance(preflight, Exception):
            failed.append({"configuration_uid": configuration_uid, "error": repr(preflight)})
            continue
        if not preflight.allowed:
            skipped.append(
                {"configuration_uid": configuration_uid, "reason": str(_blocked(preflight))}
            )
            continue
        allowed.append(configuration_uid)
    if allowed:
        try:
            run_uid, _ = _start_job_run(job, allowed)
        except Exception as exc:
            failed += [{"configuration_uid": uid, "error": repr(exc)} for uid in allowed]
        else:
            launched += [{"configuration_uid": uid, "job_run_uid": run_uid} for uid in allowed]
    return {
        "kind": kind,
        "job_uid": str(job.uid),
        "scheduled_configurations": len(configurations),
        "launched": launched,
        "skipped": skipped,
        "failed": failed,
    }


def _configuration_uid_argument(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("configuration UID must be a valid UUID") from exc


def build_job_argument_parser(kind: ConfigurationKind) -> argparse.ArgumentParser:
    """Arguments of the kind's Job: the configurations to execute, one flag each."""
    spec = JOB_SPECS[kind]
    parser = argparse.ArgumentParser(
        prog=spec.execution_path,
        description=f"Run {kind} configurations as the {spec.name!r} Job.",
    )
    parser.add_argument(
        CONFIGURATION_UID_ARGUMENT,
        dest="configuration_uids",
        action="append",
        type=_configuration_uid_argument,
        required=True,
        help=f"Execute this {kind} configuration; repeat it to run several in one run.",
    )
    return parser


def build_dispatcher_argument_parser(kind: ConfigurationKind) -> argparse.ArgumentParser:
    """Arguments of the kind's dispatcher: its scheduled run's ``--scheduled`` and nothing else."""
    spec = JOB_SPECS[kind]
    parser = argparse.ArgumentParser(
        prog=spec.dispatcher_execution_path,
        description=f"Start one {spec.name!r} run for every scheduled {kind} configuration.",
    )
    parser.add_argument(
        SCHEDULED_ARGUMENT,
        action="store_true",
        required=True,
        help=f"Start one run for every scheduled {kind} configuration (the scheduled run).",
    )
    return parser


def run_dispatcher_job(
    kind: ConfigurationKind, argv: Sequence[str] | None = None
) -> dict[str, Any]:
    """Entry point of the kind's dispatcher Job: the scheduled dispatch."""
    build_dispatcher_argument_parser(kind).parse_args(argv)
    return dispatch_scheduled_configurations(kind)


def run_configuration_job(
    kind: ConfigurationKind, argv: Sequence[str] | None = None
) -> dict[str, Any]:
    """Entry point of the kind's Job: execute the configurations ``--configuration-uid`` selects.

    One configuration runs on its own (``run_portfolio``), as the API and the CLI start it; the
    dispatcher's tick carries several, which run together (``run_portfolios``). The summary's
    ``failed`` lists each one that did not run.
    """
    from alpaca_connectors.portfolios.execution import (
        execute_portfolio_configuration,
        execute_portfolio_configurations,
    )

    args = build_job_argument_parser(kind).parse_args(argv)
    uids = list(dict.fromkeys(args.configuration_uids))
    if len(uids) == 1:
        summary = execute_portfolio_configuration(uids[0]).summary()
        return {**summary, "configuration_uid": uids[0], "job_run_uid": os.getenv("JOB_RUN_UID")}
    return {
        **execute_portfolio_configurations(uids).summary(),
        "job_run_uid": os.getenv("JOB_RUN_UID"),
    }


__all__ = [
    "ALPACA_PORTFOLIO_DISPATCHER_EXECUTION_PATH",
    "ALPACA_PORTFOLIO_EXECUTION_PATH",
    "CONFIGURATION_UID_ARGUMENT",
    "ConfigurationJobRun",
    "ConfigurationJobSpec",
    "ConfigurationJobState",
    "ConfigurationJobSubmission",
    "JOB_SPECS",
    "PORTFOLIO_DISPATCHER_JOB_NAME",
    "PORTFOLIO_JOB_NAME",
    "RUN_SCAN_LIMIT",
    "SCHEDULED_ARGUMENT",
    "WORKFLOW_PATH",
    "build_dispatcher_argument_parser",
    "build_job_argument_parser",
    "configuration_command_args",
    "configuration_job_state",
    "configuration_uid_from_command_args",
    "configuration_uids_from_command_args",
    "dispatch_scheduled_configurations",
    "get_configuration_job_run",
    "launch_configuration_job",
    "launch_portfolio_job",
    "list_configuration_runs",
    "list_portfolio_job_runs",
    "require_configuration_job",
    "require_portfolio_preflight",
    "resolve_configuration_job",
    "resolve_dispatcher_job",
    "run_configuration_job",
    "run_dispatcher_job",
]
