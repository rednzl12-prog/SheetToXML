"""
SheetMusic to MusicXML Transcriber
 A self-contained program powered by Gemini multimodal AI.
Transcribes PDF and Image sheet music to MusicXML 4.0 with W3C schema validation.
"""

import os
import sys
import time
import atexit
import base64
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
        written = False
        if self.original_stream is not None:
            try:
                self.original_stream.write(data)
                self.original_stream.flush()
                written = True
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

# Ensure standard streams are UTF-8 and never throw on write
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.stdout = SafeStream(sys.stdout, LOG_FILE)
sys.stderr = SafeStream(sys.stderr, LOG_FILE)
import io
import json
import re
import mimetypes
import argparse
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional
import mandolin_optimizer

# Third-party / installed imports
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("[ERROR] google-genai package not found. Run: pip install google-genai")
    sys.exit(1)

try:
    from lxml import etree
except ImportError:
    print("[ERROR] lxml package not found. Run: pip install lxml")
    sys.exit(1)

try:
    import pypdf
except ImportError:
    pypdf = None

# Base directories
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_FILE = BASE_DIR / ".env"

SCHEMA_DIR = BASE_DIR / "schemas"
if not SCHEMA_DIR.exists():
    SCHEMA_DIR = BASE_DIR / "musicxml-4.0" / "schema"

SAMPLE_XML = BASE_DIR / "resources" / "music-xml-example.xml"
if not SAMPLE_XML.exists():
    SAMPLE_XML = BASE_DIR / "music-xml-example.xml"

# Supported Gemini models
AVAILABLE_MODELS = [
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash (Recommended)",
        "description": "High-capacity multimodal notation transcription"
    },
    {
        "id": "gemini-3-flash-preview",
        "name": "Gemini 3 Flash Preview",
        "description": "Fast multimodal notation transcription"
    },
    {
        "id": "gemini-3.6-flash",
        "name": "Gemini 3.6 Flash (High Performance)",
        "description": "High-capacity multimodal reasoning for dense notation"
    },
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash Lite (Ultra Fast)",
        "description": "Ultra fast turnaround for clean lead sheets and single staves"
    },
    {
        "id": "gemini-3.1-flash-lite",
        "name": "Gemini 3.1 Flash Lite",
        "description": "Lightweight model for quick notation previews"
    },
    {
        "id": "gemini-flash-latest",
        "name": "Gemini Flash Latest",
        "description": "Standard multimodal Flash endpoint"
    },
    {
        "id": "qwen3.8-max",
        "name": "Qwen 3.8 Max",
        "provider": "qwen",
        "description": "Qwen flagship multimodal model for PDF and image understanding"
    },
    {
        "id": "qwen3.8-flash",
        "name": "Qwen 3.8 Flash",
        "provider": "qwen",
        "description": "Fast Qwen multimodal model for PDF and image understanding"
    },
    {
        "id": "qwen3.7-max",
        "name": "Qwen 3.7 Max",
        "provider": "qwen",
        "description": "Qwen multimodal reasoning model"
    },
    {
        "id": "qwen3.7-plus",
        "name": "Qwen 3.7 Plus",
        "provider": "qwen",
        "description": "Qwen multimodal model"
    },
    {
        "id": "qwen3.6-flash",
        "name": "Qwen 3.6 Flash",
        "provider": "qwen",
        "description": "Qwen multimodal flash model"
    },
    {
        "id": "deepseek-flash",
        "name": "DeepSeek V4.1 Flash (Vision)",
        "provider": "deepseek",
        "description": "Image-based notation transcription; PDFs use Gemini or Qwen"
    },
    {
        "id": "deepseek-v4.1-flash",
        "name": "DeepSeek V4.1 Flash via Qwen",
        "provider": "deepseek",
        "description": "DeepSeek vision model through a Qwen Token Plan key"
    }
]

