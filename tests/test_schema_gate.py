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
    assert heads[APP] == "0017"
    assert heads[MSM]


@pytest.mark.parametrize(
    ("applied", "behind"),
    [
        ((), True),  # never migrated
        (("0016",), True),  # this image's revision not applied yet
        (("0017",), False),  # current
        (("0099_from_a_newer_release",), False),  # database ahead: older image still runs
    ],
)
def test_provider_state_compares_database_with_code(applied, behind) -> None:
    state = schema_state([APP], read_applied=lambda migration: applied)

    assert state.providers == (
        ProviderSchemaState(provider=APP, applied=applied, code_head="0017", behind=behind),
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
        (
            "run_alpaca_etf_signal.py",
            "alpaca_connectors.operations.configuration_jobs.run_configuration_job",
        ),
        (
            "run_alpaca_etf_portfolio.py",
            "alpaca_connectors.operations.configuration_jobs.run_configuration_job",
        ),
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


def test_api_deploys_only_after_the_migration_job_in_one_workflow() -> None:
    import yaml

    root = JOBS.parents[1]
    workflow = yaml.safe_load(
        (root / ".mainsequence/workflows/alpaca-connectors-api.yaml").read_text()
    )
    resources = {resource["key"]: resource for resource in workflow["resources"]}
    api = resources["alpaca"]
    job = resources["alpaca-connectors-migrate"]

    # The API's key is the Agent's MCP tool prefix (ADR 0015); 2.3.0 kinds only.
    assert workflow["name"] == "alpaca-connectors-api"
    assert workflow["api_version"] == "2.3.0"
    assert api["kind"] == "fastapi"
    assert api["spec"]["mcp_enabled"] is True
    assert (root / api["spec"]["source_path"]).is_file()
    assert job["kind"] == "job"
    assert (root / job["spec"]["execution_path"]).is_file()
    for resource in (api, job):
        assert resource["spec"]["automatic_redeployment"]["enabled"] is True

    steps = workflow["execution"]["steps"]
    assert steps["image"] == {"prepare_image": "alpaca"}
    assert steps["migrate"] == {
        "run_job": "alpaca-connectors-migrate",
        "image_from": "image",
        "needs": ["image"],
    }
    assert steps["deploy_api"] == {
        "deploy": "alpaca",
        "image_from": "image",
        "needs": ["migrate"],
    }
    # The Agent deploys from its own image, and only after the API whose MCP it uses, so it also
    # waits for the migration.
    assert steps["agent_image"] == {"prepare_image": "alpaca-connectors-agent"}
    assert steps["deploy_agent"] == {
        "deploy": "alpaca-connectors-agent",
        "image_from": "agent_image",
        "needs": ["deploy_api", "agent_image"],
    }
    assert set(steps) == {"image", "migrate", "deploy_api", "agent_image", "deploy_agent"}
    assert not (root / ".mainsequence/workflows/alpaca-connectors-migrations.yaml").exists()


def test_bars_jobs_in_their_own_files_are_promoted_by_hand() -> None:
    """The graph cannot reference another file, so these two Jobs are never promoted by a push."""
    import yaml

    root = JOBS.parents[1]
    for name, key in (
        ("alpaca-bars-update.yaml", "alpaca-bars-update"),
        ("daily-stock-bars-holdings-ivv.yaml", "daily-stock-bars-holdings-ivv"),
    ):
        workflow = yaml.safe_load((root / ".mainsequence/workflows" / name).read_text())
        [resource] = workflow["resources"]
        assert resource["key"] == key
        assert resource["kind"] == "job"
        assert resource["spec"]["automatic_redeployment"] == {"enabled": False, "tag_regex": None}
        assert "execution" not in workflow


def test_every_resource_that_uses_metatables_declares_its_branch() -> None:
    """Tables belong to a Team; each workload reaches MetaTables through its declared access."""
    import yaml

    root = JOBS.parents[1]
    declared: dict[str, bool] = {}
    for path in sorted((root / ".mainsequence/workflows").glob("*.yaml")):
        workflow = yaml.safe_load(path.read_text())
        assert workflow["api_version"] == "2.3.0", path.name
        for resource in workflow["resources"]:
            branches = (resource.get("access") or {}).get("branches") or []
            declared[resource["key"]] = {"repository": "MetaTables", "level": "view"} in branches

    # The Agent's tools run in the API, so it reads no tables and needs no MetaTables access.
    assert declared.pop("alpaca-connectors-agent") is False
    assert declared == {
        "alpaca": True,
        "alpaca-connectors-migrate": True,
        "alpaca-etf-signal": True,
        "alpaca-etf-portfolio": True,
        "alpaca-bars-update": True,
        "daily-stock-bars-holdings-ivv": True,
    }
