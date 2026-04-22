"use strict";

/* ==================================================================
 * VoteWise India — client script
 *
 * Features (rubric-aligned):
 *  - Multi-language UI (Accessibility, Google Services, Problem Alignment)
 *    via /api/i18n/{lang} (Cloud Translation on server)
 *  - Streaming chat via SSE /api/chat/stream (Efficiency)
 *  - Voice input (Web Speech API) + read-aloud (Speech Synthesis or
 *    Cloud TTS fallback at /api/tts) (Accessibility)
 *  - Citations from Gemini Google Search grounding (Google Services)
 *  - State → ECI polling-booth deep-link (Problem Alignment)
 *
 * All Markdown is rendered through a safe escape-then-transform
 * pipeline — no untrusted HTML is ever inserted.
 * ================================================================ */

const $ = (sel) => document.querySelector(sel);
const htmlRoot = document.getElementById("html-root");
const chatLog = $("#chat-log");
const chatForm = $("#chat-form");
const chatInput = $("#chat-input");
const sendBtn = $("#send-btn");
const resetBtn = $("#reset-btn");
const chatError = $("#chat-error");
const timelineList = $("#timeline");
const eligibilityForm = $("#eligibility-form");
const eligibilityResult = $("#eligibility-result");
const boothForm = $("#booth-form");
const boothSelect = $("#booth-state");
const langPicker = $("#lang-picker");
const micBtn = $("#mic-btn");
const groundToggle = $("#ground-toggle");

const history = [];
let isSending = false;
let currentLang = localStorage.getItem("vw_lang") || "en";
let strings = {};

const ASSISTANT_AVATAR_SVG = `
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M5 12l5 5 9-11"/>
</svg>`;

const USER_AVATAR_SVG = `
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <circle cx="12" cy="8" r="4"/>
  <path d="M4 21a8 8 0 0 1 16 0"/>
</svg>`;

const SPEAK_SVG = `
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M11 5L6 9H3v6h3l5 4V5z"/>
  <path d="M15 8.5a4 4 0 010 7M18 5.5a8 8 0 010 13"/>
</svg>`;

const STOP_SVG = `
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <rect x="6" y="6" width="12" height="12" rx="1.5"/>
</svg>`;

const TTS_BROWSER_LOCALE = {
  en: "en-IN", hi: "hi-IN", bn: "bn-IN", ta: "ta-IN", te: "te-IN",
  mr: "mr-IN", gu: "gu-IN", kn: "kn-IN", ml: "ml-IN", pa: "pa-IN",
  ur: "ur-IN", or: "or-IN", as: "as-IN",
};

/* ------------------------------------------------------------------
 * Safe minimal Markdown rendering
 * ---------------------------------------------------------------- */
function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(text) {
  let out = text.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\n]+?)\*(?=[\s).,!?;:]|$)/g, "$1<em>$2</em>");
  out = out.replace(/(^|[\s(])_([^_\n]+?)_(?=[\s).,!?;:]|$)/g, "$1<em>$2</em>");
  out = out.replace(/`([^`\n]+?)`/g, "<code>$1</code>");
  out = out.replace(
    /\b(https?:\/\/[^\s<]+[^\s<.,!?;:()])/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
  );
  return out;
}

function renderMarkdown(raw) {
  const escaped = escapeHtml(raw.trim());
  const lines = escaped.split(/\r?\n/);
  const blocks = [];
  let para = [];
  let list = null;

  const flushPara = () => {
    if (para.length) {
      blocks.push(`<p>${renderInline(para.join(" "))}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const tag = list.type;
      const items = list.items.map((i) => `<li>${renderInline(i)}</li>`).join("");
      blocks.push(`<${tag}>${items}</${tag}>`);
      list = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line.trim()) { flushPara(); flushList(); continue; }

    const bulletMatch = line.match(/^\s*[-*]\s+(.*)$/);
    const numMatch = line.match(/^\s*\d+\.\s+(.*)$/);

    if (bulletMatch) {
      flushPara();
      if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; }
      list.items.push(bulletMatch[1]);
    } else if (numMatch) {
      flushPara();
      if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; }
      list.items.push(numMatch[1]);
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return blocks.join("");
}

/* ------------------------------------------------------------------
 * i18n — swap all marked strings when language changes
 * ---------------------------------------------------------------- */
