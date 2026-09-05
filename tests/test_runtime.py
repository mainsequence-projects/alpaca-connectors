from msm.models.registration import resolve_markets_meta_table_models

from src.runtime import application_runtime_models, portfolio_runtime_models


def test_portfolio_runtime_includes_project_foreign_key_dependencies() -> None:
    models = resolve_markets_meta_table_models(portfolio_runtime_models())
    table_names = [model.__table__.name for model in models]
    application_tables = {
        model.__table__.name
        for model in resolve_markets_meta_table_models(application_runtime_models())
    }

    signal_configuration = "alpaca_connectors__etf_signal_job_configuration"
    bars_configuration = "alpaca_connectors__bars_configuration"
    portfolio_configuration = "alpaca_connectors__etf_portfolio_configuration"

    assert signal_configuration in table_names
    assert bars_configuration in table_names
    assert application_tables.issubset(table_names)
    assert table_names.index(signal_configuration) < table_names.index(portfolio_configuration)
    assert table_names.index(bars_configuration) < table_names.index(portfolio_configuration)
