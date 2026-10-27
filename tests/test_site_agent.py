"""The project configuration names this branch's Alpaca Agent and its Environment.

The Alpaca site reads `GET /v1/project-state/configuration` on page load and opens its conversation
with the `agent_uid` and `organization_environment_uid` it returns. The API finds the Agent when the
request arrives, as the CRM API does: the card's name, checked against the Environment and branch
that the SDK context resolves, with a deployed runtime release.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alpaca_connectors.api.app import site_agent
from alpaca_connectors.api.app.services import project_state
from alpaca_connectors.api.app.site_agent import SiteAgent, resolve_site_agent
from mainsequence.client.agent_runtime_models import Agent
from mainsequence.code_repository_context import CodeRepositoryEnvironmentContextRequiredError

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_UID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BRANCH_UID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
AGENT_UID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
RELEASE_UID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OTHER_UID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _agent(**changes: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "uid": AGENT_UID,
        "name": "Alpaca Agent",
        "organization_environment_uid": ENVIRONMENT_UID,
        "code_repository_branch_uid": BRANCH_UID,
        "runtime_release_uid": RELEASE_UID,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The SDK context and the Agent listing the lookup reads; nothing reaches a backend."""

    state = SimpleNamespace(
        agents=[],
        filters=[],
        operations=[],
        environment=ENVIRONMENT_UID,
        branch=BRANCH_UID,
        error=None,
    )

    def environment(operation: str) -> str:
        state.operations.append(operation)
        if isinstance(state.environment, Exception):
            raise state.environment
        return state.environment

    def branch(operation: str) -> str:
        state.operations.append(operation)
        return state.branch

    def agents(cls, **filters: object) -> list[SimpleNamespace]:
        state.filters.append(filters)
        if state.error is not None:
            raise state.error
        return list(state.agents)

    monkeypatch.setattr(
        "mainsequence.code_repository_context.resolve_organization_environment_uid", environment
    )
    monkeypatch.setattr(
        "mainsequence.code_repository_context.resolve_code_repository_branch_uid", branch
    )
    monkeypatch.setattr(Agent, "filter", classmethod(agents))
    return state


def test_the_lookup_searches_for_the_name_the_agent_is_deployed_under() -> None:
    card = json.loads((REPO_ROOT / ".agents" / "agent_card.json").read_text(encoding="utf-8"))

    assert site_agent.CARD_PATH == REPO_ROOT / ".agents" / "agent_card.json"
    assert site_agent.agent_card_name() == card["name"] == "Alpaca Agent"


def test_the_branchs_agent_is_returned_with_its_environment(platform: SimpleNamespace) -> None:
    platform.agents = [
        _agent(uid=OTHER_UID, organization_environment_uid=OTHER_UID),  # another Environment
        _agent(uid=OTHER_UID, code_repository_branch_uid=OTHER_UID),  # another repository's branch
        _agent(uid=AGENT_UID.upper(), organization_environment_uid=ENVIRONMENT_UID.upper()),
    ]

    assert resolve_site_agent() == SiteAgent(
        agent_uid=AGENT_UID, organization_environment_uid=ENVIRONMENT_UID
    )
    assert platform.filters == [{"name": "Alpaca Agent"}]
    assert platform.operations == ["Alpaca Agent lookup", "Alpaca Agent lookup"]


