import json
from dataclasses import dataclass

import httpx

from app.core.config import settings


class LLMProviderError(RuntimeError):
    """Raised when the configured LLM provider cannot produce a response."""


@dataclass(frozen=True)
class LLMHealth:
    provider: str
    available: bool
    base_url: str
    configured_model: str
    installed_models: list[str]
    error: str | None = None


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds

    async def health(self) -> LLMHealth:
        try:
            installed_models = await self.list_models()
        except Exception as exc:
            return LLMHealth(
                provider="ollama",
                available=False,
                base_url=self.base_url,
                configured_model=self.model,
                installed_models=[],
                error=str(exc),
            )

        return LLMHealth(
            provider="ollama",
            available=self.model in installed_models,
            base_url=self.base_url,
            configured_model=self.model,
            installed_models=installed_models,
            error=None if self.model in installed_models else "Configured model is not installed.",
        )

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()

        return sorted(model["name"] for model in payload.get("models", []))

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        """Generate with Ollama's JSON mode, which constrains decoding to valid JSON.

        Without `format: "json"` a 1B model returns prose around its JSON often enough to be
        unusable as a grader. With it, parsing failure means a genuinely broken response rather
        than the model being chatty, so the caller's fallback stays a real signal.
        """
        payload = {
            "model": model or self.model,
            "stream": False,
            "format": "json",
            # Must match `generate`. This was omitted while the grader ran a model with no
            # reasoning mode, so it cost nothing and stayed invisible. On a reasoning model every
            # grading, rewrite, and follow-up-resolution call would silently think first — measured
            # at 44.7s versus 6.1s — and the symptom would look like "the bigger model is too slow"
            # rather than a missing flag.
            "think": settings.ollama_think,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": 0.0},
        }
        async with httpx.AsyncClient(timeout=timeout_seconds or self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

        content = data.get("message", {}).get("content", "").strip()
        if not content:
            raise LLMProviderError("Ollama returned an empty response.")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"Ollama returned invalid JSON: {content[:200]}") from exc
        if not isinstance(parsed, dict):
            raise LLMProviderError("Expected a JSON object at the top level.")
        return parsed

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            # Reasoning models spend most of their time thinking before answering. Measured on
            # qwen3:8b over CPU, disabling it cut a cited two-sentence answer from 44.7s to 6.1s
            # with no quality difference: synthesis restates evidence the grader has already
            # judged, so the reasoning budget buys nothing here. Non-thinking models ignore this.
            "think": settings.ollama_think,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "temperature": 0.2,
                "top_p": 0.9,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

        content = data.get("message", {}).get("content", "").strip()
        if not content:
            raise LLMProviderError("Ollama returned an empty response.")
        return content

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": settings.embedding_model, "input": texts}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/api/embed", json=payload)
            response.raise_for_status()
            data = response.json()

        embeddings = data.get("embeddings", [])
        if len(embeddings) != len(texts):
            raise LLMProviderError("Ollama returned an unexpected number of embeddings.")
        if any(len(vector) != settings.embedding_dimension for vector in embeddings):
            raise LLMProviderError(
                f"Expected {settings.embedding_dimension}-dimensional embeddings."
            )
        return embeddings


def fallback_weekly_brief_answer() -> str:
    return (
        "Project Atlas is currently on track for Sprint 24, with delivery velocity improving "
        "12% compared with last week [3]. The main risks are payment gateway sandbox "
        "instability, which remains a high-impact blocker [1], and a staging data backfill "
        "failure with medium impact [2]. Recent engineering activity is concentrated in the "
        "backend, including retry logic, upload-flow work, and dependency updates [5]. "
        "The latest recorded product decision is to use Stripe Connect for multi-tenant "
        "payouts and roll it out behind a feature flag [4]."
    )


def fallback_answer_from_evidence(query: str, evidence_lines: list[str]) -> str:
    if not evidence_lines:
        return (
            "I could not find indexed evidence for this question. Sync the relevant project "
            "sources and try again."
        )
    facts = " ".join(evidence_lines[:4])
    return (
        f"Local model generation is unavailable. Relevant indexed evidence for '{query}': {facts}"
    )


def build_answer_prompt(
    query: str, evidence_lines: list[str], *, insist_on_citations: bool = False
) -> tuple[str, str]:
    """Build the synthesis prompt.

    `insist_on_citations` is the second attempt. The model produces good answers that carry no
    `[n]` markers often enough to matter, and an uncited answer is stripped of its citations by the
    validator — so a correct answer arrives looking unsupported. Retrying once with an explicit
    instruction is cheaper and more honest than attaching citations the answer never claimed.
    """
    system_prompt = (
        "You are an engineering project intelligence agent. Answer only from the supplied "
        "evidence. Every sentence that states a fact must end with the bracketed id of the "
        "evidence it came from, copied exactly, for example [1]. An answer with no bracketed ids "
        "is not acceptable. If evidence is missing, say what is missing instead of inventing "
        "facts. Keep the answer concise."
    )
    if insist_on_citations:
        system_prompt += (
            " Your previous attempt contained no bracketed ids. Rewrite it so every factual "
            "sentence carries the id of the evidence line it came from."
        )
    user_prompt = (
        f"User query: {query}\n\n"
        "Evidence:\n" + "\n".join(evidence_lines) + "\n\nReturn a cited engineering project answer."
    )
    return system_prompt, user_prompt
