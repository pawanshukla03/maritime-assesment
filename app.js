const messagesEl = document.getElementById("messages");
const composerEl = document.getElementById("composer");
const messageInput = document.getElementById("messageInput");
const sendBtn = composerEl.querySelector(".composer-btn--send");
const newChatBtn = document.getElementById("newChatBtn");
const conversationListEl = document.getElementById("conversationList");
const addPdfBtn = document.getElementById("addPdfBtn");
const pdfUploadInput = document.getElementById("pdfUploadInput");
const clearChatBtn = document.getElementById("clearChatBtn");
const viewLogsBtn = document.getElementById("viewLogsBtn");
const chatAttachInput = document.getElementById("chatAttachInput");
const chatAttachBtn = document.getElementById("chatAttachBtn");
const composerAttachmentsEl = document.getElementById("composerAttachments");

const MAX_CHAT_ATTACHMENTS = 5;
const MAX_CHAT_ATTACHMENTS_BYTES = 25 * 1024 * 1024; // 25MB

const API_BASE = "http://127.0.0.1:8000";
const CLIENT_ERROR_STORAGE_KEY = "vessel_safety_last_client_error";
let toastTimeout = null;

function reportErrorToLogs(context, message) {
  const payload = JSON.stringify({ message, context });
  fetch(API_BASE + "/api/log-client-error", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
  }).catch(() => {
    try {
      localStorage.setItem(CLIENT_ERROR_STORAGE_KEY, JSON.stringify({ context, message, time: new Date().toISOString() }));
    } catch (e) {}
  });
}

function flushStoredErrorToLogs() {
  try {
    const raw = localStorage.getItem(CLIENT_ERROR_STORAGE_KEY);
    if (!raw) return;
    const { context, message } = JSON.parse(raw);
    localStorage.removeItem(CLIENT_ERROR_STORAGE_KEY);
    fetch(API_BASE + "/api/log-client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, context: (context || "unknown") + " (reported on next load)" }),
    }).catch(() => {
      localStorage.setItem(CLIENT_ERROR_STORAGE_KEY, raw);
    });
  } catch (e) {}
}

function showToast(message, type = "success") {
  const existing = document.getElementById("uploadToast");
  if (existing) existing.remove();
  clearTimeout(toastTimeout);
  const toast = document.createElement("div");
  toast.id = "uploadToast";
  toast.className = "toast toast--" + (type === "error" ? "error" : "success");
  toast.textContent = message;
  document.body.appendChild(toast);
  toastTimeout = setTimeout(() => {
    toast.remove();
  }, 5000);
}
const WELCOME_TEXT = "Ask me anything about vessel safety and standards for cargo and passenger vessels. I'll use the knowledge base to answer.";
const CHAT_STORAGE_KEY = "vessel_safety_chat_history";

// conversations: { id, title, messages: [{ role, content }], updatedAt }
let conversations = [];
let currentConversationId = null;

function loadConversationsFromStorage() {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return [];
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function saveConversationsToStorage() {
  try {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(conversations));
  } catch (e) {
    console.warn("Could not save chat history:", e);
  }
}

function nextId() {
  return "c-" + Date.now() + "-" + Math.random().toString(36).slice(2, 9);
}

