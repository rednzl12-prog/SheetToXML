"""Vision-LLM transport for every provider in PROVIDERS: Gemini (google-genai), Anthropic (Messages API over
httpx SSE) and OpenAI-compatible chat endpoints (OpenAI, Qwen, DeepSeek, xAI, Mistral, OpenRouter, any local or
custom server).

A prompt is a list of parts: str = text, bytes = PNG image. Every call streams, so long
reasoning runs never hit a read timeout, and every failure is mapped to an LLMError whose
`kind` says what to do about it (auth/bad_request: stop; rate/overload/network: retry;
model: try the next model).
"""

import base64
import json
import os
import random
import re
import threading
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
LOCAL = ["http://localhost:11434/v1", "http://localhost:1234/v1"]     # Ollama, LM Studio defaults
ANTHROPIC_VERSION = "2023-06-01"


def _m(id_: str, name: str, description: str, fallbacks: List[str] = (), vision: bool = True) -> dict:
    return {"id": id_, "name": name, "description": description, "fallbacks": list(fallbacks), "vision": vision}


# Display order. Facts checked 2026-10-06; the URL next to each entry is where they came from.
PROVIDERS: Dict[str, dict] = {
    "gemini": {  # https://ai.google.dev/gemini-api/docs/models (ids verified live with the user's key)
        "name": "Google Gemini", "kind": "gemini", "env": "GEMINI_API_KEY", "keyHint": "AIza…",
        "keyUrl": "https://aistudio.google.com/app/apikey", "keyOptional": False, "baseUrl": "", "thinking": "low",
        "models": [_m("gemini-3.8-flash", "Gemini 3.8 Flash", "Fast and accurate; free tier has a small daily quota",
                      ["gemini-3.7-flash", "gemini-3.5-flash"]),
                   _m("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview (premium)",
                      "Strongest Gemini reader; quota-limited on the free tier", ["gemini-pro-latest", "gemini-3.8-flash"]),
                   _m("gemini-pro-latest", "Gemini Pro Latest (premium)", "Latest Gemini Pro; quota-limited on the free tier",
                      ["gemini-3.8-flash"])]},
    "anthropic": {  # https://platform.claude.com/docs/en/about-claude/models/overview , .../api/errors , .../build-with-claude/vision
        # Opus 5.5 / Sonnet 5.5 cannot disable thinking; depth is output_config.effort (Haiku 4.5 rejects effort)
        "name": "Anthropic Claude", "kind": "anthropic", "env": "ANTHROPIC_API_KEY", "keyHint": "sk-ant-…",
        "keyUrl": "https://platform.claude.com/settings/keys", "keyOptional": False,
        "baseUrl": "https://api.anthropic.com/v1", "thinking": "low",
        "models": [_m("claude-sonnet-5-5", "Claude Sonnet 5.5", "Accurate reader at $2/$10 per M tokens",
                      ["claude-haiku-4-5"]),
                   _m("claude-opus-5-5", "Claude Opus 5.5 (premium)", "Strongest Claude; $4/$20 per M tokens, slower",
                      ["claude-sonnet-5-5"]),
                   _m("claude-haiku-4-5", "Claude Haiku 4.5 (budget)", "Cheapest Claude, $1/$5 per M tokens; weaker on dense pages")]},
    "openai": {  # https://developers.openai.com/api/docs/models (gpt-6.1-sol: effort low..max, no none/minimal)
        "name": "OpenAI", "kind": "openai", "env": "OPENAI_API_KEY", "keyHint": "sk-proj-…",
        "keyUrl": "https://platform.openai.com/api-keys", "keyOptional": False,
        "baseUrl": "https://api.openai.com/v1", "thinking": "low",
        "models": [_m("gpt-6.1-sol", "GPT-6.1 Sol", "Strong vision reasoning model, $2/$10 per M tokens", ["gpt-6-luna"]),
                   _m("gpt-6-astra", "GPT-6 Astra (premium)", "OpenAI flagship, $10/$50 per M tokens", ["gpt-6.1-sol"]),
                   _m("gpt-6-luna", "GPT-6 Luna (budget)", "Very cheap ($0.10/$0.50 per M tokens); less precise")]},
    "qwen": {  # https://docs.qwencloud.com/token-plan/quickstart (Token Plan keys sk-sp-, pay-as-you-go sk-ws-)
        "name": "Qwen", "kind": "openai", "env": "QWEN_API_KEY", "keyHint": "sk-sp-…",
        "keyUrl": "https://home.qwencloud.com/", "keyOptional": False, "baseUrl": QWEN_PLAN[0], "thinking": "off",
        "models": [_m("qwen3.8-flash", "Qwen 3.8 Flash", "Fast; 98% of notes on the hardest test page", ["qwen3.6-flash"]),
                   _m("qwen3.7-plus", "Qwen 3.7 Plus", "Mid-size Qwen vision model", ["qwen3.8-flash"]),
                   _m("qwen3.8-max", "Qwen 3.8 Max (very slow)", "About 2.5 minutes per staff system", ["qwen3.8-flash"]),
                   _m("qwen3.6-flash", "Qwen 3.6 Flash", "Older fast Qwen vision model"),
                   _m("deepseek-v4.1-flash", "DeepSeek V4.1 Flash (via Qwen Token Plan)",
                      "DeepSeek vision model served on the Qwen Token Plan key")]},
    "deepseek": {  # https://api-docs.deepseek.com/guides/vision/ , https://api-docs.deepseek.com/updates/
        "name": "DeepSeek", "kind": "openai", "env": "DEEPSEEK_API_KEY", "keyHint": "sk-…",
        "keyUrl": "https://platform.deepseek.com/api_keys", "keyOptional": False, "baseUrl": DEEPSEEK[0], "thinking": "off",
        "models": [_m("deepseek-flash", "DeepSeek V4.1 Flash", "DeepSeek's vision model; very cheap"),
                   _m("deepseek-v4-pro", "DeepSeek V4 Pro (text only)", "No image input; usable for the AI note only",
                      vision=False)]},
    "xai": {  # https://docs.x.ai/developers/models/grok-4.7 , .../grok-4.3 (text+image -> text)
        "name": "xAI Grok", "kind": "openai", "env": "XAI_API_KEY", "keyHint": "xai-…",
        "keyUrl": "https://console.x.ai/", "keyOptional": False, "baseUrl": "https://api.x.ai/v1", "thinking": "low",
        "models": [_m("grok-4.7", "Grok 4.7", "xAI flagship with image input, $2/$6 per M tokens", ["grok-4.3"]),
                   _m("grok-4.3", "Grok 4.3 (budget)", "Cheaper Grok with image input, $1.25/$2.50 per M tokens")]},
    "mistral": {  # https://docs.mistral.ai/capabilities/vision (image_url is a plain string)
        "name": "Mistral", "kind": "openai", "env": "MISTRAL_API_KEY", "keyHint": "32 characters",
        "keyUrl": "https://console.mistral.ai/api-keys", "keyOptional": False,
        "baseUrl": "https://api.mistral.ai/v1", "thinking": None,
        "models": [_m("mistral-medium-latest", "Mistral Medium", "Mistral's vision workhorse (Medium 3.5)",
                      ["mistral-small-latest"]),
                   _m("mistral-large-latest", "Mistral Large", "Largest Mistral model with vision", ["mistral-medium-latest"]),
                   _m("mistral-small-latest", "Mistral Small (budget)", "Cheap Mistral vision model")]},
    "openrouter": {  # https://openrouter.ai/docs/api/reference/overview ; ids from GET /api/v1/models (input_modalities)
        "name": "OpenRouter", "kind": "openai", "env": "OPENROUTER_API_KEY", "keyHint": "sk-or-…",
        "keyUrl": "https://openrouter.ai/keys", "keyOptional": False,
        "baseUrl": "https://openrouter.ai/api/v1", "thinking": None,
        "models": [_m("google/gemini-3.8-flash", "Gemini 3.8 Flash (OpenRouter)", "Paid Gemini without the free-tier quota",
                      ["qwen/qwen3.8-flash"]),
                   _m("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 (OpenRouter)", "Accurate; $2/$10 per M tokens",
                      ["google/gemini-3.8-flash"]),
                   _m("openai/gpt-6.1-sol", "GPT-6.1 Sol (OpenRouter)", "$2/$10 per M tokens", ["google/gemini-3.8-flash"]),
                   _m("qwen/qwen3.8-flash", "Qwen 3.8 Flash (OpenRouter)", "Very cheap and fast")]},
    "custom": {  # Ollama https://docs.ollama.com/api/openai-compatibility , LM Studio https://lmstudio.ai/docs/app/api/endpoints/openai
        "name": "Custom / local (OpenAI-compatible)", "kind": "openai", "env": "CUSTOM_API_KEY", "keyHint": "optional",
        "keyUrl": "", "keyOptional": True, "baseUrl": "", "thinking": None,
        "models": []},   # discovered from the server (e.g. qwen3-vl or gemma3 in Ollama) or typed by the user
}
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_PREFIXES = [("sk-ant-", "anthropic"), ("sk-sp-", "qwen"), ("sk-ws-", "qwen"), ("sk-or-", "openrouter"),
             ("sk-proj-", "openai"), ("sk-svcacct-", "openai"), ("xai-", "xai"), ("AIza", "gemini"), ("AQ.", "gemini")]

