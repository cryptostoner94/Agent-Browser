from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

import httpx


RouteName = Literal["gemini_heavy", "gemini_fast", "groq_fast", "local_guarded"]


@dataclass
class LLMUsage:
    provider: str
    model: str
    prompt_tokens_estimate: int = 0
    completion_tokens_estimate: int = 0
    requests: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    last_error: str = ""

    def record_success(self, prompt: str, completion: str, latency_ms: float) -> None:
        self.requests += 1
        self.prompt_tokens_estimate += max(1, len(prompt) // 4)
        self.completion_tokens_estimate += max(1, len(completion) // 4)
        self.total_latency_ms += latency_ms
        self.last_error = ""

    def record_failure(self, error: Exception) -> None:
        self.failures += 1
        self.last_error = f"{type(error).__name__}: {error}"

    @property
    def average_latency_ms(self) -> float:
        if self.requests == 0:
            return 0.0
        return round(self.total_latency_ms / self.requests, 2)


@dataclass
class ModelRoute:
    name: RouteName
    provider: str
    model: str
    reason: str
    max_input_chars: int


@dataclass
class LLMResponse:
    text: str
    route: ModelRoute
    usage: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)
    degraded: bool = False


class NexusLLMRouter:
    """Production-safe LLM router for NexusOS-Ultra.

    Gemini is used for large-context reasoning and file analysis.
    Groq is used for fast browser/action loops.
    A deterministic local response is used when keys are missing so the stack stays bootable.
    """

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self.openclaw_base_url = os.getenv("OPENCLAW_BASE_URL", "").strip().rstrip("/")
        self.openclaw_api_key = os.getenv("OPENCLAW_API_KEY", "").strip()

        self.gemini_model_heavy = os.getenv("GEMINI_MODEL_HEAVY", "gemini-1.5-pro").strip()
        self.gemini_model_fast = os.getenv("GEMINI_MODEL_FAST", "gemini-1.5-flash").strip()
        self.groq_model_fast = os.getenv("GROQ_MODEL_FAST", "llama-3.1-70b-versatile").strip()

        self._usage: Dict[str, LLMUsage] = {
            "gemini_heavy": LLMUsage("google", self.gemini_model_heavy),
            "gemini_fast": LLMUsage("google", self.gemini_model_fast),
            "groq_fast": LLMUsage("groq", self.groq_model_fast),
            "local_guarded": LLMUsage("local", "nexusos-guarded-deterministic"),
        }

    def route(self, prompt: str, task_type: str = "general", requires_json: bool = False) -> ModelRoute:
        prompt_len = len(prompt)
        lowered = task_type.lower().strip()

        if not self.gemini_key and not self.groq_key:
            return ModelRoute(
                name="local_guarded",
                provider="local",
                model="nexusos-guarded-deterministic",
                reason="No live LLM API keys are configured; using guarded local response.",
                max_input_chars=200_000,
            )

        if lowered in {"browser", "execution", "loop", "tool", "fast"} and self.groq_key and prompt_len <= 48_000:
            return ModelRoute(
                name="groq_fast",
                provider="groq",
                model=self.groq_model_fast,
                reason="Fast action loop selected for browser/tool dispatch.",
                max_input_chars=48_000,
            )

        if requires_json and self.groq_key and prompt_len <= 32_000:
            return ModelRoute(
                name="groq_fast",
                provider="groq",
                model=self.groq_model_fast,
                reason="Fast JSON response selected for structured control output.",
                max_input_chars=32_000,
            )

        if prompt_len > 48_000 and self.gemini_key:
            return ModelRoute(
                name="gemini_heavy",
                provider="google",
                model=self.gemini_model_heavy,
                reason="Large context selected for deep file/log analysis.",
                max_input_chars=900_000,
            )

        if self.gemini_key:
            return ModelRoute(
                name="gemini_fast",
                provider="google",
                model=self.gemini_model_fast,
                reason="Gemini fast route selected for balanced reasoning.",
                max_input_chars=250_000,
            )

        return ModelRoute(
            name="groq_fast",
            provider="groq",
            model=self.groq_model_fast,
            reason="Gemini unavailable; Groq selected.",
            max_input_chars=48_000,
        )

    def usage_snapshot(self) -> Dict[str, Any]:
        return {name: {**asdict(usage), "average_latency_ms": usage.average_latency_ms} for name, usage in self._usage.items()}

    def ask(
        self,
        prompt: str,
        task_type: str = "general",
        system: Optional[str] = None,
        requires_json: bool = False,
        temperature: float = 0.2,
    ) -> LLMResponse:
        route = self.route(prompt, task_type=task_type, requires_json=requires_json)
        bounded_prompt = prompt[: route.max_input_chars]
        if system:
            full_prompt = f"SYSTEM:\n{system.strip()}\n\nUSER:\n{bounded_prompt}"
        else:
            full_prompt = bounded_prompt

        start = time.perf_counter()
        try:
            if route.name in {"gemini_heavy", "gemini_fast"}:
                text, raw = self._ask_gemini(route.model, full_prompt, temperature=temperature)
            elif route.name == "groq_fast":
                text, raw = self._ask_groq(route.model, full_prompt, temperature=temperature, requires_json=requires_json)
            else:
                text, raw = self._local_guarded_response(full_prompt, requires_json=requires_json), {}
            elapsed = (time.perf_counter() - start) * 1000.0
            self._usage[route.name].record_success(full_prompt, text, elapsed)
            self._notify_openclaw("llm_response", {"route": asdict(route), "degraded": False})
            return LLMResponse(text=text, route=route, usage=self.usage_snapshot(), raw=raw, degraded=(route.name == "local_guarded"))
        except Exception as exc:
            self._usage[route.name].record_failure(exc)
            fallback_route = ModelRoute(
                name="local_guarded",
                provider="local",
                model="nexusos-guarded-deterministic",
                reason=f"Live route failed: {type(exc).__name__}. Returned safe local fallback.",
                max_input_chars=200_000,
            )
            fallback = self._local_guarded_response(full_prompt, requires_json=requires_json)
            self._usage["local_guarded"].record_success(full_prompt, fallback, (time.perf_counter() - start) * 1000.0)
            self._notify_openclaw("llm_response", {"route": asdict(fallback_route), "degraded": True})
            return LLMResponse(text=fallback, route=fallback_route, usage=self.usage_snapshot(), raw={"error": str(exc)}, degraded=True)

    def ask_json(
        self,
        prompt: str,
        task_type: str = "general",
        system: Optional[str] = None,
        schema_hint: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], LLMResponse]:
        schema_text = ""
        if schema_hint:
            schema_text = "\nReturn JSON compatible with this shape:\n" + json.dumps(schema_hint, indent=2)
        response = self.ask(
            prompt + schema_text,
            task_type=task_type,
            system=system,
            requires_json=True,
            temperature=0.1,
        )
        parsed = self._extract_json_object(response.text)
        return parsed, response

    def _ask_gemini(self, model: str, prompt: str, temperature: float) -> Tuple[str, Dict[str, Any]]:
        if not self.gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "topP": 0.95,
                "maxOutputTokens": 8192,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ],
        }
        params = {"key": self.gemini_key}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, params=params, json=payload)
            response.raise_for_status()
            raw = response.json()
        candidates = raw.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {raw}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "\n".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise RuntimeError("Gemini returned an empty response.")
        return text, raw

    def _ask_groq(self, model: str, prompt: str, temperature: float, requires_json: bool) -> Tuple[str, Dict[str, Any]]:
        if not self.groq_key:
            raise RuntimeError("GROQ_API_KEY is not configured.")
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": 4096,
        }
        if requires_json:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            raw = response.json()
        choices = raw.get("choices", [])
        if not choices:
            raise RuntimeError(f"Groq returned no choices: {raw}")
        text = choices[0].get("message", {}).get("content", "").strip()
        if not text:
            raise RuntimeError("Groq returned an empty response.")
        return text, raw

    def _local_guarded_response(self, prompt: str, requires_json: bool = False) -> str:
        summary = prompt.strip().replace("\r", "\n")
        summary = "\n".join(line.strip() for line in summary.splitlines() if line.strip())
        summary = summary[:1600]
        if requires_json:
            return json.dumps(
                {
                    "status": "degraded",
                    "provider": "local_guarded",
                    "message": "Live model keys are not configured or the selected provider failed.",
                    "safe_next_action": "Add GEMINI_API_KEY and GROQ_API_KEY to .env, restart the stack, and retry the request.",
                    "prompt_digest": summary,
                },
                ensure_ascii=False,
            )
        return (
            "NexusOS-Ultra is running in guarded local mode. Add valid Gemini/Groq keys to enable live reasoning.\n\n"
            f"Request digest:\n{summary}"
        )

    def _extract_json_object(self, text: str) -> Dict[str, Any]:
        stripped = text.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                return json.loads(stripped[start : end + 1])
            raise

    def _notify_openclaw(self, event: str, payload: Dict[str, Any]) -> None:
        if not self.openclaw_base_url:
            return
        headers = {"Content-Type": "application/json"}
        if self.openclaw_api_key:
            headers["Authorization"] = f"Bearer {self.openclaw_api_key}"
        safe_payload = {"event": event, "payload": payload, "source": "nexusos-ultra"}
        try:
            with httpx.Client(timeout=4.0) as client:
                client.post(f"{self.openclaw_base_url}/events", headers=headers, json=safe_payload)
        except Exception:
            return


def build_default_router() -> NexusLLMRouter:
    return NexusLLMRouter()


if __name__ == "__main__":
    router = build_default_router()
    response = router.ask("Return a one sentence health report for NexusOS-Ultra.", task_type="fast")
    print(json.dumps({"text": response.text, "route": asdict(response.route), "usage": response.usage}, indent=2))
