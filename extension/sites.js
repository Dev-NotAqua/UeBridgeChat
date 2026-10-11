// How to find the message box, the send button and the "still answering" signal on each chat site.
// Every entry is only a hint: when a selector matches nothing, generic detection takes over, and the
// popup lets the user override selectors per site without editing this file.

const UECB_SITES = [
  { id: "ChatGPT", host: /(^|\.)(chatgpt\.com|chat\.openai\.com)$/, input: "#prompt-textarea", send: 'button[data-testid="send-button"], #composer-submit-button', stop: 'button[data-testid="stop-button"]' },
  { id: "Gemini", host: /gemini\.google\.com$/, input: 'rich-textarea .ql-editor, .ql-editor[contenteditable="true"]', send: 'button.send-button, button[aria-label*="Send"]', stop: 'button.stop, button[aria-label*="Stop"]' },
  { id: "Claude", host: /(^|\.)claude\.ai$/, input: 'div.ProseMirror[contenteditable="true"]', send: 'button[aria-label="Send message"], button[aria-label*="Send"]', stop: 'button[aria-label="Stop response"]' },
  { id: "DeepSeek", host: /(^|\.)deepseek\.com$/, input: "textarea#chat-input, textarea" },
  { id: "Qwen", host: /chat\.qwen\.ai$/, input: "textarea#chat-input, textarea" },
  { id: "Kimi", host: /kimi\.(ai|com)$/, input: '[contenteditable="true"]' },
  { id: "GLM", host: /chat\.z\.ai$/, input: "textarea#chat-input, textarea" },
  { id: "Meta AI", host: /meta\.ai$/, input: '[contenteditable="true"], textarea' },
  { id: "Copilot", host: /copilot\.microsoft\.com$/, input: "textarea#userInput, textarea" },
  { id: "Grok", host: /(^|\.)grok\.com$/, input: 'textarea, [contenteditable="true"]' },
  { id: "Mistral", host: /chat\.mistral\.ai$/, input: 'textarea, [contenteditable="true"]' },
  { id: "LMArena", host: /lmarena\.ai$/, input: "textarea" },
  { id: "Arena", host: /(^|\.)arena\.ai$/, input: "textarea" },
  { id: "Perplexity", host: /perplexity\.ai$/, input: 'textarea, [contenteditable="true"]' },
  { id: "Poe", host: /(^|\.)poe\.com$/, input: "textarea" },
  { id: "HuggingChat", host: /huggingface\.co$/, input: "textarea" },
];

const UECB_SEND_WORDS = /send|submit|invia|enviar|envoyer|senden|发送|傳送|送信/i;
const UECB_STOP_WORDS = /stop|interrupt|ferma|arrêter|detener|停止/i;

function uecbVisible(el) {
  if (!el || !el.isConnected) return false;
  const box = el.getBoundingClientRect();
  return box.width > 4 && box.height > 4 && getComputedStyle(el).visibility !== "hidden";
}

function uecbFirstVisible(selector, root) {
  if (!selector) return null;
  try {
    return [...(root || document).querySelectorAll(selector)].find(uecbVisible) || null;
  } catch (err) {
    return null; // a bad custom selector must not break the page
  }
}

// The site adapter. `overrides` are the user's custom selectors for this host, if any.
function uecbAdapter(overrides) {
  const site = UECB_SITES.find((s) => s.host.test(location.hostname)) || { id: location.hostname };
  const hints = Object.assign({}, site, overrides || {});

  function findInput() {
    const hinted = uecbFirstVisible(hints.input);
    if (hinted) return hinted;
    // generic: the largest visible text box in the lower half of the window
    const boxes = [...document.querySelectorAll('textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"]')]
      .filter((el) => uecbVisible(el) && !el.closest("#uecb-host"));
    const low = boxes.filter((el) => el.getBoundingClientRect().bottom > innerHeight * 0.4);
    const area = (el) => { const b = el.getBoundingClientRect(); return b.width * b.height; };
    return (low.length ? low : boxes).sort((a, b) => area(b) - area(a))[0] || null;
  }

  function readInput(el) {
    return (el.value !== undefined ? el.value : el.innerText) || "";
  }

  function setInput(el, text) {
    el.focus();
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      // React tracks the value through the native setter, so set it there and announce the change
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, text);
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      return readInput(el).length > 0;
    }
    const range = document.createRange();
    range.selectNodeContents(el);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    // rich editors (ProseMirror, Quill, Lexical) take text through a paste event
    const data = new DataTransfer();
    data.setData("text/plain", text);
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    if (readInput(el).trim().length < Math.min(text.trim().length, 20)) {
      document.execCommand("insertText", false, text);
    }
    return readInput(el).trim().length > 0;
  }

  function findSend(input) {
    const hinted = uecbFirstVisible(hints.send);
    if (hinted) return hinted;
    let scope = input;
    for (let depth = 0; depth < 7 && scope && scope !== document.body; depth++) {
      scope = scope.parentElement;
      const buttons = [...scope.querySelectorAll('button, [role="button"]')].filter(uecbVisible);
      const named = buttons.find((b) => UECB_SEND_WORDS.test(`${b.getAttribute("aria-label") || ""} ${b.title || ""} ${b.getAttribute("data-testid") || ""} ${b.textContent.trim().slice(0, 20)}`));
      if (named) return named;
      const submit = buttons.find((b) => b.type === "submit");
      if (submit) return submit;
    }
    return null;
  }

  function pressEnter(el) {
    for (const type of ["keydown", "keypress", "keyup"]) {
      el.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    }
  }

  // Click send (or press Enter) and report whether the message left the box.
  async function send(input) {
    const before = readInput(input).trim();
    const sent = () => !input.isConnected || readInput(input).trim().length < Math.max(1, before.length / 2);
    for (let attempt = 0; attempt < 24; attempt++) {
      const button = findSend(input);
      if (button && !button.disabled && button.getAttribute("aria-disabled") !== "true") button.click();
      else if (attempt % 2 === 1 || !button) pressEnter(input);
      await new Promise((r) => setTimeout(r, 500));
      if (sent()) return true;
    }
    return false;
  }

  // true / false when the site shows a stop button, null when this site gives no such signal
  function isStreaming() {
    if (hints.stop) return !!uecbFirstVisible(hints.stop);
    const input = findInput();
    let scope = input;
    for (let depth = 0; depth < 6 && scope && scope !== document.body; depth++) {
      scope = scope.parentElement;
      const stop = [...scope.querySelectorAll("button")].find((b) => uecbVisible(b) && UECB_STOP_WORDS.test(`${b.getAttribute("aria-label") || ""} ${b.title || ""}`));
      if (stop) return true;
    }
    return null;
  }

  // Hand an image to the message box the way a user pastes one. Sites without image input ignore it.
  async function attachImage(input, dataUrl) {
    const blob = await (await fetch(dataUrl)).blob();
    const data = new DataTransfer();
    data.items.add(new File([blob], "unreal-viewport.png", { type: blob.type || "image/png" }));
    input.focus();
    input.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  }

  return { name: site.id, findInput, readInput, setInput, send, isStreaming, attachImage };
}
