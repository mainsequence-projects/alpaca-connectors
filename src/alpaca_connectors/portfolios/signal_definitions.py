"""Signal definitions: the one kind of signal this connector makes (ADR 0028, decision 5).

ms-markets' signal belongs to nobody, and any ``Portfolio`` may follow any signal. This connector
builds one kind of portfolio (ADR 0027), so it makes one kind of signal, and only for an account:

- A definition belongs to one account (``CASCADE``) and is created by the account's owner. The
  application gives its key, unique within the account.
- Its signal is ms-markets' ``ApplicationWeights`` with provider ``alpaca-connectors`` and the key
  ``signal_key``: the account's UID with the application's key, so two accounts never produce the
  same signal. A definition adopted from a portfolio created before ADR 0028 keeps that
  portfolio's configuration UID as its key (decision 9), so its signal and history continue.
- Only the account's portfolios follow it: the portfolio configuration's
  ``(signal_definition_uid, account_uid)`` references ``(uid, account_uid)`` here.
- Its rows (the signal-asset rows here, and in Markets its observations and ``SignalMetadata``,
  which ``end_signal`` deletes) are deleted with it, once no ``Portfolio`` follows the signal
  (ADR 0031, decision 7).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from msm.api.base import MarketsMetaTableRow
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from msm.models import AccountTable
from pydantic import ConfigDict
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

UTC = dt.timezone.utc
SIGNAL_KEY_SEPARATOR = "/"
_UNSET_DESCRIPTION = object()


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class SignalDefinitionTable(ProjectStorageNameMixin, AlpacaMarketsMetaTableMixin, MarketsBase):
    """One signal an account's application provides weights for (ADR 0028, decision 5)."""

    __project_storage_concept__ = "signal_definition"
    __markets_base_identifier__ = "AlpacaSignalDefinition"
    __metatable_description__ = (
        "One row per weights signal an application provides for one registered Alpaca account. "
        "The application's own key for the stream of weights, unique within the account, and the "
        "key of the Main Sequence signal it publishes to; only the account's portfolios follow "
        "it, and it is deleted with the account; created by the account's owner through the "
        "Alpaca Connectors application."
    )
    __metatable_labels__ = ["signals", "portfolios", "configuration", "alpaca"]
    __table_args__ = markets_table_args(
        "AlpacaSignalDefinition",
        CheckConstraint("length(trim(key)) > 0", name="ck_signal_definition_key"),
        UniqueConstraint("account_uid", "key", name="uq_signal_definition_account_key"),
        UniqueConstraint("uid", "account_uid", name="uq_signal_definition_uid_account"),
        Index(None, "account_uid"),
        Index(None, "signal_key", unique=True),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Signal definition UID",
            "description": "Public UUID that identifies the signal definition.",
        },
    )
    account_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AccountTable.__table__.fullname}.uid", ondelete="CASCADE"),
        nullable=False,
        info={
            "label": "Account UID",
            "description": (
                "Main Sequence account UID of the registered Alpaca brokerage account the signal "
                "belongs to; the signal is deleted with the account."
            ),
        },
    )
    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Key",
            "description": (
                "The application's own stable key for this stream of weights, unique within the "
                "account."
            ),
        },
    )
    signal_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        info={
            "label": "Signal key",
            "description": (
                "Key of the Main Sequence application-weights signal the definition publishes to: "
                "the account UID and the application's key, or the configuration UID of a "
                "portfolio created before signal definitions existed."
            ),
        },
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={"label": "Name", "description": "Display name of the signal."},
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={
            "label": "Description",
            "description": "Free-text description of the signal; null when none was given.",
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created at", "description": "Time, in UTC, the definition was created."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={"label": "Updated at", "description": "Time, in UTC, the definition last changed."},
    )


class SignalDefinition(MarketsMetaTableRow):
    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[SignalDefinitionTable]] = SignalDefinitionTable
    __required_tables__: ClassVar[list[type[MarketsBase]]] = [SignalDefinitionTable]

    account_uid: uuid.UUID
    key: str
    signal_key: str
    name: str
    description: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


class SignalDefinitionError(Exception):
    """A refusal with a stable code; the message never quotes a key or a name."""

    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def account_signal_key(account_uid: Any, key: str) -> str:
    """The ms-markets key of a new definition's signal: the account's UID with its key."""
    return f"{uuid.UUID(str(account_uid))}{SIGNAL_KEY_SEPARATOR}{key}"


