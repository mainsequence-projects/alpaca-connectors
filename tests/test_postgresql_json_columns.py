"""JSON columns written to PostgreSQL, the hosted dialect, for real (issue #27).

MetaTables before 0.1.65 compiled a JSON column's parameter without a type, and its PostgreSQL
backend bound a list as a PostgreSQL array: every account sync failed with ``400
compiled_sql_database_error: cannot adapt type 'dict' using placeholder '%s'``. 0.1.65 types
those parameters ``json`` (MetaTables#111), which this project now requires.

Set ``ALPACA_CONNECTORS_TEST_POSTGRES_URL`` to a disposable ``alpaca_*`` database on localhost to
run these tests; they are skipped otherwise. Each test creates the project's tables in that
database, runs the connector's own write functions, and sends every compiled statement the way
the MetaTables PostgreSQL backend does: its values bound by the backend's own ``parameters_for``,
executed with ``prepare=True``, and the rows answered in the backend's wire form (``json_value``).
Only the HTTP hop to the MetaTables API, and the runtime attachment that would ask it for the
tables, are left out.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

URL_VARIABLE = "ALPACA_CONNECTORS_TEST_POSTGRES_URL"
DATA_SOURCE_UID = "00000000-0000-0000-0000-000000000027"


def _with_references(tables: list[sa.Table]) -> list[sa.Table]:
    found: dict[str, sa.Table] = {}
    pending = list(tables)
    while pending:
        table = pending.pop()
        if table.fullname in found:
            continue
        found[table.fullname] = table
        pending.extend(key.column.table for key in table.foreign_keys)
    return list(found.values())


@pytest.fixture
def postgresql(monkeypatch):
    url = os.environ.get(URL_VARIABLE)
    if not url:
        pytest.skip(f"Set {URL_VARIABLE} to a disposable PostgreSQL database")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or not (parsed.database or "").startswith(
        "alpaca_"
    ):
        pytest.fail("These tests accept only a disposable alpaca_* database on localhost")

    import psycopg
    from msm.api.base import MarketsMetaTableRow
    from msm.base import MarketsBase
    from msm.repositories.base import MarketsRepositoryContext

    import metatables.runtime_context
    from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails
    from alpaca_connectors.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from alpaca_connectors.operations import account_sync
    from alpaca_connectors.operations.account_sync import AccountSyncOperationTable
    from alpaca_connectors.operations.asset_registration import AssetRegistrationOperationTable
    from metatables import MetaTable, MetaTableCompiledSQLOperation
    from metatables.api.backend.backends.restricted_sql import json_value, parameters_for
    from metatables.runtime_contract import RuntimeContext

    project_tables = [
        AccountSyncOperationTable.__table__,
        AssetRegistrationOperationTable.__table__,
        AlpacaAssetDetailsTable.__table__,
        AlpacaAccountDetails.__table__,
    ]
    engine = sa.create_engine(parsed.set(drivername="postgresql+psycopg"))
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
        MarketsBase.metadata.create_all(connection, tables=_with_references(project_tables))

    runtime = RuntimeContext(
        git_source=None,
        dialect="postgresql",
        paramstyle="pyformat",
        data_source={"uid": DATA_SOURCE_UID},
        default_schema="public",
    )
    monkeypatch.setattr(metatables.runtime_context, "get_runtime_context", lambda **_: runtime)
    dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    sent: list[SimpleNamespace] = []
    untyped: set[str] = set()

    def execute_operation(cls, operation, *, timeout=None):
        # The operation crosses the wire as JSON, exactly as the client posts it.
        wire = MetaTableCompiledSQLOperation.model_validate_json(operation.model_dump_json())
        statement = wire.statement
        # ``untyped`` drops a declared type, as MetaTables before 0.1.65 sent the parameter.
        types = {
            name: token
            for name, token in (statement.parameter_types or {}).items()
            if name not in untyped
        }
        sent.append(SimpleNamespace(parameters=statement.parameters, types=types))
        values = parameters_for(statement.parameters, types, "postgresql")
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(statement.sql, values or None, prepare=True)
            rows = cursor.fetchall() if cursor.description else []
            names = [column.name for column in cursor.description or ()]
        return {"ok": True, "rows": [json_value(dict(zip(names, row))) for row in rows]}

    monkeypatch.setattr(MetaTable, "execute_operation", classmethod(execute_operation))
    context = MarketsRepositoryContext()
    monkeypatch.setattr(MarketsMetaTableRow, "_active_context", classmethod(lambda cls: context))
    monkeypatch.setattr(account_sync, "_start_engine", lambda: None)
    monkeypatch.setattr(
        "alpaca_connectors.runtime.start_markets_engine",
        lambda **_: SimpleNamespace(context=context),
    )
    monkeypatch.setattr(
        "msm.bootstrap.resolve_runtime", lambda **_: SimpleNamespace(context=context)
    )

    def stored(table, uid: str, column: str) -> tuple[object, str]:
        """A JSON column as PostgreSQL holds it, and its JSON type."""
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    f"SELECT {column}, json_typeof({column}) FROM {table.__table__.fullname} "
                    "WHERE uid = :uid"
                ),
                {"uid": uid},
            ).one()
        return row[0], row[1]

    yield SimpleNamespace(engine=engine, sent=sent, stored=stored, untyped=untyped)
    engine.dispose()


def _sync_owner():
    from alpaca_connectors.account.ownership import AccountOwner

    return AccountOwner.main_sequence_user(str(uuid.uuid4()))


def test_an_untyped_list_could_not_be_bound(postgresql) -> None:
    """The defect itself, as MetaTables before 0.1.65 sent the steps: the hosted 400's cause."""
    import psycopg

    from alpaca_connectors.operations.account_sync import start_account_sync_operation

    postgresql.untyped.add("steps")
    with pytest.raises(psycopg.ProgrammingError, match="cannot adapt type 'dict'"):
        start_account_sync_operation(account_uid=str(uuid.uuid4()), owner=_sync_owner())


