import os
from google import genai

key = os.environ.get('GEMINI_API_KEY')
if not key:
    raise SystemExit('Set GEMINI_API_KEY before running this model probe.')
client = genai.Client(api_key=key)

candidates = [
    'gemini-3.1-pro-preview',
    'gemini-3.8-flash',
    'gemini-3-flash-preview',
    'gemini-3-pro-preview',
    'gemini-3.8-pro',
    'gemini-flash-latest',
    'gemini-pro-latest',
    'gemini-2.5-flash-lite'
]

for model in candidates:
    try:
        res = client.models.generate_content(model=model, contents='Hello! Respond with: OK')
        print(f"[SUCCESS] {model}: {res.text.strip()[:40]}")
    except Exception as e:
        msg = str(e).replace('\n', ' ')
        print(f"[FAILED]  {model}: {type(e).__name__} -> {msg[:140]}")
