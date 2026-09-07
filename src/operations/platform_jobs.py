"""Strict Main Sequence Job projection including timezone-aware crontab responses.

The backend schedule contract includes ``timezone`` and ``timezone_explicit`` on
crontab schedules.  Main Sequence SDK 8.1.0 accepts those fields on writes but
its response model does not yet declare them, so the base Pydantic model drops
them.  This project-level projection keeps the public ``Job`` transport and
endpoint while preserving the backend-owned response fields.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from mainsequence.client import Job
from mainsequence.client.models_helpers import (
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
)


class TimezoneAwareCrontabSchedule(CrontabSchedule):
    """Canonical backend crontab response."""

    timezone: str
    timezone_explicit: bool


class TimezoneAwarePeriodicTask(PeriodicTask):
    """Periodic task whose crontab branch preserves timezone metadata."""

    schedule: IntervalSchedule | TimezoneAwareCrontabSchedule | None = Field(
        default=None,
        description="Nested interval or timezone-aware crontab schedule.",
    )


class PlatformJob(Job):
    """Job using the current backend response contract at the public Job endpoint."""

    CLASS_NAME: ClassVar[str] = "Job"
    task_schedule: TimezoneAwarePeriodicTask | None = Field(
        default=None,
        description="Nested periodic task returned by the backend.",
    )


__all__ = [
    "PlatformJob",
    "TimezoneAwareCrontabSchedule",
    "TimezoneAwarePeriodicTask",
]
