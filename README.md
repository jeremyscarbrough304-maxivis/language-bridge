# 🌉 Language Bridge — deploy to Vercel

Intent-first English → Spanish translator as a web app.
Offline phrase-bank engine works with **zero API keys**; optional LLM
upgrade via env vars.

## Files

| File | What it is |
|---|---|
| `index.html` | The web UI (served at your Vercel URL) |
| `api/translate.py` | Serverless function: `POST /api/translate` → JSON |
| `intent_translator.py` | The translation engine (unchanged from the CLI build) |
| `requirements.txt` | No hard deps (stdlib only) |

## Deploy (5 minutes)

1. On github.com, create a new repo called **`language-bridge`**.
2. Upload these files, keeping the `api/` folder structure:
   - `index.html`, `intent_translator.py`, `requirements.txt` → repo root
   - `api/translate.py` → inside an `api/` folder
   - (Tip: on the GitHub website use **Add file → Upload files**; create the
     `api` folder first via **Add file → Create new file** named `api/.gitkeep`,
     then upload `translate.py` into it — or push from a computer with git.)
3. Go to [vercel.com](https://vercel.com) → **Add New → Project** →
   **Import** the `language-bridge` repo → **Deploy**.
   (Your Vercel GitHub App is already installed, so the repo shows up.)
4. Done — you get a `language-bridge.vercel.app` URL. Every push redeploys.

## Optional: full LLM coverage

The offline engine handles common phrases. For everything else, add one of
these in Vercel → Project → **Settings → Environment Variables**, then redeploy:

- `ANTHROPIC_API_KEY` → Claude primary
- `GEMINI_API_KEY` → Gemini fallback

Without keys, unknown phrases return an honest low-confidence fallback
instead of a hallucinated translation.

## API

```
POST /api/translate
{"text": "tell her I miss her like crazy", "dialect": "latam"}   # or "castilian"

→ {"translation": "…", "intent": "affection", "tone": "tender",
    "confidence": 0.93, "engine": "offline-phrase-bank",
    "literal_gloss": "…", "note": "…", "corrected": false}
```
