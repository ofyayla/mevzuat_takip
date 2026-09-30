"""LLM katmanı (plan §7): OpenAI (yerel geliştirme) ve vLLM/Qwen (kurum) için tek istemci.

- Yapılandırılmış çıktı: ``response_format=json_schema`` (strict). Sunucu şema zorlamasını desteklemiyorsa
  ``LLM_JSON_MODE=json_object`` ile düz JSON moduna düşülür; doğrulama her durumda Pydantic ile yapılır.
- Qwen thinking: görev ``LLM_THINKING_TASKS`` içindeyse ``chat_template_kwargs.enable_thinking`` açılır. Düşünme
  çıktısı ``reasoning_content`` alanında ayrı gelir (reasoning parser açık) ya da içerikte ``<think>…</think>``
  bloğu olarak gelir (parser kapalı); ikisi de desteklenir.
- Şemaya uymayan yanıt için bir kez "şemaya uy" onarma denemesi; bağlantı/zaman aşımı/5xx/429 için üstel retry.
- Her çağrı ``llm_call`` tablosuna yazılır. Aynı görev + prompt sürümü + model + girdi için başarılı bir kayıt
  varsa LLM yeniden çağrılmaz (``force=True`` ile kapatılır) — yeniden işleme (İK-7) ucuzlar.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import LlmCall
from app.settings import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMError(Exception):
    """LLM çağrısı başarısız (bağlantı, geçersiz çıktı). Düzenleme AI_FAILED olur ve yeniden denenir."""


@dataclass
class LLMResult(Generic[T]):
    outputs: list[T]
    raw: list[str] = field(default_factory=list)
    reasoning: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    cached: bool = False
    call_id: int | None = None


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic şemasını OpenAI strict JSON schema biçimine getirir (tüm alanlar zorunlu, ek alan yok)."""
    schema = copy.deepcopy(model.model_json_schema())

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            for k in ("title", "default", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
                node.pop(k, None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


def split_reasoning(content: str) -> tuple[str, str | None]:
    """``<think>…</think>`` bloğunu içerikten ayırır; kod çitlerini ve baştaki/sondaki metni temizler."""
    reasoning = None
    if m := _THINK.search(content or ""):
        reasoning = m.group(0)[7:-8].strip()
        content = _THINK.sub("", content)
    elif "</think>" in (content or ""):          # açılış etiketi şablonda kalmışsa
        reasoning, content = content.split("</think>", 1)
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    if not content.startswith("{") and (i := content.find("{")) >= 0 and (j := content.rfind("}")) > i:
        content = content[i:j + 1]
    return content, reasoning


def input_hash(task: str, prompt_version: str, model: str, messages: list[dict], n: int, temperature: float) -> str:
    payload = json.dumps([task, prompt_version, model, messages, n, temperature], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLM(Protocol):
    provider: str
    model: str

    def complete_json(self, task: str, prompt_version: str, messages: list[dict], schema: type[T], *,
                      temperature: float = 0.1, n: int = 1, session: Session | None = None,
                      regulation_id: int | None = None, force: bool = False) -> LLMResult[T]: ...


class _Base:
    provider = "base"
    model = "base"

    def __init__(self, settings: Settings):
        self.settings = settings

    # alt sınıf: ham çağrı → (içerik listesi, reasoning, prompt_tokens, completion_tokens)
    def _call(self, task: str, messages: list[dict], schema: type[BaseModel], temperature: float,
              n: int) -> tuple[list[str], str | None, int | None, int | None]:  # pragma: no cover
        raise NotImplementedError

    def complete_json(self, task, prompt_version, messages, schema, *, temperature=0.1, n=1, session=None,
                      regulation_id=None, force=False):
        h = input_hash(task, prompt_version, self.model, messages, n, temperature)
        if session is not None and not force:
            cached = session.scalar(select(LlmCall).where(
                LlmCall.input_hash == h, LlmCall.status == "ok").order_by(LlmCall.id.desc()).limit(1))
            if cached is not None:
                outs = [schema.model_validate(o) for o in cached.response["outputs"]]
                return LLMResult(outs, cached.response.get("raw", []), cached.reasoning, cached.prompt_tokens,
                                 cached.completion_tokens, cached.latency_ms, cached=True, call_id=cached.id)
        t0 = time.monotonic()
        status, error, outputs, raws, reasoning, pt, ct = "error", None, [], [], None, None, None
        try:
            raws_in, reasoning, pt, ct = self._call(task, messages, schema, temperature, n)
            reasonings = [reasoning] if reasoning else []
            errors = []
            for content in raws_in:
                clean, r = split_reasoning(content)
                if r:
                    reasonings.append(r)
                raws.append(clean)
                try:
                    outputs.append(schema.model_validate_json(clean))
                except ValidationError as e:
                    errors.append((clean, e))
            if not outputs and errors:
                outputs = self._repair(task, messages, schema, errors[0])
            reasoning = "\n---\n".join(reasonings) or None
            if not outputs:
                status, error = "invalid", f"şemaya uygun çıktı yok: {errors[0][1] if errors else 'boş yanıt'}"
                raise LLMError(error)
            status = "ok"
            return LLMResult(outputs, raws, reasoning, pt, ct, int((time.monotonic() - t0) * 1000))
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 — SDK hata tipleri çeşitli; hepsi LLMError'a çevrilir
            error = f"{type(e).__name__}: {e}"
            raise LLMError(error) from e
        finally:
            if session is not None:
                row = LlmCall(task=task, provider=self.provider, model=self.model, prompt_version=prompt_version,
                              input_hash=h, regulation_id=regulation_id,
                              request={"messages": _truncate(messages), "n": n, "temperature": temperature},
                              response={"outputs": [o.model_dump(mode="json") for o in outputs], "raw": raws},
                              reasoning=reasoning if self.settings.llm_store_reasoning else None,
                              latency_ms=int((time.monotonic() - t0) * 1000), prompt_tokens=pt,
                              completion_tokens=ct, status=status, error=error)
                session.add(row)
                session.flush()

    def _repair(self, task, messages, schema, failed) -> list:
        content, err = failed
        fix_messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"Yanıtın JSON şemasına uymuyor: {str(err)[:500]}. Yalnızca şemaya uygun "
                                        f"geçerli bir JSON nesnesi döndür."}]
        try:
            raws, _, _, _ = self._call(task, fix_messages, schema, 0.0, 1)
            clean, _ = split_reasoning(raws[0]) if raws else ("", None)
            return [schema.model_validate_json(clean)]
        except (ValidationError, IndexError):
            return []


def _truncate(messages: list[dict], limit: int = 4000) -> list[dict]:
    return [{**m, "content": m["content"][:limit] + ("…" if len(m["content"]) > limit else "")} for m in messages]


class OpenAICompatibleLLM(_Base):
    """OpenAI ve vLLM (OpenAI uyumlu) — aynı SDK."""

    def __init__(self, settings: Settings, client=None):
        super().__init__(settings)
        from openai import OpenAI

        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self.client = client or OpenAI(base_url=settings.llm_base_url or None,
                                       api_key=settings.llm_api_key or "EMPTY",
                                       timeout=settings.llm_timeout_s, max_retries=0)
        self.thinking_tasks = {t.strip() for t in settings.llm_thinking_tasks.split(",") if t.strip()}

    def _call(self, task, messages, schema, temperature, n):
        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
        from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature, "n": n}
        if self.settings.llm_json_mode == "json_schema":
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                "name": task, "schema": strict_schema(schema), "strict": True}}
        else:
            kwargs["response_format"] = {"type": "json_object"}
        if self.provider == "vllm":
            kwargs["extra_body"] = {"chat_template_kwargs": {
                "enable_thinking": self.settings.llm_enable_thinking and task in self.thinking_tasks}}

        @retry(retry=retry_if_exception_type((APIConnectionError, APITimeoutError, InternalServerError,
                                              RateLimitError)),
               stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
        def call():
            return self.client.chat.completions.create(**kwargs)

        resp = call()
        contents, reasonings = [], []
        for choice in resp.choices:
            contents.append(choice.message.content or "")
            extra = getattr(choice.message, "model_extra", None) or {}
            if r := (getattr(choice.message, "reasoning_content", None) or extra.get("reasoning_content")):
                reasonings.append(r)
        usage = getattr(resp, "usage", None)
        return (contents, "\n---\n".join(reasonings) or None,
                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


class FakeLLM(_Base):
    """Test ve geliştirme için: ``handler(task, messages, n) -> list[dict | str]``."""

    provider = "fake"
    model = "fake"

    def __init__(self, settings: Settings, handler):
        super().__init__(settings)
        self.handler = handler
        self.calls: list[tuple[str, list[dict], int]] = []

    def _call(self, task, messages, schema, temperature, n):
        self.calls.append((task, messages, n))
        out = self.handler(task, messages, n)
        return [o if isinstance(o, str) else json.dumps(o, ensure_ascii=False) for o in out], None, 100, 20


def get_llm(settings: Settings) -> LLM | None:
    if settings.llm_provider in (None, "", "none"):
        return None
    if settings.llm_provider in ("openai", "vllm"):
        return OpenAICompatibleLLM(settings)
    raise ValueError(f"Bilinmeyen LLM_PROVIDER: {settings.llm_provider}")
