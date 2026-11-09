// PAIN Web Client
// Single-page responsive interface for Personal AI Network

const API = "/api";

let activeConversation = null;
let activePage = "chat";
let isGenerating = false;
let abortController = null;
let systemTimer = null;
let activeToolActivities = new Map();
let previousModel = "";

// Tracks the ongoing assistant response turn during live streaming
let currentTurn = {
    messageEl: null,
    bodyEl: null,
    toolsWrapperEl: null,
    preToolEl: null,
    preToolText: "",
    postToolEl: null,
    postToolText: "",
    hasTools: false
};

const $ = (id) => document.getElementById(id);


// --------------------------------------------------
// UTILITIES & ESCAPING
// --------------------------------------------------

function escapeHtml(str) {
    if (typeof str !== "string") str = String(str ?? "");
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function formatBytes(bytes) {
    if (bytes === null || bytes === undefined || isNaN(bytes)) {
        return "Unavailable";
    }
    if (bytes === 0) return "0 B";

    const units = ["B", "KB", "MB", "GB", "TB"];
    const sign = bytes < 0 ? "-" : "";
    let value = Math.abs(bytes);
    let index = 0;

    while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
    }

    return `${sign}${value.toFixed(2)} ${units[index]}`;
}

function updateConnectionStatus(status) {
    const statusText = $("connection-status");
    const statusDot = document.querySelector(".status-dot");
    if (!statusText || !statusDot) return;

    statusDot.classList.remove("connected", "disconnected", "connecting");

    if (status === "connected") {
        statusDot.classList.add("connected");
        statusText.textContent = "Connected";
    } else if (status === "connecting") {
        statusDot.classList.add("connecting");
        statusText.textContent = "Connecting...";
    } else {
        statusDot.classList.add("disconnected");
        statusText.textContent = "Disconnected";
    }
}


// --------------------------------------------------
// API HELPER
// --------------------------------------------------

async function api(path, options = {}) {
    try {
        const response = await fetch(API + path, {
            headers: {
                "Content-Type": "application/json",
                ...options.headers
            },
            ...options
        });

        if (!response.ok) {
            let message = `HTTP ${response.status}`;
            try {
                const error = await response.json();
                message = error.detail || message;
            } catch {}
            throw new Error(message);
        }

        updateConnectionStatus("connected");
        return response.json();
    } catch (err) {
        if (!options.signal?.aborted) {
            if (err.name === "TypeError" && err.message.includes("fetch")) {
                updateConnectionStatus("disconnected");
            }
        }
        throw err;
    }
}


// --------------------------------------------------
// MARKDOWN & SYNTAX HIGHLIGHTING ENGINE
// --------------------------------------------------

function setupMarkdownParser() {
    if (!window.marked) return;

    const renderer = new marked.Renderer();

    // 1. Code blocks: Syntax highlighting + language badge + copy button
    // Handles both marked v12+ token object { text, lang, escaped } and older positional (code, language)
    renderer.code = function(codeArg, languageArg) {
        let code = '';
        let language = '';

        if (typeof codeArg === 'object' && codeArg !== null) {
            code = codeArg.text || '';
            language = codeArg.lang || '';
        } else {
            code = String(codeArg || '');
            language = String(languageArg || '');
        }

        const lang = (language || '').trim().toLowerCase();
        let highlighted;

        if (window.hljs && lang && hljs.getLanguage(lang)) {
            try {
                highlighted = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
            } catch (e) {
                highlighted = escapeHtml(code);
            }
        } else if (window.hljs && code.length < 40000) {
            try {
                const auto = hljs.highlightAuto(code);
                highlighted = auto.value;
            } catch (e) {
                highlighted = escapeHtml(code);
            }
        } else {
            highlighted = escapeHtml(code);
        }

        const encodedCode = encodeURIComponent(code);
        const displayLang = lang || 'code';

        return `
            <div class="code-block">
                <div class="code-header">
                    <div class="code-lang-tag">
                        <svg class="code-tag-icon" viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2.2" fill="none"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
                        <span class="code-lang-name">${escapeHtml(displayLang)}</span>
                    </div>
                    <button class="copy-code-btn" type="button" data-code="${encodedCode}" title="Copy code snippet">
                        <svg class="copy-icon" viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        <span class="copy-text">Copy</span>
                    </button>
                </div>
                <pre class="code-content"><code class="hljs ${escapeHtml(displayLang)}">${highlighted}</code></pre>
            </div>
        `;
    };

    // 2. Hyperlinks: [text](url) -> Clickable, safe, external icon
    // Handles both marked v12+ token object { href, title, tokens } and positional (href, title, text)
    renderer.link = function(hrefArg, titleArg, textArg) {
        let href = '';
        let title = '';
        let text = '';

        if (typeof hrefArg === 'object' && hrefArg !== null) {
            href = hrefArg.href || '';
            title = hrefArg.title || '';
            text = (this.parser && hrefArg.tokens)
                ? this.parser.parseInline(hrefArg.tokens)
                : (hrefArg.text || hrefArg.raw || '');
        } else {
            href = String(hrefArg || '');
            title = String(titleArg || '');
            text = String(textArg || '');
        }

        const cleanHref = href.trim();
        const isSafe = /^(https?:\/\/|mailto:|\/|#)/i.test(cleanHref);
        const safeHref = isSafe ? cleanHref : '#';
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';

        return `<a href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer"${titleAttr} class="markdown-link">${text}<svg class="external-icon" viewBox="0 0 24 24" width="10" height="10" stroke="currentColor" stroke-width="2.2" fill="none"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg></a>`;
    };

    // 3. Inline codespan: handles both token object and string
    renderer.codespan = function(codeArg) {
        const text = (typeof codeArg === 'object' && codeArg !== null) ? (codeArg.text || '') : String(codeArg || '');
        return `<code class="inline-code">${escapeHtml(text)}</code>`;
    };

    // 4. Tables: Responsive wrapper
    // Handles marked v12+ token object vs positional (header, body)
    renderer.table = function(headerArg, bodyArg) {
        if (typeof headerArg === 'object' && headerArg !== null && !bodyArg) {
            const defaultTableHtml = marked.Renderer.prototype.table.call(this, headerArg);
            return `<div class="table-wrapper">${defaultTableHtml}</div>`;
        }
        return `<div class="table-wrapper"><table class="markdown-table"><thead>${headerArg}</thead><tbody>${bodyArg}</tbody></table></div>`;
    };

    marked.setOptions({
        renderer: renderer,
        gfm: true,
        breaks: true
    });
}

// Initialize on script execution
setupMarkdownParser();

function renderMathExpression(expr, displayMode = false) {
    if (window.katex && typeof window.katex.renderToString === "function") {
        try {
            return window.katex.renderToString(expr, {
                displayMode: displayMode,
                throwOnError: true,
                strict: false
            });
        } catch (e) {
            // Parse error or invalid LaTeX expression - fallback to styled placeholder
        }
    }
    const safe = escapeHtml(expr);
    return displayMode
        ? `<div class="katex-display"><code class="math-placeholder">$$${safe}$$</code></div>`
        : `<code class="math-placeholder">$${safe}$</code>`;
}

function extractMathAndPlaceholders(text) {
    if (!text) return { text: "", blocks: [], inlines: [] };

    const blocks = [];
    const inlines = [];
    const codes = [];

    // 1. Temporarily shield multi-line and inline code blocks so backticks and internal $ are preserved
    let shielded = text.replace(/```[\s\S]*?```/g, (m) => {
        codes.push(m);
        return `%%%CODE_PLACEHOLDER_${codes.length - 1}%%%`;
    }).replace(/`[^`\n]+`/g, (m) => {
        codes.push(m);
        return `%%%CODE_PLACEHOLDER_${codes.length - 1}%%%`;
    });

    // 2. Extract display/block math: $$...$$
    shielded = shielded.replace(/\$\$([\s\S]+?)\$\$/g, (_, expr) => {
        blocks.push(expr.trim());
        return `%%%MATH_BLOCK_${blocks.length - 1}%%%`;
    });

    // 3. Extract inline math / placeholders: $...$
    // Exclude currency ranges like "$10 to $20" or "$50 and $100"
    shielded = shielded.replace(/(?<!\\)\$([^\$\n]+?)(?<!\\)\$/g, (m, expr) => {
        const trimmed = expr.trim();
        if (!trimmed) return m;

        // Skip currency descriptions like "$10 to" or "$50 and"
        if (/^\d+(\.\d+)?\s*(?:and|or|to|,|-)\s*$/i.test(trimmed)) {
            return m;
        }

        inlines.push(trimmed);
        return `%%%MATH_INLINE_${inlines.length - 1}%%%`;
    });

    // 4. Restore shielded code blocks
    shielded = shielded.replace(/%%%CODE_PLACEHOLDER_(\d+)%%%/g, (_, idx) => codes[Number(idx)]);

    return { text: shielded, blocks, inlines };
}

function prepareStreamingMarkdown(text) {
    if (!text) return "";

    // Count triple backtick occurrences
    const fenceMatches = text.match(/```/g);
    const fenceCount = fenceMatches ? fenceMatches.length : 0;

    // If unclosed code fence at stream end, append closing fence temporarily
    let prepared = text;
    if (fenceCount % 2 !== 0) {
        prepared = prepared + "\n```";
    }

    return prepared;
}

