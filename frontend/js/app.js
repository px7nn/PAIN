// PAIN Web Client
// Single-page responsive interface for Personal AI Network

const API = "/api";

let activeConversation = null;
let activePage = "chat";
let isGenerating = false;
let abortController = null;
let activeRequestId = null;
let systemTimer = null;
let activeToolActivities = new Map();
let previousModel = "";
let availableModels = [];
let modelsDirPath = "";
let switchingModelName = null;
let pendingAttachments = [];
let nextAttachmentSeq = 1;
let dragDepth = 0;

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
            return `<div class="table-wrapper">${defaultTableHtml.replace(/^<table>/, '<table class="markdown-table">')}</div>`;
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

function isEscapedChar(str, index) {
    let slashCount = 0;
    for (let k = index - 1; k >= 0 && str[k] === "\\"; k--) {
        slashCount++;
    }
    return slashCount % 2 === 1;
}

function isValidInlineMath(inner) {
    if (!inner) return false;
    const trimmed = inner.trim();
    if (!trimmed) return false;

    const hasLatexCmd = /\\[a-zA-Z]+/.test(trimmed);
    const hasCoreMathOp = /[=^_<>+\*]/.test(trimmed);

    // Reject trailing dangling binary operators after numbers (e.g. "$500 + $x")
    if (/^\d[\d,]*(?:\.\d+)?\s*[+\-*/=,]$/.test(trimmed)) {
        return false;
    }

    // Thousands-formatted currency numbers (e.g., "5,000 to", "100,000") without explicit math syntax
    if (/^\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:\s|$|[a-zA-Z),.;:!?])/.test(trimmed) && !hasLatexCmd && !hasCoreMathOp) {
        return false;
    }

    // Currency-like numbers followed by whitespace/punctuation/prose (e.g., "$500 to", "$500. Let", "$500 USD")
    // while allowing real math expressions starting with digits (e.g., "$2^3 = 8$", "$1 + 2 = 3$")
    if (/^\d[\d,]*(?:\.\d+)?(?:\s|[.,;:!?\)\]\-\u2013\u2014\/]|$)/.test(trimmed)) {
        if (!hasLatexCmd && !hasCoreMathOp) {
            return false;
        }
        if (/[a-zA-Z]{2,}/.test(trimmed) && !hasLatexCmd && !/[=^_<>]/.test(trimmed)) {
            return false;
        }
    }

    // Multi-word prose chunks between literal dollar signs without LaTeX commands or core math syntax
    if (!hasLatexCmd && !/[=^_<>]/.test(trimmed)) {
        const words = trimmed.match(/\b[a-zA-Z]{2,}\b/g);
        if (words && words.length >= 2) {
            return false;
        }
        if (/[a-zA-Z]{2,}\s+$/.test(inner)) {
            return false;
        }
    }

    return true;
}