def legacy_signal_key(configuration_uid: Any) -> str:
    """The key of the signal a portfolio created before ADR 0028 publishes to: its own UID."""
    return str(uuid.UUID(str(configuration_uid)))


def signal_configuration(signal_key: str) -> Any:
    from msm_portfolios.contrib.signals import ApplicationWeightsConfig

    from alpaca_connectors.settings import PROJECT_NAMESPACE

    return ApplicationWeightsConfig(provider=PROJECT_NAMESPACE, key=str(signal_key))


def weights_signal(signal_key: str) -> Any:
    """The ``ApplicationWeights`` signal of a key, on Markets' published series (ADR 0031).

    No output table: the signal's weights are Markets' own rows, and this application keeps no
    table of them.
    """
    from msm_portfolios.contrib.signals import ApplicationWeights

    return ApplicationWeights.from_signal_configuration(signal_configuration(signal_key))


class _SignalIdentity:
    """The payload ``ApplicationWeights`` hashes into its signal UID, without an updater.

    Building the updater binds its output table to the backend catalog; a UID is a pure hash of
    the class path and the configuration, so listings and the bars resolution compute it here.
    """

    def __init__(self, signal_key: str) -> None:
        self._configuration = signal_configuration(signal_key)

    def build_configuration(self) -> dict[str, Any]:
        from msm_portfolios.contrib.signals import ApplicationWeights
        from msm_portfolios.time_index.signals.weights import _class_import_path

        return {
            "table_updater_class_import_path": _class_import_path(ApplicationWeights),
            "config": self._configuration,
        }


def signal_uid_for_key(signal_key: str) -> str:
    """The UID of the signal published under ``signal_key``."""
    from msm_portfolios.time_index.signals.weights import compute_signal_uid

    return str(compute_signal_uid(_SignalIdentity(signal_key)))


def definition_signal_uid(definition: SignalDefinition) -> str:
    return signal_uid_for_key(definition.signal_key)


def normalize_key(value: Any) -> str:
    key = str(value or "")
    if not key.strip():
        raise SignalDefinitionError("invalid_signal_key", "The signal key must not be empty.")
    if key != key.strip():
        raise SignalDefinitionError(
            "invalid_signal_key", "The signal key must not start or end with spaces."
        )
    if len(key) > 255:
        raise SignalDefinitionError(
            "invalid_signal_key", "The signal key must be at most 255 characters."
        )
    return key


def _name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise SignalDefinitionError("invalid_signal_name", "The signal name must not be empty.")
    if len(name) > 255:
        raise SignalDefinitionError(
            "invalid_signal_name", "The signal name must be at most 255 characters."
        )
    return name


def _description(value: Any) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _start_runtime() -> None:
    from alpaca_connectors.runtime import application_runtime_models, start_markets_engine

    start_markets_engine(models=application_runtime_models())


def get_signal_definition(definition_uid: Any) -> SignalDefinition | None:
    _start_runtime()
    return SignalDefinition.get_by_uid(definition_uid)


def signal_definitions_by_uids(definition_uids: Any) -> dict[str, SignalDefinition]:
    """The definitions of the given UIDs, in requests of at most ``MARKETS_BATCH_SIZE`` keys."""
    from alpaca_connectors.markets_api import read_in_batches

    uids = sorted({str(uuid.UUID(str(uid))) for uid in definition_uids if uid is not None})
    if not uids:
        return {}
    _start_runtime()
    rows = read_in_batches(
        lambda batch: SignalDefinition.filter(uid__in=batch, limit=len(batch)), uids
    )
    return {str(row.uid): row for row in rows}


def account_signal_definition(account_uid: Any, key: str) -> SignalDefinition | None:
    _start_runtime()
    rows = SignalDefinition.filter(
        account_uid=uuid.UUID(str(account_uid)), key=normalize_key(key), limit=1
    )
    return rows[0] if rows else None


