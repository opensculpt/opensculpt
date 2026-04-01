"""Tests for A2A protocol data models."""


from agos.a2a.models import (
    A2AArtifact,
    A2AMessage,
    A2APart,
    A2ATask,
    AgentCard,
    AgentProvider,
    AgentSkill,
    JsonRpcRequest,
    JsonRpcResponse,
    TaskState,
)


class TestAgentSkill:
    def test_basic_skill(self):
        skill = AgentSkill(
            id="code_review",
            name="Code Review",
            description="Reviews code for bugs",
            tags=["review", "code"],
            examples=["review this PR"],
        )
        assert skill.id == "code_review"
        assert skill.tags == ["review", "code"]

    def test_skill_defaults(self):
        skill = AgentSkill(id="test", name="Test")
        assert skill.input_modes == ["text/plain"]
        assert skill.output_modes == ["text/plain"]
        assert skill.tags == []

    def test_skill_serialization(self):
        skill = AgentSkill(id="x", name="X")
        data = skill.model_dump(by_alias=True)
        assert "inputModes" in data
        assert "outputModes" in data


class TestAgentCard:
    def test_basic_card(self):
        card = AgentCard(
            name="TestAgent",
            description="A test agent",
            url="http://localhost:9000",
            skills=[
                AgentSkill(id="s1", name="Skill One", tags=["test"]),
            ],
        )
        assert card.name == "TestAgent"
        assert len(card.skills) == 1
        assert card.version == "1.0.0"

    def test_card_defaults(self):
        card = AgentCard(name="A")
        assert card.provider.organization == "OpenSculpt"
        assert card.capabilities.streaming is False

    def test_card_serialization_roundtrip(self):
        card = AgentCard(
            name="Test",
            url="http://x",
            skills=[AgentSkill(id="s", name="S", tags=["a"])],
        )
        data = card.model_dump(by_alias=True)
        card2 = AgentCard(**data)
        assert card2.name == card.name
        assert card2.skills[0].id == "s"

    def test_card_with_provider(self):
        card = AgentCard(
            name="Test",
            provider=AgentProvider(organization="Acme", url="https://acme.com"),
        )
        assert card.provider.organization == "Acme"


class TestMessage:
    def test_basic_message(self):
        msg = A2AMessage(
            role="user",
            parts=[A2APart(text="Hello")],
        )
        assert msg.role == "user"
        assert msg.parts[0].text == "Hello"
        assert msg.message_id  # auto-generated

    def test_data_part(self):
        part = A2APart(kind="data", data={"key": "value"})
        assert part.kind == "data"
        assert part.data["key"] == "value"


class TestTask:
    def test_task_creation(self):
        task = A2ATask()
        assert task.task_id  # auto-generated
        assert task.context_id
        assert task.status.state == TaskState.WORKING

    def test_task_states(self):
        assert TaskState.COMPLETED.is_terminal
        assert TaskState.FAILED.is_terminal
        assert TaskState.CANCELED.is_terminal
        assert not TaskState.WORKING.is_terminal
        assert not TaskState.INPUT_REQUIRED.is_terminal

    def test_task_with_messages_and_artifacts(self):
        task = A2ATask(
            messages=[A2AMessage(parts=[A2APart(text="do X")])],
            artifacts=[A2AArtifact(name="result", parts=[A2APart(text="done")])],
        )
        assert len(task.messages) == 1
        assert len(task.artifacts) == 1
        assert task.artifacts[0].name == "result"

    def test_task_serialization(self):
        task = A2ATask()
        data = task.model_dump(by_alias=True)
        assert "taskId" in data
        assert "contextId" in data
        task2 = A2ATask(**data)
        assert task2.task_id == task.task_id


class TestJsonRpc:
    def test_request(self):
        req = JsonRpcRequest(method="message/send", id=1, params={"key": "val"})
        assert req.jsonrpc == "2.0"
        assert req.method == "message/send"

    def test_success_response(self):
        resp = JsonRpcResponse.success(1, {"task_id": "abc"})
        assert resp.id == 1
        assert resp.result == {"task_id": "abc"}
        assert resp.error is None

    def test_error_response(self):
        resp = JsonRpcResponse.err(1, -32601, "Method not found")
        assert resp.error["code"] == -32601
        assert resp.error["message"] == "Method not found"
        assert resp.result is None
