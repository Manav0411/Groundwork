import pytest
import respx
from httpx import Response

from app.services.llm import OllamaClient, build_answer_prompt


@pytest.mark.asyncio
@respx.mock
async def test_ollama_health_reports_installed_model() -> None:
    client = OllamaClient(base_url="http://ollama.test", model="qwen3:8b")
    respx.get("http://ollama.test/api/tags").mock(
        return_value=Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )

    health = await client.health()

    assert health.available is True
    assert health.installed_models == ["qwen3:8b"]


@pytest.mark.asyncio
@respx.mock
async def test_ollama_generate_returns_chat_message_content() -> None:
    client = OllamaClient(base_url="http://ollama.test", model="qwen3:8b")
    respx.post("http://ollama.test/api/chat").mock(
        return_value=Response(200, json={"message": {"content": "Cited answer [1]."}})
    )

    answer = await client.generate("system", "user")

    assert answer == "Cited answer [1]."


@pytest.mark.asyncio
@respx.mock
async def test_ollama_embed_returns_vectors() -> None:
    client = OllamaClient(base_url="http://ollama.test")
    vector = [0.1] * 768
    respx.post("http://ollama.test/api/embed").mock(
        return_value=Response(200, json={"embeddings": [vector]})
    )

    embeddings = await client.embed(["engineering update"])

    assert embeddings == [vector]


def test_build_answer_prompt_requires_citations() -> None:
    system_prompt, user_prompt = build_answer_prompt("brief", ["[1] jira: blocker"])

    assert "supplied evidence" in system_prompt
    assert "[1] jira: blocker" in user_prompt
