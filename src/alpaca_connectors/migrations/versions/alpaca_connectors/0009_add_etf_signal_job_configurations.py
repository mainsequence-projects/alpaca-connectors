"""add ETF signal Job configurations

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-04 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alpaca_connectors__etf_signal_job_configuration",
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("universe_uid", sa.Uuid(), nullable=False),
        sa.Column("account_uid", sa.Uuid(), nullable=False),
        sa.Column("job_uid", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_type", sa.String(length=16), nullable=False),
        sa.Column("schedule_every", sa.Integer(), nullable=True),
        sa.Column("schedule_period", sa.String(length=16), nullable=True),
        sa.Column("schedule_expression", sa.String(length=128), nullable=True),
        sa.Column("schedule_start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cpu_request", sa.String(length=16), nullable=False),
        sa.Column("memory_request", sa.String(length=16), nullable=False),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column("spot", sa.Boolean(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lifecycle_state IN ('provisioning', 'ready', 'paused', 'error', 'deleting')",
            name="ck__alpaca_connectors__etf_signal_job_configuration_6bdc019766",
        ),
        sa.CheckConstraint(
            "max_runtime_seconds > 0",
            name="ck__alpaca_connectors__etf_signal_job_configuration_6ca9021401",
        ),
        sa.CheckConstraint(
            "schedule_period IS NULL OR schedule_period IN ('seconds', 'minutes', 'hours', 'days')",
            name="ck__alpaca_connectors__etf_signal_job_configuration_701748c3a5",
        ),
        sa.CheckConstraint(
            "(schedule_type = 'interval' AND schedule_every IS NOT NULL "
            "AND schedule_every > 0 AND schedule_period IS NOT NULL "
            "AND schedule_expression IS NULL) OR "
            "(schedule_type = 'crontab' AND schedule_every IS NULL "
            "AND schedule_period IS NULL AND schedule_expression IS NOT NULL)",
            name="ck__alpaca_connectors__etf_signal_job_configuration_871f2fa48b",
        ),
        sa.CheckConstraint(
            "schedule_type IN ('interval', 'crontab')",
            name="ck__alpaca_connectors__etf_signal_job_configuration_40b87e34e1",
        ),
        sa.ForeignKeyConstraint(
            ["account_uid"],
            ["ms_markets__account.uid"],
            name="fk__alpaca_connectors__etf_signal_job_configuration_6bfe1585f8",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["universe_uid"],
            ["alpaca_connectors__asset_universe.uid"],
            name="fk__alpaca_connectors__etf_signal_job_configuration_272f8dcd06",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "uid",
            name=op.f("pk__alpaca_connectors__etf_signal_job_configuration"),
        ),
        info={
            "namespace": "mainsequence.markets",
            "identifier": "AlpacaETFSignalJobConfiguration",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, column, unique in (
        ("ix__alpaca_connectors__etf_signal_job_configuration__name", "name", False),
        (
            "uix__alpaca_connectors__etf_signal_job_configuration_f4a84bcf45",
            "universe_uid",
            True,
        ),
        (
            "ix__alpaca_connectors__etf_signal_job_configuration_11eac71748",
            "account_uid",
            False,
        ),
        (
            "uix__alpaca_connectors__etf_signal_job_configuration__job_uid",
            "job_uid",
            True,
        ),
        ("ix__alpaca_connectors__etf_signal_job_configuration__enabled", "enabled", False),
        (
            "ix__alpaca_connectors__etf_signal_job_configuration_e46c395bf8",
            "lifecycle_state",
            False,
        ),
    ):
        op.create_index(
            name,
            "alpaca_connectors__etf_signal_job_configuration",
            [column],
            unique=unique,
        )


def downgrade() -> None:
    for name in (
        "ix__alpaca_connectors__etf_signal_job_configuration_e46c395bf8",
        "ix__alpaca_connectors__etf_signal_job_configuration__enabled",
        "uix__alpaca_connectors__etf_signal_job_configuration__job_uid",
        "ix__alpaca_connectors__etf_signal_job_configuration_11eac71748",
        "uix__alpaca_connectors__etf_signal_job_configuration_f4a84bcf45",
        "ix__alpaca_connectors__etf_signal_job_configuration__name",
    ):
        op.drop_index(
            name,
            table_name="alpaca_connectors__etf_signal_job_configuration",
        )
    op.drop_table("alpaca_connectors__etf_signal_job_configuration")