SYSTEM_PROMPT = """You are an expert musicologist, master engraver, and specialized Optical Music Recognition (OMR) system.
Your mission is to transcribe the provided sheet music document (PDF or image) with absolute accuracy into valid, well-formed MusicXML 4.0 partwise format.

### CORE TRANSCRIPTION RULES:
1. XML FORMAT & COMPLIANCE:
   - Output must be strict MusicXML 4.0 (partwise).
   - Root element: <score-partwise version="4.0">
   - Include valid DOCTYPE:
     <!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">
   - Every <part id="PX"> in the body must correspond to a <score-part id="PX"> in <part-list>.

2. METADATA:
   - Extract title into <work><work-title>...</work-title></work> or <movement-title>.
   - Extract composer/arranger into <identification><creator type="composer">...</creator></identification>.

3. ATTRIBUTES (Measure 1 or when changing):
   - <divisions>: Choose a clean integer (e.g. 4 for quarter=4, eighth=2, 16th=1; or 8 or 24 for triplets).
   - <key>: Provide <fifths> (-7 to 7) and <mode> (major, minor).
   - <time>: Provide <beats> and <beat-type> (e.g. 4/4, 3/4, 6/8, 2/4).
   - <clef>: Provide <sign> (G, F, C, TAB) and <line> (2 for treble G, 4 for bass F).
   - For piano/organ/grand staff: specify <staves>2</staves> and use <staff>1</staff> and <staff>2</staff> with <clef number="1"> and <clef number="2">.

4. NOTE & REST ACCURACY:
   - Pitch: <pitch><step>A-G</step><alter>-1/1</alter><octave>0-9</octave></pitch>
     (Middle C is C4. Treble G line is G4. Bass F line is F3).
   - Accidental: Include <accidental>sharp/flat/natural</accidental> when explicitly written in sheet music.
   - Rest: <note><rest/><duration>D</duration><type>quarter</type><voice>V</voice></note>.
   - Chords: When multiple notes are struck simultaneously on the same beat in the same voice, all notes after the first MUST contain the self-closing tag <chord/>.
   - Note Type: <type>whole, half, quarter, eighth, 16th, 32nd</type>.
   - Dotted notes: Include <dot/> immediately after <type>, and ensure duration is 1.5x of base duration.
   - Beams: Mark <beam number="1">begin</beam>, <beam number="1">continue</beam>, <beam number="1">end</beam>.
   - Ties & Slurs: Use <tie type="start/stop"/> and <notations><tied type="start/stop"/></notations> or <slur type="start/stop" number="1"/>.
   - Triplets / Tuplets: Include <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification> and <notations><tuplet type="start/stop"/></notations>.
   - Dynamics & Expressions: <direction><direction-type><dynamics><p/></dynamics></direction-type></direction> (e.g. p, mp, mf, f, ff, crescendo, diminuendo).
   - Multi-voice / Multi-staff: Use <backup><duration>...</duration></backup> to rewind the time position when entering a second voice or second staff in the same measure.

5. MATHEMATICAL MEASURE INTEGRITY (CRITICAL):
   - Measure numbers must be strictly sequential (1, 2, 3...).
   - The sum of <duration> of all non-chord notes and rests in each voice of a measure MUST mathematically match the time signature:
     Total Measure Duration = beats * divisions * (4 / beat-type).
   - Never leave incomplete measures unless it is an upbeat / pickup measure (measure number="0").

6. MANDOLIN TABLATURE & FINGERING SPECIFICATION:
   - Target instrument: Standard 8-string soprano mandolin (4 doubled courses, tuned G3-D4-A4-E5).
   - In MusicXML: String 1 = E5 (open), String 2 = A4 (open), String 3 = D4 (open), String 4 = G3 (open).
   - Provide <clef number="1"><sign>TAB</sign><line>5</line></clef> and <staff-details number="1"><staff-lines>4</staff-lines>...
   - For every pitched note, include <notations><technical><string>S</string><fret>F</fret></technical></notations>.
   - Ergonomic rules (MANDOLIN_TAB_FINGERING_SPEC):
     * Strongly prioritize first position (frets 0-7) and idiomatic open strings (D0, A0, E0).
     * G3 through C#4 are playable only on course 4 (G course).
     * Avoid gratuitous frets 12+ or rapid erratic position leaps.
     * Optimize across the phrase for hand continuity and compact finger windows.
     * Chords: No two simultaneous fretted notes on the same course. Keep chord fret spread <= 4 frets.

7. OUTPUT CONSTRAINTS:
   - Return ONLY the pure MusicXML document.
   - Enclose the output within ```xml and ``` fences.
   - Do NOT include markdown conversation, preamble, or commentary outside the XML block.

8. STRICT FULL TRANSCRIPTION & ZERO OMISSIONS (MANDATORY):
   - You MUST transcribe EVERY SINGLE MEASURE present in the document from the first measure to the very last measure.
   - NEVER truncate, skip, summarize, or abbreviate any measures.
   - ABSOLUTELY FORBIDDEN: Writing comments such as "<!-- Measures X-Y omitted for brevity -->", "omitted in template", "patterns continue", or jumping over measures.
   - Every single bar, note, rest, and beat in the input document MUST be fully written out in pure XML.
   - If the score has 50+ measures, output all 50+ measures sequentially without shortcuts.
"""

def load_api_key() -> str:
    """Load Gemini API Key from environment or .env file."""
    # 1. Check environment variable
    key = re.sub(r"\s+", "", os.environ.get("GEMINI_API_KEY", ""))
    if key:
        return key
    
    # 2. Check local .env file
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY="):
                        k = re.sub(r"\s+", "", line.split("=", 1)[1].strip().strip('"').strip("'"))
                        if k:
                            return k
        except Exception:
            pass
            
    return ""

def load_qwen_api_key() -> str:
    """Load QwenCloud/DashScope key without mixing it with Gemini credentials."""
    key = re.sub(r"\s+", "", os.environ.get("QWEN_API_KEY", ""))
    if key:
        return key
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("QWEN_API_KEY="):
                return re.sub(r"\s+", "", line.split("=", 1)[1].strip().strip('"').strip("'"))
    return load_api_key()

def load_deepseek_api_key() -> str:
    key = re.sub(r"\s+", "", os.environ.get("DEEPSEEK_API_KEY", ""))
    if key:
        return key
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DEEPSEEK_API_KEY="):
                return re.sub(r"\s+", "", line.split("=", 1)[1].strip().strip('"').strip("'"))
    return load_api_key()

