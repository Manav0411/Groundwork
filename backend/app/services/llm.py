import asyncio
import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import settings
from app.core.observability import LLM_CALLS


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


class ChatClient(Protocol):
    """The surface that genuinely has two implementations.

    `embed` is deliberately absent. Embeddings cannot move to another provider: the initial
    migration hardcodes `Vector(dim=768)` and every stored vector came from `embeddinggemma`, so a
    different embedding model is a different vector space. Retrieval would not fail, it would
    silently return unrelated chunks. Keeping embeddings off this protocol makes that a type error
    rather than a debugging session.
    """

    model: str

    async def health(self) -> LLMHealth: ...

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict: ...

    async def generate(self, system_prompt: str, user_prompt: str) -> str: ...


def _model_installed(configured: str, installed: list[str]) -> bool:
    """Match a configured model against what `/api/tags` reports.

    Ollama treats a bare name as `:latest` but always reports the explicit tag, so a plain string
    comparison says `embeddinggemma` is missing when `embeddinggemma:latest` is right there. Latent
    until the embedder got its own health check: `llama3.2:3b` carries a tag and matched, the
    embedding model does not and never did.
    """
    wanted = configured if ":" in configured else f"{configured}:latest"
    return wanted in installed


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

        installed = _model_installed(self.model, installed_models)
        return LLMHealth(
            provider="ollama",
            available=installed,
            base_url=self.base_url,
            configured_model=self.model,
            installed_models=installed_models,
            error=None if installed else "Configured model is not installed.",
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


# A free-tier window resets in under a minute; anything longer than this is a real outage or a
# daily cap, and waiting on it would turn one slow answer into a hung request.
MAX_RETRY_AFTER_SECONDS = 15.0


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Read the server's own backoff. Groq sends fractional seconds; the RFC allows a date."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class OpenAICompatClient:
    """Chat against an OpenAI `/chat/completions` endpoint.

    Named for the protocol rather than the vendor, because that is what it implements. Written
    against the OpenAI chat-completions API and verified against Groq; other servers speaking the
    same shape (OpenRouter, Together, vLLM) should work but are untested, so this claims
    compatibility with a specification rather than with a list of vendors.

    Two Ollama concepts have to be translated. `format: "json"` becomes
    `response_format: {"type": "json_object"}`. `think` has no equivalent and is dropped rather than
    faked — its counterpart here is `reasoning_effort`, which is a different knob and is set
    explicitly below.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.hosted_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else (settings.hosted_api_key or "")
        self.model = model or settings.hosted_model
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds
        self.reasoning_effort = reasoning_effort or settings.hosted_reasoning_effort

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def health(self) -> LLMHealth:
        if not self.api_key:
            return LLMHealth(
                provider="openai_compat",
                available=False,
                base_url=self.base_url,
                configured_model=self.model,
                installed_models=[],
                error="No API key is configured.",
            )
        try:
            installed_models = await self.list_models()
        except Exception as exc:
            return LLMHealth(
                provider="openai_compat",
                available=False,
                base_url=self.base_url,
                configured_model=self.model,
                installed_models=[],
                error=str(exc),
            )
        return LLMHealth(
            provider="openai_compat",
            available=self.model in installed_models,
            base_url=self.base_url,
            configured_model=self.model,
            installed_models=installed_models,
            error=None if self.model in installed_models else "Configured model is not offered.",
        )

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/models", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        return sorted(str(model["id"]) for model in payload.get("data", []))

    def _payload(self, system_prompt: str, user_prompt: str, model: str | None) -> dict:
        return {
            "model": model or self.model,
            "temperature": 0.0,
            # The counterpart of Ollama's `think: false`, and it matters for the same reason plus
            # one more. Measured on gpt-oss-20b for a grading-shaped call: the default effort spent
            # 190 of its 205 completion tokens on reasoning, against 25 of 63 at "low" -- 3x the
            # latency for a job that returns one bit. It also spends a free-tier budget that is
            # capped per minute, so the waste is not only time.
            "reasoning_effort": self.reasoning_effort,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

    async def _post(self, payload: dict, timeout_seconds: float | None) -> str:
        async with httpx.AsyncClient(timeout=timeout_seconds or self.timeout_seconds) as client:
            for attempt in range(2):
                response = await client.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
                )
                if response.status_code != 429:
                    break
                # A free tier caps tokens per minute, and a grading call carrying 8-16 chunks is
                # large enough that two in quick succession can trip it. That is a wait, not a
                # failure, so one bounded retry on the server's own schedule is the honest response.
                # Measured: an eval run of 20 back-to-back gradings exhausted the budget after 8.
                delay = _retry_after_seconds(response)
                if attempt == 1 or delay is None or delay > MAX_RETRY_AFTER_SECONDS:
                    # Surfaced explicitly rather than folded into a generic failure. Otherwise it
                    # reaches the user as the deterministic fallback, which reads as a quality
                    # regression rather than a quota that resets in a minute.
                    raise LLMProviderError(
                        f"Rate limited by {self.base_url} ({payload['model']}): "
                        f"{response.text[:200]}"
                    )
                await asyncio.sleep(delay)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise LLMProviderError("Provider returned no choices.")
        content = (choices[0].get("message") or {}).get("content") or ""
        content = content.strip()
        if not content:
            raise LLMProviderError("Provider returned an empty response.")
        return content

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        payload = self._payload(system_prompt, user_prompt, model)
        payload["response_format"] = {"type": "json_object"}
        content = await self._post(payload, timeout_seconds)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"Provider returned invalid JSON: {content[:200]}") from exc
        if not isinstance(parsed, dict):
            raise LLMProviderError("Expected a JSON object at the top level.")
        return parsed

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = self._payload(system_prompt, user_prompt, None)
        payload["top_p"] = 0.9
        payload["temperature"] = 0.2
        return await self._post(payload, None)


ChatRole = Literal["grader", "synthesis"]


class _CountingChatClient:
    """Records the outcome of every model call, then gets out of the way.

    A wrapper rather than a mixin because `OllamaClient` also embeds, and embedding is not a chat
    call. Failures are counted and re-raised unchanged: the caller's fallback behaviour is the
    product's contract and must not shift because something is watching.
    """

    def __init__(self, inner: ChatClient, *, provider: str, role: str) -> None:
        self._inner = inner
        self._provider = provider
        self._role = role

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def provider(self) -> str:
        """Which provider actually serves this role. Exposed so callers and tests can assert the
        resolution without reaching through the wrapper for a concrete class."""
        return self._provider

    async def health(self) -> LLMHealth:
        return await self._inner.health()

    async def _counted(self, coro):
        try:
            result = await coro
        except LLMProviderError as exc:
            outcome = "rate_limited" if "Rate limited" in str(exc) else "provider_error"
            LLM_CALLS.labels(provider=self._provider, role=self._role, outcome=outcome).inc()
            raise
        except Exception:
            LLM_CALLS.labels(
                provider=self._provider, role=self._role, outcome="error"
            ).inc()
            raise
        LLM_CALLS.labels(provider=self._provider, role=self._role, outcome="ok").inc()
        return result

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        return await self._counted(
            self._inner.generate_json(
                system_prompt, user_prompt, model=model, timeout_seconds=timeout_seconds
            )
        )

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        return await self._counted(self._inner.generate(system_prompt, user_prompt))


def chat_client(role: ChatRole) -> ChatClient:
    """Build the chat client for one role, resolving provider, model and timeout together.

    Roles rather than model names, because the caller should not know which provider is configured.
    `grading.py` previously passed `model=settings.grader_model` explicitly, which leaked Ollama's
    naming into a module that has no business with it.

    The two roles are separate models on purpose. Locally that was forced by memory -- one model had
    to serve both -- and hosted it is forced by rate limits, which are counted per model, so a
    corrective loop cannot exhaust the budget the answer still needs.
    """
    is_grader = role == "grader"
    timeout = settings.grader_timeout_seconds if is_grader else settings.llm_timeout_seconds
    if settings.llm_provider == "openai_compat":
        model = settings.hosted_grader_model if is_grader else settings.hosted_model
        client: ChatClient = OpenAICompatClient(model=model, timeout_seconds=timeout)
    else:
        model = settings.grader_model if is_grader else settings.ollama_model
        client = OllamaClient(model=model, timeout_seconds=timeout)
    # Counted here rather than inside each client, so both providers report identically and a third
    # implementation cannot forget to.
    return _CountingChatClient(client, provider=settings.llm_provider, role=role)


def embedding_client() -> OllamaClient:
    """Embeddings are always local. See `ChatClient` for why this cannot be configurable.

    The model is passed explicitly so `health()` reports on the embedder. `embed()` reads
    `settings.embedding_model` directly and always worked, but `health()` reads `self.model`, which
    defaulted to the *chat* model. That was invisible while one box held both; on a deployment that
    pulls only the embedder it reported a correctly-configured embedder as unavailable, which is a
    false alarm pointing at the wrong component.
    """
    return OllamaClient(model=settings.embedding_model)


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
    # Measured on the deployed model before this wording: the first attempt carried no markers on
    # a third of runs, the retry rescued most of them, and 8% of answers still reached the user
    # uncited. The requirement was true but buried in a paragraph of prose. Stating it as a rule
    # list, showing the shape once, and naming the ids as a closed set are all cheaper than paying
    # for a second generation on a third of every question.
    system_prompt = (
        "You are an engineering project intelligence agent. Answer only from the supplied "
        "evidence.\n\n"
        "Citation format. This is a hard requirement, not a preference:\n"
        "- Every sentence that states a fact ends with the bracketed id of the evidence it came "
        "from, copied exactly: [3]\n"
        "- A sentence resting on two evidence lines ends with both: [2][5]\n"
        "- Use only ids that appear in the evidence list. Never invent one.\n"
        "- An answer containing no bracketed ids is rejected and thrown away.\n\n"
        "Required shape:\n"
        "  The grader stayed at 3B because the larger candidate scored worse on the same "
        "retrieval set [2]. The choice was recorded in configuration rather than code [5].\n\n"
        "If evidence is missing, say what is missing instead of inventing facts. Keep the answer "
        "concise."
    )
    if insist_on_citations:
        system_prompt += (
            "\n\nYour previous attempt contained no bracketed ids and was discarded. Rewrite it so "
            "every factual sentence ends with the id of the evidence line it came from."
        )
    # Naming the ids as an explicit closed set, separately from the evidence block, gives the
    # model something short to copy from rather than something long to parse.
    available = ", ".join(f"[{ordinal}]" for ordinal in _evidence_ordinals(evidence_lines))
    user_prompt = (
        f"User query: {query}\n\n"
        "Evidence:\n" + "\n".join(evidence_lines) + "\n\n"
        f"Cite using only these ids: {available or 'none available'}.\n"
        "Return a cited engineering project answer."
    )
    return system_prompt, user_prompt


_EVIDENCE_ORDINAL = re.compile(r"^\s*\[(\d+)\]")


def _evidence_ordinals(evidence_lines: list[str]) -> list[int]:
    """The ids the caller actually offered, read back off the lines it built.

    Parsed rather than passed so the prompt cannot list an id the evidence block does not show —
    the two would drift the moment either side changed.
    """
    found = [
        int(match.group(1))
        for line in evidence_lines
        if (match := _EVIDENCE_ORDINAL.match(line))
    ]
    return sorted(dict.fromkeys(found))
