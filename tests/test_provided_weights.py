"""Portfolios built from provided weights (ADR 0024)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.market_data.alpaca_bars import (
    AlpacaStockBarsConfig,
    AlpacaStockBarsPortfoliosConfig,
)
from alpaca_connectors.market_data.configurations import (
    AlpacaBarsConfigurationTable,
    validate_configuration_scope,
)
from alpaca_connectors.portfolios import bars_scope, provided_weights
from alpaca_connectors.portfolios.configurations import (
    AlpacaETFPortfolioConfiguration,
    AlpacaETFPortfolioConfigurationTable,
    create_portfolio_configuration_row,
)
from alpaca_connectors.portfolios.execution import (
    configuration_portfolio_identifier,
    portfolio_unique_identifier,
)
from alpaca_connectors.portfolios.provided_weights import (
    PortfolioOwnedByAnotherError,
    ProvidedWeightsError,
    normalize_observations,
    provided_signal_uid,
    provided_weights_signal,
    publish_provided_weights,
    require_portfolio_owner,
)
from alpaca_connectors.portfolios.signal_assets import SignalAssetTable
from alpaca_connectors.portfolios.signal_history import SignalObservationBounds
from alpaca_connectors.settings import PROJECT_NAMESPACE

CONFIGURATION_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_CONFIGURATION_UID = uuid.UUID("22222222-2222-4222-8222-222222222222")
BARS_UID = uuid.UUID("33333333-3333-4333-8333-333333333333")
REBALANCE_UID = uuid.UUID("44444444-4444-4444-8444-444444444444")
OWNER = AccountOwner.main_sequence_user("owner-uid")
EXTERNAL_OWNER = AccountOwner(gateway_uid="gateway-uid", user_uid="person-17")
NVDA = "ALPACA::nvda"
JPM = "ALPACA::jpm"
AAPL = "ALPACA::aapl"
MONDAY = dt.datetime(2026, 11, 16, 14, tzinfo=dt.UTC)
WEDNESDAY = dt.datetime(2026, 11, 18, 14, tzinfo=dt.UTC)


def provided_configuration(**overrides) -> AlpacaETFPortfolioConfiguration:
    values = {
        "uid": CONFIGURATION_UID,
        "name": "Thesis basket",
        "description": None,
        "weights_source": "provided",
        "signal_configuration_uid": None,
        "owner_gateway_uid": OWNER.gateway_uid,
        "owner_user_uid": OWNER.user_uid,
        "bars_configuration_uid": BARS_UID,
        "rebalance_configuration_uid": REBALANCE_UID,
        "portfolio_uid": None,
        "scheduled": True,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "valuation_maximum_staleness_seconds": 86_400,
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "created_at": MONDAY,
        "updated_at": MONDAY,
    }
    values.update(overrides)
    return AlpacaETFPortfolioConfiguration.model_validate(values)


# ------------------------------------------------------------------------------------------------
# Observations
# ------------------------------------------------------------------------------------------------
def test_observations_become_utc_times_oldest_first() -> None:
    later = dt.datetime(2026, 11, 18, 9, tzinfo=dt.timezone(dt.timedelta(hours=-5)))

    normalized = normalize_observations(
        [
            {"observed_at": later, "weights": {NVDA: 0.5, AAPL: 0.5}},
            {"observed_at": MONDAY.isoformat(), "weights": {NVDA: 0.6, JPM: 0.4}},
        ]
    )

    assert list(normalized) == [MONDAY, WEDNESDAY]
    assert normalized[WEDNESDAY] == {NVDA: 0.5, AAPL: 0.5}
    # Stored as given: no normalisation, negatives allowed.
    assert normalize_observations([{"observed_at": MONDAY, "weights": {NVDA: -0.25}}]) == {
        MONDAY: {NVDA: -0.25}
    }


@pytest.mark.parametrize(
    ("observations", "code"),
    [
        ([], "empty_observations"),
        ([{"observed_at": dt.datetime(2026, 11, 16, 14), "weights": {NVDA: 1.0}}], None),
        ([{"observed_at": MONDAY, "weights": {NVDA: float("nan")}}], None),
        ([{"observed_at": MONDAY, "weights": {NVDA: True}}], None),
        ([{"observed_at": MONDAY, "weights": {" ": 1.0}}], None),
        (
            [
                {"observed_at": MONDAY, "weights": {NVDA: 1.0}},
                {"observed_at": MONDAY, "weights": {JPM: 1.0}},
            ],
            None,
        ),
    ],
)
def test_invalid_observations_are_refused_without_quoting_them(observations, code) -> None:
    with pytest.raises(ProvidedWeightsError) as raised:
        normalize_observations(observations)

    assert raised.value.code == (code or "invalid_observation")
    assert raised.value.status_code == 422
    assert "nvda" not in str(raised.value).lower()


# ------------------------------------------------------------------------------------------------
# The signal
# ------------------------------------------------------------------------------------------------
def test_each_portfolio_has_one_stable_application_weights_signal() -> None:
    from msm_portfolios.contrib.signals import ApplicationWeights
    from msm_portfolios.time_index.signals.weights import compute_signal_uid

    from alpaca_connectors.instances import AlpacaSignalWeightsStorage

    with patch.object(ApplicationWeights, "from_signal_configuration") as build:
        provided_weights_signal(CONFIGURATION_UID)
    configuration = build.call_args.args[0]
    assert (configuration.provider, configuration.key) == (
        PROJECT_NAMESPACE,
        str(CONFIGURATION_UID),
    )
    assert build.call_args.kwargs == {"output_table": AlpacaSignalWeightsStorage}

    # The UID the connector computes is the one ms-markets' signal publishes under, and it
    # depends only on the configuration, so new weights never change it.
    signal = ApplicationWeights.__new__(ApplicationWeights)
    signal.signal_configuration = configuration
    assert provided_signal_uid(CONFIGURATION_UID) == compute_signal_uid(signal)
    assert provided_signal_uid(str(CONFIGURATION_UID)) == provided_signal_uid(CONFIGURATION_UID)
    assert provided_signal_uid(OTHER_CONFIGURATION_UID) != provided_signal_uid(CONFIGURATION_UID)


def test_provided_portfolios_have_their_own_portfolio_identifier() -> None:
    assert portfolio_unique_identifier(CONFIGURATION_UID, weights_source="provided") == (
        "ALPACA_PORTFOLIO__11111111111141118111111111111111"
    )
    assert portfolio_unique_identifier(CONFIGURATION_UID) == (
        "ALPACA_ETF_PORTFOLIO__11111111111141118111111111111111"
    )
    assert configuration_portfolio_identifier(provided_configuration()).startswith(
        "ALPACA_PORTFOLIO__"
    )


# ------------------------------------------------------------------------------------------------
# Publishing
# ------------------------------------------------------------------------------------------------
def _publish_patches(*, last_stored=None, details=None, assets=None):
    known = assets if assets is not None else [NVDA, JPM, AAPL]
    asset_rows = {
        identifier: SimpleNamespace(uid=uuid.uuid5(uuid.NAMESPACE_URL, identifier))
        for identifier in known
    }
    detail_rows = (
        details
        if details is not None
        else {str(row.uid): ("SYM", True) for row in asset_rows.values()}
    )
    return (
        patch(
            "alpaca_connectors.markets_api.assets_by_identifier",
            side_effect=lambda identifiers: {
                identifier: asset_rows[identifier]
                for identifier in identifiers
                if identifier in asset_rows
            },
        ),
        patch(
            "alpaca_connectors.holdings.allocation.read_alpaca_details",
            return_value=detail_rows,
        ),
        patch(
            "alpaca_connectors.portfolios.signal_history.read_signal_last_observation_at",
            side_effect=(
                last_stored if isinstance(last_stored, list) else lambda signal_uid: last_stored
            ),
        ),
    )


def test_publish_records_the_assets_before_the_signal_publishes() -> None:
    order = MagicMock()
    signal = Mock(signal_uid="signal-a")
    signal.publish.side_effect = lambda *args, **kwargs: order.publish()
    assets, details, bounds = _publish_patches(last_stored=MONDAY)
    with (
        assets,
        details,
        bounds,
        patch.object(provided_weights, "provided_weights_signal", return_value=signal),
        patch(
            "alpaca_connectors.portfolios.signal_assets.record_signal_assets",
            side_effect=lambda *args, **kwargs: order.record(*args),
        ),
        patch("alpaca_connectors.portfolios.datasets.register_signal_dataset") as register,
    ):
        published = publish_provided_weights(
            provided_configuration(),
            [{"observed_at": WEDNESDAY, "weights": {NVDA: 0.5, AAPL: 0.5}}],
        )

    assert order.mock_calls == [call.record("signal-a", [AAPL, NVDA]), call.publish()]
    signal.publish.assert_called_once_with({WEDNESDAY: {NVDA: 0.5, AAPL: 0.5}}, description=None)
    register.assert_called_once_with("signal-a")
    assert (published.observation_count, published.asset_count) == (1, 2)
    assert published.last_observed_at == WEDNESDAY


@pytest.mark.parametrize(
    ("kwargs", "code", "status"),
    [
        ({"assets": [NVDA]}, "unknown_asset", 422),
        ({"details": {}}, "asset_not_alpaca", 422),
        ({"last_stored": WEDNESDAY}, "observation_not_after_last", 409),
    ],
)
def test_publish_refuses_before_writing_anything(kwargs, code, status) -> None:
    signal = Mock(signal_uid="signal-a")
    assets, details, bounds = _publish_patches(**kwargs)
    with (
        assets,
        details,
        bounds,
        patch.object(provided_weights, "provided_weights_signal", return_value=signal),
        patch("alpaca_connectors.portfolios.signal_assets.record_signal_assets") as record,
        pytest.raises(ProvidedWeightsError) as raised,
    ):
        publish_provided_weights(
            provided_configuration(),
            [{"observed_at": WEDNESDAY, "weights": {NVDA: 0.5, AAPL: 0.5}}],
        )

    assert (raised.value.code, raised.value.status_code) == (code, status)
    record.assert_not_called()
    signal.publish.assert_not_called()
    assert "ALPACA::" not in str(raised.value)


@pytest.mark.parametrize(
    ("error", "stored_after", "code"),
    [
        (LookupError("Unknown asset unique identifiers: ['ALPACA::nvda']."), None, "unknown_asset"),
        # Another call published first: the stored last time, not the message, decides.
        (ValueError("any wording"), MONDAY, "observation_not_after_last"),
        (ValueError("Observation at 2026 is at or before ..."), None, "invalid_observation"),
    ],
)
def test_signal_refusals_become_codes_that_quote_nothing(error, stored_after, code) -> None:
    signal = Mock(signal_uid="signal-a")
    signal.publish.side_effect = error
    assets, details, bounds = _publish_patches(last_stored=[None, stored_after])
    with (
        assets,
        details,
        bounds,
        patch.object(provided_weights, "provided_weights_signal", return_value=signal),
        patch("alpaca_connectors.portfolios.signal_assets.record_signal_assets"),
        pytest.raises(ProvidedWeightsError) as raised,
    ):
        publish_provided_weights(
            provided_configuration(), [{"observed_at": MONDAY, "weights": {NVDA: 1.0}}]
        )

    assert raised.value.code == code
    assert "ALPACA::" not in str(raised.value)


def test_an_etf_portfolio_takes_no_provided_weights() -> None:
    etf = provided_configuration(
        weights_source="etf_signal",
        signal_configuration_uid=uuid.uuid4(),
        owner_gateway_uid=None,
        owner_user_uid=None,
    )
    with pytest.raises(ProvidedWeightsError) as raised:
        publish_provided_weights(etf, [{"observed_at": MONDAY, "weights": {NVDA: 1.0}}])

    assert raised.value.code == "not_a_provided_weights_portfolio"


# ------------------------------------------------------------------------------------------------
# Ownership (decision 6)
# ------------------------------------------------------------------------------------------------
def test_only_the_owner_changes_a_provided_weights_portfolio() -> None:
    require_portfolio_owner(provided_configuration(), OWNER)
    external = provided_configuration(
        owner_gateway_uid=EXTERNAL_OWNER.gateway_uid,
        owner_user_uid=EXTERNAL_OWNER.user_uid,
    )
    require_portfolio_owner(external, EXTERNAL_OWNER)

    for configuration, caller in (
        (provided_configuration(), AccountOwner.main_sequence_user("someone-else")),
        (external, AccountOwner(gateway_uid="gateway-uid", user_uid="person-18")),
        (external, AccountOwner.main_sequence_user("gateway-uid")),
    ):
        with pytest.raises(PortfolioOwnedByAnotherError) as raised:
            require_portfolio_owner(configuration, caller)
        assert (raised.value.code, raised.value.status_code) == ("portfolio_owned_by_another", 403)
        assert "person" not in str(raised.value) and "owner-uid" not in str(raised.value)


def test_etf_portfolios_have_no_owner_and_keep_their_rules() -> None:
    etf = provided_configuration(
        weights_source="etf_signal",
        signal_configuration_uid=uuid.uuid4(),
        owner_gateway_uid=None,
        owner_user_uid=None,
    )
    require_portfolio_owner(etf, AccountOwner.main_sequence_user("anyone"))


def test_the_row_shape_depends_on_the_weights_source() -> None:
    with pytest.raises(ValueError, match="requires its owner"):
        create_portfolio_configuration_row(
            name="x",
            weights_source="provided",
            bars_configuration_uid=BARS_UID,
            rebalance_configuration_uid=REBALANCE_UID,
        )
    with pytest.raises(ValueError, match="no signal configuration"):
        create_portfolio_configuration_row(
            name="x",
            weights_source="provided",
            signal_configuration_uid=uuid.uuid4(),
            owner_gateway_uid="a",
            owner_user_uid="a",
            bars_configuration_uid=BARS_UID,
            rebalance_configuration_uid=REBALANCE_UID,
        )
    with pytest.raises(ValueError, match="has no owner"):
        create_portfolio_configuration_row(
            name="x",
            signal_configuration_uid=uuid.uuid4(),
            owner_gateway_uid="a",
            owner_user_uid="a",
            bars_configuration_uid=BARS_UID,
            rebalance_configuration_uid=REBALANCE_UID,
        )


# ------------------------------------------------------------------------------------------------
# Create
# ------------------------------------------------------------------------------------------------
def _create(**overrides):
    values = {
        "owner": OWNER,
        "name": "Thesis basket",
        "rebalance_configuration_uid": REBALANCE_UID,
        "observations": [{"observed_at": MONDAY, "weights": {NVDA: 0.6, JPM: 0.4}}],
        "bars_configuration": {
            "name": "Thesis prices",
            "account_uid": uuid.uuid4(),
            "frequency_id": "1d",
            "feed": "sip",
            "adjustment": "all",
        },
    }
    values.update(overrides)
    return provided_weights.create_provided_weights_portfolio(**values)


def test_create_makes_the_bars_row_publishes_and_names_the_portfolio() -> None:
    configuration = provided_configuration()
    stored = provided_configuration(portfolio_uid=uuid.UUID(int=7))
    portfolio_row = SimpleNamespace(uid=uuid.UUID(int=7), unique_identifier="ALPACA_PORTFOLIO__X")
    published = provided_weights.PublishedWeights("signal-a", 1, 2, MONDAY, MONDAY)
    configurations = "alpaca_connectors.portfolios.configurations"
    with (
        patch.object(provided_weights, "require_alpaca_assets"),
        patch(
            "alpaca_connectors.market_data.configurations.create_bar_configuration",
            return_value=SimpleNamespace(uid=BARS_UID),
        ) as create_bars,
        patch(
            f"{configurations}.create_portfolio_configuration_row", return_value=configuration
        ) as create_row,
        patch.object(provided_weights, "ensure_provided_signal_metadata"),
        patch.object(provided_weights, "_publish_validated", return_value=published) as publish,
        patch.object(provided_weights, "_upsert_portfolio_row", return_value=portfolio_row),
        patch(
            f"{configurations}.update_portfolio_configuration_row", return_value=stored
        ) as update,
    ):
        created = _create()

    assert create_bars.call_args.kwargs["asset_source"] == "portfolios"
    row_kwargs = create_row.call_args.kwargs
    assert row_kwargs["weights_source"] == "provided"
    assert row_kwargs["bars_configuration_uid"] == BARS_UID
    assert (row_kwargs["owner_gateway_uid"], row_kwargs["owner_user_uid"]) == (
        "owner-uid",
        "owner-uid",
    )
    update.assert_called_once_with(CONFIGURATION_UID, values={"portfolio_uid": uuid.UUID(int=7)})
    # The assets were validated once, by the create, before any row existed.
    assert publish.call_args.args[2] == [JPM, NVDA]
    assert created.portfolio_uid == str(uuid.UUID(int=7))
    assert created.bars_configuration_created is True


def test_a_refused_publication_leaves_no_row_behind() -> None:
    configurations = "alpaca_connectors.portfolios.configurations"
    refused = ProvidedWeightsError("unknown_asset", "refused")
    with (
        patch.object(provided_weights, "require_alpaca_assets"),
        patch(
            "alpaca_connectors.market_data.configurations.create_bar_configuration",
            return_value=SimpleNamespace(uid=BARS_UID),
        ),
        patch(
            "alpaca_connectors.market_data.configurations.delete_bar_configuration"
        ) as delete_bars,
        patch(
            f"{configurations}.create_portfolio_configuration_row",
            return_value=provided_configuration(),
        ),
        patch(f"{configurations}.delete_portfolio_configuration_row") as delete_row,
        patch.object(provided_weights, "ensure_provided_signal_metadata"),
        patch.object(provided_weights, "_publish_validated", side_effect=refused),
        pytest.raises(ProvidedWeightsError),
    ):
        _create()

    delete_row.assert_called_once_with(CONFIGURATION_UID)
    delete_bars.assert_called_once_with(BARS_UID)


def test_create_names_exactly_one_bars_configuration() -> None:
    for overrides in ({"bars_configuration_uid": BARS_UID}, {"bars_configuration": None}):
        with pytest.raises(ProvidedWeightsError) as raised:
            _create(**overrides)
        assert raised.value.code == "invalid_bars_configuration"


# ------------------------------------------------------------------------------------------------
# Bars: the `portfolios` source (decision 5)
# ------------------------------------------------------------------------------------------------
def test_the_portfolios_source_names_no_assets_or_universe() -> None:
    assert validate_configuration_scope(
        asset_source="portfolios", asset_uids=None, universe_uid=None
    ) == ("portfolios", [], None)
    with pytest.raises(ValueError, match="portfolios source forbids"):
        validate_configuration_scope(
            asset_source="portfolios", asset_uids=[uuid.uuid4()], universe_uid=None
        )
    config = AlpacaStockBarsPortfoliosConfig(
        frequency_id="1d", feed="sip", adjustment="all", bars_configuration_uid=BARS_UID
    )
    assert (config.asset_source, config.bars_configuration_uid) == ("portfolios", BARS_UID)
    with pytest.raises(ValueError, match="requires AlpacaStockBarsPortfoliosConfig"):
        AlpacaStockBarsConfig(
            frequency_id="1d", feed="sip", adjustment="all", asset_source="portfolios"
        )


def _update_hash(config) -> str:
    from metatables.updaters import configuration

    serialized = configuration.Serializer().serialize_init_kwargs({"config": config})
    return configuration.hash_signature(dict(serialized))[0]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {"asset_source": "assets", "asset_list": ["ALPACA::a"]},
            "8d7f146530a1fef7895529a2b918fc17",
        ),
        (
            {"asset_source": "universe", "asset_category_unique_identifier": "HOLDINGS__IVV"},
            "8475f7b71a8744e69d5d3ba96fb93179",
        ),
        (
            {
                "asset_source": "account_holdings",
                "account_uid": uuid.UUID("11111111-1111-4111-8111-111111111111"),
            },
            "2e920225987d44ac053b3bd7ccc63b7d",
        ),
    ],
)
def test_existing_bars_configurations_keep_their_update_hash(values, expected) -> None:
    # The hashes of the release before ADR 0024: adding the `portfolios` source must not give an
    # existing bars configuration a new updater identity.
    config = AlpacaStockBarsConfig(frequency_id="1d", feed="sip", adjustment="all", **values)
    assert _update_hash(config) == expected


def test_portfolios_bars_configurations_hash_their_own_uid() -> None:
    first, second = (
        AlpacaStockBarsPortfoliosConfig(
            frequency_id="1d", feed="sip", adjustment="all", bars_configuration_uid=uid
        )
        for uid in (BARS_UID, uuid.uuid4())
    )
    assert _update_hash(first) != _update_hash(second)


def test_the_scope_is_the_live_portfolios_signal_assets_in_bounded_reads() -> None:
    etf_configuration_uid = uuid.uuid4()
    live_portfolio, retired_portfolio = uuid.UUID(int=1), uuid.UUID(int=2)
    rows = [
        # A: provided, live.
        {
            "uid": CONFIGURATION_UID,
            "weights_source": "provided",
            "signal_configuration_uid": None,
            "portfolio_uid": live_portfolio,
        },
        # B: provided, its Portfolio retired.
        {
            "uid": OTHER_CONFIGURATION_UID,
            "weights_source": "provided",
            "signal_configuration_uid": None,
            "portfolio_uid": retired_portfolio,
        },
        # C: an ETF portfolio that has not run yet, so it has no Portfolio row.
        {
            "uid": uuid.uuid4(),
            "weights_source": "etf_signal",
            "signal_configuration_uid": etf_configuration_uid,
            "portfolio_uid": None,
        },
    ]
    portfolios = [
        SimpleNamespace(uid=live_portfolio, retired_at=None),
        SimpleNamespace(uid=retired_portfolio, retired_at=MONDAY),
    ]
    with (
        patch.object(bars_scope, "_naming_configurations", return_value=rows) as naming,
        patch("msm.api.portfolios.Portfolio.filter", return_value=portfolios) as portfolio_filter,
        patch(
            "alpaca_connectors.operations.signal_job_configurations_by_uids",
            return_value={str(etf_configuration_uid): "etf-configuration"},
        ) as signal_configurations,
        patch(
            "alpaca_connectors.operations.signal_uid_for_configuration",
            return_value="etf-signal",
        ),
        patch(
            "alpaca_connectors.portfolios.signal_assets.assets_for_signals",
            return_value=[AAPL, JPM, NVDA],
        ) as assets,
    ):
        scope = bars_scope.resolve_portfolio_bars_scope(BARS_UID, at=WEDNESDAY)

    naming.assert_called_once_with(BARS_UID)
    portfolio_filter.assert_called_once()
    signal_configurations.assert_called_once_with([str(etf_configuration_uid)])
    provided_signal = provided_signal_uid(CONFIGURATION_UID)
    assets.assert_called_once_with(tuple(sorted({provided_signal, "etf-signal"})))
    assert scope.retired_configuration_uids == (str(OTHER_CONFIGURATION_UID),)
    assert len(scope.configuration_uids) == 2
    assert scope.asset_identifiers == (AAPL, JPM, NVDA)
    assert scope.snapshot() == {
        "portfolio_count": 2,
        "retired_portfolio_count": 1,
        "signal_count": 2,
    }


def test_a_portfolio_retired_later_still_counts() -> None:
    portfolio_uid = uuid.UUID(int=1)
    with patch(
        "msm.api.portfolios.Portfolio.filter",
        return_value=[SimpleNamespace(uid=portfolio_uid, retired_at=WEDNESDAY)],
    ):
        assert bars_scope.retired_portfolio_uids([str(portfolio_uid)], at=MONDAY) == set()
        assert bars_scope.retired_portfolio_uids([str(portfolio_uid)], at=WEDNESDAY) == {
            str(portfolio_uid)
        }


# ------------------------------------------------------------------------------------------------
# Schema (revision 0025)
# ------------------------------------------------------------------------------------------------
def _revision():
    path = (
        Path(__file__).resolve().parents[1]
        / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
        / "0025_add_provided_weights_portfolios.py"
    )
    spec = importlib.util.spec_from_file_location("revision_0025", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


def test_revision_0025_matches_the_models() -> None:
    revision = _revision()
    portfolio = AlpacaETFPortfolioConfigurationTable.__table__
    bars = AlpacaBarsConfigurationTable.__table__
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in portfolio.constraints
        if type(constraint).__name__ == "CheckConstraint"
    }
    bars_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in bars.constraints
        if type(constraint).__name__ == "CheckConstraint"
    }

    assert (revision.revision, revision.down_revision) == ("0025", "0024")
    assert revision.PORTFOLIO_TABLE == portfolio.name
    assert revision.BARS_TABLE == bars.name
    assert revision.SIGNAL_ASSET_TABLE == SignalAssetTable.__table__.name
    for name, condition in revision.PORTFOLIO_CHECKS:
        assert checks[name] == condition
    assert "'portfolios'" in bars_checks[revision.BARS_SOURCE_CHECK]
    assert portfolio.columns["signal_configuration_uid"].nullable is True
    assert str(portfolio.columns["weights_source"].server_default.arg) == "etf_signal"
    assert [column.name for column in SignalAssetTable.__table__.primary_key.columns] == [
        "signal_uid",
        "asset_identifier",
    ]

    op = MagicMock()
    with patch.object(revision, "op", op):
        revision.upgrade()
    added = {added.args[1].name for added in op.add_column.call_args_list}
    assert added == {"weights_source", "owner_gateway_uid", "owner_user_uid"}
    op.create_table.assert_called_once()
    # The one full read of the signal weights: filling the signal-asset table once.
    [backfill] = op.execute.call_args_list
    assert "GROUP BY signal_uid, asset_identifier" in str(backfill.args[0])


# ------------------------------------------------------------------------------------------------
# API
# ------------------------------------------------------------------------------------------------
SERVICES = "alpaca_connectors.api.app.services.portfolio_configurations"


def test_create_route_makes_the_caller_the_owner(api_client, local_caller) -> None:
    with patch(
        f"{SERVICES}.create_provided_weights_portfolio", side_effect=LookupError("stop")
    ) as create:
        response = api_client.post(
            "/v1/portfolio-configurations/provided-weights",
            json={
                "name": "Thesis basket",
                "bars_configuration_uid": str(BARS_UID),
                "rebalance_configuration_uid": str(REBALANCE_UID),
                "observations": [{"observed_at": MONDAY.isoformat(), "weights": {NVDA: 1.0}}],
            },
        )

    assert response.status_code == 404
    kwargs = create.call_args.kwargs
    assert kwargs["owner"] == AccountOwner.main_sequence_user(local_caller.uid)
    assert kwargs["bars_configuration_uid"] == str(BARS_UID)
    assert kwargs["observations"] == [{"observed_at": MONDAY, "weights": {NVDA: 1.0}}]


def test_create_route_needs_exactly_one_bars_configuration(api_client) -> None:
    response = api_client.post(
        "/v1/portfolio-configurations/provided-weights",
        json={
            "name": "Thesis basket",
            "rebalance_configuration_uid": str(REBALANCE_UID),
            "observations": [{"observed_at": MONDAY.isoformat(), "weights": {NVDA: 1.0}}],
        },
    )
    assert response.status_code == 422


def test_inline_bars_need_an_account_the_caller_may_configure(api_client) -> None:
    from alpaca_connectors.account.ownership import AccountOwnedByAnotherError

    with (
        patch(
            "alpaca_connectors.api.app.services.accounts.require_owner_may_configure_account",
            side_effect=AccountOwnedByAnotherError(),
        ),
        patch(f"{SERVICES}.create_provided_weights_portfolio") as create,
    ):
        response = api_client.post(
            "/v1/portfolio-configurations/provided-weights",
            json={
                "name": "Thesis basket",
                "bars_configuration": {"name": "Prices", "account_uid": str(uuid.uuid4())},
                "rebalance_configuration_uid": str(REBALANCE_UID),
                "observations": [{"observed_at": MONDAY.isoformat(), "weights": {NVDA: 1.0}}],
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_owned_by_another"
    create.assert_not_called()


def test_another_caller_cannot_give_weights_pause_or_delete(api_client, local_caller) -> None:
    owned = provided_configuration(
        owner_gateway_uid=local_caller.uid, owner_user_uid=local_caller.uid
    )
    observation = {"observations": [{"observed_at": WEDNESDAY.isoformat(), "weights": {NVDA: 1.0}}]}
    with (
        patch(
            "alpaca_connectors.portfolios.configurations.get_portfolio_configuration",
            return_value=owned,
        ),
        patch(f"{SERVICES}.get_portfolio_configuration", return_value=owned),
        patch.object(provided_weights, "publish_provided_weights") as publish,
        patch(f"{SERVICES}.update_portfolio_configuration") as update,
        patch(f"{SERVICES}.delete_portfolio_configuration_row") as delete,
    ):
        other = local_caller.other_headers
        responses = [
            api_client.post(
                f"/v1/portfolio-configurations/{CONFIGURATION_UID}/weights",
                json=observation,
                headers=other,
            ),
            api_client.patch(
                f"/v1/portfolio-configurations/{CONFIGURATION_UID}",
                json={"scheduled": False},
                headers=other,
            ),
            api_client.delete(f"/v1/portfolio-configurations/{CONFIGURATION_UID}", headers=other),
        ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "portfolio_owned_by_another"
        assert local_caller.uid not in response.text
    publish.assert_not_called()
    update.assert_not_called()
    delete.assert_not_called()


def test_the_owner_gives_weights(api_client, local_caller) -> None:
    owned = provided_configuration(
        owner_gateway_uid=local_caller.uid, owner_user_uid=local_caller.uid
    )
    published = provided_weights.PublishedWeights("signal-a", 1, 2, WEDNESDAY, WEDNESDAY)
    with (
        patch(
            "alpaca_connectors.portfolios.configurations.get_portfolio_configuration",
            return_value=owned,
        ),
        patch.object(provided_weights, "publish_provided_weights", return_value=published) as pub,
    ):
        response = api_client.post(
            f"/v1/portfolio-configurations/{CONFIGURATION_UID}/weights",
            json={
                "observations": [
                    {"observed_at": WEDNESDAY.isoformat(), "weights": {NVDA: 0.5, AAPL: 0.5}}
                ]
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["signal_uid"] == "signal-a"
    assert pub.call_args.args[1] == [{"observed_at": WEDNESDAY, "weights": {NVDA: 0.5, AAPL: 0.5}}]


def test_responses_carry_the_weights_source_but_not_the_owner() -> None:
    from alpaca_connectors.api.app.services import portfolio_configurations as service
    from alpaca_connectors.operations import ConfigurationJobState

    row = provided_configuration()
    state = ConfigurationJobState(job=None, dispatcher=None, latest_runs={})
    with (
        patch.object(service, "configuration_job_state", return_value=state),
        patch.object(service, "signal_job_configurations_by_uids", return_value={}) as signals,
        patch.object(
            service,
            "rebalance_configurations_by_uids",
            return_value={str(REBALANCE_UID): SimpleNamespace(strategy="calendar_event_signal")},
        ),
    ):
        [response] = service._responses([row])

    signals.assert_called_once_with([])
    assert response.weights_source == "provided"
    assert response.signal_configuration_uid is None
    assert response.signal_uid == provided_signal_uid(CONFIGURATION_UID)
    assert "owner" not in response.model_dump_json()


# ------------------------------------------------------------------------------------------------
# The signal-asset table and the bars run
# ------------------------------------------------------------------------------------------------
def test_recording_inserts_only_the_pairs_not_yet_recorded() -> None:
    from alpaca_connectors.portfolios import signal_assets

    statements = []
    with (
        patch.object(signal_assets, "_start_runtime", return_value=SimpleNamespace(context="ctx")),
        patch(
            "msm.repositories.base.compile_markets_statement",
            side_effect=lambda statement, **kwargs: statements.append(statement) or statement,
        ),
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": [{"asset_identifier": NVDA}]},
        ),
        patch("msm.repositories.crud.bulk_upsert_model") as upsert,
    ):
        missing = signal_assets.record_signal_assets("signal-a", [NVDA, AAPL, NVDA], at=WEDNESDAY)

    assert missing == [AAPL]
    # One primary-key read of the given pairs, then one insert of the new ones.
    assert len(statements) == 1
    assert "signal_asset" in str(statements[0])
    assert upsert.call_args.kwargs["values"] == [
        {"signal_uid": "signal-a", "asset_identifier": AAPL, "first_published_at": WEDNESDAY}
    ]
    assert upsert.call_args.kwargs["conflict_columns"] == ("signal_uid", "asset_identifier")


def test_a_signals_assets_are_one_read_of_the_signal_asset_table() -> None:
    from alpaca_connectors.portfolios import signal_assets

    statements = []
    with (
        patch.object(signal_assets, "_start_runtime", return_value=SimpleNamespace(context="ctx")),
        patch(
            "msm.repositories.base.compile_markets_statement",
            side_effect=lambda statement, **kwargs: statements.append(statement) or statement,
        ),
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": [{"asset_identifier": AAPL}, {"asset_identifier": NVDA}]},
        ),
    ):
        assets = signal_assets.assets_for_signals(["signal-b", "signal-a", "signal-a"])

    assert assets == [AAPL, NVDA]
    [statement] = statements
    compiled = str(statement)
    assert "alpaca_connectors__signal_asset" in compiled
    assert "signalweights" not in compiled
    assert signal_assets.assets_for_signals([]) == []


def test_the_bars_run_resolves_a_portfolios_source_by_its_own_uid() -> None:
    from alpaca_connectors.market_data.services import build_market_data_update

    configuration = SimpleNamespace(
        uid=BARS_UID,
        enabled=True,
        account_uid=uuid.uuid4(),
        asset_source="portfolios",
        asset_uids=[],
        universe_uid=None,
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        model_dump=lambda mode=None: {"uid": str(BARS_UID)},
    )
    scope = bars_scope.PortfolioBarsScope(
        bars_configuration_uid=str(BARS_UID),
        configuration_uids=("a",),
        retired_configuration_uids=(),
        signal_uids=("signal-a",),
        asset_identifiers=(JPM, NVDA),
    )
    nodes = []

    class FakeNode:
        def __init__(self, **kwargs):
            nodes.append(kwargs)
            self.update_hash = "hash"

    with (
        patch("alpaca_connectors.runtime.account_runtime_models", return_value=[]),
        patch("alpaca_connectors.runtime.start_markets_engine"),
        patch(
            "alpaca_connectors.market_data.configurations.get_bar_configuration",
            return_value=configuration,
        ),
        patch(
            "alpaca_connectors.account.services.get_account_registration",
            return_value={"account_uid": "x"},
        ),
        patch("alpaca_connectors.market_data.storage.storage_for", return_value=object()),
        patch("alpaca_connectors.market_data.services._dataset_from_storage", return_value={}),
        patch("alpaca_connectors.market_data.services.asdict", return_value={}),
        patch(
            "alpaca_connectors.market_data.services._asset_uids_from_identifiers",
            return_value=["uid-jpm", "uid-nvda"],
        ),
        patch.object(bars_scope, "resolve_portfolio_bars_scope", return_value=scope) as resolve,
        patch("alpaca_connectors.market_data.alpaca_bars.AlpacaStockBarsNode", FakeNode),
    ):
        _, summary = build_market_data_update(configuration_uid=BARS_UID)

    resolve.assert_called_once_with(BARS_UID)
    assert summary["asset_identifiers"] == [JPM, NVDA]
    assert summary["source_snapshot"] == scope.snapshot()
    [node] = nodes
    # The hashed scope is the bars configuration itself, so new assets never mint a new updater.
    assert isinstance(node["config"], AlpacaStockBarsPortfoliosConfig)
    assert node["config"].bars_configuration_uid == BARS_UID
    assert node["config"].asset_list in (None, [])
    assert node["resolved_asset_identifiers"] == [JPM, NVDA]


def test_the_portfolio_run_resolves_the_provided_signal() -> None:
    from alpaca_connectors.portfolios import execution
    from alpaca_connectors.portfolios.signal_history import (
        SignalObservationAsset,
        SignalObservationMatrix,
    )

    signal_uid = provided_signal_uid(CONFIGURATION_UID)
    matrix = SignalObservationMatrix(
        signal_uid=signal_uid,
        time_indexes=(WEDNESDAY,),
        assets=(
            SignalObservationAsset(asset_identifier=NVDA, symbol="NVDA", name=None, weights=(0.5,)),
            SignalObservationAsset(asset_identifier=JPM, symbol="JPM", name=None, weights=(0.0,)),
        ),
    )
    with (
        patch.object(
            execution, "get_portfolio_configuration", return_value=provided_configuration()
        ),
        patch.object(
            execution,
            "validate_portfolio_references",
            return_value=(
                None,
                SimpleNamespace(uid=BARS_UID, frequency_id="1d", feed="sip", adjustment="all"),
                Mock(uid=REBALANCE_UID, strategy="calendar_event_signal"),
            ),
        ) as references,
        patch.object(execution, "read_signal_observation_matrix", return_value=matrix) as read,
        patch.object(
            execution,
            "read_signal_observation_bounds",
            return_value=SignalObservationBounds(signal_uid, MONDAY, WEDNESDAY),
        ),
        patch.object(execution, "resolve_alpaca_bars_time_index_meta_table_uid", return_value="t"),
        patch(
            "alpaca_connectors.portfolios.signal_assets.assets_for_signals",
            return_value=[AAPL, JPM, NVDA],
        ) as recorded,
    ):
        resolved = execution.resolve_portfolio_configuration(CONFIGURATION_UID)

    assert references.call_args.kwargs["signal_configuration_uid"] is None
    read.assert_called_once_with(signal_uid=signal_uid, observation_limit=1)
    assert resolved.signal_uid == signal_uid
    # Every asset it ever held (AAPL was dropped before the latest observation), so a history the
    # portfolio started with is priced too.
    recorded.assert_called_once_with([signal_uid])
    assert resolved.asset_identifiers == (AAPL, JPM, NVDA)
    assert resolved.signal_start_time == MONDAY
    assert resolved.summary()["weights_source"] == "provided"
    assert resolved.summary()["signal_configuration_uid"] is None