function extractInlineDollarMath(line, inlines) {
    let out = "";
    let i = 0;
    const n = line.length;

    while (i < n) {
        if (line[i] === "$" && !isEscapedChar(line, i) && line[i + 1] !== "$") {
            let j = i + 1;
            let matched = false;

            while (j < n) {
                if (line[j] === "$" && !isEscapedChar(line, j)) {
                    // Never close inline math at '$$' or a currency '$2000' followed immediately by a digit
                    if (line[j + 1] === "$" || /\d/.test(line[j + 1] || "")) {
                        break;
                    }
                    const inner = line.slice(i + 1, j);
                    if (isValidInlineMath(inner)) {
                        inlines.push(inner.trim());
                        out += `@@PAINMATHI${inlines.length - 1}@@`;
                        i = j + 1;
                        matched = true;
                    }
                    break;
                }
                j++;
            }

            if (matched) continue;
        }

        out += line[i];
        i++;
    }

    return out;
}

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
    let shielded = text.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]+?`+/g, (m) => {
        codes.push(m);
        return `@@PAINCODE${codes.length - 1}@@`;
    });

    // 2. Extract display/block math: $$...$$ and \[...\]
    shielded = shielded.replace(/\$\$([\s\S]+?)\$\$/g, (_, expr) => {
        blocks.push(expr.trim());
        return `@@PAINMATHB${blocks.length - 1}@@`;
    });
    shielded = shielded.replace(/(?<!\\)\\\[([\s\S]+?)(?<!\\)\\\]/g, (m, expr) => {
        if (!expr.trim()) return m;
        blocks.push(expr.trim());
        return `@@PAINMATHB${blocks.length - 1}@@`;
    });

    // 3. Extract inline math: \(...\) and valid $...$
    shielded = shielded.replace(/(?<!\\)\\\(([^\n]+?)(?<!\\)\\\)/g, (m, expr) => {
        if (!expr.trim()) return m;
        inlines.push(expr.trim());
        return `@@PAINMATHI${inlines.length - 1}@@`;
    });

    shielded = shielded
        .split("\n")
        .map((line) => (line.includes("$") ? extractInlineDollarMath(line, inlines) : line))
        .join("\n");

    // 4. Restore shielded code blocks
    shielded = shielded.replace(/@@PAINCODE(\d+)@@/g, (_, idx) => codes[Number(idx)]);

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
    html = html.replace(/<p>\s*(@@PAINMATHB\d+@@)\s*<\/p>/g, "$1");

    // Substitute math expressions
    html = html.replace(/@@PAINMATHB(\d+)@@/g, (_, i) => renderMathExpression(blocks[Number(i)] || "", true));
    html = html.replace(/@@PAINMATHI(\d+)@@/g, (_, i) => renderMathExpression(inlines[Number(i)] || "", false));

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
// LLAMA.CPP STYLE REAL-TIME CONTEXT GAUGE & METRICS
// --------------------------------------------------

let currentContextMetrics = null;
let isCompacting = false;
let streamingOutputTokens = 0;

function formatTokens(count) {
    if (count === null || count === undefined || isNaN(count)) return "0";
    if (count >= 1000000) return (count / 1000000).toFixed(1) + "M";
    if (count >= 1000) return (count / 1000).toFixed(1) + "k";
    return String(count);
}

function updateContextGaugeUI(metrics) {
    if (!metrics) return;
    currentContextMetrics = metrics;

    const used = metrics.context_used || 0;
    const total = metrics.context_size || 8192;
    const available = metrics.context_available ?? Math.max(0, total - used);
    const percent = Math.min(100, Math.max(0, metrics.context_percent ?? (total > 0 ? (used / total) * 100 : 0)));

    // Level: ok (<80%), warning (80-95%), critical (>=95%)
    let level = "ok";
    if (percent >= 95) level = "critical";
    else if (percent >= 80) level = "warning";

    // 1. Composer Gauge Dial & Pill
    const dialBar = $("gauge-bar");
    if (dialBar) {
        const circumference = 69.115; // 2 * Math.PI * 11
        const offset = circumference * (1 - percent / 100);
        dialBar.style.strokeDashoffset = String(Math.max(0, offset));
        dialBar.classList.remove("level-ok", "level-warning", "level-critical");
        dialBar.classList.add(`level-${level}`);
    }

    const gaugeVal = $("gauge-val");
    if (gaugeVal) {
        gaugeVal.textContent = `${formatTokens(used)} / ${formatTokens(total)}`;
    }

    const gaugeTag = $("gauge-percent-tag");
    if (gaugeTag) {
        gaugeTag.textContent = `${percent.toFixed(0)}%`;
        gaugeTag.classList.remove("level-ok", "level-warning", "level-critical");
        gaugeTag.classList.add(`level-${level}`);
    }

    // 2. Topbar Context Pill
    const topbarDot = $("topbar-ctx-dot");
    if (topbarDot) {
        topbarDot.classList.remove("level-ok", "level-warning", "level-critical");
        topbarDot.classList.add(`level-${level}`);
    }

    const topbarText = $("topbar-ctx-text");
    if (topbarText) {
        topbarText.textContent = `Context: ${used.toLocaleString()} / ${total.toLocaleString()}`;
    }

    const topbarBadge = $("topbar-ctx-badge");
    if (topbarBadge) {
        topbarBadge.textContent = `${percent.toFixed(0)}%`;
    }

    // 3. Popup Card
    const popupFraction = $("popup-usage-fraction");
    if (popupFraction) {
        popupFraction.textContent = `${used.toLocaleString()} / ${total.toLocaleString()} tok`;
    }

    const popupLevelTag = $("popup-level-tag");
    if (popupLevelTag) {
        popupLevelTag.textContent = `${percent.toFixed(1)}% used`;
        popupLevelTag.classList.remove("level-ok", "level-warning", "level-critical");
        popupLevelTag.classList.add(`level-${level}`);
    }

    const popupFill = $("popup-progress-fill");
    if (popupFill) {
        popupFill.style.width = `${percent}%`;
        popupFill.classList.remove("level-ok", "level-warning", "level-critical");
        popupFill.classList.add(`level-${level}`);
    }

    const popupMetaUsed = $("popup-meta-used");
    if (popupMetaUsed) {
        popupMetaUsed.textContent = `${percent.toFixed(1)}% used`;
    }

    const popupMetaRemaining = $("popup-meta-remaining");
    if (popupMetaRemaining) {
        popupMetaRemaining.textContent = `${available.toLocaleString()} tok remaining`;
    }

    // Breakdown
    const b = metrics.breakdown || {};
    const histEl = $("popup-history-tok");
    if (histEl) histEl.textContent = `${(b.history || 0).toLocaleString()} tok`;

    const sysEl = $("popup-system-tok");
    if (sysEl) sysEl.textContent = `${(b.system || 0).toLocaleString()} tok`;

    const toolsEl = $("popup-tools-tok");
    if (toolsEl) toolsEl.textContent = `${(b.tools || 0).toLocaleString()} tok`;

    const summaryRow = $("popup-summary-row");
    const summaryEl = $("popup-summary-tok");
    if (summaryRow && summaryEl) {
        if (metrics.has_summary && b.summary) {
            summaryRow.style.display = "flex";
            summaryEl.textContent = `${b.summary.toLocaleString()} tok`;
        } else {
            summaryRow.style.display = "none";
        }
    }

    // Llama.cpp Telemetry (--metrics)
    const sm = metrics.server_metrics || {};
    const genSpeedEl = $("popup-gen-speed");
    if (genSpeedEl) {
        genSpeedEl.textContent = sm.generation_speed != null ? `${sm.generation_speed} tok/s` : "— tok/s";
    }

    const evalSpeedEl = $("popup-eval-speed");
    if (evalSpeedEl) {
        evalSpeedEl.textContent = sm.prompt_eval_speed != null ? `${sm.prompt_eval_speed} tok/s` : "— tok/s";
    }

    const kvEl = $("popup-kv-cache");
    if (kvEl) {
        kvEl.textContent = sm.kv_cache_tokens != null ? `${sm.kv_cache_tokens.toLocaleString()} tok` : "— tok";
    }

    const tokensGenEl = $("popup-tokens-generated");
    if (tokensGenEl) {
        tokensGenEl.textContent = sm.tokens_predicted_total != null ? `${sm.tokens_predicted_total.toLocaleString()} tok` : "— tok";
    }

    const promptEvalEl = $("popup-prompt-evaluated");
    if (promptEvalEl) {
        promptEvalEl.textContent = sm.prompt_tokens_total != null ? `${sm.prompt_tokens_total.toLocaleString()} tok` : "— tok";
    }

    // Summarize Button State
    const summarizeBtn = $("summarize-btn");
    const summarizeLabel = $("summarize-btn-label");
    const summarizeSubtext = $("summarize-subtext");
    if (summarizeBtn && summarizeLabel) {
        const canCompact = Boolean(metrics.can_summarize && activeConversation);
        summarizeBtn.disabled = !canCompact || isGenerating || isCompacting;

        if (metrics.has_summary) {
            summarizeLabel.textContent = isCompacting ? "Compacting..." : "Re-summarize conversation";
            if (summarizeSubtext) {
                summarizeSubtext.textContent = "Active summary in place. Click to refresh the condensed brief.";
            }
        } else {
            summarizeLabel.textContent = isCompacting ? "Compacting..." : "Summarize conversation";
            if (summarizeSubtext) {
                summarizeSubtext.textContent = "Condenses earlier turns into a dense brief so context drops back down.";
            }
        }
    }
}

async function refreshContextMetrics(conversationId = activeConversation, model = null) {
    try {
        const selectedModel = model || $("model-selector")?.value || "";
        const query = new URLSearchParams();
        if (conversationId) query.set("conversation_id", String(conversationId));
        if (selectedModel) query.set("model", selectedModel);

        const data = await api(`/chat/context?${query.toString()}`);
        if (data) {
            updateContextGaugeUI(data);
        }
    } catch (err) {
        console.warn("Failed to refresh context metrics:", err);
    }
}

function toggleContextGaugePopup(force = null) {
    const popup = $("context-gauge-popup");
    if (!popup) return;

    const isVisible = popup.style.display !== "none";
    const nextState = force !== null ? force : !isVisible;

    if (nextState) {
        popup.style.display = "block";
        refreshContextMetrics();
    } else {
        popup.style.display = "none";
    }
}

async function handleSummarizeConversation() {
    if (!activeConversation || isGenerating || isCompacting) return;

    const summarizeBtn = $("summarize-btn");
    const summarizeLabel = $("summarize-btn-label");

    try {
        isCompacting = true;
        if (summarizeBtn) {
            summarizeBtn.classList.add("loading");
            summarizeBtn.disabled = true;
        }
        if (summarizeLabel) summarizeLabel.textContent = "Summarizing conversation...";

        const selectedModel = $("model-selector")?.value || null;
        const res = await api("/chat/summarize", {
            method: "POST",
            body: JSON.stringify({
                conversation_id: activeConversation,
                model: selectedModel
            })
        });

        if (res && res.ok) {
            // Append compaction message into the chat messages container
            appendMessage("system", `[Conversation Compacted]\nSummary of earlier exchange:\n${res.summary}`);

            // Update metrics immediately
            if (res.context) {
                updateContextGaugeUI(res.context);
            }

            if (summarizeLabel) {
                summarizeLabel.textContent = `Summarized! Saved ${res.saved_tokens || 0} tokens`;
            }

            setTimeout(() => {
                refreshContextMetrics();
            }, 1800);
        }
    } catch (err) {
        alert("Summarization failed: " + (err.message || String(err)));
    } finally {
        isCompacting = false;
        if (summarizeBtn) summarizeBtn.classList.remove("loading");
        refreshContextMetrics();
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

function formatModelSize(bytes) {
    if (!bytes || bytes <= 0) return "";
    const gb = bytes / (1024 * 1024 * 1024);
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / (1024 * 1024))} MB`;
}