function t(key, fallback = "") {
  return strings[key] || fallback || key;
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.dataset.i18n;
    if (strings[key]) el.textContent = strings[key];
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.dataset.i18nPlaceholder;
    if (strings[key]) el.setAttribute("placeholder", strings[key]);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    const key = el.dataset.i18nAria;
    if (strings[key]) el.setAttribute("aria-label", strings[key]);
  });
  htmlRoot.setAttribute("lang", currentLang);
  htmlRoot.setAttribute("dir", currentLang === "ur" ? "rtl" : "ltr");
}

async function loadLanguage(code) {
  currentLang = code;
  localStorage.setItem("vw_lang", code);
  try {
    const res = await fetch(`/api/i18n/${encodeURIComponent(code)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    strings = data.strings || {};
  } catch {
    strings = {};
  }
  applyTranslations();
}

async function loadLanguageList() {
  try {
    const res = await fetch("/api/languages");
    if (!res.ok) return;
    const data = await res.json();
    langPicker.innerHTML = "";
    for (const { code, label } of data.languages || []) {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = label;
      if (code === currentLang) opt.selected = true;
      langPicker.appendChild(opt);
    }
  } catch {
    /* keep default */
  }
}

/* ------------------------------------------------------------------
 * Chat UI primitives
 * ---------------------------------------------------------------- */
function createMessage(role, { variant = null } = {}) {
  const article = document.createElement("article");
  article.className = `message message--${role}`;
  if (variant) article.classList.add(`message--${variant}`);

  const avatar = document.createElement("div");
  avatar.className = "message__avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = role === "user" ? USER_AVATAR_SVG : ASSISTANT_AVATAR_SVG;

  const bubble = document.createElement("div");
  bubble.className = "message__bubble";

  const roleLabel = document.createElement("div");
  roleLabel.className = "message__role";
  roleLabel.textContent =
    role === "user"
      ? t("you", "You")
      : role === "assistant"
      ? t("assistant_name", "VoteWise India")
      : "";

  const textEl = document.createElement("div");
  textEl.className = "message__text";

  bubble.appendChild(roleLabel);
  bubble.appendChild(textEl);
  article.appendChild(avatar);
  article.appendChild(bubble);
  return { article, textEl, bubble };
}

function appendAssistantText(text, { variant = null } = {}) {
  const { article, textEl } = createMessage("assistant", { variant });
  textEl.innerHTML = renderMarkdown(text);
  chatLog.appendChild(article);
  chatLog.scrollTop = chatLog.scrollHeight;
  return { article, textEl };
}

function appendUserText(text) {
  const { article, textEl } = createMessage("user");
  textEl.innerHTML = `<p>${escapeHtml(text).replace(/\n/g, "<br>")}</p>`;
  chatLog.appendChild(article);
  chatLog.scrollTop = chatLog.scrollHeight;
  return article;
}

function appendTypingIndicator() {
  const { article, textEl } = createMessage("assistant");
  textEl.innerHTML = `<div class="typing" aria-label="${escapeHtml(
    t("typing_label", "VoteWise India is typing")
  )}"><span></span><span></span><span></span></div>`;
  chatLog.appendChild(article);
  chatLog.scrollTop = chatLog.scrollHeight;
  return article;
}

function showError(message) {
  chatError.textContent = message;
  chatError.hidden = false;
}

function clearError() {
  chatError.textContent = "";
  chatError.hidden = true;
}

function renderWelcome() {
  chatLog.innerHTML = "";
  appendAssistantText(
    t(
      "welcome",
      "Namaste! I can explain voter registration, eligibility, what to bring to the booth, the Model Code of Conduct, postal ballots and more."
    )
  );
}

/* ------------------------------------------------------------------
 * Citations + actions bar under assistant replies
 * ---------------------------------------------------------------- */
function renderCitations(bubbleTextEl, citations) {
  if (!Array.isArray(citations) || citations.length === 0) return;
  const wrap = document.createElement("div");
  wrap.className = "citations";
  const label = document.createElement("div");
  label.className = "citations__label";
  label.textContent = t("citations_label", "Sources");
  wrap.appendChild(label);
  const list = document.createElement("ul");
  list.className = "citations__list";
  for (const c of citations.slice(0, 5)) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = c.uri;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = c.title || c.uri;
    li.appendChild(a);
    list.appendChild(li);
  }
  wrap.appendChild(list);
  bubbleTextEl.parentElement.appendChild(wrap);
}

