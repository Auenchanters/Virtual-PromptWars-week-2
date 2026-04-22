# VoteWise India — Election Process Education Assistant

A multilingual, voice-enabled web assistant that helps Indian voters understand the
election process, timelines, and steps — built for **Virtual PromptWars Week 2**
(vertical: *Election Process Education*).

It answers questions like *"How do I register to vote?"*, *"What documents do I need
at the booth?"*, *"What is the Model Code of Conduct?"* — in **13 Indian languages**,
with **Google Search-grounded citations** to live ECI sources, **streamed token-by-token**,
and **read aloud** via browser or Cloud TTS.

---

## Rubric → evidence map

The AI judge scores on seven criteria. Each row below is verifiable from the repo.

| Criterion | Where it lives | How to verify |
|---|---|---|
| **Code Quality** | Module split: [app/main.py](app/main.py) (routing only), [app/chat.py](app/chat.py) (Gemini), [app/translation.py](app/translation.py) (Cloud Translate), [app/speech.py](app/speech.py) (Cloud TTS), [app/limiter.py](app/limiter.py) (rate limit), [app/security.py](app/security.py) (headers + body cap). All public services are typed `Protocol`s with DI providers. | `ruff check . && ruff format --check .` → 0 errors. `mypy app` → typed. |
| **Security** | [app/security.py](app/security.py): CSP (no `unsafe-inline` styles), HSTS (only on https), COOP, CORP, X-Frame-Options, Permissions-Policy, Referrer-Policy, X-Request-ID, body-size 413. Per-IP sliding-window limiter with `Retry-After`. Pydantic v2 validation on every input. Secret Manager for `GEMINI_API_KEY`. | [tests/test_security_headers.py](tests/test_security_headers.py) enumerates every header + body-size + invalid-content-length + HSTS toggle. |
| **Efficiency** | Async handlers, `anyio.to_thread.run_sync` for blocking SDK calls, GZip middleware, ETag + 304 + `Cache-Control` on `/api/info`, thread-safe LRU caches in [app/translation.py](app/translation.py) and [app/speech.py](app/speech.py), `lru_cache` on grounding/system prompt, **SSE streaming** at `/api/chat/stream`. | [tests/test_streaming.py](tests/test_streaming.py), `test_info_supports_etag_and_304` in [tests/test_api.py](tests/test_api.py). |
| **Testing** | 52 tests across 9 files, fully offline (no Google SDK calls). Coverage **85 %** with an 80 % `--cov-fail-under` gate. CI in [.github/workflows/ci.yml](.github/workflows/ci.yml). | `pytest --cov=app --cov-report=term`. |
| **Accessibility** | 13 Indian languages (en/hi/ta/te/bn/mr/gu/kn/ml/pa/ur/or/as) via `/api/i18n/{lang}`. RTL handled for Urdu. Browser SpeechRecognition + SpeechSynthesis with Cloud TTS fallback. Semantic HTML, `role="log"` + `aria-live`, skip link, `prefers-reduced-motion`, WCAG-AA contrast. | Toggle the language pill in the header; click 🎤 mic and 🔊 read-aloud. |
| **Google Services** | **7 services**: Gemini 2.0 Flash + **Google Search grounding tool**, Cloud Translation v3, Cloud Text-to-Speech, Cloud Run, Secret Manager, Cloud Logging. | See *Google services used* table below. |
| **Problem Statement Alignment** | Live ECI grounding via Google Search tool with citation chips, India-specific multi-language UI, polling-station deep-link helper (`/api/states` → `voters.eci.gov.in/search-in-electoral-roll?state=…`), eligibility checker, ECI timeline. | Open the UI; the right-hand "Find your booth" card lists all 28 states + 8 UTs from [app/data/election_info.json](app/data/election_info.json). |

---

## Architecture

