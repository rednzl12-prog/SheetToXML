"""
SheetXML - sheet music (PDF / images / MusicXML / ABC) to MusicXML 4.0 with a mandolin TAB staff.

Engines (see README.md):
  vector     exact reader for born-digital PDFs (vectorpdf), no AI
  ai         guided vision-model pipeline, one staff system at a time (pipeline)
  audiveris  optional free offline OMR (audiveris)
  auto       vector for PDFs that have music-font glyphs, else audiveris when installed, else ai when a key exists
Every engine yields an abcxml.Score; abcxml writes the MusicXML. One run_job() serves the web UI and the CLI.
"""

import os
import sys
import time
import atexit
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "sheetxml.log"


class SafeStream:
    """Safe wrapper for stdout/stderr that prevents crashes when running in GUI/detached mode."""
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
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from lxml import etree
    import abcxml
    import audiveris
    import llm
    import pipeline
    import vectorpdf
except ImportError as e:
    print(f"[ERROR] {e}. Run: pip install -r requirements.txt")
    sys.exit(1)

STATIC_DIR = BASE_DIR / "static"
CONFIG_FILE = BASE_DIR / ".env"
OUT_DIR = BASE_DIR / "transcriptions"
SCHEMA_DIR = BASE_DIR / "schemas"
SAMPLE_XML = BASE_DIR / "resources" / "music-xml-example.xml"
MAX_BODY = 60 * 1024 * 1024
JOB_TTL = 30 * 60

# Models verified to read images with the user's keys. DeepSeek runs through the Qwen Token Plan key.
MODELS = [
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash", "provider": "gemini",
     "description": "Fast and accurate; free tier has a small daily quota",
     "fallbacks": ["gemini-3.7-flash", "gemini-3.5-flash"]},
    {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview (premium)", "provider": "gemini",
     "description": "Strongest Gemini reader; quota-limited on the free tier",
     "fallbacks": ["gemini-pro-latest", "gemini-3.8-flash"]},
    {"id": "gemini-pro-latest", "name": "Gemini Pro Latest (premium)", "provider": "gemini",
     "description": "Latest Gemini Pro; quota-limited on the free tier", "fallbacks": ["gemini-3.8-flash"]},
    {"id": "qwen3.8-flash", "name": "Qwen 3.8 Flash", "provider": "qwen",
     "description": "Fast; 98% of notes on the hardest test page", "fallbacks": ["qwen3.6-flash"]},
    {"id": "qwen3.7-plus", "name": "Qwen 3.7 Plus", "provider": "qwen",
     "description": "Mid-size Qwen vision model", "fallbacks": ["qwen3.8-flash"]},
    {"id": "qwen3.8-max", "name": "Qwen 3.8 Max (very slow)", "provider": "qwen",
     "description": "About 2.5 minutes per staff system", "fallbacks": ["qwen3.8-flash"]},
    {"id": "qwen3.6-flash", "name": "Qwen 3.6 Flash", "provider": "qwen",
     "description": "Older fast Qwen vision model", "fallbacks": []},
    {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash (via Qwen Token Plan)", "provider": "qwen",
     "description": "DeepSeek vision model served on the Qwen Token Plan key", "fallbacks": []},
]
MODEL_IDS = [m["id"] for m in MODELS]
PROVIDER_NAMES = {"gemini": "Gemini", "qwen": "Qwen", "deepseek": "DeepSeek"}
ENGINES = ("auto", "vector", "ai", "audiveris")
QUOTA_MSG = "Gemini's free daily quota is used up - try again later or pick a Qwen/DeepSeek model."


# ------------------------------------------------------------------ keys