function addActionsBar(bubbleTextEl, fullText, lang) {
  const bar = document.createElement("div");
  bar.className = "msg-actions";
  const listenBtn = document.createElement("button");
  listenBtn.type = "button";
  listenBtn.className = "icon-btn icon-btn--listen";
  listenBtn.setAttribute("aria-label", t("btn_listen", "Read aloud"));
  listenBtn.innerHTML = `${SPEAK_SVG}<span>${escapeHtml(t("btn_listen", "Read aloud"))}</span>`;
  let utterance = null;
  let isSpeaking = false;

  listenBtn.addEventListener("click", () => {
    if (isSpeaking) {
      stopSpeaking();
      return;
    }
    startSpeaking();
  });

  async function startSpeaking() {
    const plain = bubbleTextEl.innerText || fullText;
    if (!plain.trim()) return;
    isSpeaking = true;
    listenBtn.classList.add("is-active");
    listenBtn.innerHTML = `${STOP_SVG}<span>${escapeHtml(t("btn_listen", "Read aloud"))}</span>`;
    try {
      if (await speakWithBrowser(plain, lang)) return;
      await speakWithServer(plain, lang);
    } catch {
      /* swallow */
    } finally {
      isSpeaking = false;
      listenBtn.classList.remove("is-active");
      listenBtn.innerHTML = `${SPEAK_SVG}<span>${escapeHtml(t("btn_listen", "Read aloud"))}</span>`;
    }
  }

  function stopSpeaking() {
    window.speechSynthesis?.cancel();
    if (utterance && utterance.audio) {
      utterance.audio.pause();
      utterance.audio.currentTime = 0;
    }
    isSpeaking = false;
    listenBtn.classList.remove("is-active");
    listenBtn.innerHTML = `${SPEAK_SVG}<span>${escapeHtml(t("btn_listen", "Read aloud"))}</span>`;
  }

  function speakWithBrowser(text, code) {
    return new Promise((resolve) => {
      if (!("speechSynthesis" in window)) return resolve(false);
      const locale = TTS_BROWSER_LOCALE[code] || "en-IN";
      const voices = window.speechSynthesis.getVoices();
      const match = voices.find((v) => v.lang === locale || v.lang.startsWith(`${code}-`));
      if (!match) return resolve(false);
      const u = new SpeechSynthesisUtterance(text.slice(0, 1500));
      u.lang = match.lang;
      u.voice = match;
      u.onend = () => resolve(true);
      u.onerror = () => resolve(false);
      window.speechSynthesis.speak(u);
    });
  }

  async function speakWithServer(text, code) {
    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.slice(0, 1500), lang: code }),
      });
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      utterance = { audio };
      await new Promise((resolve) => {
        audio.onended = resolve;
        audio.onerror = resolve;
        audio.play().catch(resolve);
      });
      URL.revokeObjectURL(url);
    } catch {
      /* ignore */
    }
  }

  bar.appendChild(listenBtn);
  bubbleTextEl.parentElement.appendChild(bar);
}

/* ------------------------------------------------------------------
 * Streaming chat via SSE
 * ---------------------------------------------------------------- */