function renderWelcomeModels() {
    const grid = $("welcome-model-grid");
    const hint = $("welcome-models-hint");
    if (!grid) return;

    if (hint) {
        hint.textContent = modelsDirPath
            ? `Click to select model · Path: ${modelsDirPath}`
            : "Select a local model to load for this session";
    }

    grid.replaceChildren();

    if (!Array.isArray(availableModels) || availableModels.length === 0) {
        const empty = document.createElement("div");
        empty.className = "model-card-empty";
        empty.textContent = "No GGUF models detected in configured models directory.";
        grid.appendChild(empty);
        return;
    }

    const active = $("model-selector")?.value || previousModel;

    availableModels.forEach(model => {
        const isActive = model.name === active;
        const isSwitching = switchingModelName === model.name;
        const isAvailable = model.available !== false;

        const card = document.createElement("button");
        card.type = "button";
        card.className = `model-card${isActive ? " active" : ""}${isSwitching ? " switching" : ""}`;
        card.dataset.modelName = model.name;
        if (!isAvailable || (switchingModelName && !isSwitching)) {
            card.disabled = true;
        }

        const quantLabel = model.quantization || "GGUF";
        const ctxLabel = model.context_size ? `${Math.round(model.context_size / 1024)}k ctx` : "";
        const sizeLabel = formatModelSize(model.size_bytes);
        const metaText = [sizeLabel, ctxLabel].filter(Boolean).join(" · ");

        let statusHtml = `<span>Select ↵</span>`;
        if (!isAvailable) {
            statusHtml = `<span>Missing file</span>`;
        } else if (isSwitching) {
            statusHtml = `<span>Loading...</span>`;
        } else if (isActive) {
            statusHtml = `<span class="model-active-dot"></span><span>ACTIVE</span>`;
        }

        card.innerHTML = `
            <div class="model-card-header">
                <span class="model-quant-pill">${escapeHtml(quantLabel)}</span>
                <span class="model-card-status">${statusHtml}</span>
            </div>
            <div class="model-card-title">${escapeHtml(model.name)}</div>
            <div class="model-card-role">${escapeHtml(model.role || "Local inference")}</div>
            <div class="model-card-meta">${escapeHtml(metaText || model.file || "")}</div>
        `;

        grid.appendChild(card);
    });
}