def list_signal_definitions(
    *,
    account_uids: Any = None,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    uids: Any = None,
) -> tuple[list[SignalDefinition], int]:
    """A page of definitions, optionally of the given accounts or ``uids`` only, by name."""
    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    table = SignalDefinitionTable
    _start_runtime()
    runtime = resolve_runtime(models=[table], row_model_name="SignalDefinition")
    statement = select(table)
    if account_uids is not None:
        accounts = [uuid.UUID(str(uid)) for uid in account_uids]
        if not accounts:
            return [], 0
        statement = statement.where(table.account_uid.in_(accounts))
    if uids is not None:
        statement = statement.where(table.uid.in_([uuid.UUID(str(uid)) for uid in uids]))
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(or_(table.name.ilike(pattern), table.key.ilike(pattern)))
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    page = compile_markets_statement(
        statement.order_by(table.name.asc(), table.uid.asc()).limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
    )
    count = compile_markets_statement(count_statement, context=runtime.context, operation="select")
    rows = [
        SignalDefinition.model_validate(row)
        for row in operation_result_rows(execute_markets_operation(page, context=runtime.context))
    ]
    count_rows = operation_result_rows(execute_markets_operation(count, context=runtime.context))
    return rows, int(count_rows[0].get("count", 0)) if count_rows else 0


def _require_signal_free(signal_key: str) -> None:
    """Refuse a key whose signal still holds observations: a kept signal is never reused."""
    from alpaca_connectors.portfolios.signal_observations import read_signal_observation_bounds

    bounds = read_signal_observation_bounds(signal_uid=signal_uid_for_key(signal_key))
    if bounds.last_observation_at is not None:
        raise SignalDefinitionError(
            "signal_key_taken",
            "A retired portfolio still follows the signal of this key; give another key.",
            status_code=409,
        )


def create_signal_definition(
    *,
    account_uid: Any,
    key: Any,
    name: Any,
    description: Any = None,
    signal_key: str | None = None,
    key_checked: bool = False,
) -> SignalDefinition:
    """Store a definition of the account; the caller has checked the account's owner.

    ``signal_key`` is given only for a portfolio created before ADR 0028, whose signal keeps its
    configuration UID as its key (decision 9). ``key_checked`` is a portfolio create's, which has
    just read that the account has no definition with this key, or made the key from a new UID:
    the key is not read again, and the account's unique key refuses a concurrent one (#46).
    """
    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    account = uuid.UUID(str(account_uid))
    normalized_key = normalize_key(key)
    values = {
        "uid": new_markets_uid(),
        "account_uid": account,
        "key": normalized_key,
        "signal_key": signal_key or account_signal_key(account, normalized_key),
        "name": _name(name),
        "description": _description(description),
    }
    _start_runtime()
    if not key_checked and account_signal_definition(account, normalized_key) is not None:
        raise SignalDefinitionError(
            "signal_key_exists",
            "This account already has a signal with this key.",
            status_code=409,
        )
    if signal_key is None:
        _require_signal_free(values["signal_key"])
    now = utc_now()
    runtime = resolve_runtime(models=[SignalDefinitionTable], row_model_name="SignalDefinition")
    result = create_model(
        runtime.context,
        model=SignalDefinitionTable,
        values={**values, "created_at": now, "updated_at": now},
    )
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Signal definition create returned no row.")
    return SignalDefinition.model_validate(rows[0])


def update_signal_definition(
    definition_uid: Any,
    *,
    name: Any = None,
    description: Any = _UNSET_DESCRIPTION,
) -> SignalDefinition:
    """Change the name or the description; the key and the account never change."""
    if get_signal_definition(definition_uid) is None:
        raise LookupError(f"Signal definition {definition_uid!s} does not exist.")
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = _name(name)
    if description is not _UNSET_DESCRIPTION:
        values["description"] = _description(description)
    if not values:
        raise ValueError("At least one signal definition field must be provided.")
    return SignalDefinition.update(definition_uid, {**values, "updated_at": utc_now()})


def signal_kept_by_ended_portfolio(signal_uid: str) -> bool:
    """Whether a retired ``Portfolio`` of a deleted portfolio still follows the signal."""
    from alpaca_connectors.portfolios.configurations import ended_signal_uids

    return str(signal_uid) in ended_signal_uids([signal_uid])


def delete_signal_rows(signal_uid: str) -> str:
    """Delete a signal's rows unless a retired ``Portfolio`` still follows it.

    The signal-asset rows here, then its weights and ``SignalMetadata`` in Markets with
    ``end_signal``, which Markets refuses while a ``Portfolio`` follows the signal (ADR 0028,
    decision 5; ADR 0031, decision 7). Answers ``"deleted"`` or ``"kept"``.
    """
    return delete_signals_rows([signal_uid])[str(signal_uid)]