function fallbackMarkdown(text) {
    if (!text) return "";
    let safe = escapeHtml(text);

    // Code blocks
    safe = safe.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, function(_, lang, code) {
        return `<div class="code-block"><pre class="code-content"><code class="hljs ${lang}">${code}</code></pre></div>`;
    });

    // Hyperlinks [text](url)
    safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="markdown-link">$1</a>');
    // Bold **text**
    safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic *text*
    safe = safe.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Inline code `code`
    safe = safe.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
    // Blockquote
    safe = safe.replace(/^>\s+(.+)$/gm, '<blockquote>$1</blockquote>');
    // Unordered lists
    safe = safe.replace(/^[\*\-]\s+(.+)$/gm, '<li>$1</li>');
    safe = safe.replace(/((?:<li>.*?<\/li>\s*)+)/gs, '<ul>$1</ul>');
    return safe;
}

function renderMarkdown(text, isStreaming = false) {
    if (!text) return "";

    const raw = isStreaming ? prepareStreamingMarkdown(text) : text;
    const { text: cleanText, blocks, inlines } = extractMathAndPlaceholders(raw);

    let html = "";
    if (window.marked && typeof window.marked.parse === "function") {
        try {
            html = window.marked.parse(cleanText);
        } catch (err) {
            console.error("marked parse error:", err);
            html = fallbackMarkdown(cleanText);
        }
    } else {
        html = fallbackMarkdown(cleanText);
    }

    // Unwrap paragraph around standalone display math blocks
    html = html.replace(/<p>\s*(%%%MATH_BLOCK_\d+%%%)\s*<\/p>/g, "$1");

    // Substitute math expressions
    html = html.replace(/%%%MATH_BLOCK_(\d+)%%%/g, (_, i) => renderMathExpression(blocks[Number(i)] || "", true));
    html = html.replace(/%%%MATH_INLINE_(\d+)%%%/g, (_, i) => renderMathExpression(inlines[Number(i)] || "", false));

    return html;
}


// --------------------------------------------------
// SCROLL BEHAVIOR
// --------------------------------------------------

function scrollToBottom(force = false) {
    const container = $("messages");
    if (!container) return;

    const threshold = 140;
    const isNearBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight <= threshold;

    if (force || isNearBottom) {
        container.scrollTop = container.scrollHeight;
    }
}


// --------------------------------------------------
// NAVIGATION & SIDEBAR
// --------------------------------------------------

function toggleSidebar(open) {
    const sidebar = $("sidebar");
    const overlay = $("sidebar-overlay");
    if (!sidebar || !overlay) return;

    if (open === undefined) {
        sidebar.classList.toggle("open");
        overlay.classList.toggle("active");
    } else if (open) {
        sidebar.classList.add("open");
        overlay.classList.add("active");
    } else {
        sidebar.classList.remove("open");
        overlay.classList.remove("active");
    }
}

$("sidebar-toggle")?.addEventListener("click", () => toggleSidebar(true));
$("sidebar-overlay")?.addEventListener("click", () => toggleSidebar(false));