Part = object  # str | bytes
IMAGE_RESOLUTION = "MEDIA_RESOLUTION_ULTRA_HIGH"
DAILY = float("inf")         # retry_after of a 429 that means "quota used up for today"
_base_cache: Dict[Tuple[str, str, str], str] = {}
_exhausted: Dict[Tuple[str, str], float] = {}     # (key tail, model) -> time until which it is skipped


class LLMError(Exception):
    """kind: auth | bad_request | rate | overload | network | model | truncated | cancelled | other"""

    def __init__(self, message: str, kind: str = "other", status: Optional[int] = None, retry_after: float = 0):
        super().__init__(message)
        self.kind, self.status, self.retry_after = kind, status, retry_after


def provider_of(model: str, catalog: List[dict]) -> str:
    """Best guess for callers that only know a model id; prefer passing the provider explicitly."""
    for m in catalog:
        if m["id"] == model:
            return m["provider"]
    for prefix, p in [("qwen", "qwen"), ("deepseek", "deepseek"), ("claude", "anthropic"), ("gpt", "openai"),
                      ("grok", "xai"), ("mistral", "mistral"), ("ministral", "mistral"), ("pixtral", "mistral")]:
        if model.startswith(prefix):
            return p
    return "openrouter" if "/" in model else "gemini"


