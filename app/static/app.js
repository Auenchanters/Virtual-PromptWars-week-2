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

/* ---------- Chat ---------- */

function appendMessage(role, text, { variant = null } = {}) {
  const article = document.createElement("article");
  article.className = `message message--${role}`;
  if (variant) article.classList.add(`message--${variant}`);

  const roleEl = document.createElement("div");
  roleEl.className = "message__role";
  roleEl.textContent = role === "user" ? "You" : role === "assistant" ? "Assistant" : "Notice";

  const textEl = document.createElement("div");
  textEl.className = "message__text";
  textEl.textContent = text;

  article.appendChild(roleEl);
  article.appendChild(textEl);
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

  appendMessage("user", trimmed);
  const pending = appendMessage("assistant", "Thinking", { variant: "pending" });

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
      appendMessage("assistant", msg, { variant: "error" });
      return;
    }

    const data = await res.json();
    pending.remove();
    appendMessage("assistant", data.reply);

    history.push({ role: "user", text: trimmed });
    history.push({ role: "assistant", text: data.reply });
  } catch (err) {
    pending.remove();
    appendMessage("assistant", "Network error. Please check your connection and try again.", {
      variant: "error",
    });
  } finally {
    isSending = false;
    sendBtn.disabled = false;
    resetBtn.disabled = false;
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const value = chatInput.value;
  chatInput.value = "";
  sendMessage(value);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

resetBtn.addEventListener("click", () => {
  history.length = 0;
  chatLog.innerHTML = "";
  appendMessage(
    "assistant",
    "Conversation cleared. Ask anything about voter registration, eligibility, polling day, or the election timeline.",
  );
  clearError();
  chatInput.focus();
});

document.querySelectorAll(".chip[data-prompt]").forEach((chip) => {
  chip.addEventListener("click", () => {
    sendMessage(chip.dataset.prompt);
  });
});

/* ---------- Eligibility checker ---------- */

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
    eligibilityResult.textContent =
      "Not eligible yet because " + reasons.join(", ") + ".";
    eligibilityResult.dataset.state = "no";
  }
});

/* ---------- Timeline ---------- */

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

loadTimeline();
