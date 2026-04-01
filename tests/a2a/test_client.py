"""Tests for A2A client — discover, delegate, directory."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agos.a2a.client import A2AClient, A2ADirectory
from agos.a2a.models import (
    A2AMessage,
    A2APart,
    AgentCard,
    AgentSkill,
)


class TestA2AClient:
    @pytest.mark.asyncio
    async def test_discover_parses_card(self):
        card_data = AgentCard(
            name="RemoteAgent",
            url="http://remote:9000",
            skills=[AgentSkill(id="s1", name="Skill One", tags=["test"])],
        ).model_dump(by_alias=True)

        mock_response = MagicMock()
        mock_response.json.return_value = card_data
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("agos.a2a.client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            client = A2AClient()
            card = await client.discover("http://remote:9000")
            assert card.name == "RemoteAgent"
            assert len(card.skills) == 1

    @pytest.mark.asyncio
    async def test_send_message(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0", "id": 1,
            "result": {"taskId": "abc123", "status": {"state": "working"}},
        }
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("agos.a2a.client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            client = A2AClient()
            msg = A2AMessage(parts=[A2APart(text="do something")])
            result = await client.send_message("http://remote:9000", msg)
            assert result["taskId"] == "abc123"

    @pytest.mark.asyncio
    async def test_rpc_error_raises(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32000, "message": "Something went wrong"},
        }
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("agos.a2a.client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            client = A2AClient()
            with pytest.raises(RuntimeError, match="Something went wrong"):
                await client.get_task("http://remote:9000", "task1")


class TestA2ADirectory:
    def test_empty_directory(self):
        d = A2ADirectory()
        assert d.list_agents() == []

    def test_find_by_skill_no_agents(self):
        d = A2ADirectory()
        results = d.find_by_skill(["monitoring"])
        assert results == []

    def test_find_by_skill_matches_tags(self):
        d = A2ADirectory()
        card = AgentCard(
            name="MonitorBot",
            url="http://monitor:9000",
            skills=[
                AgentSkill(
                    id="uptime",
                    name="Uptime Monitor",
                    description="Monitors website uptime",
                    tags=["monitoring", "uptime", "alerting"],
                    examples=["monitor my website"],
                ),
            ],
        )
        d._agents["http://monitor:9000"] = card

        results = d.find_by_skill(["monitoring"])
        assert len(results) == 1
        assert results[0][0].name == "MonitorBot"

    def test_find_by_skill_matches_examples(self):
        d = A2ADirectory()
        card = AgentCard(
            name="Reviewer",
            url="http://review:9000",
            skills=[
                AgentSkill(
                    id="review",
                    name="Code Review",
                    tags=["code"],
                    examples=["review this pull request"],
                ),
            ],
        )
        d._agents["http://review:9000"] = card

        results = d.find_by_skill(["pull request"])
        assert len(results) == 1

    def test_find_by_skill_ranks_by_score(self):
        d = A2ADirectory()
        # Agent with many matching tags
        card1 = AgentCard(
            name="SpecialistBot",
            url="http://s:9000",
            skills=[AgentSkill(
                id="s1", name="Full Stack Monitor",
                tags=["monitoring", "uptime", "alerting", "devops"],
            )],
        )
        # Agent with one matching tag
        card2 = AgentCard(
            name="GeneralBot",
            url="http://g:9000",
            skills=[AgentSkill(
                id="s2", name="General",
                tags=["monitoring"],
            )],
        )
        d._agents["http://s:9000"] = card1
        d._agents["http://g:9000"] = card2

        results = d.find_by_skill(["monitoring", "uptime"])
        assert len(results) == 2
        assert results[0][0].name == "SpecialistBot"  # higher score

    def test_list_agents(self):
        d = A2ADirectory()
        d._agents["http://a:9000"] = AgentCard(
            name="AgentA", url="http://a:9000",
            skills=[AgentSkill(id="s", name="S")],
        )
        agents = d.list_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "AgentA"
        assert agents[0]["url"] == "http://a:9000"

    def test_persistence_save_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a2a_dir.json"

            # Save
            d1 = A2ADirectory(state_path=path)
            d1._agents["http://a:9000"] = AgentCard(
                name="Persisted",
                url="http://a:9000",
                skills=[AgentSkill(id="p", name="P", tags=["test"])],
            )
            d1._save()
            assert path.exists()

            # Load
            d2 = A2ADirectory(state_path=path)
            assert len(d2._agents) == 1
            assert d2._agents["http://a:9000"].name == "Persisted"

    def test_unregister(self):
        d = A2ADirectory()
        d._agents["http://x:9000"] = AgentCard(name="X", url="http://x:9000")
        d.unregister("http://x:9000")
        assert len(d._agents) == 0