def guess_provider(key: str) -> Optional[str]:
    """Provider implied by a key's prefix; None when it can't tell (e.g. a plain sk- key)."""
    return next((p for prefix, p in _PREFIXES if key.startswith(prefix)), None)


def key_problem(provider: str, key: str) -> str:
    """Why this key obviously can't belong to `provider` ('' when it plausibly can)."""
    if provider == "custom" or not key:
        return ""
    name = PROVIDERS.get(provider, {}).get("name", provider)
    guess = guess_provider(key)
    if provider == "deepseek" and key.startswith("sk-sp-"):
        return "This is a Qwen Token Plan key (sk-sp-...). Use it under Qwen, or pick the DeepSeek model listed under Qwen."
    if provider == "gemini" and key.startswith("sk-") and not guess:
        return "This looks like a Qwen/DeepSeek/OpenAI key (starts with sk-), not a Google Gemini key (AIza... or AQ....)."
    if guess and guess != provider:
        g = PROVIDERS[guess]
        return f"This looks like a {g['name']} key ({g['keyHint']}), not a {name} key."
    return ""


def base_candidates(provider: str, key: str, override: str = "") -> List[str]:
    override = override.strip().rstrip("/")
    if provider in ("qwen", "deepseek"):
        # Token Plan keys (sk-sp-) serve Qwen and DeepSeek models on the plan hosts only
        bases = QWEN_PLAN if key.startswith("sk-sp-") else QWEN_PAYG if provider == "qwen" else DEEPSEEK
        return [override] + [b for b in bases if b != override] if override else list(bases)
    if override:
        return [override]
    if provider == "custom":
        env = os.environ.get("CUSTOM_BASE_URL", "").strip().rstrip("/")
        return [env] if env else list(LOCAL)
    return [PROVIDERS[provider]["baseUrl"]]


def _headers(provider: str, key: str) -> Dict[str, str]:
    if PROVIDERS[provider]["kind"] == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    h = {"Authorization": f"Bearer {key}"} if key else {}
    if provider == "openrouter":     # optional attribution
        h.update({"HTTP-Referer": "http://localhost:5050", "X-Title": "SheetXML", "X-OpenRouter-Title": "SheetXML"})
    return h


def _daily(text: str) -> bool:
    """A 429 for a used-up daily quota (Gemini free tier: quotaId ...PerDay..., or no per-minute metric named)."""
    return "PerDay" in text or "per day" in text.lower() or ("Quota exceeded for metric" in text and "PerMinute" not in text)


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
        return LLMError(f"HTTP 429 rate limit: {msg}", "rate", status, DAILY if wait > 60 or _daily(body) else wait)
    if status >= 500 or status in (408, 409):       # Anthropic 529 = overloaded
        return LLMError(f"HTTP {status}: {msg}", "overload", status, wait)
    return LLMError(f"HTTP {status}: {msg}", "bad_request", status)   # incl. 402 billing / out of credits


# ------------------------------------------------------------------ discovery / key check

def discover(provider: str, key: str, base_url: str = "", timeout: float = 25) -> Tuple[str, List[str]]:
    """Cheap, token-free authenticated call. Returns (working base url, model ids)."""
    if provider == "gemini":
        try:
            with genai.Client(api_key=key, http_options=types.HttpOptions(
                    timeout=int(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1))) as client:
                return "", [m.name.replace("models/", "") for m in client.models.list()]
        except Exception as e:
            raise _map_gemini(e)
    tried, headers = [], _headers(provider, key)
    for base in base_candidates(provider, key, base_url):
        try:
            if provider == "openrouter":       # its /models is public; /key is what checks the key
                r = httpx.get(base + "/key", headers=headers, timeout=timeout)
                if r.status_code != 200:
                    tried.append(f"{base}: {_error_from_http(r.status_code, r.text)}")
                    continue
            r = httpx.get(base + "/models", headers=headers, timeout=timeout,
                          params={"limit": 1000} if provider == "anthropic" else None)
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            tried.append(f"{base}: {type(e).__name__}")
            continue
        if r.status_code == 200:
            try:
                ids = [m["id"] for m in r.json().get("data", [])]
            except Exception:
                ids = []
            _base_cache[(provider, key[-8:], base_url)] = base
            return base, ids
        tried.append(f"{base}: {_error_from_http(r.status_code, r.text)}")
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
            wait = float(m.group(1)) if m else 0
            return LLMError(f"Gemini rate limit: {msg}", "rate", code, DAILY if wait > 60 or _daily(str(e)) else wait)
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
        with genai.Client(api_key=key, http_options=types.HttpOptions(       # closes its pooled connections
                timeout=int(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1))) as client:
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