function showPage(page) {
    activePage = page;

    // Guaranteed interval cleanup to prevent leaks
    if (systemTimer) {
        clearInterval(systemTimer);
        systemTimer = null;
    }

    document.querySelectorAll(".page").forEach(element => {
        element.classList.remove("active");
    });

    document.querySelectorAll(".nav-item").forEach(element => {
        element.classList.toggle(
            "active",
            element.dataset.page === page
        );
    });

    const pageElement = $(`${page}-page`);
    if (pageElement) {
        pageElement.classList.add("active");
    }

    $("page-title").textContent =
        page.charAt(0).toUpperCase() + page.slice(1);

    if (page === "system") {
        refreshSystem();
        systemTimer = setInterval(refreshSystem, 2000);
    } else if (page === "settings") {
        refreshSettings();
    }

    toggleSidebar(false);
}

document.querySelectorAll(".nav-item").forEach(button => {
    button.addEventListener("click", () => {
        showPage(button.dataset.page);
    });
});


// --------------------------------------------------
// MODELS
// --------------------------------------------------

async function loadModels() {
    try {
        const data = await api("/models");
        const selector = $("model-selector");
        selector.replaceChildren();

        if (Array.isArray(data.models) && data.models.length > 0) {
            data.models.forEach(model => {
                const option = document.createElement("option");
                option.value = model.name;

                let desc = model.name;
                if (model.context_size) {
                    desc += ` · ${(model.context_size / 1024).toFixed(0)}k`;
                }

                option.textContent = desc;

                if (model.name === data.active_model) {
                    option.selected = true;
                }
                selector.appendChild(option);
            });

            if (data.active_model) {
                selector.value = data.active_model;
                previousModel = data.active_model;
            }
        }
    } catch (err) {
        console.warn("Could not load dynamic models list:", err);
    }
}

$("model-selector").addEventListener("change", async event => {
    if (isGenerating) {
        event.target.value = previousModel;
        return;
    }

    const newModel = event.target.value;
    $("model-status").textContent = "Switching...";

    try {
        await api("/models/switch", {
            method: "POST",
            body: JSON.stringify({ model: newModel })
        });

        previousModel = newModel;
        $("model-status").textContent = "Ready";
    } catch (error) {
        $("model-status").textContent = "Error";
        event.target.value = previousModel;
        alert(`Failed to switch model: ${error.message}`);
    }
});


// --------------------------------------------------
// CONVERSATIONS
// --------------------------------------------------

async function loadConversations() {
    try {
        const conversations = await api("/conversations");
        const container = $("conversation-list");
        container.replaceChildren();

        conversations.forEach(conversation => {
            const row = document.createElement("div");
            row.className = "conversation-item";

            const select = document.createElement("button");
            select.className = "conversation-select";
            select.type = "button";
            select.textContent = conversation.title || "New conversation";
            select.title = conversation.title || "New conversation";

            select.classList.toggle(
                "active",
                conversation.id === activeConversation
            );

            select.addEventListener("click", () => {
                openConversation(conversation.id);
            });

            // Double click to rename
            select.addEventListener("dblclick", (e) => {
                e.stopPropagation();
                startRenameConversation(conversation, row, select);
            });

            const actions = document.createElement("div");
            actions.className = "conversation-actions";

            const renameBtn = document.createElement("button");
            renameBtn.className = "conversation-action-btn";
            renameBtn.type = "button";
            renameBtn.textContent = "✎";
            renameBtn.title = "Rename conversation";
            renameBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                startRenameConversation(conversation, row, select);
            });

            const remove = document.createElement("button");
            remove.className = "conversation-action-btn delete-btn";
            remove.type = "button";
            remove.textContent = "×";
            remove.title = "Delete conversation";

            remove.addEventListener("click", async (e) => {
                e.stopPropagation();

                if (isGenerating && activeConversation === conversation.id) {
                    alert("Cannot delete active conversation while generating.");
                    return;
                }

                if (!confirm(`Delete "${conversation.title || 'conversation'}"?`)) return;

                try {
                    await api(`/conversations/${conversation.id}`, {
                        method: "DELETE"
                    });

                    if (activeConversation === conversation.id) {
                        activeConversation = null;
                        clearMessages();
                    }

                    await loadConversations();
                } catch (error) {
                    alert(error.message);
                }
            });

            actions.append(renameBtn, remove);
            row.append(select, actions);
            container.appendChild(row);
        });

        const chatCountEl = $("settings-chat-count");
        if (chatCountEl) chatCountEl.textContent = String(conversations.length);
    } catch (err) {
        console.warn("Failed to load conversations:", err);
    }
}