def save_api_key(api_key: str):
    """Save Gemini API Key to local .env file."""
    lines = []
    found = False
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GEMINI_API_KEY="):
                        lines.append(f'GEMINI_API_KEY="{api_key}"\n')
                        found = True
                    else:
                        lines.append(line)
        except Exception:
            pass
    if not found:
        lines.append(f'GEMINI_API_KEY="{api_key}"\n')
    
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.environ["GEMINI_API_KEY"] = api_key

def save_qwen_api_key(api_key: str):
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines(True) if CONFIG_FILE.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith("QWEN_API_KEY="):
            lines[i] = f'QWEN_API_KEY="{api_key}"\n'
            break
    else:
        lines.append(f'QWEN_API_KEY="{api_key}"\n')
    CONFIG_FILE.write_text("".join(lines), encoding="utf-8")
    os.environ["QWEN_API_KEY"] = api_key

def save_deepseek_api_key(api_key: str):
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines(True) if CONFIG_FILE.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith("DEEPSEEK_API_KEY="):
            lines[i] = f'DEEPSEEK_API_KEY="{api_key}"\n'
            break
    else:
        lines.append(f'DEEPSEEK_API_KEY="{api_key}"\n')
    CONFIG_FILE.write_text("".join(lines), encoding="utf-8")
    os.environ["DEEPSEEK_API_KEY"] = api_key

def transcribe_with_qwen(file_bytes: bytes, mime_type: str, api_key: str, model_name: str, prompt: str) -> str:
    """Call QwenCloud's OpenAI-compatible multimodal chat endpoint with local PDF/image data."""
    if mime_type == "application/pdf":
        media = {"type": "file", "file": {"filename": "sheet-music.pdf", "file_data": f"data:application/pdf;base64,{base64.b64encode(file_bytes).decode()}"}}
    else:
        media = {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(file_bytes).decode()}"}}
    body = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": [media, {"type": "text", "text": prompt}]}],
        "temperature": 0.1,
        "max_tokens": 65536
    }).encode("utf-8")
    base_url = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1" if api_key.startswith("sk-sp-") else "https://maas.qwencloudapi.com/compatible-mode/v1"
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
    return content

def transcribe_with_deepseek(file_bytes: bytes, mime_type: str, api_key: str, prompt: str, model_name: str = "deepseek-flash") -> str:
    """Call DeepSeek Flash vision. DeepSeek's vision API accepts images, not PDFs."""
    if mime_type == "application/pdf":
        raise ValueError("DeepSeek Flash vision accepts images, not PDFs. Select Gemini or Qwen for PDF transcription.")
    content = [{"type": "text", "text": prompt}]
    if file_bytes:
        content.insert(0, {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(file_bytes).decode()}"}})
    body = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "max_tokens": 65536
    }).encode("utf-8")
    base_url = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1" if api_key.startswith("sk-sp-") else "https://api.deepseek.com"
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload["choices"][0]["message"]["content"]
    return "".join(item.get("text", "") for item in result if isinstance(item, dict)) if isinstance(result, list) else result

def validate_musicxml_schema(xml_content: str) -> Tuple[bool, List[str]]:
    """Validate MusicXML string against local MusicXML 4.0 schema."""
    if not SCHEMA_DIR.exists():
        return True, ["Schema directory not found, skipping deep XSD validation."]
    
    try:
        class LocalSchemaResolver(etree.Resolver):
            def resolve(self, url, pubid, context):
                filename = url.split('/')[-1]
                local_path = SCHEMA_DIR / filename
                if local_path.exists():
                    return self.resolve_filename(str(local_path), context)
                return None

        parser = etree.XMLParser()
        parser.resolvers.add(LocalSchemaResolver())
        schema_file = SCHEMA_DIR / "musicxml.xsd"
        with open(schema_file, "rb") as f:
            schema_doc = etree.parse(f, parser)
        schema = etree.XMLSchema(schema_doc)

        doc = etree.fromstring(xml_content.encode("utf-8"))
        is_valid = schema.validate(doc)
        errors = [f"Line {e.line}, Col {e.column}: {e.message}" for e in schema.error_log]
        return is_valid, errors
    except Exception as e:
        return False, [f"XML parsing/validation notice: {str(e)}"]