# ------------------------------------------------------------------ SSE transports (Anthropic, OpenAI-compatible)

def _b64(p: bytes) -> str:
    return base64.b64encode(p).decode()


def _sse_lines(r: httpx.Response):
    """Lines split at CR, LF or CRLF only. httpx's iter_lines uses str.splitlines, which also breaks at
    U+2028/U+0085 inside raw-UTF-8 JSON and so silently drops that chunk of the answer."""
    buf = ""
    for text in r.iter_text():
        *done, buf = re.split(r"\r\n|\r|\n", buf + text)
        yield from done
    if buf:
        yield buf


def _sse(url: str, body: dict, headers: Dict[str, str], timeout: float, cancel, on_event: Callable[[dict], None]):
    """POST a streaming request and hand each `data:` JSON object to on_event (stops at [DONE] or when it returns True)."""
    try:
        with httpx.stream("POST", url, json=body, headers=headers,
                          timeout=httpx.Timeout(connect=20, read=timeout, write=60, pool=20)) as r:
            if r.status_code != 200:
                r.read()
                raise _error_from_http(r.status_code, r.text, r.headers.get("retry-after", ""))
            for line in _sse_lines(r):
                if cancel and cancel():
                    raise LLMError("Cancelled", "cancelled")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if isinstance(event, dict) and on_event(event):
                    break
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise LLMError(f"Network error: {type(e).__name__}: {e}", "network")


_ANTHROPIC_KINDS = {"overloaded_error": "overload", "api_error": "overload", "timeout_error": "overload",
                    "rate_limit_error": "rate", "authentication_error": "auth", "permission_error": "auth",
                    "billing_error": "bad_request", "invalid_request_error": "bad_request", "not_found_error": "model"}


def _anthropic(base, model, key, system, parts, extra, timeout, cancel, on_chunk) -> str:
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(p)}}
               if isinstance(p, bytes) else {"type": "text", "text": p} for p in parts]
    body = {"model": model, "system": system, "stream": True, **extra, "messages": [{"role": "user", "content": content}]}
    out, state = [], {"stop": None}

    def on_event(ev: dict) -> bool:
        t = ev.get("type")
        if t == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
            piece = ev["delta"].get("text", "")
            out.append(piece)
            if on_chunk and piece:
                on_chunk(piece)
        elif t == "message_delta":
            state["stop"] = (ev.get("delta") or {}).get("stop_reason") or state["stop"]
        elif t == "error":               # mid-stream error after a 200
            err = ev.get("error") or {}
            raise LLMError(f"Claude: {err.get('type')}: {err.get('message', '')}"[:300],
                           _ANTHROPIC_KINDS.get(err.get("type"), "other"))
        return t == "message_stop"

    _sse(base + "/messages", body, {**_headers("anthropic", key), "content-type": "application/json"},
         timeout, cancel, on_event)
    text = "".join(out)
    if state["stop"] == "max_tokens":
        raise LLMError("Output hit the token limit before finishing.", "truncated")
    if state["stop"] == "refusal":
        raise LLMError("Claude declined this request.", "model")
    if not text.strip():
        raise LLMError(f"Claude returned no text (stop: {state['stop']}).", "overload")
    return text


