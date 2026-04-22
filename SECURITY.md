# Security policy

VoteWise India handles only public ECI information and never persists user PII
server-side. Conversations live in the browser; the server logs request IDs and
status codes, not chat content.

## Reporting a vulnerability

Please email <votewisesupport@gmail.com> with:

- A description of the issue and its impact.
- Steps to reproduce (request, headers, body).
- Your name/handle for credit (optional).

We aim to acknowledge within **3 business days** and to ship a fix or mitigation
within **14 days** for high-severity issues. Please do not file a public issue or
exploit the vulnerability against the live Cloud Run deployment beyond what is
needed to demonstrate it.

## Scope

In scope: this repository and the deployed Cloud Run service.
Out of scope: third-party APIs (Gemini, Cloud Translation, Cloud TTS) — please
report those to Google directly.

## Hardening already in place

CSP (no `unsafe-inline` styles), HSTS on HTTPS, COOP, CORP, X-Frame-Options,
Permissions-Policy, X-Content-Type-Options, Referrer-Policy, X-Request-ID,
16 KiB request-body cap, per-IP sliding-window rate limiter with `Retry-After`,
Pydantic v2 input validation, Secret Manager for the Gemini API key, non-root
container user.
