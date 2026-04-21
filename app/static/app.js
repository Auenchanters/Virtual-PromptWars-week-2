"use strict";

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const resetBtn = document.getElementById("reset-btn");
const chatError = document.getElementById("chat-error");
const timelineList = document.getElementById("timeline");
const eligibilityForm = document.getElementById("eligibility-form");
const eligibilityResult = document.getElementById("eligibility-result");

/** @type {{role: "user" | "assistant", text: string}[]} */
const history = [];
let isSending = false;

const WELCOME_TEXT =
  "Namaste! I can explain voter registration, eligibility, what to bring to the booth, the Model Code of Conduct, postal ballots and more. Try a quick question below, or type your own.";

const ASSISTANT_AVATAR_SVG = `
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M5 12l5 5 9-11"/>
</svg>`;

const USER_AVATAR_SVG = `
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <circle cx="12" cy="8" r="4"/>
  <path d="M4 21a8 8 0 0 1 16 0"/>
</svg>`;

/* ------------------------------------------------------------------
 * Safe minimal Markdown rendering
 *  Escapes all HTML, then applies:
 *    - **bold**
 *    - *italic* / _italic_
 *    - `code`
 *    - bullet lists starting with "* " or "- "
 *    - numbered lists starting with "1. "
 *    - paragraphs (blank-line separated)
 *    - bare URLs become safe links (http/https only)
 * Never inserts untrusted HTML.
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
  // bold **..**
  let out = text.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
  // italic *..* or _.._
  out = out.replace(/(^|[\s(])\*([^*\n]+?)\*(?=[\s).,!?;:]|$)/g, "$1<em>$2</em>");
  out = out.replace(/(^|[\s(])_([^_\n]+?)_(?=[\s).,!?;:]|$)/g, "$1<em>$2</em>");
  // inline code
  out = out.replace(/`([^`\n]+?)`/g, "<code>$1</code>");
  // bare http(s) URLs → safe links
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
  let list = null; // {type: 'ul'|'ol', items: []}

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
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }

    const bulletMatch = line.match(/^\s*[-*]\s+(.*)$/);
    const numMatch = line.match(/^\s*\d+\.\s+(.*)$/);

    if (bulletMatch) {
      flushPara();
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(bulletMatch[1]);
    } else if (numMatch) {
      flushPara();
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
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
 * Chat UI
 * ---------------------------------------------------------------- */
function createMessage(role, options = {}) {
  const article = document.createElement("article");
  article.className = `message message--${role}`;
  if (options.variant) article.classList.add(`message--${options.variant}`);

  const avatar = document.createElement("div");
  avatar.className = "message__avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.innerHTML = role === "user" ? USER_AVATAR_SVG : ASSISTANT_AVATAR_SVG;

  const bubble = document.createElement("div");
  bubble.className = "message__bubble";

  const roleLabel = document.createElement("div");
  roleLabel.className = "message__role";
  roleLabel.textContent =
    role === "user" ? "You" : role === "assistant" ? "VoteWise India" : "Notice";

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
  return article;
}

function appendUserText(text) {
  const { article, textEl } = createMessage("user");
  // User input: show as plain escaped text with preserved newlines
  textEl.innerHTML = `<p>${escapeHtml(text).replace(/\n/g, "<br>")}</p>`;
  chatLog.appendChild(article);
  chatLog.scrollTop = chatLog.scrollHeight;
  return article;
}

function appendTypingIndicator() {
  const { article, textEl } = createMessage("assistant");
  textEl.innerHTML = `<div class="typing" aria-label="VoteWise India is typing"><span></span><span></span><span></span></div>`;
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
  appendAssistantText(WELCOME_TEXT);
}

async function sendMessage(text) {
  if (isSending) return;
  const trimmed = text.trim();
  if (!trimmed) return;
  if (trimmed.length > 1000) {
    showError("Please keep your message under 1000 characters.");
    return;
  }

  clearError();
  isSending = true;
  sendBtn.disabled = true;
  resetBtn.disabled = true;

  appendUserText(trimmed);
  const pending = appendTypingIndicator();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: history.slice(-20), message: trimmed }),
    });

    if (!res.ok) {
      let msg = "Sorry, something went wrong. Please try again.";
      if (res.status === 429) msg = "Too many requests. Please wait a moment and try again.";
      else if (res.status === 503) msg = "The assistant is temporarily unavailable. Please try again shortly.";
      pending.remove();
      appendAssistantText(msg, { variant: "error" });
      return;
    }

    const data = await res.json();
    pending.remove();
    appendAssistantText(data.reply);

    history.push({ role: "user", text: trimmed });
    history.push({ role: "assistant", text: data.reply });
  } catch (err) {
    pending.remove();
    appendAssistantText(
      "Network error. Please check your connection and try again.",
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
    sendMessage(chip.dataset.prompt);
  });
});

/* ------------------------------------------------------------------
 * Eligibility checker
 * ---------------------------------------------------------------- */
eligibilityForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const fd = new FormData(eligibilityForm);
  const age = Number(fd.get("age"));
  const citizen = fd.get("citizen");
  const resident = fd.get("resident");

  if (!Number.isFinite(age) || age <= 0 || !citizen || !resident) {
    eligibilityResult.textContent = "Please fill in all fields.";
    eligibilityResult.dataset.state = "no";
    return;
  }

  const reasons = [];
  if (age < 18) reasons.push(`you must be at least 18 (you entered ${age})`);
  if (citizen !== "yes") reasons.push("you must be an Indian citizen");
  if (resident !== "yes") reasons.push("you must be ordinarily resident in the constituency");

  if (reasons.length === 0) {
    eligibilityResult.textContent =
      "You appear eligible to register as a general voter. Fill Form 6 on voters.eci.gov.in.";
    eligibilityResult.dataset.state = "ok";
  } else {
    eligibilityResult.textContent = "Not eligible yet because " + reasons.join(", ") + ".";
    eligibilityResult.dataset.state = "no";
  }
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
  } catch (err) {
    timelineList.innerHTML = "<li>Unable to load the timeline. Please refresh the page.</li>";
    timelineList.setAttribute("aria-busy", "false");
  }
}

/* ------------------------------------------------------------------
 * Init
 * ---------------------------------------------------------------- */
renderWelcome();
loadTimeline();
autoGrow();