function startRenameConversation(conversation, row, selectBtn) {
    if (isGenerating) return;

    const input = document.createElement("input");
    input.type = "text";
    input.className = "conversation-edit-input";
    input.value = conversation.title || "New conversation";
    input.maxLength = 120;

    let finished = false;

    const commitRename = async () => {
        if (finished) return;
        finished = true;

        const newTitle = input.value.trim();
        if (newTitle && newTitle !== conversation.title) {
            try {
                await api(`/conversations/${conversation.id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ title: newTitle })
                });
                await loadConversations();
            } catch (err) {
                alert(`Rename failed: ${err.message}`);
                input.replaceWith(selectBtn);
            }
        } else {
            input.replaceWith(selectBtn);
        }
    };

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            commitRename();
        } else if (e.key === "Escape") {
            e.preventDefault();
            finished = true;
            input.replaceWith(selectBtn);
        }
    });

    input.addEventListener("blur", () => {
        commitRename();
    });

    selectBtn.replaceWith(input);
    input.focus();
    input.select();
}

function clearMessages() {
    $("messages").replaceChildren();

    const welcome = document.createElement("div");
    welcome.className = "welcome";
    welcome.id = "welcome-screen";

    welcome.innerHTML = `
        <div class="hud-container">
            <div class="hud-header">
                <div class="hud-status-strip">
                    <span class="hud-status-pill">
                        <span class="hud-status-dot"></span>
                        <span class="hud-mono">CORE RUNTIME</span>
                    </span>
                    <span class="hud-divider">/</span>
                    <span class="hud-meta-item">LOCAL INFERENCE</span>
                    <span class="hud-divider">/</span>
                    <span class="hud-meta-item">TOOLS READY</span>
                    <span class="hud-divider">/</span>
                    <span class="hud-meta-item">PERSISTENCE MOUNTED</span>
                </div>
            </div>

            <div class="hud-hero">
                <h1 class="hud-title">PAIN Workstation</h1>
                <p class="hud-subtitle">
                    Autonomous local intelligence engine with tool-calling runtime, real-time web grounding, and persistent memory.
                </p>
            </div>

            <div class="hud-section-header">
                <span class="hud-section-label">COMMAND SHORTCUTS</span>
                <span class="hud-section-hint">Click to populate prompt or press Enter in composer</span>
            </div>

            <div class="suggestion-grid">
                <button class="suggestion-card" type="button" data-prompt="What festival or holiday was yesterday?">
                    <div class="suggestion-card-header">
                        <span class="suggestion-cmd">/temporal</span>
                        <span class="suggestion-action-hint">Run ↵</span>
                    </div>
                    <div class="suggestion-title">Check Recent Festivals & Holidays</div>
                    <div class="suggestion-desc">Query calendar anchor and real-time external festival events</div>
                </button>

                <button class="suggestion-card" type="button" data-prompt="Search your persistent memory for my saved preferences">
                    <div class="suggestion-card-header">
                        <span class="suggestion-cmd">/memory</span>
                        <span class="suggestion-action-hint">Run ↵</span>
                    </div>
                    <div class="suggestion-title">Query Long-Term Semantic Memory</div>
                    <div class="suggestion-desc">Retrieve stored user preferences, profile context, and notes</div>
                </button>

                <button class="suggestion-card" type="button" data-prompt="Write a Python script to monitor system CPU and RAM and alert when high">
                    <div class="suggestion-card-header">
                        <span class="suggestion-cmd">/script</span>
                        <span class="suggestion-action-hint">Run ↵</span>
                    </div>
                    <div class="suggestion-title">Generate Telemetry Monitor</div>
                    <div class="suggestion-desc">Synthesize production-grade Python scripts with syntax formatting</div>
                </button>

                <button class="suggestion-card" type="button" data-prompt="Search the web for the latest advancements in local AI inference">
                    <div class="suggestion-card-header">
                        <span class="suggestion-cmd">/search</span>
                        <span class="suggestion-action-hint">Run ↵</span>
                    </div>
                    <div class="suggestion-title">Real-Time Web Intelligence</div>
                    <div class="suggestion-desc">DuckDuckGo live retrieval with grounded synthesis and citations</div>
                </button>
            </div>
        </div>
    `;

    $("messages").appendChild(welcome);
}

async function openConversation(id) {
    if (isGenerating) {
        alert("Please wait for generation to complete or stop it first.");
        return;
    }

    try {
        const conversation = await api(`/conversations/${id}`);
        activeConversation = id;

        const container = $("messages");
        container.replaceChildren();

        if (!conversation.messages || conversation.messages.length === 0) {
            clearMessages();
        } else {
            conversation.messages.forEach(message => {
                appendMessage(message.role, message.content);
            });
            scrollToBottom(true);
        }

        await loadConversations();
        showPage("chat");
        toggleSidebar(false);
    } catch (error) {
        alert(error.message);
    }
}

$("new-chat").addEventListener("click", () => {
    if (isGenerating) return;

    activeConversation = null;
    clearMessages();
    loadConversations();
    showPage("chat");
    toggleSidebar(false);
    $("message-input").focus();
});


// --------------------------------------------------
// MESSAGE RENDERING
// --------------------------------------------------

function appendMessage(role, content = "") {
    const welcome = document.querySelector(".welcome");
    if (welcome) welcome.remove();

    const message = document.createElement("div");
    message.className = `message ${role}`;

    const header = document.createElement("div");
    header.className = "message-header";

    if (role === "user") {
        header.innerHTML = `
            <div class="message-role-tag user-tag">
                <span class="role-marker">❯</span>
                <span class="message-sender">PROMPT</span>
            </div>
        `;
    } else if (role === "assistant") {
        header.innerHTML = `
            <div class="message-role-tag assistant-tag">
                <span class="role-marker">●</span>
                <span class="message-sender">PAIN</span>
                <span class="message-badge">RUNTIME</span>
            </div>
            <div class="message-actions">
                <button class="copy-msg-btn" type="button" title="Copy message">
                    <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    <span>Copy</span>
                </button>
            </div>
        `;
    } else {
        header.innerHTML = `
            <div class="message-role-tag system-tag">
                <span class="role-marker">§</span>
                <span class="message-sender">SYSTEM</span>
            </div>
        `;
    }

    const body = document.createElement("div");
    body.className = role === "assistant" ? "message-body" : "message-content";

    if (role === "assistant") {
        const textDiv = document.createElement("div");
        textDiv.className = "message-content";
        textDiv.innerHTML = renderMarkdown(content);
        body.appendChild(textDiv);
    } else {
        body.textContent = content;
    }

    message.append(header, body);
    $("messages").appendChild(message);
    scrollToBottom();

    return { messageEl: message, bodyEl: body, contentEl: role === "assistant" ? body.firstChild : body };
}


// Event delegation for copying code blocks, tool data, and assistant messages
$("messages").addEventListener("click", (event) => {
    // 0. Suggestion card click
    const card = event.target.closest(".suggestion-card");
    if (card && card.dataset.prompt) {
        const input = $("message-input");
        if (input && !isGenerating) {
            input.value = card.dataset.prompt;
            input.dispatchEvent(new Event("input"));
            input.focus();
        }
        return;
    }

    // 1. Copy code block
    const copyCodeBtn = event.target.closest(".copy-code-btn");
    if (copyCodeBtn) {
        const rawCode = decodeURIComponent(copyCodeBtn.dataset.code || "");
        navigator.clipboard.writeText(rawCode).then(() => {
            const textEl = copyCodeBtn.querySelector(".copy-text") || copyCodeBtn;
            const originalText = textEl.textContent;
            textEl.textContent = "Copied!";
            copyCodeBtn.classList.add("copied");
            setTimeout(() => {
                textEl.textContent = originalText;
                copyCodeBtn.classList.remove("copied");
            }, 1800);
        }).catch(err => {
            console.error("Clipboard copy failed:", err);
        });
        return;
    }

    // 2. Copy tool section data
    const toolCopyBtn = event.target.closest(".tool-copy-btn");
    if (toolCopyBtn) {
        const textToCopy = decodeURIComponent(toolCopyBtn.dataset.copy || "");
        navigator.clipboard.writeText(textToCopy).then(() => {
            const orig = toolCopyBtn.textContent;
            toolCopyBtn.textContent = "Copied!";
            setTimeout(() => {
                toolCopyBtn.textContent = orig;
            }, 1800);
        }).catch(err => {
            console.error("Clipboard copy failed:", err);
        });
        return;
    }

    // 3. Copy message text
    const copyMsgBtn = event.target.closest(".copy-msg-btn");
    if (copyMsgBtn) {
        const msgDiv = copyMsgBtn.closest(".message");
        const bodyDiv = msgDiv ? msgDiv.querySelector(".message-body, .message-content") : null;
        if (bodyDiv) {
            const textToCopy = bodyDiv.innerText;
            navigator.clipboard.writeText(textToCopy).then(() => {
                const textSpan = copyMsgBtn.querySelector("span") || copyMsgBtn;
                const orig = textSpan.textContent;
                textSpan.textContent = "Copied!";
                copyMsgBtn.classList.add("copied");
                setTimeout(() => {
                    textSpan.textContent = orig;
                    copyMsgBtn.classList.remove("copied");
                }, 1800);
            }).catch(err => {
                console.error("Clipboard copy failed:", err);
            });
        }
        return;
    }
});


// --------------------------------------------------
// TOOL ACTIVITY COMPONENT
// --------------------------------------------------

function formatToolData(value) {
    if (value === undefined || value === null) return "No data";

    if (typeof value === "string") {
        try {
            return JSON.stringify(JSON.parse(value), null, 2);
        } catch {
            return value;
        }
    }

    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

function formatArgsPreview(args) {
    if (!args || typeof args !== "object" || Object.keys(args).length === 0) {
        return "()";
    }

    const parts = [];
    for (const [k, v] of Object.entries(args)) {
        let valStr = typeof v === "string" ? `"${v}"` : JSON.stringify(v);
        if (valStr && valStr.length > 25) {
            valStr = valStr.slice(0, 22) + "...";
        }
        parts.push(`${k}=${valStr}`);
    }

    const preview = parts.join(", ");
    return preview.length > 55 ? `(${preview.slice(0, 52)}...)` : `(${preview})`;
}

function extractToolResult(rawResult, error = null) {
    if (error) {
        const errMsg = String(error);
        return { isError: true, displayData: errMsg, snippet: `[Failed: ${errMsg}]` };
    }

    if (!rawResult || typeof rawResult !== "object") {
        const str = String(rawResult ?? "");
        return { isError: false, displayData: rawResult, snippet: str ? `→ ${str}` : "" };
    }

    if (rawResult.ok === false || rawResult.error !== undefined) {
        const errMsg = String(rawResult.error || "Tool failed");
        return { isError: true, displayData: errMsg, snippet: `[Failed: ${errMsg}]` };
    }

    if (rawResult.ok === true && rawResult.result !== undefined) {
        const res = rawResult.result;
        let snippet = "";
        if (typeof res === "string" || typeof res === "number" || typeof res === "boolean") {
            snippet = `→ ${res}`;
        } else if (res && typeof res === "object") {
            if (res.result !== undefined) snippet = `→ ${res.result}`;
            else if (res.time) snippet = `→ ${res.time}`;
            else {
                const preview = Object.values(res).slice(0, 2).map(v => typeof v === 'object' ? '...' : String(v)).join(", ");
                snippet = preview ? `→ {${preview}}` : "";
            }
        }
        return { isError: false, displayData: res, snippet };
    }

    return { isError: false, displayData: rawResult, snippet: "" };
}

function appendToolActivity(container, name, args = {}, callId = null) {
    const activity = document.createElement("details");
    activity.className = "tool-activity";
    activity.open = true; // Open while running so user sees active invocation

    const summary = document.createElement("summary");
    summary.className = "tool-activity-summary";

    const iconSpan = document.createElement("span");
    iconSpan.className = "tool-activity-icon";
    const spinner = document.createElement("span");
    spinner.className = "tool-spinner";
    iconSpan.appendChild(spinner);

    const info = document.createElement("span");
    info.className = "tool-activity-info";

    const nameSpan = document.createElement("span");
    nameSpan.className = "tool-activity-name";
    nameSpan.textContent = name || "tool";

    const argsPreviewSpan = document.createElement("span");
    argsPreviewSpan.className = "tool-activity-preview";
    argsPreviewSpan.textContent = formatArgsPreview(args);

    const resultSnippetSpan = document.createElement("span");
    resultSnippetSpan.className = "tool-activity-result-snippet";

    info.append(nameSpan, argsPreviewSpan, resultSnippetSpan);

    const badge = document.createElement("span");
    badge.className = "tool-activity-badge running";
    badge.textContent = "Running";

    const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chevron.setAttribute("class", "tool-chevron");
    chevron.setAttribute("viewBox", "0 0 24 24");
    chevron.setAttribute("fill", "none");
    chevron.setAttribute("stroke", "currentColor");
    chevron.setAttribute("stroke-width", "2");
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("points", "6 9 12 15 18 9");
    chevron.appendChild(polyline);

    summary.append(iconSpan, info, badge, chevron);

    const body = document.createElement("div");
    body.className = "tool-activity-body";

    // Section 1: Arguments
    const argsSec = document.createElement("div");
    argsSec.className = "tool-section";

    const argsHeader = document.createElement("div");
    argsHeader.className = "tool-section-header";
    const argsTitle = document.createElement("span");
    argsTitle.className = "tool-section-title";
    argsTitle.textContent = "Arguments";

    const argsCopyBtn = document.createElement("button");
    argsCopyBtn.className = "tool-copy-btn";
    argsCopyBtn.type = "button";
    argsCopyBtn.textContent = "Copy";
    const formattedArgs = formatToolData(args);
    argsCopyBtn.dataset.copy = encodeURIComponent(formattedArgs);

    argsHeader.append(argsTitle, argsCopyBtn);

    const argsPre = document.createElement("pre");
    argsPre.className = "tool-activity-data";
    argsPre.textContent = formattedArgs;

    argsSec.append(argsHeader, argsPre);

    // Section 2: Result
    const resSec = document.createElement("div");
    resSec.className = "tool-section tool-result-section";

    const resHeader = document.createElement("div");
    resHeader.className = "tool-section-header";
    const resTitle = document.createElement("span");
    resTitle.className = "tool-section-title";
    resTitle.textContent = "Result";

    const resCopyBtn = document.createElement("button");
    resCopyBtn.className = "tool-copy-btn";
    resCopyBtn.type = "button";
    resCopyBtn.textContent = "Copy";
    resCopyBtn.style.display = "none";

    resHeader.append(resTitle, resCopyBtn);

    const resPre = document.createElement("pre");
    resPre.className = "tool-activity-data tool-result-data";
    resPre.textContent = "Waiting for tool output...";

    resSec.append(resHeader, resPre);

    body.append(argsSec, resSec);
    activity.append(summary, body);

    container.appendChild(activity);
    scrollToBottom();

    const key = callId || `tool-${Date.now()}-${activeToolActivities.size}`;
    activeToolActivities.set(key, {
        activity,
        iconSpan,
        badge,
        resultSnippetSpan,
        resPre,
        resCopyBtn
    });

    return key;
}

function completeToolActivity(callId, result, error = null) {
    let target = null;
    let matchedKey = null;

    if (callId && activeToolActivities.has(callId)) {
        target = activeToolActivities.get(callId);
        matchedKey = callId;
    } else {
        // Fallback to most recent running tool
        for (const [key, item] of [...activeToolActivities.entries()].reverse()) {
            if (item.badge.textContent === "Running") {
                target = item;
                matchedKey = key;
                break;
            }
        }
    }

    if (!target) return;

    const extracted = extractToolResult(result, error);

    target.iconSpan.replaceChildren();

    if (extracted.isError) {
        target.iconSpan.className = "tool-activity-icon failed";
        target.iconSpan.textContent = "✕";

        target.badge.className = "tool-activity-badge failed";
        target.badge.textContent = "Failed";

        target.resultSnippetSpan.className = "tool-activity-result-snippet error";
        target.resultSnippetSpan.textContent = extracted.snippet;

        target.resPre.className = "tool-activity-data tool-result-data error";
        target.activity.open = true; // keep expanded on failure so error is visible
    } else {
        target.iconSpan.className = "tool-activity-icon completed";
        target.iconSpan.textContent = "✓";

        target.badge.className = "tool-activity-badge completed";
        target.badge.textContent = "Completed";

        target.resultSnippetSpan.className = "tool-activity-result-snippet";
        target.resultSnippetSpan.textContent = extracted.snippet;

        target.resPre.className = "tool-activity-data tool-result-data";
        target.activity.open = false; // auto-collapse on success for sleek appearance!
    }

    const formattedData = formatToolData(extracted.displayData);
    target.resPre.textContent = formattedData;
    target.resCopyBtn.dataset.copy = encodeURIComponent(formattedData);
    target.resCopyBtn.style.display = "inline-block";

    if (matchedKey) activeToolActivities.delete(matchedKey);
}


// --------------------------------------------------
// STREAMING CHAT & CANCELLATION
// --------------------------------------------------

function setSendButtonMode(generating) {
    const btn = $("send-button");
    if (!btn) return;
    if (generating) {
        btn.classList.add("stop-mode");
        btn.innerHTML = `<span class="stop-box">■</span><span>Stop</span>`;
        btn.title = "Stop generating";
        btn.disabled = false;
        btn.type = "button";
    } else {
        btn.classList.remove("stop-mode");
        btn.innerHTML = `<svg viewBox="0 0 24 24" width="13" height="13" stroke="currentColor" stroke-width="2.2" fill="none"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg><span>Send</span>`;
        btn.title = "Send message";
        btn.disabled = false;
        btn.type = "submit";
    }
}

function stopGeneration() {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
}

$("send-button").addEventListener("click", (e) => {
    if (isGenerating) {
        e.preventDefault();
        e.stopPropagation();
        stopGeneration();
    }
});

function getOrCreateAssistantTurn() {
    if (currentTurn.messageEl) {
        return currentTurn;
    }

    const welcome = document.querySelector(".welcome");
    if (welcome) welcome.remove();

    const message = document.createElement("div");
    message.className = "message assistant";

    const header = document.createElement("div");
    header.className = "message-header";
    header.innerHTML = `
        <div class="message-role-tag assistant-tag">
            <span class="role-marker">●</span>
            <span class="message-sender">PAIN</span>
            <span class="message-badge streaming">STREAMING</span>
        </div>
        <div class="message-actions">
            <button class="copy-msg-btn" type="button" title="Copy message">
                <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <span>Copy</span>
            </button>
        </div>
    `;

    const body = document.createElement("div");
    body.className = "message-body";

    message.append(header, body);
    $("messages").appendChild(message);

    currentTurn.messageEl = message;
    currentTurn.bodyEl = body;
    return currentTurn;
}

async function sendMessage(text) {
    if (isGenerating || !text.trim()) return;

    isGenerating = true;
    const isNewConversation = (activeConversation === null);

    setSendButtonMode(true);
    $("model-selector").disabled = true;
    $("new-chat").disabled = true;

    // Append user message
    appendMessage("user", text);

    // Reset current assistant turn state
    currentTurn = {
        messageEl: null,
        bodyEl: null,
        toolsWrapperEl: null,
        preToolEl: null,
        preToolText: "",
        postToolEl: null,
        postToolText: "",
        hasTools: false
    };
    activeToolActivities.clear();

    const model = $("model-selector").value;
    $("model-status").textContent = "Generating...";

    abortController = new AbortController();

    try {
        const response = await fetch(API + "/chat/stream", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: text,
                conversation_id: activeConversation,
                model: model || undefined
            }),
            signal: abortController.signal
        });

        if (!response.ok) {
            let errMsg = `HTTP ${response.status}`;
            try {
                const errData = await response.json();
                errMsg = errData.detail || errMsg;
            } catch {}
            throw new Error(errMsg);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        const handleSSEData = (data) => {
            if (data.type === "start") {
                activeConversation = data.conversation_id;

                // Auto-name new conversation from first user query
                if (isNewConversation && activeConversation) {
                    const previewTitle = text.trim().replace(/\s+/g, " ").slice(0, 40);
                    api(`/conversations/${activeConversation}`, {
                        method: "PATCH",
                        body: JSON.stringify({ title: previewTitle })
                    }).then(() => loadConversations()).catch(() => {});
                }

            } else if (
                data.type === "tool_start" ||
                (data.type === "tool" && data.phase === "start")
            ) {
                const turn = getOrCreateAssistantTurn();
                turn.hasTools = true;

                if (!turn.toolsWrapperEl) {
                    turn.toolsWrapperEl = document.createElement("div");
                    turn.toolsWrapperEl.className = "tool-activities-wrapper";
                    turn.bodyEl.appendChild(turn.toolsWrapperEl);
                }

                appendToolActivity(
                    turn.toolsWrapperEl,
                    data.name || data.tool,
                    data.arguments ?? data.args ?? {},
                    data.call_id || data.id || null
                );

            } else if (
                data.type === "tool_result" ||
                (data.type === "tool" && data.phase === "result")
            ) {
                completeToolActivity(
                    data.call_id || data.id || null,
                    data.result,
                    data.error || null
                );

            } else if (data.type === "token") {
                const turn = getOrCreateAssistantTurn();

                if (turn.hasTools) {
                    // Streaming after tool execution
                    if (!turn.postToolEl) {
                        turn.postToolEl = document.createElement("div");
                        turn.postToolEl.className = "message-content post-tool-content";
                        turn.bodyEl.appendChild(turn.postToolEl);
                    }
                    turn.postToolText += data.content;
                    turn.postToolEl.innerHTML = renderMarkdown(turn.postToolText, true);
                } else {
                    // Streaming before any tool execution
                    if (!turn.preToolEl) {
                        turn.preToolEl = document.createElement("div");
                        turn.preToolEl.className = "message-content pre-tool-content";
                        turn.bodyEl.appendChild(turn.preToolEl);
                    }
                    turn.preToolText += data.content;
                    turn.preToolEl.innerHTML = renderMarkdown(turn.preToolText, true);
                }

                scrollToBottom(false);

            } else if (data.type === "error") {
                throw new Error(data.message || "Engine error during generation");

            } else if (data.type === "done") {
                $("model-status").textContent = "Ready";
                if (currentTurn.preToolEl && currentTurn.preToolText) {
                    currentTurn.preToolEl.innerHTML = renderMarkdown(currentTurn.preToolText, false);
                }
                if (currentTurn.postToolEl && currentTurn.postToolText) {
                    currentTurn.postToolEl.innerHTML = renderMarkdown(currentTurn.postToolText, false);
                }
                const badge = currentTurn.messageEl?.querySelector(".message-badge");
                if (badge) {
                    badge.textContent = "RUNTIME";
                    badge.classList.remove("streaming");
                }
            }
        };

        const processEvent = (rawEvent) => {
            const lines = rawEvent.split(/\r?\n/);
            const dataLines = [];

            for (const line of lines) {
                if (line.startsWith("data:")) {
                    dataLines.push(line.slice(5).trimStart());
                }
            }

            if (dataLines.length === 0) return;

            const dataStr = dataLines.join("\n");
            try {
                const data = JSON.parse(dataStr);
                handleSSEData(data);
            } catch (err) {
                console.warn("Failed to parse SSE JSON:", dataStr, err);
            }
        };

        while (true) {
            const { value, done } = await reader.read();

            if (done) {
                buffer += decoder.decode();
                break;
            }

            buffer += decoder.decode(value, { stream: true });

            const events = buffer.split(/\r?\n\r?\n/);
            buffer = events.pop() || "";

            for (const rawEvent of events) {
                processEvent(rawEvent);
            }
        }

        if (buffer.trim()) {
            processEvent(buffer);
        }

        await loadConversations();

    } catch (error) {
        if (error.name === "AbortError" || abortController?.signal?.aborted) {
            $("model-status").textContent = "Stopped";
            const turn = getOrCreateAssistantTurn();
            const targetEl = turn.postToolEl || turn.preToolEl;
            if (targetEl) {
                targetEl.innerHTML += renderMarkdown("\n\n*[Generation stopped]*");
            }
        } else {
            $("model-status").textContent = "Error";
            const turn = getOrCreateAssistantTurn();
            const errDiv = document.createElement("div");
            errDiv.className = "message-content";
            errDiv.innerHTML = `<span style="color: var(--red);">[Error: ${escapeHtml(error.message)}]</span>`;
            turn.bodyEl.appendChild(errDiv);
        }
    } finally {
        isGenerating = false;
        abortController = null;

        if (currentTurn.preToolEl && currentTurn.preToolText) {
            currentTurn.preToolEl.innerHTML = renderMarkdown(currentTurn.preToolText, false);
        }
        if (currentTurn.postToolEl && currentTurn.postToolText) {
            currentTurn.postToolEl.innerHTML = renderMarkdown(currentTurn.postToolText, false);
        }
        const badge = currentTurn.messageEl?.querySelector(".message-badge");
        if (badge) {
            badge.textContent = "RUNTIME";
            badge.classList.remove("streaming");
        }

        setSendButtonMode(false);
        $("model-selector").disabled = false;
        $("new-chat").disabled = false;

        const input = $("message-input");
        input.focus();
    }
}


// --------------------------------------------------
// INPUT & KEYBOARD
// --------------------------------------------------

$("chat-form").addEventListener("submit", async event => {
    event.preventDefault();

    if (isGenerating) return;

    const input = $("message-input");
    const text = input.value.trim();

    if (!text) return;

    input.value = "";
    input.style.height = "auto";

    await sendMessage(text);
});

$("message-input").addEventListener("keydown", event => {
    if (event.key === "Escape" && isGenerating) {
        event.preventDefault();
        stopGeneration();
        return;
    }

    if (
        event.key === "Enter" &&
        !event.shiftKey &&
        !event.isComposing
    ) {
        event.preventDefault();
        if (!isGenerating) {
            $("chat-form").requestSubmit();
        }
    }
});

$("message-input").addEventListener("input", event => {
    const input = event.target;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
});


// --------------------------------------------------
// SYSTEM MONITORING
// --------------------------------------------------

function setMetric(name, value, details) {
    const valEl = $(`${name}-value`);
    const detEl = $(`${name}-details`);
    if (valEl) valEl.textContent = value;
    if (detEl) detEl.textContent = details;
}

function setBar(name, value) {
    const bar = $(`${name}-bar`);
    if (!bar) return;

    if (value === null || value === undefined || isNaN(value)) {
        bar.style.width = "0%";
        return;
    }

    bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
}

async function refreshSystem() {
    try {
        const data = await api("/system");

        // CPU
        const cpu = data.cpu || {};
        const cpuUsage = typeof cpu.usage_percent === "number" ? cpu.usage_percent : 0;
        const logicalCores = cpu.core_count ?? "?";
        const physicalCores = cpu.physical_cores ?? "?";
        const freq = cpu.frequency_mhz ? `${cpu.frequency_mhz.toFixed(0)} MHz` : "Frequency unavailable";

        setMetric(
            "cpu",
            `${cpuUsage.toFixed(1)}%`,
            `${logicalCores} logical · ${physicalCores} physical cores · ${freq}`
        );
        setBar("cpu", cpuUsage);

        // RAM
        const ram = data.memory || {};
        const ramUsage = typeof ram.usage_percent === "number" ? ram.usage_percent : 0;
        setMetric(
            "ram",
            `${ramUsage.toFixed(1)}%`,
            `${formatBytes(ram.used_bytes)} used of ${formatBytes(ram.total_bytes)} · ${formatBytes(ram.available_bytes)} free`
        );
        setBar("ram", ramUsage);

        // GPU
        const gpu = Array.isArray(data.gpu) && data.gpu.length > 0 ? data.gpu[0] : null;

        if (gpu) {
            const gpuUsage = typeof gpu.utilization_percent === "number" ? gpu.utilization_percent : null;
            const temp = typeof gpu.temperature_c === "number" ? `${gpu.temperature_c.toFixed(1)} °C` : "Temp unavailable";

            setMetric(
                "gpu",
                gpuUsage !== null ? `${gpuUsage}%` : "N/A",
                `${gpu.name || "GPU"} · ${temp}`
            );
            setBar("gpu", gpuUsage);

            if (gpu.vram_total_bytes && gpu.vram_total_bytes > 0) {
                const used = gpu.vram_used_bytes ?? 0;
                const percent = Math.min(100, Math.max(0, (used / gpu.vram_total_bytes) * 100));
                setMetric(
                    "vram",
                    `${percent.toFixed(1)}%`,
                    `${formatBytes(used)} / ${formatBytes(gpu.vram_total_bytes)}`
                );
                setBar("vram", percent);
            } else if (gpu.gtt_total_bytes && gpu.gtt_total_bytes > 0) {
                const used = gpu.gtt_used_bytes ?? 0;
                const percent = Math.min(100, Math.max(0, (used / gpu.gtt_total_bytes) * 100));
                setMetric(
                    "vram",
                    "Shared",
                    `${formatBytes(used)} / ${formatBytes(gpu.gtt_total_bytes)} GTT`
                );
                setBar("vram", percent);
            } else {
                setMetric("vram", "N/A", "Memory telemetry unavailable");
                setBar("vram", null);
            }
        } else {
            setMetric("gpu", "N/A", "GPU telemetry unavailable");
            setMetric("vram", "N/A", "GPU memory telemetry unavailable");
            setBar("gpu", null);
            setBar("vram", null);
        }

        // DISK
        const disk = data.disk || {};
        const diskUsage = typeof disk.usage_percent === "number" ? disk.usage_percent : 0;
        setMetric(
            "disk",
            `${diskUsage.toFixed(1)}%`,
            `${formatBytes(disk.used_bytes)} used of ${formatBytes(disk.total_bytes)} · ${formatBytes(disk.free_bytes)} free`
        );
        setBar("disk", diskUsage);

        // SWAP
        const swap = data.swap || {};
        const swapUsage = typeof swap.usage_percent === "number" ? swap.usage_percent : 0;
        setMetric(
            "swap",
            `${swapUsage.toFixed(1)}%`,
            `${formatBytes(swap.used_bytes)} used of ${formatBytes(swap.total_bytes)}`
        );
        setBar("swap", swapUsage);

        // NETWORK
        const network = data.network || {};
        $("download-value").textContent = `${formatBytes(network.download_bytes_per_sec || 0)}/s`;
        $("upload-value").textContent = `${formatBytes(network.upload_bytes_per_sec || 0)}/s`;

        // HARDWARE
        const info = $("hardware-info");
        info.replaceChildren();

        const rows = [
            ["CPU cores", `${logicalCores} logical (${physicalCores} physical)`],
            ["CPU frequency", freq],
            ["GPU", gpu ? gpu.name : "Not detected"],
            ["RAM", formatBytes(ram.total_bytes)],
            ["Disk", formatBytes(disk.total_bytes)]
        ];

        rows.forEach(([label, value]) => {
            const row = document.createElement("div");
            row.className = "hardware-row";

            const key = document.createElement("span");
            key.textContent = label;

            const val = document.createElement("strong");
            val.textContent = value;

            row.append(key, val);
            info.appendChild(row);
        });

        $("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (error) {
        $("last-updated").textContent = `Monitoring error: ${error.message}`;
    }
}


// --------------------------------------------------
// SETTINGS
// --------------------------------------------------

async function refreshSettings() {
    try {
        const health = await api("/health");
        $("settings-app-name").textContent = health.name || "PAIN";
        $("settings-app-version").textContent = health.version || "0.1.0";
        $("settings-backend-status").textContent = health.status === "online" ? "Online" : health.status;

        const status = await api("/models/status");
        $("settings-active-model").textContent = status.model || "None";
        $("settings-inference-status").textContent = status.inference?.running ? "Running" : "Idle";

        const modelsData = await api("/models");
        const activeModelInfo = modelsData.models?.find(m => m.name === status.model);
        $("settings-context-size").textContent = activeModelInfo?.context_size
            ? `${activeModelInfo.context_size} tokens`
            : "Default";
    } catch (err) {
        console.warn("Failed to refresh settings:", err);
    }
}

$("clear-all-conversations")?.addEventListener("click", async () => {
    if (isGenerating) {
        alert("Cannot clear conversations while generating.");
        return;
    }

    if (!confirm("Are you sure you want to delete ALL conversations? This cannot be undone.")) {
        return;
    }

    try {
        const conversations = await api("/conversations");
        for (const conv of conversations) {
            await api(`/conversations/${conv.id}`, { method: "DELETE" });
        }

        activeConversation = null;
        clearMessages();
        await loadConversations();
        refreshSettings();
        alert("All conversations have been deleted.");
    } catch (err) {
        alert(`Failed to delete conversations: ${err.message}`);
    }
});


// --------------------------------------------------
// INITIALIZATION
// --------------------------------------------------

async function initialize() {
    updateConnectionStatus("connecting");

    try {
        await api("/health");
        updateConnectionStatus("connected");

        await loadModels();
        await loadConversations();

        const status = await api("/models/status");
        if (status.inference?.running) {
            $("model-status").textContent = "Loaded";
        } else {
            $("model-status").textContent = "Ready";
        }
    } catch (error) {
        updateConnectionStatus("disconnected");
        $("model-status").textContent = "Offline";
        console.error("Initialization failed:", error);
    }
}

initialize();