def sanitize_musicxml(raw_text: str) -> str:
    """Clean markdown code fences, ensure XML declaration, and format nicely."""
    cleaned = raw_text.strip()
    
    # Strip markdown block if present
    match = re.search(r'```(?:xml)?(.*?)```', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    
    # Locate beginning of XML
    start_xml = cleaned.find('<?xml')
    start_score = cleaned.find('<score-partwise')
    if start_xml != -1:
        start_idx = start_xml
    elif start_score != -1:
        start_idx = start_score
    else:
        start_idx = 0
        
    end_idx = cleaned.rfind('</score-partwise>')
    if end_idx != -1:
        cleaned = cleaned[start_idx:end_idx + len('</score-partwise>')]
    else:
        cleaned = cleaned[start_idx:]

    # Auto-repair common LLM XML syntax glitches:
    # 1. <rest><duration>D</duration></rest> -> <rest/><duration>D</duration>
    cleaned = re.sub(r'<rest>\s*<duration>(\d+)</duration>\s*</rest>', r'<rest/><duration>\1</duration>', cleaned)
    # 2. Strip any omission comments that could confuse parsers
    cleaned = re.sub(r'<!--\s*(?:Measures?|Remaining|Jumping|Following).*?-->', '', cleaned, flags=re.IGNORECASE)

    if not cleaned.startswith('<?xml'):
        cleaned = '<?xml version="1.0" encoding="UTF-8"?>\n' + cleaned
        
    try:
        parser = etree.XMLParser(remove_blank_text=True)
        doc = etree.fromstring(cleaned.encode('utf-8'), parser=parser)
        formatted = etree.tostring(doc, pretty_print=True, encoding='utf-8', xml_declaration=True).decode('utf-8')
        return formatted
    except Exception:
        # Fallback to cleaned text if lxml formatter encounters non-fatal issue
        return cleaned

def extract_metadata(xml_text: str) -> Dict[str, Any]:
    """Extract musical score metadata (title, composer, key, time signature, etc.)."""
    try:
        doc = etree.fromstring(xml_text.encode('utf-8'))
    except Exception:
        return {
            "title": "Sheet Music Transcription",
            "composer": "Unknown",
            "parts": ["Part 1"],
            "measureCount": 0,
            "timeSignature": "4/4",
            "keySignature": "C major",
            "clef": "G",
            "tempo": "120"
        }

    title = None
    title_elem = doc.find('.//work-title')
    if title_elem is not None and title_elem.text:
        title = title_elem.text.strip()
    if not title:
        mov_elem = doc.find('.//movement-title')
        if mov_elem is not None and mov_elem.text:
            title = mov_elem.text.strip()
    if not title:
        for cw in doc.findall('.//credit-words'):
            if cw.text and len(cw.text.strip()) > 1:
                title = cw.text.strip()
                break

    composer = None
    for c in doc.findall('.//creator'):
        if c.text:
            composer = c.text.strip()
            break

    parts = []
    for sp in doc.findall('.//score-part'):
        pname = sp.find('part-name')
        if pname is not None and pname.text:
            parts.append(pname.text.strip())
        else:
            parts.append(sp.get('id', 'Part'))

    first_part = doc.find('.//part')
    measures = first_part.findall('measure') if first_part is not None else []
    measure_count = len(measures)

    time_sig = "4/4"
    key_sig = "C major"
    clef = "G"
    tempo = "120"

    first_measure = measures[0] if measures else None
    if first_measure is not None:
        beats = first_measure.find('.//time/beats')
        beat_type = first_measure.find('.//time/beat-type')
        if beats is not None and beat_type is not None:
            time_sig = f"{beats.text.strip()}/{beat_type.text.strip()}"

        fifths = first_measure.find('.//key/fifths')
        mode = first_measure.find('.//key/mode')
        if fifths is not None and fifths.text:
            try:
                fifths_val = int(fifths.text.strip())
                mode_val = mode.text.strip() if mode is not None and mode.text else 'major'
                key_names = {
                    -7: 'Cb', -6: 'Gb', -5: 'Db', -4: 'Ab', -3: 'Eb', -2: 'Bb', -1: 'F',
                    0: 'C', 1: 'G', 2: 'D', 3: 'A', 4: 'E', 5: 'B', 6: 'F#', 7: 'C#'
                }
                key_sig = f"{key_names.get(fifths_val, str(fifths_val))} {mode_val}"
            except ValueError:
                pass

        clef_sign = first_measure.find('.//clef/sign')
        if clef_sign is not None and clef_sign.text:
            clef = clef_sign.text.strip()

        sound_tempo = first_measure.find('.//sound[@tempo]')
        if sound_tempo is not None and sound_tempo.get('tempo'):
            tempo = sound_tempo.get('tempo')
        else:
            bpm = first_measure.find('.//metronome/per-minute')
            if bpm is not None and bpm.text:
                tempo = bpm.text.strip()

    return {
        "title": title or "Transcribed Score",
        "composer": composer or "Traditional / AI Transcribed",
        "parts": parts or ["Part 1"],
        "measureCount": measure_count,
        "timeSignature": time_sig,
        "keySignature": key_sig,
        "clef": clef,
        "tempo": tempo
    }

def slice_pdf_pages(file_bytes: bytes, page_range_str: Optional[str] = None) -> Tuple[bytes, str]:
    """Slice PDF to specified pages using pypdf to avoid 503 high-demand compute limits."""
    if not pypdf:
        return file_bytes, "all pages (pypdf not available)"

    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        total_pages = len(reader.pages)
        if total_pages <= 1:
            return file_bytes, f"page 1 of 1"

        target_indices = []
        if page_range_str and page_range_str.strip():
            parts = page_range_str.split(',')
            for part in parts:
                part = part.strip()
                if '-' in part:
                    s_str, e_str = part.split('-', 1)
                    if s_str.isdigit() and e_str.isdigit():
                        s = max(1, int(s_str))
                        e = min(total_pages, int(e_str))
                        target_indices.extend(range(s - 1, e))
                elif part.isdigit():
                    idx = int(part) - 1
                    if 0 <= idx < total_pages:
                        target_indices.append(idx)

        # Blank page range means the complete document. Never silently drop pages.
        if not target_indices:
            target_indices = list(range(total_pages))
            summary = f"all {total_pages} pages"
        else:
            target_indices = sorted(list(set(target_indices)))
            summary = f"page(s) {', '.join(str(i + 1) for i in target_indices)} of {total_pages}"

        writer = pypdf.PdfWriter()
        for idx in target_indices:
            writer.add_page(reader.pages[idx])

        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue(), summary

    except Exception as e:
        print(f"[SheetXML Warning] PDF slicing skipped: {e}")
        return file_bytes, "original PDF"

def transcribe_sheet_music(
    file_bytes: bytes,
    mime_type: str,
    api_key: str,
    model_name: str = "gemini-3.8-flash",
    page_range: Optional[str] = None,
    mandolin_tab: bool = True,
    skill_level: str = "beginner"
) -> Dict[str, Any]:
    """Send sheet music file to Gemini API and return sanitized, validated MusicXML."""
    provider = "qwen" if model_name.startswith("qwen") else "deepseek" if model_name.startswith("deepseek") else "gemini"
    if not api_key:
        raise ValueError(f"{provider.title()} API key is required. Please provide a key in Settings or the environment.")

    # Obsolete / deprecated model mapping
    OBSOLETE_MAP = {
        "gemini-2.5-pro": "gemini-3-flash-preview",
        "gemini-2.5-flash": "gemini-3-flash-preview",
        "gemini-2.0-flash": "gemini-3-flash-preview",
        "gemini-1.5-pro": "gemini-3-flash-preview",
        "gemini-1.5-flash": "gemini-3-flash-preview",
    }
    model_name = OBSOLETE_MAP.get(model_name, model_name)

    # Slice PDF if applicable
    pdf_info = None
    if mime_type == "application/pdf" or file_bytes[:4] == b"%PDF":
        file_bytes, pdf_info = slice_pdf_pages(file_bytes, page_range)
        print(f"[SheetXML] Processed PDF: {pdf_info}")

    user_prompt = (
        "Transcribe this complete sheet music into precise MusicXML 4.0 format. "
        "MANDATORY REQUIREMENT: Transcribe EVERY single measure from the first measure to the very last measure of the entire piece. "
        "Do NOT skip, summarize, or omit any measures for brevity under any circumstances. Every measure and note must be fully transcribed in XML. "
        "Include all notes, accidentals, chords, rests, keys, meters, and expressive directions."
    )
    if mandolin_tab:
        user_prompt += (
            " Format with Standard Notation and playable Mandolin Tablature (4 courses G3-D4-A4-E5, "
            "frets 0-7 first position prioritized, <technical><string> and <fret> on notes)."
        )
    if page_range and page_range.strip() and not pdf_info:
        user_prompt += f" Note: Focus specifically on pages {page_range.strip()}."

    requested_model = model_name
    if provider == "qwen":
        print(f"[SheetXML] Calling Qwen model '{model_name}'...")
        raw_text = transcribe_with_qwen(file_bytes, mime_type, api_key, model_name, SYSTEM_PROMPT + "\n\n" + user_prompt)
        model_succeeded = model_name
        response = None
    elif provider == "deepseek":
        print("[SheetXML] Calling DeepSeek Flash vision...")
        raw_text = transcribe_with_deepseek(file_bytes, mime_type, api_key, SYSTEM_PROMPT + "\n\n" + user_prompt, model_name)
        model_succeeded = model_name
        response = None
    else:
        client = genai.Client(api_key=api_key)
        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

    # Keep a fallback for transient provider failures, but return the actual model used.
    FALLBACK_CASCADE = [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite"
    ]
    models_to_try = [model_name]
    for fb in FALLBACK_CASCADE:
        if fb not in models_to_try:
            models_to_try.append(fb)

    response = response if provider == "gemini" else None
    last_error = None

    for m_attempt in models_to_try if provider == "gemini" else []:
        for attempt_num in range(2):  # Try current model up to 2 times
            try:
                print(f"[SheetXML] Calling Gemini model '{m_attempt}' (Attempt {attempt_num + 1})...")
                response = client.models.generate_content(
                    model=m_attempt,
                    contents=[file_part, user_prompt],
                    config=types.GenerateContentConfig(
                        temperature=0.1,  # Low temperature for strict notation precision
                        system_instruction=SYSTEM_PROMPT,
                        max_output_tokens=65536  # Support full multi-measure scores
                    )
                )
                if response and response.text and response.text.strip():
                    model_succeeded = m_attempt
                    break
            except Exception as e:
                last_error = e
                err_str = str(e)
                # Check for transient overload (503), quota limits (429), or deprecated endpoints (404)
                is_transient = "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str or "high demand" in err_str or "404" in err_str
                if is_transient:
                    if attempt_num == 0:
                        print(f"[SheetXML Warning] Model '{m_attempt}' busy/high demand. Retrying in 1.5s...")
                        time.sleep(1.5)
                        continue
                    else:
                        print(f"[SheetXML Notice] Model '{m_attempt}' unavailable. Cascading to next available model...")
                        break
                else:
                    # Non-transient error (e.g. invalid API key)
                    raise e
        if model_succeeded:
            break

    if not model_succeeded or (provider == "gemini" and not response):
        raise RuntimeError(f"Transcription failed: All model endpoints were busy ({last_error}). Please try again in a few moments.")

    raw_text = raw_text if provider == "qwen" else (response.text or "")
    if not raw_text.strip():
        raise RuntimeError(f"{provider.title()} returned an empty response. Please verify the document clarity.")

    # Sanitize & format XML
    sanitized_xml = sanitize_musicxml(raw_text)

    # Mandolin Tab & Ergonomics Optimization (Spec compliance)
    mandolin_stats = None
    if mandolin_tab:
        try:
            print(f"[SheetXML] Optimizing mandolin tablature & fingering ({skill_level} mode)...")
            sanitized_xml, mandolin_stats = mandolin_optimizer.apply_mandolin_tab_to_score(
                sanitized_xml,
                skill_level=skill_level,
                create_tab_staff=True
            )
        except Exception as opt_err:
            print(f"[SheetXML Notice] Mandolin tab optimization note: {opt_err}")

    # Validate against MusicXML 4.0 schema
    is_valid, validation_errors = validate_musicxml_schema(sanitized_xml)

    # Extract score metadata
    metadata = extract_metadata(sanitized_xml)

    return {
        "musicxml": sanitized_xml,
        "isValid": is_valid,
        "validationErrors": validation_errors,
        "metadata": metadata,
        "mandolinStats": mandolin_stats,
        "requestedModel": requested_model,
        "modelUsed": model_succeeded,
        "modelFallback": model_succeeded != requested_model,
        "pdfInfo": pdf_info
    }


# ==============================================================================
# Web Server & REST Handlers
# ==============================================================================

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP Server for fast parallel requests."""
    daemon_threads = True
    allow_reuse_address = True

class SheetXMLRequestHandler(SimpleHTTPRequestHandler):
    """Custom HTTP handler serving UI and REST API."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        try:
            sys.stderr.write(f"[SheetXML] {self.address_string()} - {format % args}\n")
        except Exception:
            pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def _parse_multipart(self, body_bytes: bytes, boundary_str: str) -> Tuple[Dict[str, str], Optional[Dict[str, Any]]]:
        """Binary-safe parser for multipart form data without deprecated cgi module."""
        boundary = boundary_str.encode('utf-8')
        parts = body_bytes.split(b'--' + boundary)
        fields: Dict[str, str] = {}
        file_info: Optional[Dict[str, Any]] = None

        for part in parts:
            if not part or part == b'--\r\n' or part == b'--':
                continue
            if b'\r\n\r\n' not in part:
                continue
            header_bytes, payload = part.split(b'\r\n\r\n', 1)
            if payload.endswith(b'\r\n'):
                payload = payload[:-2]

            header_text = header_bytes.decode('utf-8', errors='replace')
            disp_match = re.search(
                r'Content-Disposition:\s*form-data;\s*name="([^"]+)"(?:;\s*filename="([^"]+)")?',
                header_text,
                re.IGNORECASE
            )
            if not disp_match:
                continue

            field_name = disp_match.group(1)
            filename = disp_match.group(2)

            if filename is not None:
                ct_match = re.search(r'Content-Type:\s*([^\r\n]+)', header_text, re.IGNORECASE)
                content_type = ct_match.group(1).strip() if ct_match else 'application/octet-stream'
                file_info = {
                    "field": field_name,
                    "filename": filename,
                    "contentType": content_type,
                    "data": payload
                }
            else:
                fields[field_name] = payload.decode('utf-8', errors='replace')

        return fields, file_info

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.path = "/index.html"
            return super().do_GET()

        if path == "/api/config":
            key = load_api_key()
            data = {
                "hasKey": bool(key),
                "hasQwenKey": bool(load_qwen_api_key()),
                "hasDeepSeekKey": bool(load_deepseek_api_key()),
                "maskedKey": f"***{key[-4:]}" if len(key) >= 4 else "",
                "availableModels": AVAILABLE_MODELS,
                "defaultModel": "gemini-3.8-flash"
            }
            self._send_json(200, data)
            return

        if path == "/api/sample":
            if SAMPLE_XML.exists():
                with open(SAMPLE_XML, "r", encoding="utf-8") as f:
                    content = sanitize_musicxml(f.read())
                try:
                    content, m_stats = mandolin_optimizer.apply_mandolin_tab_to_score(content, skill_level="beginner")
                except Exception:
                    m_stats = None
                meta = extract_metadata(content)
                is_valid, errors = validate_musicxml_schema(content)
                self._send_json(200, {
                    "musicxml": content,
                    "isValid": is_valid,
                    "validationErrors": errors,
                    "metadata": meta,
                    "mandolinStats": m_stats,
                    "filename": "music-xml-example.xml"
                })
            else:
                self._send_json(404, {"error": "Sample file not found."})
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/save-config":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8")
                req_data = json.loads(body)
                api_key = req_data.get("apiKey", "").strip()
                provider = req_data.get("provider", "gemini")
                if api_key:
                    if api_key.replace(" ", "").startswith("sk-sp-"):
                        save_qwen_api_key(api_key)
                        save_deepseek_api_key(api_key)
                    else:
                        ({"qwen": save_qwen_api_key, "deepseek": save_deepseek_api_key}.get(provider, save_api_key))(api_key)
                self._send_json(200, {"success": True, "message": "API Key saved successfully."})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if path == "/api/verify-key":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8")
                req_data = json.loads(body)
                provider = req_data.get("provider", "gemini")
                test_key = req_data.get("apiKey", "").strip() or ({"qwen": load_qwen_api_key, "deepseek": load_deepseek_api_key}.get(provider, load_api_key))()
                if not test_key:
                    self._send_json(400, {"error": "No API Key provided to verify."})
                    return

                if provider == "qwen":
                    transcribe_with_qwen(b"", "image/png", test_key, "qwen3.8-flash", "Reply with: OK")
                elif provider == "deepseek":
                    transcribe_with_deepseek(b"", "image/png", test_key, "Reply with: OK", "deepseek-v4.1-flash" if test_key.startswith("sk-sp-") else "deepseek-flash")
                else:
                    client = genai.Client(api_key=test_key)
                    client.models.generate_content(model="gemini-3-flash-preview", contents="Reply with: OK")
                self._send_json(200, {
                    "success": True,
                    "message": "Gemini AI connection verified successfully! Your subscription is active."
                })
            except Exception as e:
                self._send_json(400, {
                    "success": False,
                    "error": f"Connection failed: {str(e)}"
                })
            return

        if path == "/api/transcribe":
            try:
                content_type = self.headers.get("Content-Type", "")
                if "multipart/form-data" not in content_type:
                    self._send_json(400, {"error": "Expected multipart/form-data"})
                    return

                boundary = ""
                for part in content_type.split(";"):
                    part = part.strip()
                    if part.startswith("boundary="):
                        boundary = part.split("=", 1)[1].strip('"').strip("'")
                if not boundary:
                    self._send_json(400, {"error": "Missing boundary in Content-Type header"})
                    return

                content_len = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(content_len)
                fields, file_info = self._parse_multipart(body_bytes, boundary)

                if not file_info or not file_info.get("data"):
                    self._send_json(400, {"error": "No sheet music file received in upload."})
                    return

                filename = file_info["filename"]
                file_bytes = file_info["data"]
                mime_type = file_info.get("contentType", "")
                if not mime_type or mime_type == "application/octet-stream":
                    if filename.lower().endswith(".pdf"):
                        mime_type = "application/pdf"
                    else:
                        mime_type = mimetypes.guess_type(filename)[0] or "image/png"

                model = fields.get('model', 'gemini-3.8-flash')
                if model not in {item["id"] for item in AVAILABLE_MODELS}:
                    self._send_json(400, {"error": f"Unsupported Gemini model: {model}"})
                    return
                page_range = fields.get('pageRange', None)
                mandolin_tab = fields.get('mandolinTab', 'true').lower() in ('true', '1', 'yes')
                skill_level = fields.get('skillLevel', 'beginner')
                api_key_param = fields.get('apiKey', '')
                provider = "qwen" if model.startswith("qwen") else "deepseek" if model.startswith("deepseek") else "gemini"
                api_key = api_key_param.strip() if api_key_param else ({"qwen": load_qwen_api_key, "deepseek": load_deepseek_api_key}.get(provider, load_api_key))()

                if not api_key:
                    self._send_json(400, {
                        "error": "Gemini API Key missing. Please click Settings to configure your key from Google AI Studio."
                    })
                    return

                print(f"[SheetXML] Received file '{filename}' ({len(file_bytes)} bytes). Transcribing with {model} (Mandolin TAB: {mandolin_tab}, Skill: {skill_level})...")
                result = transcribe_sheet_music(
                    file_bytes=file_bytes,
                    mime_type=mime_type,
                    api_key=api_key,
                    model_name=model,
                    page_range=page_range,
                    mandolin_tab=mandolin_tab,
                    skill_level=skill_level
                )
                result["filename"] = filename
                result["success"] = True

                self._send_json(200, result)

            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_json(500, {"error": str(e)})
            return

        if path == "/api/save-file":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8")
                req = json.loads(body)
                filename = req.get("filename", "score.musicxml")
                xml_content = req.get("musicxml", "")

                if not filename.endswith(".musicxml") and not filename.endswith(".xml"):
                    filename += ".musicxml"

                out_dir = BASE_DIR / "transcriptions"
                out_dir.mkdir(exist_ok=True)
                target_path = out_dir / filename
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(xml_content)

                self._send_json(200, {
                    "success": True,
                    "savedPath": str(target_path),
                    "filename": filename
                })
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        self._send_json(404, {"error": "Not Found"})

    def _send_json(self, status_code: int, data: Dict[str, Any]):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