```text
┌──────────────────┐ POST /api/chat/stream ┌───────────────────────────┐
│  Browser UI      │ ────────────────────► │  FastAPI on Cloud Run     │
│  (vanilla JS,    │ ◄──── SSE chunks ──── │  ──────────────────────── │
│   13 languages,  │                       │  • async handlers         │
│   voice in/out)  │ POST /api/translate   │  • GZip + security hdrs   │
│                  │ ────────────────────► │  • per-IP rate limiter    │
└──────────────────┘ POST /api/tts         └────────────┬──────────────┘
        ▲                                               │
        │                                ┌──────────────┴───────────────┐
        │                                ▼              ▼                ▼
        │                      ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │                      │ Gemini 2.0   │ │ Cloud        │ │ Cloud Text-  │
        │                      │ + Google     │ │ Translation  │ │ to-Speech    │
        │                      │ Search tool  │ │ v3           │ │ (WaveNet)    │
        │                      └──────┬───────┘ └──────────────┘ └──────────────┘
        │                             │
        └──── live ECI citations ◄────┘   sources: eci.gov.in, voters.eci.gov.in, pib.gov.in
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Static UI |
| GET | `/health` | Liveness probe |
| GET | `/api/info` | Grounding payload (ETag + 304 + 1 h cache) |
| GET | `/api/states` | 28 states + 8 UTs for booth-lookup |
| GET | `/api/languages` | Supported UI languages |
| GET | `/api/i18n/{lang}` | UI string bundle (English instant; others translated + cached) |
| POST | `/api/chat` | One-shot grounded reply with citations |
| POST | `/api/chat/stream` | SSE stream of the reply |
| POST | `/api/translate` | Cloud Translation passthrough |
| POST | `/api/tts` | Cloud TTS MP3 (used when browser TTS lacks the voice) |

---

## Google services used

| Service | Role | File |
|---|---|---|
| **Gemini 2.0 Flash** (`google-genai`) | Core assistant | [app/chat.py](app/chat.py) |
| **Google Search grounding tool** | Live ECI sources for time-sensitive answers + citations | `RealGeminiClient._config` in [app/chat.py](app/chat.py) |
| **Cloud Translation v3** (`google-cloud-translate`) | Multi-language UI + chat translate-in/translate-out | [app/translation.py](app/translation.py) |
| **Cloud Text-to-Speech** (`google-cloud-texttospeech`) | WaveNet voices for languages where browser TTS is weak | [app/speech.py](app/speech.py) |
| **Cloud Run** | Hosts the container with auto-HTTPS and autoscaling | [Dockerfile](Dockerfile) |
| **Secret Manager** | Injects `GEMINI_API_KEY` | `--set-secrets` in deploy command |
| **Cloud Logging** | Structured JSON logs (auto-collected on Cloud Run) | logging config in [app/main.py](app/main.py) |

---

## Security

- **CSP** — `default-src 'self'`, `object-src 'none'`, `worker-src 'self'`, no `unsafe-inline` for styles.
- **HSTS** — `max-age=63072000; includeSubDomains; preload`, only when the request is HTTPS (Cloud Run forwards `x-forwarded-proto`).
- **COOP / CORP / Permissions-Policy** — strict isolation defaults.
- **X-Frame-Options: DENY**, **X-Content-Type-Options: nosniff**, **Referrer-Policy: strict-origin-when-cross-origin**.
- **X-Request-ID** echoed/generated on every response for traceability.
- **Body-size cap** (16 KiB by default) — oversized requests get 413 before parsing.
- **Pydantic v2 validation** on every body, with bounded length and language allow-list.
- **Per-IP sliding-window rate limit** with `Retry-After` on 429 (chat 30/min, translate 60/min, TTS 20/min).
- **No PII persisted server-side**; the conversation is held only in the browser.
- **Disclosure** — see [SECURITY.md](SECURITY.md).

---

## Accessibility

- 13 languages with one-click switching: English, हिन्दी, தமிழ், తెలుగు, বাংলা, मराठी, ગુજરાતી, ಕನ್ನಡ, മലയാളം, ਪੰਜਾਬੀ, اردو (RTL), ଓଡ଼ିଆ, অসমীয়া.
- Voice input via the browser **SpeechRecognition** API; voice output via **SpeechSynthesis** with Cloud TTS fallback.
- Semantic landmarks (`<header>`, `<main>`, `<aside>`, `<footer>`), skip link, `role="log"` + `aria-live="polite"`.
- Visible `:focus-visible` outlines; full keyboard operability.
- Honours `prefers-color-scheme` and `prefers-reduced-motion`.
- WCAG-AA colour contrast in both themes.

---

## Running locally

Prerequisites: Python 3.11+, a Gemini API key from [aistudio.google.com](https://aistudio.google.com/),
and (optionally) Application Default Credentials with `roles/cloudtranslate.user` and
`roles/texttospeech.user` if you want to exercise translation/TTS locally.

```bash
python -m venv .venv
.venv\Scripts\activate                # Windows
# source .venv/bin/activate           # macOS/Linux