function formatTime() {
  const now = new Date();
  return now.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function formatListTime(date) {
  const d = new Date(date);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return "Just now";
  if (diff < 86400000) return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
  if (diff < 172800000) return "Yesterday";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function getConversation(id) {
  const c = conversations.find((conv) => conv.id === id);
  if (c && !Array.isArray(c.attachments)) c.attachments = [];
  return c;
}

function getConversationTitle(conversation) {
  if (conversation.title && conversation.title.trim()) {
    const t = conversation.title.trim();
    return t.length > 35 ? t.slice(0, 35) + "…" : t;
  }
  const firstUser = conversation.messages.find((m) => m.role === "user");
  if (firstUser) {
    const text = (firstUser.content || "").trim();
    return text.length > 35 ? text.slice(0, 35) + "…" : text;
  }
  return "New chat";
}

function getPreview(conv) {
  if (!conv.messages.length) return "No messages yet";
  const t = (conv.messages[conv.messages.length - 1].content || "").trim();
  return t.slice(0, 40) + (t.length > 40 ? "…" : "") || "No messages yet";
}

function renderMarkdown(text) {
  if (!text || typeof text !== "string") return "";
  if (typeof marked === "undefined") return escapeHtml(text).replace(/\n/g, "<br>");
  const raw = marked.parse(text.trim(), { gfm: true, breaks: true });
  const div = document.createElement("div");
  div.innerHTML = raw;
  div.querySelectorAll("script, iframe, object, form").forEach((el) => el.remove());
  div.querySelectorAll("*").forEach((el) => {
    Array.from(el.attributes).forEach((attr) => {
      if (attr.name.startsWith("on")) el.removeAttribute(attr.name);
    });
  });
  return div.innerHTML;
}

function createMessageBubble(text, isSent, options = {}) {
  const { streamable = false } = options;
  const message = document.createElement("div");
  message.className = `message message--${isSent ? "sent" : "received"}`;

  if (!isSent) {
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = "M";
    message.appendChild(avatar);
  }

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const contentWrap = document.createElement("div");
  contentWrap.className = "message-content";
  if (streamable) {
    contentWrap.classList.add("message-stream");
    contentWrap.textContent = text || "";
  } else if (isSent) {
    contentWrap.textContent = text;
  } else {
    contentWrap.innerHTML = renderMarkdown(text || "");
  }
  const time = document.createElement("span");
  time.className = "message-time";
  time.textContent = options.skipTime ? "" : formatTime();
  bubble.appendChild(contentWrap);
  bubble.appendChild(time);
  message.appendChild(bubble);

  return { message, bubble, contentEl: contentWrap };
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setLoading(loading) {
  messageInput.disabled = loading;
  sendBtn.disabled = loading;
  if (chatAttachBtn) chatAttachBtn.disabled = loading;
}

function renderAttachmentChips() {
  if (!composerAttachmentsEl) return;
  const conv = currentConversationId ? getConversation(currentConversationId) : null;
  const list = conv && conv.attachments ? conv.attachments : [];
  composerAttachmentsEl.innerHTML = "";
  list.forEach((file, index) => {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.innerHTML = `
      <span class="attachment-chip-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
      <button type="button" class="attachment-chip-remove" aria-label="Remove ${escapeHtml(file.name)}" data-index="${index}">×</button>
    `;
    chip.querySelector(".attachment-chip-remove").addEventListener("click", () => {
      conv.attachments.splice(index, 1);
      renderAttachmentChips();
    });
    composerAttachmentsEl.appendChild(chip);
  });
}

function deleteConversation(id, e) {
  if (e) e.stopPropagation();
  const idx = conversations.findIndex((c) => c.id === id);
  if (idx === -1) return;
  conversations.splice(idx, 1);
  if (currentConversationId === id) {
    currentConversationId = conversations.length > 0 ? conversations[0].id : null;
    if (clearChatBtn) clearChatBtn.disabled = !currentConversationId;
    if (currentConversationId) {
      renderMessages(currentConversationId);
    } else {
      messagesEl.innerHTML = "";
    }
  }
  renderConversationList();
  saveConversationsToStorage();
}

function renderConversationList() {
  const search = (document.getElementById("searchConversations") || {}).value || "";
  const term = search.trim().toLowerCase();
  const list = term
    ? conversations.filter((c) => getConversationTitle(c).toLowerCase().includes(term))
    : [...conversations];
  list.sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));

  conversationListEl.innerHTML = "";
  list.forEach((conv) => {
    const row = document.createElement("div");
    row.className = "conversation-item" + (conv.id === currentConversationId ? " conversation-item--active" : "");
    row.dataset.conversationId = conv.id;
    const title = getConversationTitle(conv);
    row.innerHTML = `
      <div class="conversation-item-main" role="button" tabindex="0">
        <div class="conversation-avatar">${title.charAt(0).toUpperCase()}</div>
        <div class="conversation-info">
          <span class="conversation-name">${escapeHtml(title)}</span>
          <span class="conversation-preview">${escapeHtml(getPreview(conv))}</span>
        </div>
        <span class="conversation-time">${formatListTime(conv.updatedAt)}</span>
      </div>
      <button type="button" class="conversation-item-delete" aria-label="Delete chat" title="Delete chat">🗑</button>
    `;
    const main = row.querySelector(".conversation-item-main");
    const deleteBtn = row.querySelector(".conversation-item-delete");
    main.addEventListener("click", () => selectConversation(conv.id));
    main.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectConversation(conv.id); } });
    deleteBtn.addEventListener("click", (e) => deleteConversation(conv.id, e));
    conversationListEl.appendChild(row);
  });
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function renderMessages(conversationId) {
  messagesEl.innerHTML = "";
  const conv = getConversation(conversationId);
  if (!conv) return;

  if (conv.messages.length === 0) {
    const { message } = createMessageBubble(WELCOME_TEXT, false, { skipTime: true });
    messagesEl.appendChild(message);
  } else {
    conv.messages.forEach((m) => {
      const { message } = createMessageBubble(m.content, m.role === "user");
      messagesEl.appendChild(message);
    });
  }
  scrollToBottom();
}