def delete_signals_rows(signal_uids: Any) -> dict[str, str]:
    """``delete_signal_rows`` for several signals, set-based (issue #46).

    One read of which signals a retired ``Portfolio`` keeps; for the others one delete of their
    signal-asset rows, one Markets read of their ``SignalMetadata``, then ``end_signal`` for each
    registered one, which Markets runs one signal at a time. Answers each signal's
    ``"deleted"`` or ``"kept"``.
    """
    from alpaca_connectors.markets_api import end_signals
    from alpaca_connectors.portfolios.configurations import ended_signal_uids
    from alpaca_connectors.portfolios.signal_assets import forget_signals_assets

    values = list(dict.fromkeys(str(uid) for uid in signal_uids))
    if not values:
        return {}
    kept = ended_signal_uids(values)
    free = [uid for uid in values if uid not in kept]
    if free:
        forget_signals_assets(free)
        end_signals(free)
    return {uid: "kept" if uid in kept else "deleted" for uid in values}


def end_signal_definitions(definitions: Any) -> dict[str, dict[str, Any]]:
    """Delete, set-based, the definitions no portfolio follows any more (issue #38, #46).

    One grouped read of their followers, one delete of the free ones, then their signals' rows
    as ``delete_signals_rows`` decides. Answers each definition's ``{"state", "signal"}``:
    ``followed`` (kept), or ``deleted`` with its signal's ``{"uid", "state"}``.
    """
    from alpaca_connectors.portfolios.configurations import (
        delete_rows_in,
        portfolio_counts_by_definition,
    )

    by_uid = {str(definition.uid): definition for definition in definitions}
    if not by_uid:
        return {}
    followed = portfolio_counts_by_definition(list(by_uid))
    free = [definition for uid, definition in by_uid.items() if not followed.get(uid)]
    delete_rows_in(SignalDefinitionTable, "uid", [definition.uid for definition in free])
    signals = delete_signals_rows([definition_signal_uid(definition) for definition in free])
    answers: dict[str, dict[str, Any]] = {
        uid: {"state": "followed", "signal": None} for uid in by_uid if followed.get(uid)
    }
    for definition in free:
        signal_uid = definition_signal_uid(definition)
        answers[str(definition.uid)] = {
            "state": "deleted",
            "signal": {"uid": signal_uid, "state": signals[signal_uid]},
        }
    return answers


def delete_signal_definition(definition_uid: Any) -> dict[str, Any]:
    """Delete a definition no portfolio follows, and its signal's rows when nothing keeps them.

    The caller has checked the account's owner.
    """
    from alpaca_connectors.portfolios.configurations import AlpacaPortfolioConfiguration

    definition = get_signal_definition(definition_uid)
    if definition is None:
        raise LookupError(f"Signal definition {definition_uid!s} does not exist.")
    if AlpacaPortfolioConfiguration.filter(signal_definition_uid=definition.uid, limit=1):
        raise SignalDefinitionError(
            "signal_followed",
            "Portfolios follow this signal; delete them first.",
            status_code=409,
        )
    signal_uid = definition_signal_uid(definition)
    SignalDefinition.delete(definition.uid)
    return {
        "uid": str(definition.uid),
        "deleted": True,
        "signal": {"uid": signal_uid, "state": delete_signal_rows(signal_uid)},
    }


def project_signal_definition_models() -> list[type[MarketsBase]]:
    return [SignalDefinitionTable]


__all__ = [
    "SIGNAL_KEY_SEPARATOR",
    "SignalDefinition",
    "SignalDefinitionError",
    "SignalDefinitionTable",
    "account_signal_definition",
    "account_signal_key",
    "create_signal_definition",
    "definition_signal_uid",
    "delete_signal_definition",
    "delete_signal_rows",
    "delete_signals_rows",
    "end_signal_definitions",
    "get_signal_definition",
    "legacy_signal_key",
    "list_signal_definitions",
    "project_signal_definition_models",
    "signal_configuration",
    "signal_definitions_by_uids",
    "signal_kept_by_ended_portfolio",
    "signal_uid_for_key",
    "update_signal_definition",
    "weights_signal",
]
