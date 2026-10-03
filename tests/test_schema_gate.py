from __future__ import annotations

import json
import os
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alpaca_connectors.migrations.schema_gate import (
    GATED_PROVIDERS,
    MIGRATION_PROVIDERS,
    ProviderSchemaState,
    SchemaState,
    applied_revisions,
    code_head_revision,
    load_provider,
    schema_state,
    script_directory,
    skip_if_schema_behind,
)

JOBS = Path(__file__).resolve().parents[1] / "src" / "jobs"
MSM = "msm_migrations:migration"
APP = "alpaca_connectors.migrations:migration"


def _heads() -> dict[str, str]:
    return {ref: code_head_revision(script_directory(load_provider(ref))) for ref in (MSM, APP)}


def test_job_migrates_only_this_application_but_the_gate_also_waits_for_ms_markets() -> None:
    # ms-markets 2.1.1: only the ms-markets deployment migrates the ms-markets schema.
    assert MIGRATION_PROVIDERS == (APP,)
    assert GATED_PROVIDERS == (MSM, APP)


def test_each_provider_ships_one_head() -> None:
    heads = _heads()
    assert heads[APP] == "0015"
    assert heads[MSM]


@pytest.mark.parametrize(
    ("applied", "behind"),
    [
        ((), True),  # never migrated
        (("0014",), True),  # this image's revision not applied yet
        (("0015",), False),  # current
        (("0099_from_a_newer_release",), False),  # database ahead: older image still runs
    ],
)
def test_provider_state_compares_database_with_code(applied, behind) -> None:
    state = schema_state([APP], read_applied=lambda migration: applied)

    assert state.providers == (
        ProviderSchemaState(provider=APP, applied=applied, code_head="0015", behind=behind),
    )
    assert state.behind is behind


def test_a_behind_ms_markets_schema_blocks_the_run_even_when_ours_is_current() -> None:
    heads = _heads()

    def read_applied(migration):
        return () if migration.migration_provider_key.startswith("msm") else (heads[APP],)

    state = schema_state(read_applied=read_applied)

    assert [provider.behind for provider in state.providers] == [True, False]
    assert state.behind


def test_behind_schema_skips_and_names_the_provider() -> None:
    messages: list[str] = []
    state = SchemaState(
        providers=(
            ProviderSchemaState(provider=MSM, applied=("x",), code_head="x", behind=False),
            ProviderSchemaState(provider=APP, applied=("0014",), code_head="0015", behind=True),
        )
    )

    assert skip_if_schema_behind("Signal Job", read_state=lambda: state, emit=messages.append)
    assert f"{APP} at 0014, code at 0015" in messages[0]
    assert MSM not in messages[0]


def test_current_schema_runs_silently() -> None:
    messages: list[str] = []
    state = SchemaState(
        providers=(
            ProviderSchemaState(provider=APP, applied=("0015",), code_head="0015", behind=False),
        )
    )

    assert not skip_if_schema_behind("Signal Job", read_state=lambda: state, emit=messages.append)
    assert messages == []


@pytest.mark.parametrize("registrations", [[], [SimpleNamespace(provisioning_status="reserved")]])
def test_unfinalized_version_table_means_no_applied_revision(registrations) -> None:
    import metatables

    with (
        patch.object(metatables.MetaTable, "filter", return_value=registrations),
        patch.object(metatables.MetaTable, "execute_operation") as execute,
    ):
        assert applied_revisions(load_provider(APP)) == ()

    # A reserved table may not physically exist yet, so it is never queried.
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("reference", "table"),
    [(APP, "alpaca_connectors__alembic_version"), (MSM, "ms_markets__alembic_version")],
)
def test_active_version_table_is_read_with_a_governed_select(reference, table) -> None:
    import metatables

    with (
        patch.object(
            metatables.MetaTable,
            "filter",
            return_value=[SimpleNamespace(provisioning_status="active")],
        ) as find,
        patch(
            "metatables.compiled_sql.v1.compile_sqlalchemy_statement",
            return_value="compiled",
        ) as compile_statement,
        patch.object(metatables.MetaTable, "execute_operation", return_value={}),
        patch("msm.api.base.operation_result_rows", return_value=[{"version_num": "rev"}]),
    ):
        assert applied_revisions(load_provider(reference)) == ("rev",)

    assert find.call_args.kwargs["identifier"].endswith(".alembic_version")
    statement = compile_statement.call_args.args[0]
    assert table in str(statement)
    assert compile_statement.call_args.kwargs["operation"] == "select"


def test_migration_job_applies_only_this_application(capsys) -> None:
    main = runpy.run_path(str(JOBS / "migrate_alpaca_connectors.py"))["main"]
    result = {"revision": "0015", "migrated": True, "data_source_uid": "ds-uid"}

    with patch("metatables.upgrade_application", return_value=result) as upgrade:
        assert main() == 0

    assert [call.args[0] for call in upgrade.call_args_list] == [APP]
    assert json.loads(capsys.readouterr().out) == {
        "data_source_uid": "ds-uid",
        "provider": APP,
        "revision": "0015",
        "state": "migrated",
    }


def test_migration_job_failure_fails_the_job() -> None:
    main = runpy.run_path(str(JOBS / "migrate_alpaca_connectors.py"))["main"]

    with patch("metatables.upgrade_application", side_effect=RuntimeError("DDL failed")) as upgrade:
        with pytest.raises(RuntimeError, match="DDL failed"):
            main()

    assert [call.args[0] for call in upgrade.call_args_list] == [APP]


@pytest.mark.parametrize(
    ("launcher", "work"),
    [
        ("run_alpaca_etf_signal.py", "alpaca_connectors.operations.execute_current_signal_job"),
        ("run_alpaca_etf_portfolio.py", "alpaca_connectors.runtime.start_portfolio_job_engine"),
        (
            "run_daily_stock_bars_holdings_ivv.py",
            "alpaca_connectors.market_data.execute_market_data_update",
        ),
    ],
)
def test_launchers_do_no_work_while_the_schema_is_behind(launcher, work) -> None:
    main = runpy.run_path(str(JOBS / launcher))["main"]
    with (
        patch.dict(os.environ, {"ALPACA_BARS_CONFIGURATION_UID": "configuration-uid"}),
        patch(
            "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind",
            return_value=True,
        ),
        patch(work, MagicMock()) as work_call,
    ):
        assert main() == 0

    work_call.assert_not_called()


def test_migration_workflow_runs_the_migration_job_on_every_push() -> None:
    import yaml

    workflow = yaml.safe_load(
        (JOBS.parents[1] / ".mainsequence/workflows/alpaca-connectors-migrations.yaml").read_text()
    )
    (job,) = workflow["resources"]
    assert job["spec"]["execution_path"] == "src/jobs/migrate_alpaca_connectors.py"
    assert (JOBS / "migrate_alpaca_connectors.py").is_file()
    assert job["spec"]["automatic_redeployment"]["enabled"] is True
    assert workflow["execution"]["steps"]["migrate"] == {
        "run_job": job["key"],
        "image_from": "image",
        "needs": ["image"],
    }