def test_every_json_parameter_is_typed_json(postgresql) -> None:
    from alpaca_connectors.operations.account_sync import start_account_sync_operation

    start_account_sync_operation(account_uid=str(uuid.uuid4()), owner=_sync_owner())
    insert = postgresql.sent[-1]
    for column in ("steps", "request", "result", "error"):
        names = [name for name in insert.parameters if name.split("_m")[0] == column]
        assert names and all(insert.types.get(name) == "json" for name in names), column
    steps = next(insert.parameters[name] for name in insert.parameters if name == "steps")
    assert isinstance(steps, list)


def test_an_account_sync_is_recorded_and_succeeds(postgresql) -> None:
    from alpaca_connectors.operations.account_sync import (
        AccountSyncOperationTable,
        get_account_sync_operation,
        start_account_sync_operation,
        succeed_account_sync_operation,
    )

    account_uid = str(uuid.uuid4())
    started = start_account_sync_operation(account_uid=account_uid, owner=_sync_owner())
    assert started.status == "running"
    assert [step.status for step in started.steps] == ["running"]
    steps, kind = postgresql.stored(AccountSyncOperationTable, str(started.uid), "steps")
    assert kind == "array"
    assert steps[0]["key"] == "capture_holdings" and steps[0]["status"] == "running"
    request, kind = postgresql.stored(AccountSyncOperationTable, str(started.uid), "request")
    assert (request, kind) == ({"account_uid": account_uid}, "object")

    result = {"account_uid": account_uid, "holdings_set_uid": str(uuid.uuid4()), "rows": 3}
    finished = succeed_account_sync_operation(started, result=result)
    assert finished.status == "succeeded"
    assert [step.status for step in finished.steps] == ["succeeded"]
    steps, kind = postgresql.stored(AccountSyncOperationTable, str(started.uid), "steps")
    assert kind == "array" and steps[0]["status"] == "succeeded"
    assert get_account_sync_operation(started.uid).result == result


