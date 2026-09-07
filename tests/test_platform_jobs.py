from __future__ import annotations

from src.operations.platform_jobs import PlatformJob, TimezoneAwareCrontabSchedule


def test_platform_job_preserves_backend_crontab_timezone_fields() -> None:
    job = PlatformJob.model_validate(
        {
            "uid": "c099820c-8b63-420a-aeb6-9672ae0112cd",
            "name": "Daily portfolio",
            "description": "",
            "code_repository_branch_uid": "945bfddd-5f1f-4541-a87a-faea3af6271f",
            "organization_environment_uid": "18595d12-f92a-4960-84cd-0c6798a1ad25",
            "execution_path": "src/jobs/run_alpaca_etf_portfolio.py",
            "task_schedule": {
                "name": "daily portfolio task",
                "task": "tdag.pod_manager.tasks.run_job_in_celery",
                "schedule": {
                    "type": "crontab",
                    "start_time": None,
                    "expression": "0 20 * * 1-5",
                    "timezone": "UTC",
                    "timezone_explicit": False,
                },
            },
            "cpu_request": "0.25",
            "cpu_limit": "0.25",
            "memory_request": "0.5",
            "memory_limit": "0.5",
            "gpu_request": None,
            "gpu_type": None,
            "spot": False,
            "max_runtime_seconds": 3600,
            "related_image_uid": "4dc88885-48a8-4664-acd5-7a994124c37a",
            "code_repository_commit_hash": "e" * 40,
            "image_status": "ready",
            "automatic_deployment": True,
            "automatic_redeployment_policy": {"tag_regex": None, "policy_revision": 1},
        }
    )

    assert PlatformJob.get_object_url().endswith("/api/v1/jobs")
    assert isinstance(job.task_schedule.schedule, TimezoneAwareCrontabSchedule)
    assert job.task_schedule.schedule.timezone == "UTC"
    assert job.task_schedule.schedule.timezone_explicit is False