ENV_NAMES = {"gemini": "GEMINI_API_KEY", "qwen": "QWEN_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


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
    """Key for gemini | qwen | deepseek. A GEMINI_API_KEY starting with sk- is a Qwen key; DeepSeek falls back to Qwen."""
    if provider == "gemini":
        return next((v for v in _values("GEMINI_API_KEY") if not v.startswith("sk-")), "")
    if provider == "qwen":
        return next(iter(_values("QWEN_API_KEY")), "") or \
            next((v for v in _values("GEMINI_API_KEY") if v.startswith("sk-")), "")
    if provider == "deepseek":
        return next(iter(_values("DEEPSEEK_API_KEY")), "") or load_key("qwen")
    return ""


def save_key(provider: str, value: str):
    var, value = ENV_NAMES[provider], re.sub(r"\s+", "", value)
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


def default_model() -> str:
    return "gemini-3.8-flash" if load_key("gemini") or not load_key("qwen") else "qwen3.8-flash"


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


def build_result(score: "abcxml.Score", opts: Dict[str, Any], filename: str, engine: str) -> Dict[str, Any]:
    """Score -> the result dict the UI and CLI show."""
    tab = bool(opts.get("mandolinTab", True))
    xml, stats = abcxml.to_musicxml(score, tab=tab, skill_level=opts.get("skillLevel") or "beginner")
    ok, errors = validate_musicxml_schema(xml)
    fifths, mode = score.key
    root = etree.fromstring(xml.encode("utf-8"))
    return {
        "musicxml": xml,
        "abc": abcxml.score_to_abc(score),
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
        "engine": engine,
        "warnings": list(score.warnings),
        "reviewBars": stats["reviewBars"],
        "filename": filename,
    }


# ------------------------------------------------------------------ the job

class JobError(Exception):
    """A user-facing failure with a ready-to-show message."""


def friendly_error(e: BaseException, model: str = "") -> str:
    if not isinstance(e, llm.LLMError):
        return str(e) or type(e).__name__
    provider = llm.provider_of(model, MODELS) if model else ""
    who = PROVIDER_NAMES.get(provider, "The AI provider")
    detail = str(e).splitlines()[0][:200]
    return {
        "cancelled": "Cancelled.",
        "auth": f"{who} rejected the API key - check the key in Settings. ({detail})",
        "rate": QUOTA_MSG if provider == "gemini" else
                f"{who} rate limit reached - wait a minute and try again, or pick another model. ({detail})",
        "overload": f"{who} is overloaded right now (high demand). Try again in a few minutes or pick another model.",
        "network": f"Could not reach {who} - check your internet connection. ({detail})",
        "model": f"This model is not available to your key - pick another model. ({detail})",
        "truncated": f"The model's answer was cut off. Try another model. ({detail})",
    }.get(e.kind, f"{who}: {detail}")


def run_job(data: bytes, filename: str, opts: Dict[str, Any],
            progress: Optional[Callable[[str, Optional[float]], None]] = None,
            cancel: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """File bytes -> result dict. opts: engine, model, votes, pageRange, mandolinTab, skillLevel, apiKey.
    Raises JobError (message ready to show) or llm.LLMError (see friendly_error)."""
    say = progress or (lambda msg, frac: None)
    filename = Path(filename or "score").name
    ext = Path(filename).suffix.lower()
    engine = opts.get("engine") or "auto"
    pages = str(opts.get("pageRange") or "").strip()
    if engine not in ENGINES:
        raise JobError(f"Unknown engine '{engine}'. Use one of: {', '.join(ENGINES)}.")

    # existing notation: just add the TAB staff
    if ext in (".musicxml", ".xml", ".mxl") or data[:2] == b"PK":
        say("Reading the MusicXML file", 0.5)
        try:
            return build_result(abcxml.parse_musicxml(data), opts, filename, "import")
        except Exception as e:
            raise JobError(f"Could not read this MusicXML file: {e}")
    if ext == ".abc":
        say("Reading the ABC file", 0.5)
        return build_result(abcxml.parse_abc(data.decode("utf-8", "replace")), opts, filename, "import")

    is_pdf = data[:5] == b"%PDF-"
    if engine in ("auto", "vector"):
        if is_pdf:
            say("Trying the exact PDF reader (no AI)", 0.02)
            score = vectorpdf.transcribe(data, pages)
            if score is not None and score.bars:
                say(f"Exact PDF reader: {len(score.bars)} measures", 1.0)
                return build_result(score, opts, filename, "vector")
        if engine == "vector":
            raise JobError("The exact PDF reader only works on PDFs exported from notation software "
                           "(MuseScore, Sibelius, Finale, Dorico). This file is a scan, photo or image - "
                           "use the AI or Audiveris engine.")
        if is_pdf:
            say("No music-font glyphs in this PDF (scanned?) - using another engine", None)

    aud = None          # Audiveris before AI: free and offline; AI only when it fails or reads badly
    if engine == "audiveris" or (engine == "auto" and audiveris.find()):
        try:
            aud = audiveris.transcribe(data, filename, pages, progress=say, cancel=cancel)
        except RuntimeError as e:
            if engine == "audiveris":
                raise JobError(str(e))
            say(f"Audiveris failed: {str(e)[:200]}", None)
        if aud is not None:
            bad = len(aud.problems())
            if engine == "audiveris" or (aud.bars and bad <= len(aud.bars) // 8):   # measured: simple scans ~1 in 15 bad, dense ones 1 in 5
                return build_result(aud, opts, filename, "audiveris")
            say(f"Audiveris left {bad} of {len(aud.bars)} measures inconsistent - trying AI if a key is set", None)

    model = opts.get("model") or default_model()
    if model not in MODEL_IDS:
        raise JobError(f"Unknown model '{model}'. Available: {', '.join(MODEL_IDS)}.")
    provider = llm.provider_of(model, MODELS)
    key = re.sub(r"\s+", "", opts.get("apiKey") or "") or load_key(provider)

    if engine == "ai" or (engine == "auto" and key):
        if not key:
            raise JobError(f"No {PROVIDER_NAMES[provider]} API key - add one in Settings (or pass -k on the command line).")
        problem = llm.key_problem(provider, key)
        if problem:
            raise llm.LLMError(problem, "auth")
        say("Checking the API key", 0.0)
        try:
            llm.discover(provider, key, timeout=15)       # token-free; a bad key fails here, not after the layout pass
        except llm.LLMError as e:
            if e.kind == "auth":
                raise
        entry = next(m for m in MODELS if m["id"] == model)
        models = [model] + entry["fallbacks"]
        skipped = set()

        def relay(msg, frac):
            m = re.match(r"(\S+) unavailable \(", msg or "")
            if m:
                skipped.add(m.group(1))
            say(msg, frac)

        votes = 3 if str(opts.get("votes", 1)) == "3" else 1
        say(f"AI pipeline with {model}" + (" (3 readings per system)" if votes == 3 else ""), 0.0)
        score = pipeline.transcribe(data, provider=provider, models=models, key=key, page_range=pages,
                                    votes=votes, progress=relay, cancel=cancel)
        result = build_result(score, opts, filename, "ai")
        result["requestedModel"] = model
        result["modelUsed"] = next((m for m in models if m not in skipped), model)
        return result

    if aud is not None and aud.bars:        # no key: a rough Audiveris reading beats nothing
        aud.warnings.append("Audiveris could not read several measures cleanly; add an AI key in Settings for a second opinion.")
        return build_result(aud, opts, filename, "audiveris")

    raise JobError(
        "No engine can read this file: " + ("it is not a PDF exported from notation software, " if is_pdf else "") +
        f"there is no {PROVIDER_NAMES[provider]} API key (add one in Settings), and Audiveris is not installed "
        "(free offline OMR: https://github.com/Audiveris/audiveris/releases).")


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
            status, error = "error", friendly_error(e, opts.get("model") or "")
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
            keys = {p: load_key(p) for p in ENV_NAMES}
            return self._send_json(200, {
                "hasGeminiKey": bool(keys["gemini"]), "maskedGeminiKey": mask(keys["gemini"]),
                "hasQwenKey": bool(keys["qwen"]), "maskedQwenKey": mask(keys["qwen"]),
                "hasDeepSeekKey": bool(keys["deepseek"]), "maskedDeepSeekKey": mask(keys["deepseek"]),
                "availableModels": MODELS,
                "defaultModel": default_model(),
                "engines": {"vector": True, "ai": bool(keys["gemini"] or keys["qwen"]),
                            "audiveris": audiveris.find() is not None},
            })
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
                        "skillLevel": req.get("skillLevel") or "beginner"}
                return self._send_json(200, build_result(score, opts, Path(str(req.get("filename") or "score")).name, "abc"))
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
        opts = {"engine": fields.get("engine", "auto"), "model": fields.get("model", ""),
                "votes": fields.get("votes", "1"), "pageRange": fields.get("pageRange", ""),
                "mandolinTab": fields.get("mandolinTab", "true").lower() in ("true", "1", "yes"),
                "skillLevel": fields.get("skillLevel", "beginner"), "apiKey": fields.get("apiKey", "")}
        if opts["engine"] not in ENGINES:
            return self._send_json(400, {"error": f"Unknown engine: {opts['engine']}"})
        if opts["model"] and opts["model"] not in MODEL_IDS:
            return self._send_json(400, {"error": f"Unsupported model: {opts['model']}"})
        name = Path(file_info["filename"] or "score").name
        print(f"[SheetXML] Received '{name}' ({len(file_info['data'])} bytes), engine={opts['engine']}, "
              f"model={opts['model'] or default_model()}, votes={opts['votes']}")
        return self._send_json(200, {"jobId": start_job(file_info["data"], name, opts)})

    def _save_config(self, req: Dict[str, Any]):
        gemini_key = str(req.get("geminiKey", "")).strip()
        qwen_key = str(req.get("qwenKey", "")).strip()
        single = re.sub(r"\s+", "", str(req.get("apiKey", "")))
        if single:
            if single.startswith("sk-"):
                qwen_key = single
            else:
                gemini_key = single
        saved = []
        if gemini_key:
            save_key("gemini", gemini_key)
            saved.append("Gemini")
        if qwen_key:
            save_key("qwen", qwen_key)
            save_key("deepseek", qwen_key)
            saved.append("Qwen")
        msg = f"{' and '.join(saved)} API key(s) saved." if saved else "Settings saved."
        return self._send_json(200, {"success": True, "message": msg})

    def _verify_key(self, req: Dict[str, Any]):
        provider = req.get("provider", "gemini")
        if provider not in ENV_NAMES:
            return self._send_json(400, {"success": False, "error": f"Unknown provider: {provider}"})
        key = re.sub(r"\s+", "", str(req.get("apiKey", ""))) or load_key(provider)
        who = PROVIDER_NAMES[provider]
        if not key:
            return self._send_json(400, {"success": False, "error": f"No {who} API key to test."})
        problem = llm.key_problem(provider, key)
        if problem:
            return self._send_json(400, {"success": False, "error": problem})
        try:
            _, ids = llm.discover(provider, key)
        except llm.LLMError as e:
            return self._send_json(400, {"success": False, "error": f"{who}: {friendly_error(e)}"})
        ours = [m["id"] for m in MODELS if llm.provider_of(m["id"], MODELS) == provider and m["id"] in ids]
        return self._send_json(200, {"success": True, "models": ours, "message":
                                     f"{who} key works ({len(ids)} models visible" +
                                     (f"; ready: {', '.join(ours)})" if ours else ")")})


def start_server(port: int = 5050, open_browser: bool = True):
    """Launch the web GUI on 127.0.0.1 and record the pid for the Windows launcher."""
    pid_file = BASE_DIR / "sheetxml.pid"
    try:
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    def _cleanup_pid():
        try:
            if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
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

def run_cli(args):
    src = Path(args.input_file)
    if not src.is_file():
        print(f"[ERROR] Input file '{src}' does not exist.")
        sys.exit(1)
    out = Path(args.output) if args.output else \
        src.with_name(src.stem + ("-tab" if src.suffix.lower() in (".musicxml", ".xml") else "") + ".musicxml")
    opts = {"engine": args.engine, "model": args.model or default_model(), "votes": args.votes,
            "pageRange": args.pages or "", "mandolinTab": not args.no_tab, "skillLevel": args.skill,
            "apiKey": args.key or ""}
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
        print(f"\n[FAILED] {friendly_error(e, opts['model'])}")
        if not isinstance(e, (JobError, llm.LLMError)):
            traceback.print_exc()
        sys.exit(1)

    out.write_text(result["musicxml"], encoding="utf-8")
    meta, stats = result["metadata"], result["mandolinStats"]
    print(f"\nEngine:     {result['engine']}" + (f" ({result['modelUsed']})" if result.get("modelUsed") else ""))
    print(f"Title:      {meta['title']}" + (f" - {meta['composer']}" if meta["composer"] else ""))
    print(f"Key / time: {meta['keySignature']}, {meta['timeSignature']}")
    print(f"Measures:   {meta['measureCount']}")
    print(f"Schema:     {'valid MusicXML 4.0' if result['isValid'] else 'NOT valid'}")
    for err in result["validationErrors"][:3]:
        print(f"            {err}")
    if stats:
        su = stats["stringUsage"]
        print(f"TAB:        {stats['totalNotes']} notes, {stats['firstPositionCount']} in frets 0-7, "
              f"{stats['openStringsCount']} open, highest fret {stats['highestFret']}, "
              f"courses E:{su[1]} A:{su[2]} D:{su[3]} G:{su[4]}")
    for w in result["warnings"]:
        print(f"WARNING:    {w}")
    if result["reviewBars"]:
        print(f"CHECK BARS: {', '.join(map(str, result['reviewBars']))} (marked 'check' in the score)")
    print(f"Saved:      {out.resolve()}  ({time.time() - t0:.1f}s)")


def main():
    parser = argparse.ArgumentParser(
        description="Sheet music (PDF / image / MusicXML / ABC) to MusicXML 4.0 with a mandolin TAB staff. "
                    "Run without a file to start the web GUI.")
    parser.add_argument("input_file", nargs="?", help="PDF, image, .musicxml/.xml/.mxl or .abc file (CLI mode)")
    parser.add_argument("-o", "--output", help="output .musicxml path (default: next to the input)")
    parser.add_argument("--engine", default="auto", choices=ENGINES,
                        help="auto (default): exact PDF reader, else Audiveris, else AI")
    parser.add_argument("-m", "--model", choices=MODEL_IDS, help="AI model (default: gemini-3.8-flash, or qwen3.8-flash without a Gemini key)")
    parser.add_argument("--votes", type=int, default=1, choices=[1, 3], help="AI readings per system (3 = careful, ~3x cost)")
    parser.add_argument("-p", "--pages", help="page range, e.g. '1-2' or '1,3'")
    parser.add_argument("--no-tab", action="store_true", help="notation only, no mandolin TAB staff")
    parser.add_argument("--skill", default="beginner", choices=["beginner", "advanced"], help="TAB fingering style")
    parser.add_argument("-k", "--key", help="API key for the model's provider (default: environment / .env)")
    parser.add_argument("--port", type=int, default=5050, help="port for the web GUI")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser when the GUI starts")
    args = parser.parse_args()
    if args.input_file:
        run_cli(args)
    else:
        start_server(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