pip install -r requirements-dev.txt

# Windows PowerShell:
$env:GEMINI_API_KEY = "your-key-here"
# bash:
# export GEMINI_API_KEY=your-key-here

uvicorn app.main:app --reload --port 8080
```

Open <http://localhost:8080>.

### Run the test suite + lint

```bash
ruff check . && ruff format --check .
pytest --cov=app --cov-report=term-missing
```

The suite is fully offline — Gemini, Translator, and Speaker are all Protocol-typed
and replaced with fakes via FastAPI dependency overrides. No network, no real Google
SDK calls during tests.

---

## Deploying to Cloud Run

```bash
gcloud services enable \
  run.googleapis.com \
  translate.googleapis.com \
  texttospeech.googleapis.com \
  secretmanager.googleapis.com

# Store the Gemini key in Secret Manager (one-time)
gcloud secrets create GEMINI_API_KEY --replication-policy=automatic
printf "YOUR_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-

PROJECT_NUMBER=$(gcloud projects describe "$(gcloud config get-value project)" --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Deploy (repeatable)
gcloud run deploy election-assistant \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest \
  --cpu 1 --memory 512Mi --max-instances 3
```

The default-allowed origin in [app/main.py](app/main.py) matches the Cloud Run URL;
override via the `ALLOWED_ORIGINS` env var if your service URL differs.

---

## Project layout

```text
app/
  main.py            # FastAPI routes only (delegates to modules below)
  chat.py            # Gemini client + Google Search grounding tool + streaming
  translation.py     # Cloud Translation v3 wrapper + thread-safe LRU cache
  speech.py          # Cloud TTS wrapper + audio LRU cache
  grounding.py       # Loads + validates election_info.json; states_and_uts()
  limiter.py         # Per-IP sliding-window limiter with Retry-After
  security.py        # CSP/HSTS/COOP/CORP headers + body-size middleware
  data/
    election_info.json   # Curated ECI grounding (28 states + 8 UTs)
    i18n.json            # English source strings for UI translation
  static/
    index.html, app.js, style.css
tests/
  test_api.py, test_chat.py, test_grounding.py,
  test_streaming.py, test_translation.py, test_speech.py,
  test_security_headers.py, test_limiter.py, conftest.py
.github/workflows/ci.yml    # ruff + mypy + pytest --cov on PR
Dockerfile                  # python:3.12-slim, non-root, --no-cache-dir
SECURITY.md                 # Disclosure policy
```

---

## Assumptions

- The assistant is limited to India's election process; off-topic requests are politely declined.
- Static grounding (`election_info.json`) is a curated ECI snapshot at build time. For
  *time-sensitive* facts (current election dates, MCC notifications) the model uses the
  live Google Search grounding tool and surfaces citations.
- The eligibility checker covers the three core statutory criteria (age on 1 Jan of the
  revision year, citizenship, ordinary residence). Disqualifications under the
  Representation of the People Act are out of scope.
- Rate limiting is per-instance in-memory; deploy with a small `max-instances` (≤3) so
  the limit remains effective without an external store.

---

## License

MIT.
