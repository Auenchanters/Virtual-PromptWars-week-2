# VoteWise India — Election Process Education Assistant

An interactive web assistant that helps Indian voters understand the election process, timelines
and steps — built for the **Virtual PromptWars Week 2** hackathon (vertical: *Election Process
Education*).

It answers questions like:

- *"How do I register to vote?"*
- *"Do I need my voter ID (EPIC) at the booth?"*
- *"What is the Model Code of Conduct?"*
- *"How does the postal ballot for senior citizens work?"*

---

## Chosen vertical

**Election Process Education.** The solution focuses on India and is grounded in publicly
available Election Commission of India (ECI) rules — voter eligibility, registration (Form 6),
voter IDs, polling-day procedure, postal ballots, and the general-election timeline.

## Approach and logic

1. **Grounded LLM.** User questions are answered by **Google Gemini** (`gemini-flash-latest`)
   via the `google-genai` SDK. A static, auditable JSON file (`app/data/election_info.json`)
   holds the facts the model is allowed to rely on. The system prompt injects that fact sheet
   and instructs the model to refuse off-topic or speculative queries and to point users to
   `eci.gov.in` when they ask for specific dates, results, or legal advice.
2. **Lightweight, accessible UI.** A single static HTML + vanilla JS + CSS frontend served by
   the same FastAPI process. No build step, no bundlers, no node_modules — which keeps the
   repo tiny and the attack surface small.
3. **Structured utilities around the chat.** Besides the free-form chat, the UI exposes:
   - Quick-action chips for common questions.
   - A deterministic **eligibility checker** (age, citizenship, residency) that runs entirely
     client-side and cites the same rule the LLM uses.
   - A **timeline view** rendered from the grounding JSON via `/api/info`.

## How the solution works

```text
┌──────────────────┐     POST /api/chat      ┌────────────────────┐   generate_content   ┌──────────┐
│   Browser UI     │ ──────────────────────► │   FastAPI (Cloud   │ ───────────────────► │  Gemini  │
│ (index/app.js)   │ ◄────────────────────── │   Run container)   │ ◄─────────────────── │  API     │
└──────────────────┘      JSON reply         └────────────────────┘                      └──────────┘
                                                     │
                                                     ▼
                                       app/data/election_info.json
                                       (grounding facts + FAQs)
```

- `GET /` — serves the UI.
- `GET /api/info` — returns the grounding JSON (used for the timeline and client-side rendering).
- `POST /api/chat` — takes `{ history, message }`, calls Gemini with the system prompt +
  recent turns, returns `{ reply, disclaimer }`. Protected by in-memory rate limiting and
  Pydantic validation.
- `GET /health` — liveness probe for Cloud Run.

### Security

- The Gemini API key is never in the repo. It is loaded from the `GEMINI_API_KEY` env var,
  which on Cloud Run is injected from **Secret Manager**.
- Strict **Content-Security-Policy**, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy`, and `Permissions-Policy` headers on every response.
- Input is validated with Pydantic (bounded message length, bounded history length, regex on role).
- A sliding-window per-IP rate limiter (30 req/min) protects the Gemini quota.
- The system prompt hardens the model against off-topic, partisan, or impersonation requests
  and makes it refuse to reveal its instructions.
- No PII is persisted server-side; the conversation is held in the browser only.

### Accessibility

- Semantic landmarks (`<header>`, `<main>`, `<aside>`, `<footer>`), a skip-link, and proper
  heading hierarchy.
- Chat log is a `role="log"` with `aria-live="polite"` so screen readers announce replies.
- All interactive controls are keyboard-operable; visible `:focus-visible` outlines.
- Respects `prefers-color-scheme` (light/dark) and `prefers-reduced-motion`.
- Colour contrast meets WCAG AA in both themes.
- Labels, `<legend>`, and `<kbd>` hints are provided throughout.

### Testing

- `pytest` covers grounding, prompt assembly, endpoint contracts, input validation, rate
  limiting, upstream failure handling, and security headers.
- Gemini is replaced with a fake via FastAPI's dependency overrides, so the suite runs fully
  offline.

Run the suite:

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Google services used

| Service | Role |
|---------|------|
| **Gemini API** (`gemini-flash-latest`, via `google-genai`) | Core assistant — turns user questions into grounded answers. |
| **Cloud Run** | Hosts the containerised FastAPI app with auto-HTTPS and autoscaling. |
| **Secret Manager** | Stores `GEMINI_API_KEY` and surfaces it to Cloud Run as an env var. |
| **Cloud Logging** | Structured request logs (automatic on Cloud Run). |

---

## Running locally

Prerequisites: Python 3.11+, a Gemini API key from [aistudio.google.com](https://aistudio.google.com/).

```bash
python -m venv .venv
.venv\Scripts\activate                # Windows
# source .venv/bin/activate           # macOS/Linux

pip install -r requirements-dev.txt

copy .env.example .env                # Windows  (cp on mac/linux)
# edit .env and set GEMINI_API_KEY

# On Windows PowerShell:
$env:GEMINI_API_KEY = "your-key-here"
# On bash:
# export GEMINI_API_KEY=your-key-here

uvicorn app.main:app --reload --port 8080
```

Open <http://localhost:8080>.

Run tests:

```bash
pytest
```

---

## Deploying to Cloud Run

One-time setup:

```bash
gcloud config set project virtual-promptwars-week2

# Store the API key in Secret Manager
gcloud secrets create GEMINI_API_KEY --replication-policy=automatic
printf "YOUR_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-

# Grant Cloud Run's runtime service account access to the secret
PROJECT_NUMBER=256416723201
gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Deploy (repeatable):

```bash
gcloud run deploy election-assistant \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest \
  --cpu 1 --memory 512Mi --max-instances 3
```

The command prints a URL like `https://election-assistant-xxx-uc.a.run.app` — that is the
**Deployed Link** to paste into the submission form.

---

## Assumptions

- The assistant is limited to India's election process. It politely declines queries about
  other countries, specific upcoming poll dates, candidates, or political analysis.
- Grounding data is a curated snapshot of ECI rules at build time. The UI and the system prompt
  consistently direct users to `voters.eci.gov.in` for anything time-sensitive (deadlines,
  schedules, current election phases).
- The eligibility checker captures the three core statutory criteria (age on 1 Jan of the
  revision year, citizenship, ordinary residence). It does not cover edge cases such as
  disqualification under the Representation of the People Act.
- Rate limiting is in-memory (per instance). The app is configured for a small `max-instances`
  value; a shared limiter store would be required at higher scale.

---

## Project layout

```text
app/
  main.py              # FastAPI app + routes + security headers + rate limit
  chat.py              # Gemini client wrapper + system prompt + history trimming
  grounding.py         # Loads & renders the grounding JSON
  data/
    election_info.json # Curated ECI facts (authoritative grounding)
  static/
    index.html         # Accessible, semantic UI
    style.css          # Responsive, light/dark, WCAG AA
    app.js             # Chat, quick actions, eligibility, timeline
tests/
  test_api.py
  test_chat.py
  test_grounding.py
Dockerfile             # python:3.11-slim, non-root user, Cloud Run ready
requirements.txt       # Runtime deps
requirements-dev.txt   # Runtime + test deps
```

---

## License

MIT — see repository for details.
