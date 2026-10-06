"""
SheetXML - sheet music (PDF / images / MusicXML / ABC) to MusicXML 4.0 with a mandolin-family TAB staff.

Engines (see README.md):
  vector     exact reader for born-digital PDFs (vectorpdf), no AI
  audiveris  free offline OMR (audiveris), when installed
  hybrid     Audiveris + the code's notehead reader + a vision model; systems they agree on cost no AI call
  ai         vision-model pipeline alone, one staff system at a time (pipeline)
  auto       import / vector, else Audiveris then hybrid when a key exists, else whichever of the two is available
Every engine yields an abcxml.Score; abcxml writes the MusicXML and the text tab, analysis describes the piece.
One run_job() serves the web UI and the CLI.

Paths: RES_DIR holds the read-only bundled files (static/, schemas/, resources/), DATA_DIR the user's files
(.env, sheetxml.log, sheetxml.pid, transcriptions/): the project folder from source, %LOCALAPPDATA%\\SheetXML
when frozen by PyInstaller; SHEETXML_DATA_DIR overrides it.
"""

import os
import sys
import time
import atexit
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
RES_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DATA_DIR = Path(os.environ.get("SHEETXML_DATA_DIR") or
                (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SheetXML" if FROZEN else BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "sheetxml.log"


class SafeStream:
    """stdout/stderr that never raises (a windowed exe has no console) and also appends to the log file."""
    def __init__(self, original_stream, log_path=None):
        self.original_stream = original_stream
        self.log_path = log_path

    def write(self, data):
        if not data:
            return
        if self.original_stream is not None:
            try:
                self.original_stream.write(data)
                self.original_stream.flush()
            except Exception:
                pass
        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(data)
            except Exception:
                pass

    def flush(self):
        if self.original_stream is not None:
            try:
                self.original_stream.flush()
            except Exception:
                pass

    def reconfigure(self, **kwargs):
        pass


for _s in (sys.stdout, sys.stderr):
    if _s and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
sys.stdout = SafeStream(sys.stdout, LOG_FILE)
sys.stderr = SafeStream(sys.stderr, LOG_FILE)

import argparse
import collections
import functools
import json
import re
import threading
import traceback
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from lxml import etree
    import abcxml
    import analysis
    import audiveris
    import llm
    import mandolin_optimizer as mando
    import pipeline
    import vectorpdf
except ImportError as e:
    print(f"[ERROR] {e}. Run: pip install -r requirements.txt")
    sys.exit(1)

STATIC_DIR = RES_DIR / "static"
SCHEMA_DIR = RES_DIR / "schemas"
SAMPLE_XML = RES_DIR / "resources" / "music-xml-example.xml"
CONFIG_FILE = DATA_DIR / ".env"
OUT_DIR = DATA_DIR / "transcriptions"
PID_FILE = DATA_DIR / "sheetxml.pid"
MAX_BODY = 60 * 1024 * 1024
JOB_TTL = 30 * 60
ENGINES = ("auto", "vector", "audiveris", "hybrid", "ai")
NOT_CHAT = re.compile(r"embed|tts|imagen|veo|aqa|whisper|dall-e|moderation|rerank|transcribe|realtime|audio|"
                      r"image|speech", re.I)


# ------------------------------------------------------------------ keys and providers

def _values(var: str) -> List[str]:
    """Non-empty values of `var`: environment first, then .env; whitespace removed."""
    vals = [os.environ.get(var, "")]
    try:
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(var + "="):
                vals.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
    except OSError:
        pass
    return [v for v in (re.sub(r"\s+", "", v) for v in vals) if v]


def load_key(provider: str) -> str:
    """The provider's key from its env var / .env. Older .env files kept a Qwen Token Plan key under
    GEMINI_API_KEY or DEEPSEEK_API_KEY; such keys serve the Qwen provider (DeepSeek runs on the plan too)."""
    p = llm.PROVIDERS.get(provider)
    if not p:
        return ""
    sources = [(p["env"], "")] + ([("GEMINI_API_KEY", "sk-"), ("DEEPSEEK_API_KEY", "sk-sp-")] if provider == "qwen" else [])
    for var, prefix in sources:
        for v in _values(var):
            if v.startswith(prefix) and not llm.key_problem(provider, v):
                return v
    return ""


def custom_base_url() -> str:
    return next(iter(_values("CUSTOM_BASE_URL")), "") or llm.PROVIDERS.get("custom", {}).get("baseUrl", "")


def base_url_for(provider: str, override: str = "") -> str:
    return (override or "").strip() or (custom_base_url() if provider == "custom" else "")


def ready(provider: str) -> bool:
    """A key is set, or (local endpoints) none is needed and a base URL is."""
    p = llm.PROVIDERS.get(provider) or {}
    return bool(load_key(provider)) or bool(p.get("keyOptional") and base_url_for(provider))


def save_env(var: str, value: str):
    value = re.sub(r"[\s\"']+", "", value)
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines(True) if CONFIG_FILE.exists() else []
    entry = f'{var}="{value}"\n'
    for i, line in enumerate(lines):
        if line.strip().startswith(var + "="):
            lines[i] = entry
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(entry)
    CONFIG_FILE.write_text("".join(lines), encoding="utf-8")
    os.environ[var] = value


def mask(key: str) -> str:
    return f"***{key[-4:]}" if len(key) >= 4 else ""


def default_provider() -> str:
    return next((p for p in llm.PROVIDERS if ready(p)), next(iter(llm.PROVIDERS)))


def default_model(provider: str) -> str:
    models = llm.PROVIDERS.get(provider, {}).get("models") or []
    return models[0]["id"] if models else ""


def provider_name(provider: str) -> str:
    return llm.PROVIDERS.get(provider, {}).get("name") or "The AI provider"


def model_chain(provider: str, model: str) -> List[str]:
    """The model followed by its curated fallbacks."""
    entry = next((m for m in llm.PROVIDERS[provider]["models"] if m["id"] == model), None)
    return [model] + [f for f in (entry or {}).get("fallbacks", []) if f != model]


# ------------------------------------------------------------------ validation / result

@functools.lru_cache(maxsize=1)
def _schema():
    class LocalSchemaResolver(etree.Resolver):
        def resolve(self, url, pubid, context):
            local_path = SCHEMA_DIR / url.split("/")[-1]
            return self.resolve_filename(str(local_path), context) if local_path.exists() else None

    parser = etree.XMLParser()
    parser.resolvers.add(LocalSchemaResolver())
    with open(SCHEMA_DIR / "musicxml.xsd", "rb") as f:
        return etree.XMLSchema(etree.parse(f, parser))


def validate_musicxml_schema(xml_content: str) -> Tuple[bool, List[str]]:
    """Validate MusicXML string against the local MusicXML 4.0 schema."""
    if not (SCHEMA_DIR / "musicxml.xsd").exists():
        return True, ["Schema directory not found, skipping deep XSD validation."]
    try:
        schema = _schema()
        doc = etree.fromstring(xml_content.encode("utf-8"))
        is_valid = schema.validate(doc)
        return is_valid, [f"Line {e.line}, Col {e.column}: {e.message}" for e in schema.error_log]
    except Exception as e:
        return False, [f"XML parsing/validation notice: {e}"]


def arrangement_opts(opts: Dict[str, Any]) -> Dict[str, Any]:
    """The job's arrangement dict, with the skill level filled in; ValueError when it is not playable as given."""
    arr = opts.get("arrangement") or {}
    if not isinstance(arr, dict):
        raise ValueError("The arrangement must be a JSON object.")
    arr = {"skill": opts.get("skillLevel") or "beginner", **arr}
    mando.arrangement(arr)
    return arr


def build_result(score: "abcxml.Score", opts: Dict[str, Any], filename: str, engine: str) -> Dict[str, Any]:
    """Score -> the result dict the UI and CLI show. ValueError for a bad arrangement."""
    tab = bool(opts.get("mandolinTab", True))
    arr = arrangement_opts(opts)
    xml, stats = abcxml.to_musicxml(score, tab=tab, arrangement=arr)
    ok, errors = validate_musicxml_schema(xml)
    fifths, mode = score.key
    root = etree.fromstring(xml.encode("utf-8"))
    try:
        details = analysis.analyze(score, stats if tab else None)
    except Exception:                       # never lose a finished transcription to the analysis
        traceback.print_exc()
        details = None
    info = dict(score.info or {})
    if isinstance(info.get("systems"), list):
        info["systems"] = [{k: v for k, v in s.items() if k != "log"} for s in info["systems"]]
    return {
        "musicxml": xml,
        "abc": abcxml.score_to_abc(score),
        "asciiTab": abcxml.to_ascii_tab(score, arrangement=arr) if tab else "",
        "details": details,
        "isValid": ok,
        "validationErrors": errors,
        "metadata": {
            "title": score.title or Path(filename).stem or "Transcribed Score",
            "composer": score.composer,
            "keySignature": f"{abcxml.key_name(fifths - abcxml.MODE_SHIFT.get(mode, 0), 'major')} {mode}",
            "timeSignature": f"{score.meter[0]}/{score.meter[1]}",
            "measureCount": len(score.bars),
            "tempo": round(score.tempo) if score.tempo else None,
            "clef": root.findtext(".//clef/sign") or "G",
            "parts": [p.text for p in root.iter("part-name") if p.text] or ["Part 1"],
        },
        "mandolinStats": stats if tab else None,
        "arrangement": {k: stats[k] for k in ("arrangement", "instrument", "tuning", "capo", "style",
                                               "positionsUsed", "positionShifts", "unplayable", "outOfRange",
                                               "transposedOctaves", "highestFret") if k in stats} if tab else None,
        "engine": info.get("engine") or engine,
        "modelsUsed": info.get("modelsUsed") or {},
        "info": info,
        "warnings": list(score.warnings),
        "reviewBars": stats["reviewBars"],
        "filename": filename,
    }


# ------------------------------------------------------------------ the job

class JobError(Exception):
    """A user-facing failure with a ready-to-show message."""


def friendly_error(e: BaseException, provider: str = "") -> str:
    if not isinstance(e, llm.LLMError):
        return str(e) or type(e).__name__
    who = provider_name(provider)
    detail = " ".join(str(e).split())[:240]
    if e.kind == "rate" and "daily quota" in str(e):
        return str(e)
    if e.kind == "network" and provider == "custom":
        return f"Could not reach the custom endpoint - is the server (Ollama, LM Studio...) running at that URL? ({detail})"
    return {
        "cancelled": "Cancelled.",
        "auth": f"{who} rejected the API key - check the key in Settings. ({detail})",
        "rate": f"{who} rate limit reached - wait a minute and try again, or pick another model. ({detail})",
        "overload": f"{who} is overloaded right now (high demand). Try again in a few minutes or pick another model.",
        "network": f"Could not reach {who} - check your internet connection. ({detail})",
        "model": f"This model is not available to your key - pick another model. ({detail})",
        "truncated": f"The model's answer was cut off. Try another model. ({detail})",
    }.get(e.kind, f"{who}: {detail}")


def run_job(data: bytes, filename: str, opts: Dict[str, Any],
            progress: Optional[Callable[[str, Optional[float]], None]] = None,
            cancel: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """File bytes -> result dict. opts: engine, provider, model, votes, pageRange, mandolinTab, skillLevel,
    arrangement, apiKey, baseUrl. Raises JobError (message ready to show) or llm.LLMError (see friendly_error)."""
    say = progress or (lambda msg, frac: None)
    filename = Path(filename or "score").name
    ext = Path(filename).suffix.lower()
    engine = opts.get("engine") or "auto"
    pages = str(opts.get("pageRange") or "").strip()
    if engine not in ENGINES:
        raise JobError(f"Unknown engine '{engine}'. Use one of: {', '.join(ENGINES)}.")
    try:
        arrangement_opts(opts)                      # fail now, not after minutes of reading
    except ValueError as e:
        raise JobError(f"Arrangement: {e}")

    def result(score, name):
        return build_result(score, opts, filename, name)

    # existing notation: just add the TAB staff
    if ext in (".musicxml", ".xml", ".mxl") or data[:2] == b"PK":
        say("Reading the MusicXML file", 0.5)
        try:
            score = abcxml.parse_musicxml(data)
        except Exception as e:
            raise JobError(f"Could not read this MusicXML file: {e}")
        return result(score, "import")
    if ext == ".abc":
        say("Reading the ABC file", 0.5)
        return result(abcxml.parse_abc(data.decode("utf-8-sig", "replace")), "import")   # Notepad writes a BOM

    is_pdf = data[:5] == b"%PDF-"
    if engine in ("auto", "vector"):
        if is_pdf:
            say("Trying the exact PDF reader (no AI)", 0.02)
            score = vectorpdf.transcribe(data, pages)
            if score is not None and score.bars:
                say(f"Exact PDF reader: {len(score.bars)} measures", 1.0)
                return result(score, "vector")
        if engine == "vector":
            raise JobError("The exact PDF reader only works on PDFs exported from notation software "
                           "(MuseScore, Sibelius, Finale, Dorico). This file is a scan, photo or image - "
                           "use Auto, Audiveris or an AI engine.")
        if is_pdf:
            say("No music-font glyphs in this PDF (scanned?) - using another engine", None)

    provider = opts.get("provider") or default_provider()
    if provider not in llm.PROVIDERS:
        raise JobError(f"Unknown AI provider '{provider}'. Use one of: {', '.join(llm.PROVIDERS)}.")
    model = str(opts.get("model") or "").strip() or default_model(provider)
    key = re.sub(r"\s+", "", opts.get("apiKey") or "") or load_key(provider)
    base_url = base_url_for(provider, opts.get("baseUrl") or "")
    has_ai = bool(model) and (bool(key) or bool(llm.PROVIDERS[provider].get("keyOptional") and base_url))
    who = provider_name(provider)

    aud = None
    if engine in ("audiveris", "hybrid") or (engine == "auto" and audiveris.find()):
        if engine == "hybrid" and not has_ai:
            raise JobError(f"The hybrid engine needs an AI key: add one for {who} in Settings, or pick another engine.")
        try:
            aud = audiveris.transcribe(data, filename, pages, progress=say, cancel=cancel)
        except RuntimeError as e:
            if engine != "auto":
                raise JobError(str(e))
            say(f"Audiveris failed: {str(e)[:200]}", None)
        if engine == "audiveris" or (aud is not None and not has_ai):
            if aud.problems() and engine == "auto":
                aud.warnings.append("Some measures do not add up; add an AI key in Settings and the hybrid "
                                    "engine will check every system against the image.")
            return result(aud, "audiveris")

    if engine in ("ai", "hybrid") or (engine == "auto" and has_ai):
        if not has_ai:
            raise JobError(f"No API key for {who} - add one in Settings (or pass -k on the command line)"
                           + (", and set its base URL" if provider == "custom" else "") + ".")
        entry = next((m for m in llm.PROVIDERS[provider]["models"] if m["id"] == model), {})
        if entry.get("vision") is False:
            raise JobError(f"{entry.get('name', model)} cannot read images - pick another model to convert with "
                           "(it can still write the AI note).")
        problem = llm.key_problem(provider, key) if key else ""
        if problem:
            raise llm.LLMError(problem, "auth")
        say("Checking the API key", 0.0)
        try:
            llm.discover(provider, key, base_url, timeout=15)   # token-free; a bad key fails here, not later
        except llm.LLMError as e:
            if e.kind == "auth":
                raise
        hints = aud if aud is not None and aud.bars else None
        votes = 3 if str(opts.get("votes", 1)) == "3" else 1
        say(("Hybrid: checking Audiveris's reading with " if hints else "AI pipeline with ") + model
            + (" (3 readings per system)" if votes == 3 else ""), 0.0)
        score = pipeline.transcribe(data, provider=provider, models=model_chain(provider, model), key=key,
                                    base_url=base_url, page_range=pages, votes=votes, progress=say, cancel=cancel,
                                    thinking=llm.PROVIDERS[provider].get("thinking"), hints=hints)
        out = result(score, "hybrid" if hints else "ai")
        out["provider"], out["requestedModel"] = provider, model
        return out

    raise JobError(
        "No engine can read this file: " + ("it is not a PDF exported from notation software, " if is_pdf else "") +
        f"there is no API key for {who} (add one in Settings), and " +
        ("Audiveris could not read it." if audiveris.find() else
         "Audiveris is not installed (free offline OMR: https://github.com/Audiveris/audiveris/releases)."))


# ------------------------------------------------------------------ background jobs

JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def start_job(data: bytes, filename: str, opts: Dict[str, Any]) -> str:
    job = {"id": uuid.uuid4().hex, "status": "running", "progress": 0.0, "log": collections.deque(maxlen=50),
           "result": None, "error": None, "cancel": threading.Event(), "finished": 0.0}
    with JOBS_LOCK:
        now = time.time()
        for jid in [j for j, v in JOBS.items() if v["finished"] and now - v["finished"] > JOB_TTL]:
            del JOBS[jid]
        JOBS[job["id"]] = job

    def say(msg, frac):
        with JOBS_LOCK:
            if msg:
                job["log"].append(msg)
            if frac is not None:
                job["progress"] = max(job["progress"], min(1.0, float(frac)))
        if msg:
            print(f"[SheetXML job {job['id'][:6]}] {msg}")

    def work():
        status, result, error = "done", None, None
        try:
            result = run_job(data, filename, opts, say, job["cancel"].is_set)
        except Exception as e:
            status, error = "error", friendly_error(e, opts.get("provider") or default_provider())
            if not isinstance(e, (JobError, llm.LLMError)):
                traceback.print_exc()
        if job["cancel"].is_set():
            status, result, error = "cancelled", None, "Cancelled."
        with JOBS_LOCK:
            job.update(status=status, result=result, error=error, finished=time.time(),
                       progress=1.0 if status == "done" else job["progress"])
        print(f"[SheetXML job {job['id'][:6]}] {status}" + (f": {error}" if error else ""))

    threading.Thread(target=work, daemon=True).start()
    return job["id"]


def job_state(job_id: str) -> Optional[Dict[str, Any]]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        out = {"status": job["status"], "progress": job["progress"], "log": list(job["log"])}
        if job["result"] is not None:
            out["result"] = job["result"]
        if job["error"]:
            out["error"] = job["error"]
        return out


# ------------------------------------------------------------------ web server

def parse_multipart(body: bytes, boundary: str) -> Tuple[Dict[str, str], Optional[Dict[str, Any]]]:
    """Binary-safe multipart/form-data parser (fields, first file)."""
    fields: Dict[str, str] = {}
    file_info = None
    for part in body.split(b"--" + boundary.encode()):
        if b"\r\n\r\n" not in part:
            continue
        head, payload = part.split(b"\r\n\r\n", 1)
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        head_text = head.decode("utf-8", "replace")
        m = re.search(r'Content-Disposition:\s*form-data;\s*name="([^"]+)"(?:;\s*filename="([^"]*)")?', head_text, re.I)
        if not m:
            continue
        if m.group(2) is not None:
            file_info = file_info or {"filename": m.group(2), "data": payload}
        else:
            fields[m.group(1)] = payload.decode("utf-8", "replace")
    return fields, file_info


def config() -> Dict[str, Any]:
    providers = []
    for pid, p in llm.PROVIDERS.items():
        key = load_key(pid)
        providers.append({"id": pid, "name": p["name"], "hasKey": bool(key), "maskedKey": mask(key),
                          "ready": ready(pid), "keyHint": p.get("keyHint", ""), "keyUrl": p.get("keyUrl", ""),
                          "keyOptional": bool(p.get("keyOptional")),
                          "baseUrl": custom_base_url() if pid == "custom" else p.get("baseUrl", ""),
                          "models": p.get("models", [])})
    ai, aud = any(p["ready"] for p in providers), audiveris.find() is not None
    dp = default_provider()
    return {"providers": providers, "defaultProvider": dp, "defaultModel": default_model(dp),
            "engines": {"vector": True, "ai": ai, "audiveris": aud, "hybrid": ai and aud},
            "presets": mando.presets()}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"     # on Windows SO_REUSEADDR lets two servers share a port


class SheetXMLRequestHandler(SimpleHTTPRequestHandler):
    """Serves static/ and the JSON API. Same-origin only: no CORS headers, Host and Origin are checked."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        if "/api/job" in getattr(self, "requestline", ""):
            return                                      # the UI polls every second
        try:
            sys.stderr.write(f"[SheetXML] {self.address_string()} - {format % args}\n")
        except Exception:
            pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def _send_json(self, status: int, data: Dict[str, Any]):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _trusted(self) -> bool:
        """Block DNS rebinding (foreign Host) and cross-site requests (foreign Origin)."""
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}") or \
                (origin is not None and urllib.parse.urlparse(origin).netloc != host):
            self._send_json(403, {"error": "Forbidden: SheetXML only answers its own page."})
            return False
        return True

    def do_GET(self):
        if not self._trusted():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.path = "/index.html"
            return super().do_GET()
        if path == "/api/config":
            return self._send_json(200, config())
        if path == "/api/job":
            state = job_state(urllib.parse.parse_qs(parsed.query).get("id", [""])[0])
            return self._send_json(200, state) if state else self._send_json(404, {"error": "Unknown job."})
        if path == "/api/sample":
            if not SAMPLE_XML.exists():
                return self._send_json(404, {"error": "Sample file not found."})
            score = abcxml.parse_musicxml(SAMPLE_XML.read_bytes())
            return self._send_json(200, build_result(score, {"mandolinTab": True}, SAMPLE_XML.name, "import"))
        return super().do_GET()

    def do_POST(self):
        if not self._trusted():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            return self._send_json(413, {"error": f"Upload too large (limit {MAX_BODY // 2 ** 20} MB)."})
        body = self.rfile.read(length)
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/transcribe":
                return self._transcribe(body)
            req = json.loads(body or b"{}")
            if not isinstance(req, dict):
                raise ValueError("Expected a JSON object.")
            if path == "/api/cancel":
                with JOBS_LOCK:
                    job = JOBS.get(str(req.get("jobId", "")))
                if not job:
                    return self._send_json(404, {"error": "Unknown job."})
                job["cancel"].set()
                return self._send_json(200, {"success": True})
            if path == "/api/render":
                score = abcxml.parse_abc(str(req.get("abc", "")))
                if not score.bars:
                    return self._send_json(400, {"error": "No measures found in the ABC text."})
                opts = {"mandolinTab": req.get("mandolinTab", True) not in (False, "false", "0"),
                        "skillLevel": req.get("skillLevel") or "beginner", "arrangement": req.get("arrangement")}
                return self._send_json(200, build_result(score, opts, Path(str(req.get("filename") or "score")).name, "abc"))
            if path == "/api/about":
                return self._about(req)
            if path == "/api/save-config":
                return self._save_config(req)
            if path == "/api/verify-key":
                return self._verify_key(req)
            if path == "/api/save-file":
                stem = re.sub(r"[^\w\- .]", "_", Path(Path(str(req.get("filename") or "score")).name).stem).strip(" .")
                OUT_DIR.mkdir(exist_ok=True)
                target = OUT_DIR / f"{stem or 'score'}.musicxml"
                target.write_text(str(req.get("musicxml", "")), encoding="utf-8")
                return self._send_json(200, {"success": True, "savedPath": str(target), "filename": target.name})
            return self._send_json(404, {"error": "Not Found"})
        except ValueError as e:                  # bad JSON, bad arrangement: the message is the answer
            return self._send_json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._send_json(400, {"error": str(e)})

    def _transcribe(self, body: bytes):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if "multipart/form-data" not in ctype or not m:
            return self._send_json(400, {"error": "Expected multipart/form-data."})
        fields, file_info = parse_multipart(body, m.group(1))
        if not file_info or not file_info["data"]:
            return self._send_json(400, {"error": "No sheet music file received in upload."})
        try:
            arrangement = json.loads(fields.get("arrangement") or "{}")
        except ValueError:
            return self._send_json(400, {"error": "The arrangement field is not valid JSON."})
        opts = {"engine": fields.get("engine", "auto"), "provider": fields.get("provider", ""),
                "model": fields.get("model", "").strip(), "votes": fields.get("votes", "1"),
                "pageRange": fields.get("pageRange", ""), "arrangement": arrangement,
                "mandolinTab": fields.get("mandolinTab", "true").lower() in ("true", "1", "yes"),
                "skillLevel": fields.get("skillLevel", "beginner"), "apiKey": fields.get("apiKey", "")}
        if opts["engine"] not in ENGINES:
            return self._send_json(400, {"error": f"Unknown engine: {opts['engine']}"})
        if opts["provider"] and opts["provider"] not in llm.PROVIDERS:
            return self._send_json(400, {"error": f"Unknown provider: {opts['provider']}"})
        try:
            arrangement_opts(opts)
        except ValueError as e:
            return self._send_json(400, {"error": f"Arrangement: {e}"})
        name = Path(file_info["filename"] or "score").name
        print(f"[SheetXML] Received '{name}' ({len(file_info['data'])} bytes), engine={opts['engine']}, "
              f"provider={opts['provider'] or default_provider()}, model={opts['model'] or '(default)'}, votes={opts['votes']}")
        return self._send_json(200, {"jobId": start_job(file_info["data"], name, opts)})

    def _about(self, req: Dict[str, Any]):
        details = req.get("details")
        provider = str(req.get("provider") or default_provider())
        if not isinstance(details, dict) or not details:
            return self._send_json(400, {"error": "Transcribe or open a score first."})
        if provider not in llm.PROVIDERS:
            return self._send_json(400, {"error": f"Unknown provider: {provider}"})
        model = str(req.get("model") or "").strip() or default_model(provider)
        key, base_url = load_key(provider), base_url_for(provider)
        if not model or not ready(provider):
            return self._send_json(400, {"error": f"Add an API key for {provider_name(provider)} in Settings (or choose "
                                                  "another AI provider) to write an AI note."})
        try:
            text = analysis.ai_summary(details, provider=provider, models=model_chain(provider, model), key=key,
                                       base_url=base_url)
        except llm.LLMError as e:
            return self._send_json(400, {"error": friendly_error(e, provider)})
        return self._send_json(200, {"text": text, "model": model})

    def _save_config(self, req: Dict[str, Any]):
        keys = req.get("keys") or {}
        if not isinstance(keys, dict):
            return self._send_json(400, {"success": False, "error": "keys must be an object."})
        keys = {str(p): re.sub(r"\s+", "", str(k or "")) for p, k in keys.items()}
        anykey = re.sub(r"\s+", "", str(req.get("anyKey") or ""))
        if anykey:
            guess = llm.guess_provider(anykey)
            if not guess:
                return self._send_json(400, {"success": False, "needsProvider": True,
                                             "error": "This key could belong to several services - pick which one."})
            keys[guess] = anykey
        saved = []
        for pid, key in keys.items():
            if not key:
                continue
            if pid not in llm.PROVIDERS:
                return self._send_json(400, {"success": False, "error": f"Unknown provider: {pid}"})
            problem = llm.key_problem(pid, key)
            if problem:
                return self._send_json(400, {"success": False, "error": f"{provider_name(pid)}: {problem}"})
            save_env(llm.PROVIDERS[pid]["env"], key)
            saved.append(provider_name(pid))
        base = str(req.get("customBaseUrl") or "").strip()
        if base:
            if not re.match(r"https?://[^\s\"']+$", base):
                return self._send_json(400, {"success": False, "error": "The base URL must start with http:// or https://"})
            save_env("CUSTOM_BASE_URL", base.rstrip("/"))
            saved.append("custom endpoint URL")
        msg = f"Saved: {', '.join(saved)}." if saved else "Nothing to save - type a key first."
        return self._send_json(200, {"success": True, "message": msg, "saved": saved,
                                     "provider": next(iter(k for k, v in keys.items() if v), "")})

    def _verify_key(self, req: Dict[str, Any]):
        provider = str(req.get("provider") or "")
        if provider not in llm.PROVIDERS:
            return self._send_json(400, {"success": False, "error": f"Unknown provider: {provider}"})
        p, who = llm.PROVIDERS[provider], provider_name(provider)
        key = re.sub(r"\s+", "", str(req.get("apiKey", ""))) or load_key(provider)
        base_url = base_url_for(provider, str(req.get("baseUrl") or ""))
        if not key and not (p.get("keyOptional") and base_url):
            return self._send_json(400, {"success": False, "error": f"No {who} API key to test."})
        problem = llm.key_problem(provider, key) if key else ""
        if problem:
            return self._send_json(400, {"success": False, "error": problem})
        try:
            _, ids = llm.discover(provider, key, base_url)
        except llm.LLMError as e:
            return self._send_json(400, {"success": False, "error": friendly_error(e, provider)})
        ids = [i for i in ids if not NOT_CHAT.search(i)]
        return self._send_json(200, {"success": True, "models": ids,
                                     "message": f"{who} works - {len(ids)} model(s) available (no tokens used)."})


def server_answers(port: int) -> bool:
    """A SheetXML server already runs on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2) as r:
            return r.status == 200 and "providers" in json.loads(r.read())
    except Exception:
        return False


def start_server(port: int = 5050, open_browser: bool = True):
    """Launch the web GUI on 127.0.0.1 and record the pid for the Windows launcher."""
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    def _cleanup_pid():
        try:
            if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass
    atexit.register(_cleanup_pid)

    try:
        httpd = Server(("127.0.0.1", port), SheetXMLRequestHandler)
    except OSError:
        port += 1
        httpd = Server(("127.0.0.1", port), SheetXMLRequestHandler)

    url = f"http://localhost:{port}"
    print("\n=======================================================")
    print("SheetXML - Sheet Music to MusicXML + Mandolin TAB")
    print("=======================================================")
    print(f"Server running at: {url}")
    print("Press Ctrl+C to stop the server.\n")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SheetXML server...")
        httpd.server_close()


# ------------------------------------------------------------------ CLI

def print_details(d: Optional[Dict[str, Any]]):
    if not d:
        return
    k = d.get("key") or {}
    dur = d.get("durationSeconds") or 0
    print(f"Key:        {k.get('name', '?')}" + (f" (sounds like {k['detected']})" if k.get("detected") and
                                                  not k.get("agrees", True) else ""))
    print(f"Meter:      {d.get('meter')} ({d.get('timeSignatureFeel')}), "
          f"{d['tempo']} bpm" if d.get("tempo") else f"Meter:      {d.get('meter')} ({d.get('timeSignatureFeel')}), no tempo marked")
    print(f"Length:     {d.get('measures')} bars, about {dur // 60}:{dur % 60:02d}")
    if d.get("range"):
        r = d["range"]
        print(f"Range:      {r['lowest']}-{r['highest']} ({r['semitones']} semitones)")
    if (d.get("form") or {}).get("pattern"):
        print(f"Form:       {d['form']['pattern']}")
    if d.get("difficulty"):
        df = d["difficulty"]
        print(f"Difficulty: {df['level']} ({df['score']}/10): {'; '.join(df['reasons'])}")
    for t in d.get("tips") or []:
        print(f"Tip:        {t}")


def run_cli(args):
    src = Path(args.input_file)
    if not src.is_file():
        print(f"[ERROR] Input file '{src}' does not exist.")
        sys.exit(1)
    out = Path(args.output) if args.output else \
        src.with_name(src.stem + ("-tab" if src.suffix.lower() in (".musicxml", ".xml") else "") + ".musicxml")
    arrangement = {k: v for k, v in {"instrument": args.instrument, "tuning": args.tuning, "capo": args.capo,
                                     "style": args.style, "position": args.position, "maxFret": args.max_fret,
                                     "skill": args.skill}.items() if v is not None}
    opts = {"engine": args.engine, "provider": args.provider or "", "model": args.model or "", "votes": args.votes,
            "pageRange": args.pages or "", "mandolinTab": not args.no_tab, "skillLevel": args.skill,
            "arrangement": arrangement, "apiKey": args.key or ""}
    print(f"SheetXML: {src.name} -> {out.name} (engine {args.engine})")

    def progress(msg, frac):
        if msg:
            print(f"  [{'    ' if frac is None else f'{frac:4.0%}'}] {msg}")

    t0 = time.time()
    try:
        result = run_job(src.read_bytes(), src.name, opts, progress)
    except KeyboardInterrupt:
        print("\n[CANCELLED]")
        sys.exit(130)
    except Exception as e:
        print(f"\n[FAILED] {friendly_error(e, args.provider or default_provider())}")
        if not isinstance(e, (JobError, llm.LLMError)):
            traceback.print_exc()
        sys.exit(1)

    out.write_text(result["musicxml"], encoding="utf-8")
    meta, stats = result["metadata"], result["mandolinStats"]
    used = ", ".join(f"{m} x{n}" for m, n in result["modelsUsed"].items())
    print(f"\nEngine:     {result['engine']}" + (f" ({used})" if used else ""))
    if result["info"].get("skippedSystems"):
        print(f"            {result['info']['skippedSystems']} system(s) confirmed by Audiveris + code, no AI call")
    print(f"Title:      {meta['title']}" + (f" - {meta['composer']}" if meta["composer"] else ""))
    print(f"Key / time: {meta['keySignature']}, {meta['timeSignature']}")
    print(f"Measures:   {meta['measureCount']}")
    print(f"Schema:     {'valid MusicXML 4.0' if result['isValid'] else 'NOT valid'}")
    for err in result["validationErrors"][:3]:
        print(f"            {err}")
    if stats:
        print(f"TAB:        {stats['arrangement']}: {stats['totalNotes']} notes, {stats['openStringsCount']} open, "
              f"frets {stats['lowestFret']}-{stats['highestFret']}, {stats['positionShifts']} position shifts"
              + (f", {stats['unplayable']} unplayable note(s) left off" if stats["unplayable"] else ""))
    for w in result["warnings"]:
        print(f"WARNING:    {w}")
    if result["reviewBars"]:
        print(f"CHECK BARS: {', '.join(map(str, result['reviewBars']))} (marked 'check' in the score)")
    if args.details:
        print()
        print_details(result["details"])
    print(f"Saved:      {out.resolve()}  ({time.time() - t0:.1f}s)")
    if args.ascii and result["asciiTab"]:
        txt = out.with_suffix(".txt")
        txt.write_text(result["asciiTab"], encoding="utf-8")
        print(f"Text tab:   {txt.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="Sheet music (PDF / image / MusicXML / ABC) to MusicXML 4.0 with a mandolin-family TAB staff. "
                    "Run without a file to start the web GUI.")
    parser.add_argument("input_file", nargs="?", help="PDF, image, .musicxml/.xml/.mxl or .abc file (CLI mode)")
    parser.add_argument("-o", "--output", help="output .musicxml path (default: next to the input)")
    parser.add_argument("--engine", default="auto", choices=ENGINES,
                        help="auto (default): exact PDF reader, else Audiveris checked by AI (hybrid), else either alone")
    parser.add_argument("--provider", choices=list(llm.PROVIDERS),
                        help="AI provider (default: the first one with a key)")
    parser.add_argument("-m", "--model", help="AI model id (default: the provider's recommended model)")
    parser.add_argument("--votes", type=int, default=1, choices=[1, 3], help="AI readings per system (3 = careful, ~3x cost)")
    parser.add_argument("-p", "--pages", help="page range, e.g. '1-2' or '1,3'")
    parser.add_argument("--no-tab", action="store_true", help="notation only, no TAB staff")
    parser.add_argument("--instrument", help="mandolin (default), mandola, octave-mandolin, mandocello")
    parser.add_argument("--tuning", help="tuning id (gdae, gdgd, aeae, ...) or 4 notes low to high, e.g. 'G3 D4 G4 D5'")
    parser.add_argument("--capo", type=int, help="capo fret (0-7)")
    parser.add_argument("--style", choices=["open", "closed", "position"], help="fingering style (default open)")
    parser.add_argument("--position", type=int, help="hand position for --style position (index finger fret)")
    parser.add_argument("--max-fret", type=int, help="highest fret to use")
    parser.add_argument("--skill", default="beginner", choices=["beginner", "advanced"], help="fingering skill level")
    parser.add_argument("--ascii", action="store_true", help="also write a plain-text tab (.txt) next to the output")
    parser.add_argument("--details", action="store_true", help="print a short analysis of the piece")
    parser.add_argument("-k", "--key", help="API key for the provider (default: environment / .env)")
    parser.add_argument("--port", type=int, default=5050, help="port for the web GUI")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser when the GUI starts")
    args = parser.parse_args()
    if args.input_file:
        run_cli(args)
    elif FROZEN and server_answers(args.port):          # the launcher was clicked again: reuse the running app
        if not args.no_browser:
            webbrowser.open(f"http://localhost:{args.port}")
    else:
        start_server(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
