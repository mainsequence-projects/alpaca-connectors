from __future__ import annotations

import json
import runpy
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# Job launchers are scripts executed by path (`src/jobs/...`), not package modules.
_LAUNCHER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "src" / "jobs" / "run_alpaca_bars_update.py")
)
main = _LAUNCHER["main"]

CONFIGURATION_UID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def schema_current():
    """Launcher tests run against a database at the code's head revision unless stated."""
    with patch(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind",
        return_value=False,
    ) as gate:
        yield gate


def test_launcher_skips_without_side_effects_when_schema_is_behind(schema_current) -> None:
    schema_current.return_value = True
    with patch("alpaca_connectors.market_data.execute_market_data_update") as execute:
        exit_code = main(["--configuration-uid", CONFIGURATION_UID])

    assert exit_code == 0
    execute.assert_not_called()


def test_launcher_requires_configuration_uid() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2


def test_launcher_rejects_malformed_configuration_uid() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--configuration-uid", "not-a-uuid"])

    assert exc_info.value.code == 2


def test_launcher_rejects_runtime_configuration_overrides() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--configuration-uid",
                CONFIGURATION_UID,
                "--force-update",
                "false",
            ]
        )

    assert exc_info.value.code == 2


def test_launcher_executes_exact_stored_configuration(capsys) -> None:
    result = {
        "dataset": {"uid": uuid.UUID("22222222-2222-4222-8222-222222222222")},
        "asset_source": "universe",
        "asset_count": 503,
        "rows_persisted": 1006,
        "update_hash": "bars-update-hash",
        "secret_key": "must-not-be-printed",
    }
    with patch(
        "alpaca_connectors.market_data.execute_market_data_update",
        return_value=result,
    ) as execute:
        exit_code = main(["--configuration-uid", CONFIGURATION_UID])

    assert exit_code == 0
    execute.assert_called_once_with(configuration_uid=CONFIGURATION_UID)
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "asset_count": 503,
        "asset_source": "universe",
        "configuration_uid": CONFIGURATION_UID,
        "dataset_uid": "22222222-2222-4222-8222-222222222222",
        "rows_persisted": 1006,
        "update_hash": "bars-update-hash",
    }
    assert "must-not-be-printed" not in json.dumps(summary)


@pytest.mark.parametrize(
    "error",
    [
        LookupError("configuration does not exist"),
        ValueError("configuration is disabled"),
        RuntimeError("updater failed"),
    ],
)
def test_launcher_propagates_update_errors(error: Exception) -> None:
    with (
        patch("alpaca_connectors.market_data.execute_market_data_update", side_effect=error),
        pytest.raises(type(error), match=str(error)),
    ):
        main(["--configuration-uid", CONFIGURATION_UID])