async function selectModel(newModel) {
    if (!newModel || isGenerating || switchingModelName) return;

    const selector = $("model-selector");
    if (newModel === previousModel) {
        renderWelcomeModels();
        $("message-input")?.focus();
        return;
    }

    switchingModelName = newModel;
    renderWelcomeModels();
    if (selector) selector.disabled = true;
    $("model-status").textContent = "Switching...";

    try {
        await api("/models/switch", {
            method: "POST",
            body: JSON.stringify({ model: newModel })
        });

        previousModel = newModel;
        if (selector) selector.value = newModel;
        $("model-status").textContent = "Ready";
        await refreshContextMetrics(activeConversation, newModel);
    } catch (error) {
        $("model-status").textContent = "Error";
        if (selector) selector.value = previousModel;
        alert(`Failed to switch model: ${error.message}`);
    } finally {
        switchingModelName = null;
        if (selector) selector.disabled = false;
        renderWelcomeModels();
        $("message-input")?.focus();
    }
}

async function loadModels() {
    try {
        const data = await api("/models");
        const selector = $("model-selector");
        selector.replaceChildren();

        availableModels = Array.isArray(data.models) ? data.models : [];
        modelsDirPath = data.model_dir || "";

        if (availableModels.length > 0) {
            availableModels.forEach(model => {
                const option = document.createElement("option");
                option.value = model.name;

                let desc = model.name;
                if (model.role) {
                    desc += ` · ${model.role}`;
                }
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

        renderWelcomeModels();
    } catch (err) {
        console.warn("Could not load dynamic models list:", err);
    }
}

$("model-selector").addEventListener("change", async event => {
    if (isGenerating || switchingModelName) {
        event.target.value = previousModel;
        return;
    }

    await selectModel(event.target.value);
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
                <span class="hud-section-label">LOCAL MODELS</span>
                <span class="hud-section-hint" id="welcome-models-hint">Select a local model to load for this session</span>
            </div>

            <div class="model-card-grid" id="welcome-model-grid">
                <div class="model-card-empty">Discovering local models...</div>
            </div>
        </div>
    `;

    $("messages").appendChild(welcome);
    renderWelcomeModels();
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
                appendMessage(message.role, message.content, message.attachments || []);
            });
            scrollToBottom(true);
        }

        await loadConversations();
        refreshContextMetrics(id);
        showPage("chat");
        toggleSidebar(false);
    } catch (error) {
        alert(error.message);
    }
}

$("new-chat").addEventListener("click", () => {
    if (isGenerating) return;

    activeConversation = null;
    pendingAttachments = [];
    renderAttachmentStrip();
    clearMessages();
    loadConversations();
    refreshContextMetrics(null);
    showPage("chat");
    toggleSidebar(false);
    $("message-input").focus();
});


// --------------------------------------------------
// MESSAGE RENDERING
// --------------------------------------------------

function appendMessage(role, content = "", attachments = []) {
    const welcome = document.querySelector(".welcome");
    if (welcome) welcome.remove();

    const message = document.createElement("div");
    message.className = `message ${role}`;

    if (role === "system" && content.startsWith("[Conversation Compacted]")) {
        message.className = "message compaction-message";
        const summaryText = content.replace("[Conversation Compacted]", "").replace(/^Summary of earlier exchange:\s*/i, "").trim();
        const safeSummaryHtml = renderMarkdown(summaryText);

        message.innerHTML = `
            <div class="compaction-divider-bar">
                <div class="compaction-pill">
                    <svg viewBox="0 0 24 24" width="13" height="13" stroke="currentColor" stroke-width="2" fill="none"><polyline points="4 8 12 16 20 8"></polyline><line x1="4" y1="4" x2="20" y2="4"></line><line x1="4" y1="20" x2="20" y2="20"></line></svg>
                    <span>Conversation compacted · Earlier exchange summarized</span>
                    <button class="compaction-toggle-btn" type="button">Expand</button>
                </div>
            </div>
            <div class="compaction-card" style="display: none;">
                <div class="compaction-card-header">COMPACTED CONTEXT BRIEF</div>
                <div class="compaction-card-body">${safeSummaryHtml}</div>
            </div>
        `;
        $("messages").appendChild(message);
        scrollToBottom();
        return { messageEl: message, bodyEl: message, contentEl: message };
    }

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
    body.className = (role === "assistant" || role === "user") ? "message-body" : "message-content";

    let contentEl = body;
    if (role === "assistant") {
        const textDiv = document.createElement("div");
        textDiv.className = "message-content";
        textDiv.innerHTML = renderMarkdown(content);
        body.appendChild(textDiv);
        contentEl = textDiv;
    } else if (role === "user") {
        if (Array.isArray(attachments) && attachments.length > 0) {
            body.appendChild(createMessageAttachmentsEl(attachments));
        }
        if (content && content.trim()) {
            const textDiv = document.createElement("div");
            textDiv.className = "message-content";
            textDiv.textContent = content;
            body.appendChild(textDiv);
            contentEl = textDiv;
        }
    } else {
        body.textContent = content;
    }

    message.append(header, body);
    $("messages").appendChild(message);
    scrollToBottom();

    return { messageEl: message, bodyEl: body, contentEl };
}


// Event delegation for copying code blocks, tool data, and assistant messages
$("messages").addEventListener("click", (event) => {
    // 0.25. Toggle attachment preview box
    const attPill = event.target.closest(".msg-attachment-pill");
    if (attPill) {
        const item = attPill.closest(".msg-attachment-item");
        const box = item ? item.querySelector(".attachment-preview-box") : null;
        const chev = attPill.querySelector(".msg-att-chevron");
        if (box) {
            const isHidden = box.style.display === "none";
            box.style.display = isHidden ? "block" : "none";
            if (chev) chev.textContent = isHidden ? "▾" : "▸";
        }
        return;
    }

    // 0. Compaction card toggle
    const toggleBtn = event.target.closest(".compaction-toggle-btn");
    if (toggleBtn) {
        const msgWrap = toggleBtn.closest(".compaction-message");
        const card = msgWrap ? msgWrap.querySelector(".compaction-card") : null;
        if (card) {
            const isHidden = card.style.display === "none";
            card.style.display = isHidden ? "block" : "none";
            toggleBtn.textContent = isHidden ? "Collapse" : "Expand";
        }
        return;
    }

    // 0.5. Welcome model card click
    const modelCard = event.target.closest(".model-card");
    if (modelCard && modelCard.dataset.modelName) {
        selectModel(modelCard.dataset.modelName);
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
    const reqId = activeRequestId;
    if (abortController) {
        abortController.abort();
    }
    if (reqId) {
        fetch(API + "/chat/stop", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ request_id: reqId }),
            keepalive: true
        }).catch(() => {});
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

async function sendMessage(text, attachments = []) {
    const cleanText = (text || "").trim();
    const cleanAttachments = Array.isArray(attachments) ? attachments : [];
    if (isGenerating || (!cleanText && cleanAttachments.length === 0)) return;

    isGenerating = true;
    const isNewConversation = (activeConversation === null);

    setSendButtonMode(true);
    $("model-selector").disabled = true;
    $("new-chat").disabled = true;

    // Append user message
    appendMessage("user", text, cleanAttachments);

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

    const requestId = (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function")
        ? crypto.randomUUID()
        : `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const requestAbort = new AbortController();
    activeRequestId = requestId;
    abortController = requestAbort;

    try {
        const response = await fetch(API + "/chat/stream", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: text,
                conversation_id: activeConversation,
                model: model || undefined,
                request_id: requestId,
                attachments: cleanAttachments
            }),
            signal: requestAbort.signal
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

                // Auto-name new conversation from first user query or attachment
                if (isNewConversation && activeConversation) {
                    const previewTitle = cleanText
                        ? cleanText.replace(/\s+/g, " ").slice(0, 40)
                        : (`File: ${cleanAttachments[0]?.filename || "attachment"}`).slice(0, 40);
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

                // Real-time live gauge update during generation
                streamingOutputTokens += Math.max(1, Math.ceil((data.content || "").length / 4));
                if (currentContextMetrics) {
                    const liveUsed = currentContextMetrics.context_used + streamingOutputTokens;
                    const total = currentContextMetrics.context_size || 8192;
                    const livePct = Math.min(100, (liveUsed / total) * 100);
                    const circumference = 69.115;
                    const offset = circumference * (1 - livePct / 100);
                    const dialBar = $("gauge-bar");
                    if (dialBar) dialBar.style.strokeDashoffset = String(Math.max(0, offset));
                    const valEl = $("gauge-val");
                    if (valEl) valEl.textContent = `${formatTokens(liveUsed)} / ${formatTokens(total)}`;
                    const tagEl = $("gauge-percent-tag");
                    if (tagEl) tagEl.textContent = `${livePct.toFixed(0)}%`;
                }

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

            } else if (data.type === "context_metrics") {
                currentContextMetrics = data.context;
                updateContextGaugeUI(currentContextMetrics);

            } else if (data.type === "truncated") {
                const turn = getOrCreateAssistantTurn();
                const targetEl = turn.postToolEl || turn.preToolEl;
                const notice = "\n\n*[Output truncated at max response token limit — send \"continue\" to keep going.]*";
                if (targetEl) {
                    if (turn.hasTools) {
                        turn.postToolText += notice;
                        targetEl.innerHTML = renderMarkdown(turn.postToolText, false);
                    } else {
                        turn.preToolText += notice;
                        targetEl.innerHTML = renderMarkdown(turn.preToolText, false);
                    }
                }

            } else if (data.type === "error") {
                throw new Error(data.message || "Engine error during generation");

            } else if (data.type === "done") {
                $("model-status").textContent = "Ready";
                streamingOutputTokens = 0;
                refreshContextMetrics();
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
            let data;
            try {
                data = JSON.parse(dataStr);
            } catch (err) {
                console.warn("Failed to parse SSE JSON:", dataStr, err);
                return;
            }
            handleSSEData(data);
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
        if (error.name === "AbortError" || requestAbort.signal.aborted) {
            $("model-status").textContent = "Stopped";
            const turn = getOrCreateAssistantTurn();
            const stoppedNote = "*[Generation stopped]*";
            if (turn.hasTools && turn.postToolEl) {
                turn.postToolText += `\n\n${stoppedNote}`;
                turn.postToolEl.innerHTML = renderMarkdown(turn.postToolText, false);
            } else if (turn.preToolEl) {
                turn.preToolText += `\n\n${stoppedNote}`;
                turn.preToolEl.innerHTML = renderMarkdown(turn.preToolText, false);
            } else {
                turn.preToolEl = document.createElement("div");
                turn.preToolEl.className = "message-content pre-tool-content";
                turn.preToolText = stoppedNote;
                turn.preToolEl.innerHTML = renderMarkdown(stoppedNote, false);
                turn.bodyEl.appendChild(turn.preToolEl);
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
        streamingOutputTokens = 0;
        if (abortController === requestAbort) {
            abortController = null;
        }
        if (activeRequestId === requestId) {
            activeRequestId = null;
        }

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
        refreshContextMetrics();

        const input = $("message-input");
        input.focus();
    }
}


// --------------------------------------------------
// ATTACHMENTS & OCR FILE PIPELINE
// --------------------------------------------------

function getAttachmentBadge(att) {
    const method = (att?.method || "").toLowerCase();
    const fmt = (att?.format || "file").toLowerCase();
    if (method === "ocr") return `${fmt === "pdf" ? "PDF·" : ""}OCR`;
    if (method === "hybrid-ocr") return "PDF·HYBRID";
    return fmt.toUpperCase();
}

function formatAttachmentMeta(att) {
    if (!att) return "";
    const parts = [];
    const meta = att.meta || {};
    if (meta.pages) parts.push(`${meta.pages}p`);
    if (meta.slides) parts.push(`${meta.slides}s`);
    if (att.estimated_tokens) parts.push(`~${formatTokens(att.estimated_tokens)} tok`);
    else if (att.char_count) parts.push(`${att.char_count} ch`);
    return parts.join(" · ");
}

function createMessageAttachmentsEl(attachments) {
    const wrap = document.createElement("div");
    wrap.className = "message-attachments";

    attachments.forEach((att) => {
        const item = document.createElement("div");
        item.className = "msg-attachment-item";

        const badge = getAttachmentBadge(att);
        const metaStr = formatAttachmentMeta(att);

        const pill = document.createElement("button");
        pill.type = "button";
        pill.className = "msg-attachment-pill";
        pill.title = "Click to inspect extracted text";
        pill.innerHTML = `
            <span class="att-badge">${escapeHtml(badge)}</span>
            <span class="att-name">${escapeHtml(att.filename || "attachment")}</span>
            ${metaStr ? `<span class="att-meta">${escapeHtml(metaStr)}</span>` : ""}
            <span class="msg-att-chevron">▸</span>
        `;

        const preview = document.createElement("pre");
        preview.className = "attachment-preview-box";
        preview.style.display = "none";
        preview.textContent = att.text || "[No extracted text]";

        item.append(pill, preview);
        wrap.appendChild(item);
    });

    return wrap;
}

function hasExtractingAttachments() {
    return pendingAttachments.some(a => a.status === "extracting");
}

function renderAttachmentStrip() {
    const strip = $("attachment-strip");
    if (!strip) return;

    if (pendingAttachments.length === 0) {
        strip.style.display = "none";
        strip.replaceChildren();
        return;
    }

    strip.style.display = "flex";
    strip.replaceChildren();

    pendingAttachments.forEach((entry) => {
        const chip = document.createElement("div");
        chip.className = `attachment-chip ${entry.status}`;

        let badgeText = "FILE";
        let metaText = "Extracting...";
        if (entry.status === "ready" && entry.data) {
            badgeText = getAttachmentBadge(entry.data);
            metaText = formatAttachmentMeta(entry.data) || "Ready";
        } else if (entry.status === "error") {
            badgeText = "ERR";
            metaText = entry.error || "Failed";
        } else {
            const ext = (entry.filename || "").split(".").pop();
            if (ext && ext !== entry.filename) badgeText = ext.toUpperCase();
        }

        const badge = document.createElement("span");
        badge.className = "att-badge";
        badge.textContent = badgeText;

        const name = document.createElement("span");
        name.className = "att-name";
        name.textContent = entry.filename;
        name.title = entry.filename;

        const meta = document.createElement("span");
        meta.className = "att-meta";
        meta.textContent = metaText;
        if (entry.error) meta.title = entry.error;

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "att-remove-btn";
        removeBtn.textContent = "×";
        removeBtn.title = "Remove attachment";
        removeBtn.addEventListener("click", () => {
            pendingAttachments = pendingAttachments.filter(item => item.id !== entry.id);
            renderAttachmentStrip();
        });

        chip.append(badge, name, meta, removeBtn);
        strip.appendChild(chip);
    });
}

async function uploadAndExtractFile(file) {
    if (!file) return;
    if (pendingAttachments.length >= 10) {
        alert("Maximum 10 attachments per message.");
        return;
    }

    const entry = {
        id: nextAttachmentSeq++,
        filename: file.name || "attachment",
        status: "extracting",
        data: null,
        error: null
    };
    pendingAttachments.push(entry);
    renderAttachmentStrip();

    try {
        const formData = new FormData();
        formData.append("file", file, entry.filename);

        const response = await fetch(API + "/files/extract", {
            method: "POST",
            body: formData
        });

        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || `HTTP ${response.status}`);
        }

        entry.status = "ready";
        entry.data = payload;
    } catch (err) {
        entry.status = "error";
        entry.error = err.message || "Extraction failed";
    } finally {
        renderAttachmentStrip();
    }
}

function handleSelectedFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    Array.from(fileList).forEach(file => uploadAndExtractFile(file));
}

$("attach-button")?.addEventListener("click", () => {
    if (isGenerating) return;
    $("file-input")?.click();
});

$("file-input")?.addEventListener("change", (event) => {
    const files = event.target.files;
    if (files && files.length > 0) {
        handleSelectedFiles(files);
    }
    event.target.value = "";
});

const MIME_EXTENSION_MAP = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "application/pdf": "pdf",
    "text/markdown": "md",
    "text/plain": "txt",
    "text/csv": "csv",
    "text/tab-separated-values": "tsv",
    "text/html": "html",
    "application/json": "json",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/epub+zip": "epub"
};

function normalizeClipboardFile(file, index = 0) {
    if (!file) return null;
    const rawName = (file.name || "").trim();
    const mime = (file.type || "").split(";")[0].trim().toLowerCase();
    const mappedExt = MIME_EXTENSION_MAP[mime] || (mime.startsWith("image/") ? mime.split("/")[1].replace("jpeg", "jpg") : "");

    const isGenericName = rawName === "" || rawName === "blob" || rawName === "image.png";
    if (!isGenericName && (rawName.includes(".") || !mappedExt)) {
        return file;
    }

    const stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
    const suffix = index > 0 ? `-${index + 1}` : "";
    const ext = mappedExt || (rawName.includes(".") ? rawName.split(".").pop() : "bin");
    const prefix = mime.startsWith("image/") ? "pasted-image" : "pasted-file";
    const finalName = !isGenericName ? `${rawName}.${ext}` : `${prefix}-${stamp}${suffix}.${ext}`;
    return new File([file], finalName, { type: file.type || "application/octet-stream" });
}

function extractClipboardFiles(clipboardData) {
    if (!clipboardData) return [];

    const rawFiles = [];
    const seen = new Set();
    const pushUnique = (f) => {
        if (!f) return;
        const key = `${f.name || ""}:${f.size}:${f.type || ""}`;
        if (seen.has(key)) return;
        seen.add(key);
        rawFiles.push(f);
    };

    if (clipboardData.items) {
        for (const item of clipboardData.items) {
            if (item.kind === "file") {
                pushUnique(item.getAsFile());
            }
        }
    }

    if (clipboardData.files) {
        for (const file of clipboardData.files) {
            pushUnique(file);
        }
    }

    return rawFiles
        .map((f, idx) => normalizeClipboardFile(f, idx))
        .filter(Boolean);
}

document.addEventListener("paste", (event) => {
    if (activePage !== "chat") return;

    const pastedFiles = extractClipboardFiles(event.clipboardData);
    if (pastedFiles.length === 0) return;

    // Prevent browsers from also inserting image placeholders or file URI strings into textareas
    event.preventDefault();
    if (isGenerating) return;

    handleSelectedFiles(pastedFiles);
    $("message-input")?.focus();
});

const chatDropZone = $("chat-page") || document.body;
chatDropZone.addEventListener("dragenter", (event) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
    dragDepth++;
    $("chat-form")?.classList.add("composer-drag-over");
});

chatDropZone.addEventListener("dragover", (event) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
});