function clearCurrentChatHistory() {
  if (!currentConversationId) return;
  const conv = getConversation(currentConversationId);
  if (!conv) return;
  conv.messages = [];
  conv.attachments = [];
  conv.updatedAt = new Date().toISOString();
  saveConversationsToStorage();
  renderAttachmentChips();
  renderMessages(currentConversationId);
  renderConversationList();
}

function selectConversation(id) {
  if (currentConversationId === id) return;
  currentConversationId = id;
  if (clearChatBtn) clearChatBtn.disabled = false;
  renderConversationList();
  renderMessages(id);
  renderAttachmentChips();
  messageInput.focus();
}

function createNewChatWithName(name) {
  const title = (name && name.trim()) ? name.trim() : "New chat";
  const id = nextId();
  const conv = {
    id,
    title,
    messages: [],
    attachments: [],
    updatedAt: new Date().toISOString(),
  };
  conversations.unshift(conv);
  currentConversationId = id;
  if (clearChatBtn) clearChatBtn.disabled = false;
  renderConversationList();
  renderMessages(id);
  renderAttachmentChips();
  messageInput.focus();
  saveConversationsToStorage();
}

function createNewChat() {
  const modal = document.getElementById("newChatModal");
  const input = document.getElementById("newChatNameInput");
  if (modal && input) {
    input.value = "";
    modal.removeAttribute("hidden");
    input.focus();
  } else {
    createNewChatWithName("");
  }
}

async function sendMessage(text) {
  const trimmed = (text || "").trim();

  if (!currentConversationId) {
    createNewChat();
    return;
  }

  const conv = getConversation(currentConversationId);
  if (!conv) return;

  const hasAttachments = conv.attachments && conv.attachments.length > 0;
  if (!trimmed && !hasAttachments) return;

  const displayText = trimmed || (hasAttachments ? "(attached file(s))" : "");

  const { message: userMsgEl } = createMessageBubble(displayText, true);
  messagesEl.appendChild(userMsgEl);
  conv.messages.push({ role: "user", content: trimmed || "(User sent attachment(s).)" });
  conv.updatedAt = new Date().toISOString();
  saveConversationsToStorage();
  messageInput.value = "";
  scrollToBottom();
  setLoading(true);

  const historyForApi = conv.messages.slice(0, -1);

  const { message: assistantMsgEl, contentEl: assistantContentEl } = createMessageBubble("", false, { streamable: true, skipTime: true });
  messagesEl.appendChild(assistantMsgEl);
  assistantContentEl.textContent = "…";
  scrollToBottom();

  try {
    let response;
    if (hasAttachments) {
      const formData = new FormData();
      formData.append("message", trimmed || "");
      formData.append("history", JSON.stringify(historyForApi));
      for (let i = 0; i < conv.attachments.length; i++) {
        formData.append("files", conv.attachments[i]);
      }
      response = await fetch(`${API_BASE}/chat-with-attachments`, {
        method: "POST",
        body: formData,
      });
      conv.attachments = [];
      renderAttachmentChips();
    } else {
      response = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed, history: historyForApi }),
      });
    }

    if (!response.ok) {
      const errText = `Error: ${response.status} ${response.statusText}. Check the Logs button in the header for details.`;
      assistantContentEl.textContent = errText;
      reportErrorToLogs("chat", response.status + " " + response.statusText);
      scrollToBottom();
      setLoading(false);
      renderConversationList();
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let full = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      full += chunk;
      assistantContentEl.textContent = full;
      scrollToBottom();
    }

    if (full) {
      const isErrorFromBackend = full.trim().toLowerCase().startsWith("error:");
      assistantContentEl.innerHTML = renderMarkdown(full)
        + (isErrorFromBackend ? '<p class="message-log-hint">Check the <strong>Logs</strong> button in the header for full error details.</p>' : "");
      conv.messages.push({ role: "assistant", content: full });
    }
    conv.updatedAt = new Date().toISOString();
    const timeSpan = assistantMsgEl.querySelector(".message-time");
    if (timeSpan) timeSpan.textContent = formatTime();
    renderConversationList();
    saveConversationsToStorage();
  } catch (err) {
    const msg = err.message || "Unknown error";
    const isFetchFailed = /failed to fetch|network error|load failed/i.test(msg);
    const hint = isFetchFailed
      ? " Is the backend running at " + API_BASE + "? Start it with Start.bat, then try again. Check the Logs button for server details."
      : " Check the Logs button in the header for server-side details.";
    assistantContentEl.textContent = "Error: " + msg + hint;
    scrollToBottom();
    renderConversationList();
    reportErrorToLogs("chat", msg);
  }

  setLoading(false);
  messageInput.focus();
}