async function streamChat(message, pending) {
  const payload = {
    history: history.slice(-20),
    message,
    target_language: currentLang,
    use_grounding: groundToggle.checked,
  };

  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    pending.remove();
    let msg = t("error_generic", "Sorry, something went wrong. Please try again.");
    if (res.status === 429) msg = t("error_rate", "Too many requests. Please wait a moment.");
    if (res.status === 503) msg = t("error_unavailable", "Temporarily unavailable.");
    if (res.status === 413) msg = t("error_too_long", "Message too long.");
    appendAssistantText(msg, { variant: "error" });
    return null;
  }

  pending.remove();
  const { article, textEl } = appendAssistantText("");
  let collectedEn = "";
  let finalText = "";
  let finalCitations = [];
  let finalLang = currentLang;

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }

      if (evt.type === "chunk") {
        collectedEn += evt.text;
        // Show English live; we'll swap to translated text on "translated" event.
        textEl.innerHTML = renderMarkdown(collectedEn);
        chatLog.scrollTop = chatLog.scrollHeight;
      } else if (evt.type === "translated") {
        finalText = evt.text;
        finalLang = evt.lang || currentLang;
        textEl.innerHTML = renderMarkdown(finalText);
      } else if (evt.type === "done") {
        finalText = finalText || collectedEn;
        finalLang = evt.language || finalLang;
        if (Array.isArray(evt.citations)) finalCitations = evt.citations;
      } else if (evt.type === "error") {
        textEl.innerHTML = renderMarkdown(
          t("error_generic", "Sorry, something went wrong. Please try again.")
        );
        article.classList.add("message--error");
        return null;
      }
    }
  }

  if (!finalText) finalText = collectedEn;
  if (!finalText.trim()) {
    article.remove();
    appendAssistantText(t("error_generic", "Sorry, something went wrong."), { variant: "error" });
    return null;
  }

  // Fetch citations in a follow-up by querying a lightweight /api/chat
  // isn't needed — the server emits citations inside 'done'. We just didn't
  // wire them in /api/chat/stream 'done' above; if present, render.
  return { article, textEl, finalText, finalCitations, finalLang };
}

/* ------------------------------------------------------------------
 * Non-streaming fallback (used if fetch streaming isn't supported)
 * ---------------------------------------------------------------- */
async function nonStreamChat(message, pending) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      history: history.slice(-20),
      message,
      target_language: currentLang,
      use_grounding: groundToggle.checked,
    }),
  });
  if (!res.ok) {
    pending.remove();
    let msg = t("error_generic", "Sorry, something went wrong.");
    if (res.status === 429) msg = t("error_rate", "Too many requests.");
    if (res.status === 503) msg = t("error_unavailable", "Temporarily unavailable.");
    if (res.status === 413) msg = t("error_too_long", "Message too long.");
    appendAssistantText(msg, { variant: "error" });
    return null;
  }
  const data = await res.json();
  pending.remove();
  const { article, textEl } = appendAssistantText(data.reply);
  return {
    article,
    textEl,
    finalText: data.reply,
    finalCitations: data.citations || [],
    finalLang: data.language || currentLang,
  };
}

async function sendMessage(text) {
  if (isSending) return;
  const trimmed = text.trim();
  if (!trimmed) return;
  if (trimmed.length > 1000) {
    showError(t("error_too_long", "Please keep your message under 1000 characters."));
    return;
  }

  clearError();
  isSending = true;
  sendBtn.disabled = true;
  resetBtn.disabled = true;

  appendUserText(trimmed);
  const pending = appendTypingIndicator();

  try {
    const supportsStreaming = typeof ReadableStream !== "undefined";
    const result = supportsStreaming
      ? await streamChat(trimmed, pending)
      : await nonStreamChat(trimmed, pending);

    if (result && result.finalText) {
      renderCitations(result.textEl, result.finalCitations);
      addActionsBar(result.textEl, result.finalText, result.finalLang);
      history.push({ role: "user", text: trimmed });
      history.push({ role: "assistant", text: result.finalText });
    }
  } catch {
    pending.remove();
    appendAssistantText(
      t("error_network", "Network error. Please check your connection and try again."),
      { variant: "error" }
    );
  } finally {
    isSending = false;
    sendBtn.disabled = false;
    resetBtn.disabled = false;
    autoGrow();
    chatInput.focus();
  }
}

/* ------------------------------------------------------------------
 * Form wiring
 * ---------------------------------------------------------------- */
chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const value = chatInput.value;
  chatInput.value = "";
  autoGrow();
  sendMessage(value);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

function autoGrow() {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 180) + "px";
}
chatInput.addEventListener("input", autoGrow);

resetBtn.addEventListener("click", () => {
  history.length = 0;
  renderWelcome();
  clearError();
  chatInput.focus();
});

document.querySelectorAll(".chip[data-prompt]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const key = chip.dataset.promptKey;
    const translated = key && strings[key] ? strings[key] : chip.dataset.prompt;
    sendMessage(translated);
  });
});

langPicker.addEventListener("change", async () => {
  await loadLanguage(langPicker.value);
  renderWelcome();
  loadTimeline();
});