def _openai(base, model, key, system, parts, extra, headers, timeout, cancel, on_chunk, image_str=False) -> str:
    def image(p: bytes):
        url = "data:image/png;base64," + _b64(p)
        return url if image_str else {"url": url}          # Mistral documents image_url as a plain string
    content = [{"type": "image_url", "image_url": image(p)} if isinstance(p, bytes) else {"type": "text", "text": p}
               for p in parts]
    body = {"model": model, "stream": True, **extra,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    out, state = [], {"finish": None}

    def on_event(ev: dict) -> bool:
        if isinstance(ev.get("error"), dict):     # OpenRouter & co. report upstream failures inside the stream
            err = ev["error"]
            code = err.get("code") if isinstance(err.get("code"), int) else 502
            raise _error_from_http(code, json.dumps({"error": err}))
        try:
            choice = ev["choices"][0]
        except (KeyError, IndexError, TypeError):
            return False
        piece = (choice.get("delta") or {}).get("content")
        if piece:
            out.append(piece)
            if on_chunk:
                on_chunk(piece)
        state["finish"] = choice.get("finish_reason") or state["finish"]
        return False

    _sse(base + "/chat/completions", body, headers, timeout, cancel, on_event)
    text = "".join(out)
    if state["finish"] == "length":
        raise LLMError("Output hit the token limit before finishing.", "truncated")
    if not text.strip():
        raise LLMError(f"The model returned no text (finish: {state['finish']}).", "overload")
    return text


def _drop_rejected(extra: dict, msg: str) -> bool:
    """Adapt optional request fields after a 400 that names one; True when something changed."""
    if "max_completion_tokens" in msg and "max_tokens" in extra:      # OpenAI reasoning models
        extra["max_completion_tokens"] = extra.pop("max_tokens")
        return True
    if "enable_thinking" in msg and extra.get("enable_thinking") is False:
        extra["enable_thinking"] = True          # e.g. glm-5.3 only runs with thinking on
        return True
    for name, words in [("vl_high_resolution_images", ["vl_high_resolution_images"]),
                        ("reasoning_effort", ["reasoning_effort", "reasoning effort"]),
                        ("reasoning", ["reasoning"]), ("thinking", ["thinking"]),
                        ("output_config", ["output_config", "effort"])]:
        if name in extra and any(w in msg for w in words):
            extra.pop(name)
            return True
    return False


# ------------------------------------------------------------------ public entry points

def generate(provider: str, model: str, key: str, system: str, parts: List[Part], *, base_url: str = "",
             max_tokens: int = 16384, thinking: Optional[str] = None, timeout: float = 300,
             cancel: Optional[Callable[[], bool]] = None, on_chunk: Optional[Callable[[str], None]] = None) -> str:
    """One streamed completion. `thinking`: Gemini thinking level; 'off'/'on' for Qwen/DeepSeek; an effort level
    (low/medium/high/...) for Anthropic, OpenAI, xAI, OpenRouter and custom servers."""
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider: {provider}", "bad_request")
    problem = key_problem(provider, key)
    if problem:
        raise LLMError(problem, "auth")
    if provider == "gemini":
        return _gemini(model, key, system, parts, max_tokens, thinking, timeout, cancel, on_chunk)
    candidates = base_candidates(provider, key, base_url)
    base = _base_cache.get((provider, key[-8:], base_url)) or (
        candidates[0] if len(candidates) == 1 else discover(provider, key, base_url)[0])
    extra: dict = {"max_tokens": max_tokens}
    if provider == "anthropic":
        if thinking in EFFORTS[2:] and "haiku" not in model:      # Haiku 4.5 rejects effort
            extra["output_config"] = {"effort": thinking}
    elif provider == "qwen" or key.startswith("sk-sp-"):
        extra["vl_high_resolution_images"] = True
        if thinking in ("on", "off"):
            extra["enable_thinking"] = thinking == "on"
    elif provider == "deepseek":
        if thinking in ("on", "off"):
            extra["thinking"] = {"type": "enabled" if thinking == "on" else "disabled"}
    else:
        if provider == "openai":
            extra["max_completion_tokens"] = extra.pop("max_tokens")   # max_tokens is rejected by reasoning models
        if thinking in EFFORTS:
            if provider == "openrouter":
                extra["reasoning"] = {"effort": thinking}
            else:
                extra["reasoning_effort"] = thinking
    for attempt in range(5):
        try:
            if provider == "anthropic":
                return _anthropic(base, model, key, system, parts, extra, timeout, cancel, on_chunk)
            return _openai(base, model, key, system, parts, extra, _headers(provider, key), timeout, cancel,
                           on_chunk, image_str=provider == "mistral")
        except LLMError as e:
            if e.kind != "bad_request" or attempt == 4 or not _drop_rejected(extra, str(e)):
                raise
    raise AssertionError("unreachable")


def _cancellable(fn: Callable[[], str], cancel: Optional[Callable[[], bool]]) -> str:
    """Run a blocking request on a helper thread and give up on it within ~0.5 s of a cancel, even while it
    waits for its first token (the abandoned stream stops at its next chunk or read timeout)."""
    if not cancel:
        return fn()
    box: dict = {}

    def run():
        try:
            box["text"] = fn()
        except BaseException as e:          # handed to the caller below
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.5)
        if cancel():
            raise LLMError("Cancelled", "cancelled")
    if "error" in box:
        raise box["error"]
    return box["text"]


def sleep_unless_cancelled(seconds: float, cancel: Optional[Callable[[], bool]]):
    end = time.time() + seconds
    while time.time() < end:
        if cancel and cancel():
            raise LLMError("Cancelled", "cancelled")
        time.sleep(0.25)


def generate_with_fallback(provider: str, models: List[str], key: str, system: str, parts: List[Part],
                           notify: Optional[Callable[[str], None]] = None, **kw) -> Tuple[str, str]:
    """Try each model in turn; retry transient failures with backoff. Returns (text, model used).

    A model whose daily quota is used up is skipped at once (and for the next hour, by every caller)."""
    last: Optional[LLMError] = None
    daily = []
    for model in models:
        if _exhausted.get((key[-8:], model), 0) > time.time():
            daily.append(model)
            if notify:
                notify(f"{model} unavailable (daily quota used up); trying next model")
            continue
        for attempt in range(3):
            try:
                return _cancellable(lambda: generate(provider, model, key, system, parts, **kw), kw.get("cancel")), model
            except LLMError as e:
                last = e
                if e.kind in ("auth", "bad_request", "cancelled", "truncated"):
                    raise
                if e.kind == "rate" and e.retry_after > 60:
                    _exhausted[(key[-8:], model)] = time.time() + 3600
                    daily.append(model)
                    break
                if e.kind == "model":
                    break
                wait = min(e.retry_after, 60) or min(20, 3 * 2 ** attempt) + random.random()   # a 503's Retry-After can be hours
                if notify:
                    notify(f"{model}: {e.kind} - retrying in {wait:.0f}s")
                sleep_unless_cancelled(wait, kw.get("cancel"))
        if notify and last and model is not models[-1]:
            notify(f"{model} unavailable ({'daily quota used up' if model in daily else last.kind}); trying next model")
    if daily and len(daily) == len(models):
        raise LLMError("Gemini's free daily quota is used up for these models — try again tomorrow or choose a "
                       "Qwen/DeepSeek model" if provider == "gemini" else
                       "The daily quota of these models is used up — try again later or choose another model.", "rate")
    raise last or LLMError("No model could be called.", "other")



