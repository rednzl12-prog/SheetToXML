"""Vision-LLM transport: Gemini (google-genai) plus OpenAI-compatible chat endpoints (Qwen, DeepSeek).

A prompt is a list of parts: str = text, bytes = PNG image. Every call streams, so long
reasoning runs never hit a read timeout, and every failure is mapped to an LLMError whose
`kind` says what to do about it (auth/bad_request: stop; rate/overload/network: retry;
model: try the next model).
"""

import base64
import json
import random
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

QWEN_PLAN = ["https://token-plan.maas.qwencloudapi.com/compatible-mode/v1",
             "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
             "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"]
QWEN_PAYG = ["https://maas.qwencloudapi.com/compatible-mode/v1",
             "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "https://dashscope.aliyuncs.com/compatible-mode/v1"]
DEEPSEEK = ["https://api.deepseek.com"]

Part = object  # str | bytes
IMAGE_RESOLUTION = "MEDIA_RESOLUTION_ULTRA_HIGH"
_base_cache: Dict[Tuple[str, str], str] = {}


class LLMError(Exception):
    """kind: auth | bad_request | rate | overload | network | model | truncated | cancelled | other"""

    def __init__(self, message: str, kind: str = "other", status: Optional[int] = None, retry_after: float = 0):
        super().__init__(message)
        self.kind, self.status, self.retry_after = kind, status, retry_after


def provider_of(model: str, catalog: List[dict]) -> str:
    for m in catalog:
        if m["id"] == model:
            return m["provider"]
    return "qwen" if model.startswith("qwen") else "deepseek" if model.startswith("deepseek") else "gemini"


def key_problem(provider: str, key: str) -> str:
    """Why this key obviously can't belong to `provider` ('' when it plausibly can)."""
    if provider == "gemini" and key.startswith("sk-"):
        return "This looks like a Qwen/DeepSeek key (starts with sk-), not a Google Gemini key (AIza... or AQ....)."
    if provider == "deepseek" and key.startswith("sk-sp-"):
        return "This is a Qwen Token Plan key (sk-sp-...). Use it under Qwen, or pick the DeepSeek model listed under Qwen."
    if provider in ("qwen", "deepseek") and (key.startswith("AIza") or key.startswith("AQ.")):
        return "This looks like a Google Gemini key, not a Qwen/DeepSeek key."
    return ""


def base_candidates(provider: str, key: str, override: str = "") -> List[str]:
    # Token Plan keys (sk-sp-) serve Qwen and DeepSeek models on the plan hosts only
    bases = QWEN_PLAN if key.startswith("sk-sp-") else QWEN_PAYG if provider == "qwen" else DEEPSEEK
    override = override.strip().rstrip("/")
    return [override] + [b for b in bases if b != override] if override else list(bases)


def _error_from_http(status: int, body: str, retry_after: str = "") -> LLMError:
    try:
        j = json.loads(body)
        msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else None
        msg = msg or j.get("message") or body
    except Exception:
        msg = body
    msg = " ".join(str(msg).split())[:300]
    try:
        wait = float(retry_after)
    except ValueError:
        wait = 0
    if status in (401, 403):
        return LLMError(f"HTTP {status}: {msg}", "auth", status)
    if status == 404:
        return LLMError(f"HTTP 404: {msg}", "model", status)
    if status == 429:
        return LLMError(f"HTTP 429 rate limit: {msg}", "rate", status, wait)
    if status >= 500 or status in (408, 409):
        return LLMError(f"HTTP {status}: {msg}", "overload", status, wait)
    return LLMError(f"HTTP {status}: {msg}", "bad_request", status)


# ------------------------------------------------------------------ discovery / key check

def discover(provider: str, key: str, base_url: str = "", timeout: float = 25) -> Tuple[str, List[str]]:
    """Cheap, token-free authenticated call. Returns (working base url, model ids)."""
    if provider == "gemini":
        try:
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=int(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
            return "", [m.name.replace("models/", "") for m in client.models.list()]
        except Exception as e:
            raise _map_gemini(e)
    tried = []
    for base in base_candidates(provider, key, base_url):
        try:
            r = httpx.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        except httpx.HTTPError as e:
            tried.append(f"{base}: {type(e).__name__}")
            continue
        if r.status_code == 200:
            try:
                ids = [m["id"] for m in r.json().get("data", [])]
            except Exception:
                ids = []
            _base_cache[(provider, key[-8:])] = base
            return base, ids
        err = _error_from_http(r.status_code, r.text)
        tried.append(f"{base}: {err}")
    kind = "auth" if any("HTTP 401" in t or "HTTP 403" in t for t in tried) else "network"
    raise LLMError("No endpoint accepted this key. Tried:\n" + "\n".join(tried), kind)


# ------------------------------------------------------------------ Gemini

def _map_gemini(e: Exception) -> LLMError:
    if isinstance(e, LLMError):
        return e
    if isinstance(e, genai_errors.APIError):
        code = getattr(e, "code", None) or 0
        msg = " ".join(str(getattr(e, "message", "") or e).split())[:300]
        if code in (401, 403) or "API key" in msg and code == 400:
            return LLMError(f"Gemini rejected the key (HTTP {code}): {msg}", "auth", code)
        if code == 404:
            return LLMError(f"Model not available to this key: {msg}", "model", code)
        if code == 429:
            m = re.search(r"retry(?:Delay'?:\s*'| in )([\d.]+)s", str(e))       # server-suggested wait
            return LLMError(f"Gemini rate limit: {msg}", "rate", code, min(60.0, float(m.group(1))) if m else 0)
        if code >= 500:
            return LLMError(f"Gemini overloaded (HTTP {code}): {msg}", "overload", code)
        return LLMError(f"Gemini HTTP {code}: {msg}", "bad_request", code)
    if isinstance(e, (httpx.HTTPError, TimeoutError, ConnectionError)):
        return LLMError(f"Network error talking to Gemini: {type(e).__name__}: {e}", "network")
    return LLMError(f"{type(e).__name__}: {e}", "other")


def _gemini(model, key, system, parts, max_tokens, thinking, timeout, cancel, on_chunk) -> str:
    # per-part resolution: a 2470x320 system strip costs ~280 tokens at LOW, ~1100 at HIGH (the
    # Gemini 3 default) and ~2200 at ULTRA_HIGH, which keeps 32nd-note beams apart
    contents = [types.Part.from_bytes(data=p, mime_type="image/png", media_resolution=IMAGE_RESOLUTION)
                if isinstance(p, bytes) else p for p in parts]
    config = types.GenerateContentConfig(
        system_instruction=system, max_output_tokens=max_tokens,   # Gemini 3: leave temperature at its default
        thinking_config=types.ThinkingConfig(thinking_level=thinking) if thinking else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    out, finish = [], None
    try:
        client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=int(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
        for chunk in client.models.generate_content_stream(model=model, contents=contents, config=config):
            if cancel and cancel():
                raise LLMError("Cancelled", "cancelled")
            if chunk.candidates and chunk.candidates[0].finish_reason:
                finish = chunk.candidates[0].finish_reason.name
            piece = chunk.text
            if piece:
                out.append(piece)
                if on_chunk:
                    on_chunk(piece)
    except Exception as e:
        raise _map_gemini(e)
    text = "".join(out)
    if finish == "MAX_TOKENS":
        raise LLMError("Output hit the token limit before finishing.", "truncated")
    if not text.strip():
        raise LLMError(f"Gemini returned no text (finish: {finish}).", "overload" if finish in (None, "STOP") else "other")
    return text


# ------------------------------------------------------------------ OpenAI-compatible (Qwen, DeepSeek)

def _openai(base, model, key, system, parts, max_tokens, extra, timeout, cancel, on_chunk) -> str:
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(p).decode()}}
               if isinstance(p, bytes) else {"type": "text", "text": p} for p in parts]
    body = {"model": model, "stream": True, "max_tokens": max_tokens, **extra,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    out, finish = [], None
    try:
        with httpx.stream("POST", base + "/chat/completions", json=body,
                          headers={"Authorization": f"Bearer {key}"},
                          timeout=httpx.Timeout(connect=20, read=timeout, write=60, pool=20)) as r:
            if r.status_code != 200:
                r.read()
                raise _error_from_http(r.status_code, r.text, r.headers.get("retry-after", ""))
            for line in r.iter_lines():
                if cancel and cancel():
                    raise LLMError("Cancelled", "cancelled")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    choice = json.loads(data)["choices"][0]
                except (ValueError, KeyError, IndexError):
                    continue
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    out.append(piece)
                    if on_chunk:
                        on_chunk(piece)
                finish = choice.get("finish_reason") or finish
    except LLMError:
        raise
    except httpx.HTTPError as e:
        raise LLMError(f"Network error: {type(e).__name__}: {e}", "network")
    text = "".join(out)
    if finish == "length":
        raise LLMError("Output hit the token limit before finishing.", "truncated")
    if not text.strip():
        raise LLMError(f"The model returned no text (finish: {finish}).", "overload")
    return text


# ------------------------------------------------------------------ public entry points

def generate(provider: str, model: str, key: str, system: str, parts: List[Part], *, base_url: str = "",
             max_tokens: int = 16384, thinking: Optional[str] = None, timeout: float = 300,
             cancel: Optional[Callable[[], bool]] = None, on_chunk: Optional[Callable[[str], None]] = None) -> str:
    """One streamed completion. `thinking`: Gemini thinking level, or 'off'/'on' for Qwen/DeepSeek."""
    problem = key_problem(provider, key)
    if problem:
        raise LLMError(problem, "auth")
    if provider == "gemini":
        return _gemini(model, key, system, parts, max_tokens, thinking, timeout, cancel, on_chunk)
    cached = _base_cache.get((provider, key[-8:]))
    base = cached or discover(provider, key, base_url)[0]
    extra: dict = {}
    if provider == "qwen" or key.startswith("sk-sp-"):
        extra["vl_high_resolution_images"] = True
        if thinking in ("on", "off"):
            extra["enable_thinking"] = thinking == "on"
    elif thinking in ("on", "off"):
        extra["thinking"] = {"type": "enabled" if thinking == "on" else "disabled"}
    for _ in range(3):
        try:
            return _openai(base, model, key, system, parts, max_tokens, extra, timeout, cancel, on_chunk)
        except LLMError as e:
            if e.kind == "bad_request" and "vl_high_resolution_images" in str(e) and "vl_high_resolution_images" in extra:
                extra.pop("vl_high_resolution_images")
            elif e.kind == "bad_request" and "enable_thinking" in str(e) and extra.get("enable_thinking") is False:
                extra["enable_thinking"] = True          # e.g. glm-5.3 only runs with thinking on
            else:
                raise
    return _openai(base, model, key, system, parts, max_tokens, extra, timeout, cancel, on_chunk)


def sleep_unless_cancelled(seconds: float, cancel: Optional[Callable[[], bool]]):
    end = time.time() + seconds
    while time.time() < end:
        if cancel and cancel():
            raise LLMError("Cancelled", "cancelled")
        time.sleep(0.25)


def generate_with_fallback(provider: str, models: List[str], key: str, system: str, parts: List[Part],
                           notify: Optional[Callable[[str], None]] = None, **kw) -> Tuple[str, str]:
    """Try each model in turn; retry transient failures with backoff. Returns (text, model used)."""
    last: Optional[LLMError] = None
    for model in models:
        for attempt in range(3):
            try:
                return generate(provider, model, key, system, parts, **kw), model
            except LLMError as e:
                last = e
                if e.kind in ("auth", "bad_request", "cancelled", "truncated"):
                    raise
                if e.kind == "model":
                    break
                wait = e.retry_after or min(20, 3 * 2 ** attempt) + random.random()
                if notify:
                    notify(f"{model}: {e.kind} - retrying in {wait:.0f}s")
                sleep_unless_cancelled(wait, kw.get("cancel"))
        if notify and last:
            notify(f"{model} unavailable ({last.kind}); trying next model")
    raise last or LLMError("No model could be called.", "other")