newChatBtn.addEventListener("click", createNewChat);

const newChatModal = document.getElementById("newChatModal");
const newChatNameForm = document.getElementById("newChatNameForm");
const newChatNameInput = document.getElementById("newChatNameInput");
const closeNewChatModal = document.getElementById("closeNewChatModal");
const cancelNewChatBtn = document.getElementById("cancelNewChatBtn");

function closeNewChatModalFn() {
  if (newChatModal) newChatModal.setAttribute("hidden", "");
}

if (newChatNameForm) {
  newChatNameForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const name = newChatNameInput ? newChatNameInput.value.trim() : "";
    closeNewChatModalFn();
    createNewChatWithName(name || "New chat");
  });
}
if (closeNewChatModal) closeNewChatModal.addEventListener("click", closeNewChatModalFn);
if (cancelNewChatBtn) cancelNewChatBtn.addEventListener("click", closeNewChatModalFn);
if (newChatModal) {
  newChatModal.addEventListener("click", (e) => {
    if (e.target === newChatModal) closeNewChatModalFn();
  });
}

document.getElementById("searchConversations").addEventListener("input", () => {
  renderConversationList();
});

composerEl.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(messageInput.value);
});

if (chatAttachBtn && chatAttachInput) {
  chatAttachBtn.addEventListener("click", () => chatAttachInput.click());
  chatAttachInput.addEventListener("change", () => {
    const files = chatAttachInput.files;
    if (!files || files.length === 0) return;
    if (!currentConversationId) {
      createNewChat();
      chatAttachInput.value = "";
      return;
    }
    const conv = getConversation(currentConversationId);
    if (!conv) return;
    let totalBytes = (conv.attachments || []).reduce((sum, f) => sum + (f.size || 0), 0);
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (conv.attachments.length >= MAX_CHAT_ATTACHMENTS) break;
      if (totalBytes + (file.size || 0) > MAX_CHAT_ATTACHMENTS_BYTES) break;
      const isPdf = (file.name || "").toLowerCase().endsWith(".pdf") || (file.type || "").toLowerCase().includes("pdf");
      const isImage = (file.type || "").startsWith("image/");
      if (isPdf || isImage) {
        conv.attachments.push(file);
        totalBytes += file.size || 0;
      }
    }
    renderAttachmentChips();
    chatAttachInput.value = "";
  });
}

if (clearChatBtn) {
  clearChatBtn.addEventListener("click", () => {
    clearCurrentChatHistory();
  });
}

if (viewLogsBtn) {
  viewLogsBtn.addEventListener("click", () => {
    window.open(API_BASE + "/api/logs/download", "_blank", "noopener");
  });
}

if (addPdfBtn && pdfUploadInput) {
  addPdfBtn.addEventListener("click", () => pdfUploadInput.click());
  pdfUploadInput.addEventListener("change", async () => {
    const files = pdfUploadInput.files;
    if (!files || files.length === 0) return;
    addPdfBtn.disabled = true;
    addPdfBtn.innerHTML = '<span class="header-btn-icon">⏳</span><span>Uploading & re-indexing…</span>';
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append("files", files[i]);
    }
    try {
      const res = await fetch(`${API_BASE}/api/upload-pdf`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok) {
        showToast(data.message || "PDF(s) added successfully.");
      } else {
        showToast(data.error || "Upload failed.", "error");
      }
    } catch (err) {
      showToast("Upload failed: " + (err.message || "network error"), "error");
    }
    addPdfBtn.disabled = false;
    addPdfBtn.innerHTML = '<span class="header-btn-icon">📄</span><span>Add Knowledge</span>';
    pdfUploadInput.value = "";
  });
}

messageInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage(messageInput.value);
  }
});

// Report any previously stored client error to backend logs (e.g. "Failed to fetch" when backend was down)
flushStoredErrorToLogs();

// Load saved history or start with new chat
(function initChatHistory() {
  const saved = loadConversationsFromStorage();
  if (saved.length > 0) {
    conversations = saved;
    conversations.sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
    currentConversationId = conversations[0].id;
    if (clearChatBtn) clearChatBtn.disabled = false;
    renderConversationList();
    renderMessages(currentConversationId);
    renderAttachmentChips();
  } else {
    currentConversationId = null;
    if (clearChatBtn) clearChatBtn.disabled = true;
    createNewChat();
  }
})();