def test_a_platform_agent_read_without_the_retired_runtime_update_is_returned(
    platform: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The platform no longer returns runtime_update; Main Sequence 9.0.30 and earlier required it.
    card = json.loads((REPO_ROOT / ".agents" / "agent_card.json").read_text(encoding="utf-8"))
    read = {
        "uid": AGENT_UID,
        "name": card["name"],
        "description": card["description"],
        "agent_card": card,
        "llm_thinking": "low",
        "repository_branch": "main",
        "organization_environment_uid": ENVIRONMENT_UID,
        "organization_environment_name": "Production",
        "code_repository_branch_uid": BRANCH_UID,
        "runtime_release_uid": RELEASE_UID,
        "orm_class": "Agent",
    }
    monkeypatch.setattr(
        Agent, "filter", classmethod(lambda cls, **filters: [cls.model_validate(read)])
    )

    assert resolve_site_agent() == SiteAgent(
        agent_uid=AGENT_UID, organization_environment_uid=ENVIRONMENT_UID
    )


@pytest.mark.parametrize(
    "agents",
    [
        pytest.param([], id="no-agent"),
        pytest.param([_agent(runtime_release_uid=None)], id="not-deployed"),
        pytest.param([_agent(name="Alpaca agent")], id="another-name"),
        pytest.param([_agent(organization_environment_uid=None)], id="no-environment"),
        pytest.param([_agent(), _agent(uid=OTHER_UID)], id="ambiguous"),
        pytest.param([_agent(uid="not-a-uid")], id="invalid-uid"),
    ],
)
def test_an_agent_that_cannot_be_named_exactly_is_not_returned(
    platform: SimpleNamespace, agents: list[SimpleNamespace]
) -> None:
    platform.agents = agents

    assert resolve_site_agent() is None


def test_a_branch_without_an_environment_has_no_agent(platform: SimpleNamespace) -> None:
    platform.environment = CodeRepositoryEnvironmentContextRequiredError("no Environment")

    assert resolve_site_agent() is None
    assert platform.filters == []


def test_a_failed_lookup_answers_no_agent_and_logs_no_details(
    platform: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    platform.error = RuntimeError("GET /api/v1/agents/ failed: internal detail")

    with caplog.at_level(logging.WARNING, logger=site_agent.__name__):
        assert resolve_site_agent() is None

    assert "RuntimeError" in caplog.text
    assert "internal detail" not in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(None, id="missing"),
        pytest.param("not json", id="not-json"),
        pytest.param('{"name": "", "description": "No name"}', id="empty-name"),
        pytest.param('{"name": "Alpaca Agent"}', id="no-description"),
    ],
)
def test_an_invalid_card_skips_the_platform_lookup(
    platform: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str | None,
) -> None:
    card = tmp_path / "agent_card.json"
    if content is not None:
        card.write_text(content, encoding="utf-8")
    monkeypatch.setattr(site_agent, "CARD_PATH", card)
    platform.agents = [_agent()]

    assert resolve_site_agent() is None
    assert platform.filters == []


@pytest.fixture
def project_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configuration's MetaTables reads and branch context, without a backend."""

    monkeypatch.setattr(project_state, "list_account_registrations", lambda limit: ([], 2))
    monkeypatch.setattr(project_state, "list_universe_sources", lambda limit: ([], 1))
    monkeypatch.setattr(project_state, "list_market_data_datasets", lambda: [])
    context = SimpleNamespace(
        organization_environment_uid=ENVIRONMENT_UID,
        code_repository_branch=SimpleNamespace(organization_environment_name="Production"),
    )
    monkeypatch.setattr(
        "mainsequence.code_repository_context.get_code_repository_context", lambda: context
    )


def test_the_configuration_returns_the_agent_and_its_environment(
    project_reads: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = SiteAgent(agent_uid=AGENT_UID, organization_environment_uid=ENVIRONMENT_UID)
    monkeypatch.setattr(project_state, "resolve_site_agent", lambda: found)

    configuration = project_state.get_project_configuration(request_user_uid=OTHER_UID)

    assert configuration.agent_uid == AGENT_UID
    assert configuration.organization_environment_uid == ENVIRONMENT_UID
    assert configuration.organization_environment_name == "Production"
    assert configuration.request_user_uid == OTHER_UID
    assert configuration.registered_account_count == 2


def test_the_configuration_without_an_agent_keeps_the_branchs_environment(
    project_reads: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_state, "resolve_site_agent", lambda: None)

    configuration = project_state.get_project_configuration()

    assert configuration.agent_uid is None
    assert configuration.organization_environment_uid == ENVIRONMENT_UID


def test_the_site_reads_both_from_the_configuration_route(project_reads: None, api_client) -> None:
    found = SiteAgent(agent_uid=AGENT_UID, organization_environment_uid=ENVIRONMENT_UID)
    with patch.object(project_state, "resolve_site_agent", return_value=found) as lookup:
        response = api_client.get("/v1/project-state/configuration")

    assert response.status_code == 200
    body = response.json()
    assert (body["agent_uid"], body["organization_environment_uid"]) == (
        AGENT_UID,
        ENVIRONMENT_UID,
    )
    # Found when the request arrives, not when the application starts.
    lookup.assert_called_once_with()