def test_a_failed_account_sync_is_recorded(postgresql) -> None:
    from msm.api.http import OperationError

    from alpaca_connectors.operations.account_sync import (
        AccountSyncOperationTable,
        fail_account_sync_operation,
        start_account_sync_operation,
    )

    started = start_account_sync_operation(account_uid=str(uuid.uuid4()), owner=_sync_owner())
    error = OperationError(code="provider_failure", message="Alpaca refused.", retryable=True)
    failed = fail_account_sync_operation(started, error=error)
    assert failed.status == "failed"
    assert failed.error.code == "provider_failure"
    steps, kind = postgresql.stored(AccountSyncOperationTable, str(started.uid), "steps")
    assert kind == "array" and steps[0]["status"] == "failed"
    stored_error, kind = postgresql.stored(AccountSyncOperationTable, str(started.uid), "error")
    assert kind == "object" and stored_error["code"] == "provider_failure"


def test_an_asset_registration_operation_is_recorded_through_every_step(postgresql) -> None:
    """The asset registration operation keeps its steps in a JSON list too."""
    from alpaca_connectors.operations.asset_registration import (
        AssetRegistrationOperationTable,
        complete_asset_registration_operation,
        create_asset_registration_operation,
        fail_asset_registration_operation,
        registration_steps,
        set_asset_registration_step,
    )

    created = create_asset_registration_operation(
        action="execute", request={"symbols": ["AAPL"]}, owner_uid=str(uuid.uuid4())
    )
    keys = [step["key"] for step in registration_steps("execute")]
    steps, kind = postgresql.stored(AssetRegistrationOperationTable, str(created.uid), "steps")
    assert kind == "array" and [step["key"] for step in steps] == keys
    request, _ = postgresql.stored(AssetRegistrationOperationTable, str(created.uid), "request")
    assert request == {"symbols": ["AAPL"]}

    running = set_asset_registration_step(created.uid, step_key=keys[0], status="running")
    assert running.current_step == keys[0]
    done = complete_asset_registration_operation(created.uid, result={"registered": 1})
    assert done.status == "succeeded"
    steps, kind = postgresql.stored(AssetRegistrationOperationTable, str(created.uid), "steps")
    assert kind == "array" and len(steps) == len(keys)

    other = create_asset_registration_operation(action="plan", request={}, owner_uid=None)
    failed = fail_asset_registration_operation(
        other.uid,
        step_key=registration_steps("plan")[0]["key"],
        error={"code": "provider_failure", "message": "Alpaca refused.", "retryable": True},
    )
    assert failed.status == "failed"
    steps, kind = postgresql.stored(AssetRegistrationOperationTable, str(other.uid), "steps")
    assert kind == "array" and steps[0]["status"] == "failed"


def test_a_refused_call_logs_its_upstream_status_and_code_but_no_text() -> None:
    """The connector's log names what MetaTables answered (issue #27), never the answer's text."""
    import requests

    from alpaca_connectors.api.app.errors import upstream_failure
    from mainsequence.client.exceptions import raise_for_response

    response = requests.Response()
    response.status_code = 400
    response.url = "https://metatables.example.test/meta-tables/execute-operation/?token=x"
    response.request = requests.Request("POST", response.url).prepare()
    response._content = json.dumps(
        {
            "detail": {
                "code": "compiled_sql_database_error",
                "detail": 'invalid input syntax for type json: Token "PKSECRET" is invalid',
            }
        }
    ).encode()
    try:
        raise_for_response(response)
    except Exception as refused:
        try:
            raise RuntimeError("wrapped") from refused
        except RuntimeError as exc:
            found = upstream_failure(exc)
    assert found == {
        "upstream_status": 400,
        "upstream_method": "POST",
        "upstream_path": "/meta-tables/execute-operation/",
        "upstream_code": "compiled_sql_database_error",
        "upstream_error": "BadRequestError",
    }
    assert "PKSECRET" not in str(found)