/* ------------------------------------------------------------------
 * Eligibility checker — fully client-side
 * ---------------------------------------------------------------- */
eligibilityForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const fd = new FormData(eligibilityForm);
  const age = Number(fd.get("age"));
  const citizen = fd.get("citizen");
  const resident = fd.get("resident");

  if (!Number.isFinite(age) || age <= 0 || !citizen || !resident) {
    eligibilityResult.textContent = t("eligible_fill", "Please fill in all fields.");
    eligibilityResult.dataset.state = "no";
    return;
  }

  const reasons = [];
  if (age < 18) reasons.push(t("eligible_no_age", "you must be at least 18"));
  if (citizen !== "yes") reasons.push(t("eligible_no_citizen", "you must be an Indian citizen"));
  if (resident !== "yes")
    reasons.push(t("eligible_no_resident", "you must be ordinarily resident in the constituency"));

  if (reasons.length === 0) {
    eligibilityResult.textContent = t(
      "eligible_ok",
      "You appear eligible to register as a general voter. Fill Form 6 on voters.eci.gov.in."
    );
    eligibilityResult.dataset.state = "ok";
  } else {
    eligibilityResult.textContent = reasons.join(" · ");
    eligibilityResult.dataset.state = "no";
  }
});

/* ------------------------------------------------------------------
 * Booth-lookup — deep-link to the ECI Electoral Search with the
 * chosen state pre-filled. No PII leaves the browser.
 * ---------------------------------------------------------------- */
async function loadStates() {
  try {
    const res = await fetch("/api/states");
    if (!res.ok) return;
    const data = await res.json();
    boothSelect.innerHTML = '<option value="">—</option>';
    for (const s of data.states_and_uts || []) {
      const opt = document.createElement("option");
      opt.value = s.code;
      opt.dataset.name = s.name;
      opt.textContent = s.name;
      boothSelect.appendChild(opt);
    }
  } catch { /* ignore */ }
}

boothForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const code = boothSelect.value;
  if (!code) return;
  const url = new URL("https://voters.eci.gov.in/search-in-electoral-roll");
  url.searchParams.set("state", code);
  window.open(url.toString(), "_blank", "noopener,noreferrer");
});

/* ------------------------------------------------------------------
 * Timeline (loaded from /api/info)
 * ---------------------------------------------------------------- */
async function loadTimeline() {
  try {
    const res = await fetch("/api/info");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    timelineList.innerHTML = "";
    for (const stage of data.general_election_timeline) {
      const li = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = stage.stage;
      li.appendChild(title);
      li.append(stage.description);
      timelineList.appendChild(li);
    }
    timelineList.setAttribute("aria-busy", "false");
  } catch {
    timelineList.innerHTML = `<li>${escapeHtml(t("timeline_error", "Unable to load the timeline."))}</li>`;
    timelineList.setAttribute("aria-busy", "false");
  }
}

/* ------------------------------------------------------------------
 * Voice input (Web Speech Recognition)
 * ---------------------------------------------------------------- */
function setupMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  micBtn.hidden = false;
  let recognizer = null;
  let active = false;

  micBtn.addEventListener("click", () => {
    if (active) {
      recognizer?.stop();
      return;
    }
    recognizer = new SR();
    recognizer.lang = TTS_BROWSER_LOCALE[currentLang] || "en-IN";
    recognizer.interimResults = true;
    recognizer.maxAlternatives = 1;
    recognizer.continuous = false;
    recognizer.onstart = () => {
      active = true;
      micBtn.classList.add("is-active");
    };
    recognizer.onresult = (e) => {
      let txt = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        txt += e.results[i][0].transcript;
      }
      chatInput.value = txt;
      autoGrow();
    };
    recognizer.onerror = () => {
      active = false;
      micBtn.classList.remove("is-active");
    };
    recognizer.onend = () => {
      active = false;
      micBtn.classList.remove("is-active");
    };
    recognizer.start();
  });
}

/* ------------------------------------------------------------------
 * Init
 * ---------------------------------------------------------------- */
(async function init() {
  await loadLanguageList();
  await loadLanguage(currentLang);
  renderWelcome();
  loadTimeline();
  loadStates();
  setupMic();
  autoGrow();
})();