# ==============================================================================
# CLI and Entry Point
# ==============================================================================

def run_cli(args):
    """Execute transcription via command line."""
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"[ERROR] Input file '{input_path}' does not exist.")
        sys.exit(1)

    api_key = args.key or load_api_key()
    if not api_key:
        print("[ERROR] Gemini API Key is required.")
        print("Provide via --key <KEY>, set GEMINI_API_KEY environment variable, or run without arguments for the GUI.")
        sys.exit(1)

    # Determine MIME type
    mime_type = mimetypes.guess_type(str(input_path))[0]
    if not mime_type:
        mime_type = "application/pdf" if input_path.suffix.lower() == ".pdf" else "image/png"

    with open(input_path, "rb") as f:
        file_bytes = f.read()

    output_path = args.output
    if not output_path:
        output_path = input_path.with_suffix(".musicxml")
    else:
        output_path = Path(output_path)

    print(f"\n==========================================")
    print(f"🎵 SheetXML Music Transcriber")
    print(f"Input:   {input_path.name} ({len(file_bytes)} bytes)")
    print(f"Model:   {args.model}")
    print(f"Output:  {output_path.name}")
    print(f"==========================================\n")
    print("[1/3] Transcribing sheet music with Gemini multimodal reasoning...")

    try:
        result = transcribe_sheet_music(
            file_bytes=file_bytes,
            mime_type=mime_type,
            api_key=api_key,
            model_name=args.model,
            page_range=args.pages,
            mandolin_tab=not args.no_tab,
            skill_level=args.skill
        )

        xml = result["musicxml"]
        is_valid = result["isValid"]
        errors = result["validationErrors"]
        meta = result["metadata"]
        m_stats = result.get("mandolinStats")

        print(f"[2/3] Validating MusicXML 4.0 structure...")
        if is_valid:
            print("      [SUCCESS] Schema validation: PASSED (W3C MusicXML 4.0 compliant)")
        else:
            print(f"      [NOTICE] Schema validation: {len(errors)} notes reported")
            for err in errors[:3]:
                print(f"         - {err}")

        if m_stats:
            print(f"      [MANDOLIN TAB] Optimized {m_stats.get('totalNotes', 0)} notes:")
            print(f"         - Frets 0-7 (1st position): {m_stats.get('firstPositionCount', 0)} ({round(m_stats.get('firstPositionCount', 0) / max(1, m_stats.get('totalNotes', 1)) * 100)}%)")
            print(f"         - Open strings:            {m_stats.get('openStringsCount', 0)}")
            print(f"         - Highest fret used:       {m_stats.get('highestFret', 0)}")
            print(f"         - Course balance:          E:{m_stats['stringUsage'].get(1,0)} | A:{m_stats['stringUsage'].get(2,0)} | D:{m_stats['stringUsage'].get(3,0)} | G:{m_stats['stringUsage'].get(4,0)}")

        print(f"[3/3] Writing MusicXML to '{output_path}'...")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml)

        print(f"\n✨ Transcription Complete!")
        print(f"   Title:          {meta.get('title')}")
        print(f"   Composer:       {meta.get('composer')}")
        print(f"   Measures:       {meta.get('measureCount')}")
        print(f"   Key Signature:  {meta.get('keySignature')}")
        print(f"   Time Signature: {meta.get('timeSignature')}")
        print(f"   Saved to:       {output_path.resolve()}\n")

    except Exception as e:
        print(f"\n[FAILED] Transcription error: {str(e)}")
        sys.exit(1)

