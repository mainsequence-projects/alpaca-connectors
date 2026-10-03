"""Schema gate for Job launchers (ADR 0012).

A Job can start on a new image before the deployment's migration Job has applied that image's
revisions. Jobs the API creates at runtime are outside the deployment workflow, so nothing orders
them after the migration. Every launcher therefore calls :func:`skip_if_schema_behind` before it
bootstraps a runtime or touches any table: when the database is behind the code for any provider
whose tables this image reads, the run ends cleanly without side effects and the next scheduled run
does the work.

Applied revisions are read from each provider's Alembic version MetaTable with an ordinary
governed SELECT, so the gate needs read access only and never takes the migration lock.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

# Providers this application's migration Job applies. ms-markets 2.1.1: only the ms-markets
# deployment's `migrate-markets` Job migrates the ms-markets schema.
MIGRATION_PROVIDERS = ("alpaca_connectors.migrations:migration",)

# Providers whose schema this image's code reads, in dependency order. The gate also waits for the
# ms-markets deployment to reach the version this image pins.
GATED_PROVIDERS = ("msm_migrations:migration", *MIGRATION_PROVIDERS)


@dataclass(frozen=True, slots=True)
class ProviderSchemaState:
    """One provider's applied revisions compared with the head revision this image ships."""

    provider: str
    applied: tuple[str, ...]
    code_head: str
    behind: bool


@dataclass(frozen=True, slots=True)
class SchemaState:
    providers: tuple[ProviderSchemaState, ...]

    @property
    def behind(self) -> bool:
        return any(provider.behind for provider in self.providers)


def load_provider(reference: str) -> Any:
    from metatables.migrations import load_alembic_metatable_migration_provider

    return load_alembic_metatable_migration_provider(reference)


def script_directory(migration: Any) -> Any:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", migration.script_location)
    config.set_main_option("version_locations", " ".join(migration.version_locations))
    return ScriptDirectory.from_config(config)


def code_head_revision(script: Any) -> str:
    """Return the single head revision a provider ships in this image."""
    heads = tuple(script.get_heads())
    if len(heads) != 1:
        raise RuntimeError(f"Expected one migration head, found {heads!r}.")
    return heads[0]


def applied_revisions(migration: Any) -> tuple[str, ...]:
    """Read a provider's applied revisions from its version MetaTable.

    The catalog reserves the version table before the first migration and activates it once
    Alembic has created it. A missing or merely reserved registration means the provider has never
    completed a migration on this database, so no revision is applied.
    """
    import sqlalchemy as sa
    from msm.api.base import operation_result_rows

    from metatables import MetaTable
    from metatables.compiled_sql.v1 import compile_sqlalchemy_statement

    registry = migration.alembic_registry
    registrations = MetaTable.filter(identifier=registry.__metatable_identifier__)
    if not any(
        getattr(registration, "provisioning_status", None) == "active"
        for registration in registrations
    ):
        return ()

    column_name = registry.__alembic_version_column_name__
    version_table = sa.Table(
        migration.version_table,
        sa.MetaData(),
        sa.Column(column_name, sa.String(32), primary_key=True),
        schema=migration.version_table_schema,
    )
    operation = compile_sqlalchemy_statement(
        sa.select(version_table.c[column_name]),
        operation="select",
    )
    rows = operation_result_rows(MetaTable.execute_operation(operation))
    return tuple(str(row[column_name]) for row in rows)


def _is_behind(script: Any, applied: tuple[str, ...], code_head: str) -> bool:
    from alembic.script.revision import ResolutionError
    from alembic.util import CommandError

    if code_head in applied:
        return False
    if not applied:
        return True
    for revision in applied:
        try:
            script.get_revision(revision)
        except (CommandError, ResolutionError):
            # A revision this image does not know: a newer release already migrated the database.
            # Revisions only add, so this older code still runs safely against it.
            return False
    return True


def schema_state(
    providers: Iterable[str] = GATED_PROVIDERS,
    *,
    read_applied: Callable[[Any], tuple[str, ...]] = applied_revisions,
) -> SchemaState:
    """Compare each provider's applied revisions with the head revision this image ships."""
    states = []
    for reference in providers:
        migration = load_provider(reference)
        script = script_directory(migration)
        code_head = code_head_revision(script)
        applied = tuple(read_applied(migration))
        states.append(
            ProviderSchemaState(
                provider=reference,
                applied=applied,
                code_head=code_head,
                behind=_is_behind(script, applied, code_head),
            )
        )
    return SchemaState(providers=tuple(states))


def skip_if_schema_behind(
    job_label: str,
    *,
    read_state: Callable[[], SchemaState] = schema_state,
    emit: Callable[[str], None] = print,
) -> bool:
    """Return ``True`` (after reporting why) when this run must end without doing any work."""
    state = read_state()
    if not state.behind:
        return False
    details = "; ".join(
        f"{provider.provider} at {', '.join(provider.applied) or 'none'}, code at "
        f"{provider.code_head}"
        for provider in state.providers
        if provider.behind
    )
    emit(
        f"{job_label}: skipped; the database schema is behind this image ({details}). The "
        "deployment's migration Job has not run yet; the next scheduled run will do the work."
    )
    return True


__all__ = [
    "GATED_PROVIDERS",
    "MIGRATION_PROVIDERS",
    "ProviderSchemaState",
    "SchemaState",
    "applied_revisions",
    "code_head_revision",
    "load_provider",
    "schema_state",
    "script_directory",
    "skip_if_schema_behind",
]