chatDropZone.addEventListener("dragleave", (event) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) {
        $("chat-form")?.classList.remove("composer-drag-over");
    }
});

chatDropZone.addEventListener("drop", (event) => {
    const files = event.dataTransfer?.files;
    dragDepth = 0;
    $("chat-form")?.classList.remove("composer-drag-over");
    if (!files || files.length === 0) return;
    event.preventDefault();
    if (!isGenerating) {
        handleSelectedFiles(files);
    }
});


// --------------------------------------------------
// INPUT & KEYBOARD
// --------------------------------------------------

$("chat-form").addEventListener("submit", async event => {
    event.preventDefault();

    if (isGenerating) return;

    if (hasExtractingAttachments()) {
        alert("Please wait for file extraction/OCR to finish.");
        return;
    }

    const input = $("message-input");
    const text = input.value.trim();
    const readyAttachments = pendingAttachments
        .filter(a => a.status === "ready" && a.data)
        .map(a => a.data);

    if (!text && readyAttachments.length === 0) return;

    input.value = "";
    input.style.height = "auto";
    pendingAttachments = [];
    renderAttachmentStrip();

    await sendMessage(text, readyAttachments);
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
// CONTEXT GAUGE & SUMMARIZE EVENT LISTENERS
// --------------------------------------------------

$("context-gauge-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleContextGaugePopup();
});

$("topbar-context-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleContextGaugePopup();
});

$("summarize-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    handleSummarizeConversation();
});

// Dismiss popup on outside click
document.addEventListener("click", (e) => {
    const popup = $("context-gauge-popup");
    if (!popup || popup.style.display === "none") return;

    if (!popup.contains(e.target) &&
        !e.target.closest("#context-gauge-btn") &&
        !e.target.closest("#topbar-context-btn")) {
        popup.style.display = "none";
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
        await refreshContextMetrics();

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