def start_server(port: int = 5050, open_browser: bool = True):
    """Launch the Web GUI server."""
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # Record PID for process lifecycle management
    pid_file = BASE_DIR / "sheetxml.pid"
    try:
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    def _cleanup_pid():
        try:
            if pid_file.exists():
                pid_file.unlink()
        except Exception:
            pass
    atexit.register(_cleanup_pid)

    server_address = ('127.0.0.1', port)
    
    try:
        httpd = ThreadedHTTPServer(server_address, SheetXMLRequestHandler)
    except OSError:
        # Try alternate port if 5050 is busy
        port = 5051
        server_address = ('127.0.0.1', port)
        httpd = ThreadedHTTPServer(server_address, SheetXMLRequestHandler)

    url = f"http://localhost:{port}"
    print(f"\n=======================================================")
    print(f"🎼 SheetXML - Sheet Music to MusicXML Transcriber")
    print(f"=======================================================")
    print(f"Server running at: {url}")
    print(f"Press Ctrl+C to stop the server.\n")

    if open_browser:
        time.sleep(0.3)
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SheetXML server...")
        httpd.server_close()
        print("Done.")

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe Sheet Music (PDF/Images) to MusicXML 4.0 using Gemini."
    )
    parser.add_argument("input_file", nargs="?", help="PDF or image file to transcribe (CLI mode)")
    parser.add_argument("-o", "--output", help="Output MusicXML file path")
    parser.add_argument("-m", "--model", default="gemini-3.8-flash", choices=[m["id"] for m in AVAILABLE_MODELS], help="Gemini model to use")
    parser.add_argument("-k", "--key", help="Gemini API Key (optional, defaults to GEMINI_API_KEY env or .env file)")
    parser.add_argument("-p", "--pages", help="Page range for multi-page PDFs (e.g. '1-2' or '1')")
    parser.add_argument("--no-tab", action="store_true", help="Disable mandolin tab fingering optimization")
    parser.add_argument("--skill", default="beginner", choices=["beginner", "advanced"], help="Mandolin fingering skill level (default: beginner)")
    parser.add_argument("--port", type=int, default=5050, help="Port for the Web GUI server")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open browser on GUI startup")

    args = parser.parse_args()

    if args.input_file:
        run_cli(args)
    else:
        start_server(port=args.port, open_browser=not args.no_browser)

if __name__ == "__main__":
    main()