def _tiny_png(size: int = 32) -> bytes:
    """A white square with a black block, stdlib only (for the self-check and live probes)."""
    import struct
    import zlib
    rows = b"".join(b"\0" + bytes(0 if 8 <= x < 24 and 8 <= y < 24 else 255 for x in range(size)) for y in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _mock_server():
    """127.0.0.1 server speaking the OpenAI chat-completions and Anthropic messages SSE formats. Returns (base, log)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    log: List[dict] = []
    counts: Dict[str, int] = {}

    def oai(text, finish="stop"):
        ev = [{"id": "c1", "object": "chat.completion.chunk", "model": "m",
               "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}]
        ev += [{"id": "c1", "object": "chat.completion.chunk", "model": "m",
                "choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]} for t in text]
        ev += [{"id": "c1", "object": "chat.completion.chunk", "model": "m",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in ev) + "data: [DONE]\n\n"

    def ant(text, stop="end_turn", thinking=False, error=None):
        ev = [("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "content": [], "model": "claude",
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}})]
        i = 0
        if thinking:
            ev += [("content_block_start", {"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "thinking_delta", "thinking": "SECRET"}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "signature_delta", "signature": "abc"}}),
                   ("content_block_stop", {"type": "content_block_stop", "index": 0})]
            i = 1
        ev += [("content_block_start", {"type": "content_block_start", "index": i,
                                        "content_block": {"type": "text", "text": ""}}), ("ping", {"type": "ping"})]
        ev += [("content_block_delta", {"type": "content_block_delta", "index": i,
                                        "delta": {"type": "text_delta", "text": t}}) for t in text]
        if error:
            ev += [("error", {"type": "error", "error": {"type": error, "message": "Overloaded"}})]
        else:
            ev += [("content_block_stop", {"type": "content_block_stop", "index": i}),
                   ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                      "usage": {"output_tokens": 15}}),
                   ("message_stop", {"type": "message_stop"})]
        return "".join(f"event: {n}\ndata: {json.dumps(e)}\n\n" for n, e in ev)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, status, body, ctype="application/json", headers=()):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def oai_err(self, status, msg, typ, headers=(), param=None, code=None):
            self.send(status, json.dumps({"error": {"message": msg, "type": typ, "param": param, "code": code}}),
                      headers=headers)

        def ant_err(self, status, typ, msg, headers=()):
            self.send(status, json.dumps({"type": "error", "error": {"type": typ, "message": msg},
                                          "request_id": "req_1"}), headers=headers)

        def do_GET(self):
            auth, xkey = self.headers.get("Authorization", ""), self.headers.get("x-api-key", "")
            if self.path.startswith("/v1/models"):
                if xkey:
                    if xkey != "sk-ant-good" or self.headers.get("anthropic-version") != ANTHROPIC_VERSION:
                        return self.ant_err(401, "authentication_error", "invalid x-api-key")
                    return self.send(200, json.dumps({"data": [{"type": "model", "id": "claude-ok"}],
                                                      "has_more": False, "first_id": "claude-ok", "last_id": "claude-ok"}))
                if auth not in ("Bearer good", "", "Bearer sk-or-good"):
                    return self.oai_err(401, "Incorrect API key provided", "invalid_request_error", code="invalid_api_key")
                return self.send(200, json.dumps({"object": "list", "data": [{"id": "ok", "object": "model"}]}))
            if self.path == "/v1/key":
                if auth != "Bearer sk-or-good":
                    return self.send(401, json.dumps({"error": {"code": 401, "message": "No auth credentials found"}}))
                return self.send(200, json.dumps({"data": {"label": "x"}}))
            self.send(404, "{}")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            log.append({"path": self.path, "body": body, "headers": dict(self.headers)})
            m = body["model"]
            counts[m] = counts.get(m, 0) + 1
            sse = "text/event-stream"
            if self.path == "/v1/messages":
                assert self.headers["x-api-key"] and self.headers["anthropic-version"] == ANTHROPIC_VERSION
                if m == "claude-529":
                    return self.ant_err(529, "overloaded_error", "Overloaded", [("retry-after", "0.01")])
                if m == "claude-429" and counts[m] == 1:
                    return self.ant_err(429, "rate_limit_error", "Number of request tokens has exceeded your "
                                        "per-minute rate limit", [("retry-after", "1")])
                if m == "claude-effort" and "output_config" in body:
                    return self.ant_err(400, "invalid_request_error", "output_config.effort: Extra inputs are not permitted")
                if m == "claude-max":
                    return self.send(200, ant(["cut"], stop="max_tokens"), sse)
                if m == "claude-midstream":
                    return self.send(200, ant(["par"], error="overloaded_error"), sse)
                return self.send(200, ant(["Hel", "lo"], thinking=m == "claude-thinking"), sse)
            if m == "unauth":
                return self.oai_err(401, "Incorrect API key provided", "invalid_request_error", code="invalid_api_key")
            if m == "rate1" and counts[m] == 1:
                return self.oai_err(429, "Rate limit reached for requests", "requests", [("retry-after", "1")])
            if m == "busy":
                return self.oai_err(503, "The server is overloaded", "server_error", [("retry-after", "0.01")])
            if m == "reasoner" and "max_tokens" in body:
                return self.oai_err(400, "Unsupported parameter: 'max_tokens' is not supported with this model. "
                                    "Use 'max_completion_tokens' instead.", "invalid_request_error",
                                    param="max_tokens", code="unsupported_parameter")
            if m == "long":
                return self.send(200, oai(["abc"], "length"), sse)
            if m == "unicode":      # raw UTF-8 line separators inside the JSON strings
                return self.send(200, oai(["A\u2028B", "C\u0085D", "\u00e9"]), sse)
            if m == "upstream":     # OpenRouter-style mid-stream error
                return self.send(200, "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk",
                                 "error": {"code": 502, "message": "Provider returned error"},
                                 "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}]})
                                 + "\n\n", sse)
            return self.send(200, oai(["Hel", "lo"]), sse)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/v1", log


def _selfcheck():
    """No-API checks: key guessing, quota classification, both SSE formats against a local mock, fallback, cancel."""
    global generate
    assert guess_provider("sk-ant-api03-x") == "anthropic" and guess_provider("sk-sp-1") == "qwen"
    assert guess_provider("sk-or-v1-x") == "openrouter" and guess_provider("xai-abc") == "xai"
    assert guess_provider("sk-proj-x") == "openai" and guess_provider("AIzaX") == "gemini" and guess_provider("sk-abc") is None
    assert key_problem("anthropic", "sk-proj-x") and key_problem("openai", "sk-ant-x") and key_problem("gemini", "sk-x")
    assert not key_problem("openai", "sk-abc") and not key_problem("custom", "anything") and not key_problem("qwen", "sk-sp-1")
    assert key_problem("deepseek", "sk-sp-1") and key_problem("qwen", "AIzaX") and not key_problem("anthropic", "sk-ant-1")
    assert list(PROVIDERS)[:2] == ["gemini", "anthropic"] and all(
        {"name", "kind", "env", "keyHint", "keyUrl", "keyOptional", "baseUrl", "models", "thinking"} <= set(v)
        for v in PROVIDERS.values())
    assert _error_from_http(429, '{"error": {"message": "Quota exceeded for metric: x, quotaId: '
                            'GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}').retry_after == DAILY
    assert _error_from_http(429, '{"error": {"message": "Quota exceeded for metric: GenerateRequestsPerMinute"}}',
                            "20").retry_after == 20

    base, log = _mock_server()
    img, kw = _tiny_png(), {"base_url": base, "timeout": 20}

    def kind_of(fn):
        try:
            fn()
        except LLMError as e:
            return e.kind
        return None

    # OpenAI-compatible: text, image format, auth header, quirks
    assert generate("custom", "ok", "good", "sys", ["hi", img], **kw) == "Hello"
    body = log[-1]["body"]
    assert body["messages"][0] == {"role": "system", "content": "sys"} and body["max_tokens"] == 16384
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert log[-1]["headers"]["Authorization"] == "Bearer good" and "temperature" not in body
    assert generate("custom", "ok", "", "s", ["x"], **kw) == "Hello" and "Authorization" not in log[-1]["headers"]
    assert generate("mistral", "ok", "good", "s", [img], **kw) == "Hello"
    assert isinstance(log[-1]["body"]["messages"][1]["content"][0]["image_url"], str)
    assert generate("custom", "reasoner", "good", "s", ["x"], **kw) == "Hello"
    assert "max_tokens" not in log[-1]["body"] and log[-1]["body"]["max_completion_tokens"] == 16384
    assert generate("openai", "ok", "sk-proj-good", "s", ["x"], thinking="low", **kw) == "Hello"
    assert log[-1]["body"]["reasoning_effort"] == "low" and "max_completion_tokens" in log[-1]["body"]
    assert generate("openrouter", "ok", "sk-or-good", "s", ["x"], thinking="high", **kw) == "Hello"
    assert log[-1]["body"]["reasoning"] == {"effort": "high"} and log[-1]["headers"]["X-Title"] == "SheetXML"
    assert generate("custom", "unicode", "good", "s", ["x"], **kw) == "A\u2028BC\u0085D\u00e9"
    assert kind_of(lambda: generate("custom", "unauth", "bad", "s", ["x"], **kw)) == "auth"
    assert kind_of(lambda: generate("custom", "busy", "good", "s", ["x"], **kw)) == "overload"
    assert kind_of(lambda: generate("custom", "long", "good", "s", ["x"], **kw)) == "truncated"
    assert kind_of(lambda: generate("openrouter", "upstream", "sk-or-good", "s", ["x"], **kw)) == "overload"
    notes: List[str] = []
    assert generate_with_fallback("custom", ["rate1"], "good", "s", ["x"], notify=notes.append, **kw) == ("Hello", "rate1")
    assert notes == ["rate1: rate - retrying in 1s"], notes
    notes.clear()
    t0 = time.time()
    assert generate_with_fallback("custom", ["busy", "ok"], "good", "s", ["x"], notify=notes.append, **kw) == ("Hello", "ok")
    assert sum("busy: overload" in n for n in notes) == 3 and time.time() - t0 < 5, notes

    # Anthropic Messages: SSE text, thinking hidden, image block, errors
    assert generate("anthropic", "claude-thinking", "sk-ant-good", "sys", ["hi", img], thinking="low", **kw) == "Hello"
    body = log[-1]["body"]
    assert body["system"] == "sys" and body["max_tokens"] == 16384 and body["output_config"] == {"effort": "low"}
    assert body["messages"][0]["content"][1]["source"]["type"] == "base64" and body["messages"][0]["content"][1]["source"]["media_type"] == "image/png"
    generate("anthropic", "claude-haiku-4-5", "sk-ant-good", "s", ["x"], thinking="low", **kw)
    assert "output_config" not in log[-1]["body"]
    assert generate("anthropic", "claude-effort", "sk-ant-good", "s", ["x"], thinking="low", **kw) == "Hello"
    assert "output_config" not in log[-1]["body"]
    assert kind_of(lambda: generate("anthropic", "claude-max", "sk-ant-good", "s", ["x"], **kw)) == "truncated"
    assert kind_of(lambda: generate("anthropic", "claude-midstream", "sk-ant-good", "s", ["x"], **kw)) == "overload"
    assert kind_of(lambda: generate("anthropic", "claude-529", "sk-ant-good", "s", ["x"], **kw)) == "overload"
    assert generate_with_fallback("anthropic", ["claude-429"], "sk-ant-good", "s", ["x"], **kw) == ("Hello", "claude-429")
    assert generate_with_fallback("anthropic", ["claude-529", "claude-ok"], "sk-ant-good", "s", ["x"], **kw)[1] == "claude-ok"

    # discovery
    assert discover("anthropic", "sk-ant-good", base) == (base, ["claude-ok"])
    assert kind_of(lambda: discover("anthropic", "sk-ant-bad", base)) == "auth"
    assert discover("openai", "good", base) == (base, ["ok"])
    assert kind_of(lambda: discover("xai", "xai-bad", base)) == "auth"
    assert discover("openrouter", "sk-or-good", base)[1] == ["ok"]
    assert kind_of(lambda: discover("openrouter", "sk-or-bad", base)) == "auth"
    assert kind_of(lambda: discover("custom", "", "http://127.0.0.1:9/v1", timeout=2)) == "network"

    # daily-quota fallback and cancel while a request hangs (patched transport)
    calls = []

    def fake(provider, model, key, system, parts, **kw):
        calls.append(model)
        if model == "slow":
            time.sleep(30)
        raise LLMError("quota", "rate", 429, DAILY)
    real, generate = generate, fake
    try:
        try:
            generate_with_fallback("gemini", ["a", "b"], "AIzaTESTKEY1", "", ["x"])
        except LLMError as e:
            assert e.kind == "rate" and "daily quota" in str(e) and calls == ["a", "b"], (e, calls)
        try:                                                              # both now skipped without a call
            generate_with_fallback("gemini", ["a", "b"], "AIzaTESTKEY1", "", ["x"])
        except LLMError as e:
            assert calls == ["a", "b"] and e.kind == "rate"
        t0, stop_at = time.time(), time.time() + 1
        try:
            generate_with_fallback("qwen", ["slow"], "sk-TESTKEY2", "", ["x"], cancel=lambda: time.time() > stop_at)
        except LLMError as e:
            assert e.kind == "cancelled" and time.time() - t0 < 2.5, (e.kind, time.time() - t0)
    finally:
        generate = real
    print("llm self-check ok")


def _live():
    """One tiny real call per configured key (Gemini may be out of quota: then the error kind must say so)."""
    from pathlib import Path
    env = {}
    for line in (Path(__file__).with_name(".env")).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    q = "What shape and colour is in the middle of this image? Answer in five words."
    probes = [("gemini", "gemini-3.8-flash", env.get("GEMINI_API_KEY", "")),
              ("qwen", "qwen3.8-flash", env.get("QWEN_API_KEY", "")),
              ("qwen", "deepseek-v4.1-flash", env.get("QWEN_API_KEY", ""))]
    for provider, model, key in probes:
        t0 = time.time()
        try:
            text = generate(provider, model, key, "Answer briefly.", [_tiny_png(64), q], max_tokens=512,
                            thinking=PROVIDERS[provider]["thinking"], timeout=90)
            print(f"{provider}/{model}: ok in {time.time() - t0:.1f}s: {text.strip()[:80]!r}")
        except LLMError as e:
            assert e.kind in ("rate", "overload"), (provider, model, e.kind, str(e)[:200])
            print(f"{provider}/{model}: {e.kind} (acceptable): {str(e).splitlines()[0][:120]}")


if __name__ == "__main__":
    import sys
    _live() if "--live" in sys.argv else _selfcheck()
