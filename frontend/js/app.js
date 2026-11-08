// This script is AI-written, has not been verified, and is therefore flagged for verification.

const API = "/api";

let activeConversation = null;
let activePage = "chat";
let isGenerating = false;
let systemTimer = null;
let activeToolActivities = new Map();

const $ = (id) => document.getElementById(id);


// --------------------------------------------------
// API HELPERS
// --------------------------------------------------

async function api(path, options = {}) {

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

    return response.json();
}


// --------------------------------------------------
// NAVIGATION
// --------------------------------------------------

function showPage(page) {

    activePage = page;

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
    } else {
        clearInterval(systemTimer);
        systemTimer = null;
    }
}

document.querySelectorAll(".nav-item").forEach(button => {

    button.addEventListener("click", () => {
        showPage(button.dataset.page);
    });

});


// --------------------------------------------------
// CONVERSATIONS
// --------------------------------------------------

async function loadConversations() {

    const conversations = await api("/conversations");

    const container = $("conversation-list");
    container.replaceChildren();

    conversations.forEach(conversation => {

        const row = document.createElement("div");
        row.className = "conversation-item";

        const select = document.createElement("button");
        select.className = "conversation-select";

        select.textContent =
            conversation.title || "New conversation";

        select.classList.toggle(
            "active",
            conversation.id === activeConversation
        );

        select.addEventListener("click", () => {
            openConversation(conversation.id);
        });

        const remove = document.createElement("button");
        remove.className = "conversation-delete";
        remove.textContent = "×";
        remove.title = "Delete conversation";

        remove.addEventListener("click", async () => {

            if (!confirm("Delete this conversation?")) return;

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

        row.append(select, remove);
        container.appendChild(row);

    });
}


function clearMessages() {

    $("messages").replaceChildren();

    const welcome = document.createElement("div");
    welcome.className = "welcome";

    const title = document.createElement("h1");
    title.textContent = "What can I help with?";

    const subtitle = document.createElement("p");
    subtitle.textContent = "Your local assistant. Ready when you are.";

    welcome.append(title, subtitle);
    $("messages").appendChild(welcome);
}


async function openConversation(id) {

    try {

        const conversation = await api(`/conversations/${id}`);

        activeConversation = id;

        const container = $("messages");
        container.replaceChildren();

        conversation.messages.forEach(message => {
            appendMessage(message.role, message.content);
        });

        await loadConversations();

        showPage("chat");

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

});


// --------------------------------------------------
// MESSAGE RENDERING
// --------------------------------------------------

function appendMessage(role, content = "") {

    const welcome = document.querySelector(".welcome");

    if (welcome) {
        welcome.remove();
    }

    const message = document.createElement("div");
    message.className = `message ${role}`;

    const label = document.createElement("div");
    label.className = "message-role";
    label.textContent = role === "user" ? "You" : "PAIN";

    const body = document.createElement("div");
    body.className = "message-content";
    body.textContent = content;

    message.append(label, body);

    $("messages").appendChild(message);

    $("messages").scrollTop = $("messages").scrollHeight;

    return body;
}


// --------------------------------------------------
// TOOL ACTIVITY
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


function appendToolActivity(name, args = {}, callId = null) {
    const welcome = document.querySelector(".welcome");
    if (welcome) welcome.remove();

    const activity = document.createElement("details");
    activity.className = "tool-activity";
    activity.open = true;

    const summary = document.createElement("summary");
    summary.className = "tool-activity-summary";

    const indicator = document.createElement("span");
    indicator.className = "tool-activity-indicator running";

    const title = document.createElement("span");
    title.className = "tool-activity-title";
    title.textContent = `Using ${name || "tool"}`;

    const state = document.createElement("span");
    state.className = "tool-activity-state";
    state.textContent = "Running";

    summary.append(indicator, title, state);

    const body = document.createElement("div");
    body.className = "tool-activity-body";

    const argsLabel = document.createElement("div");
    argsLabel.className = "tool-activity-label";
    argsLabel.textContent = "Arguments";

    const argsContent = document.createElement("pre");
    argsContent.className = "tool-activity-data";
    argsContent.textContent = formatToolData(args);

    const resultLabel = document.createElement("div");
    resultLabel.className = "tool-activity-label tool-result-label";
    resultLabel.textContent = "Result";

    const resultContent = document.createElement("pre");
    resultContent.className = "tool-activity-data";
    resultContent.textContent = "Waiting for tool result...";

    body.append(argsLabel, argsContent, resultLabel, resultContent);
    activity.append(summary, body);

    const container = $("messages");
    container.appendChild(activity);
    container.scrollTop = container.scrollHeight;

    const key = callId || `tool-${Date.now()}-${activeToolActivities.size}`;
    activeToolActivities.set(key, {
        activity, indicator, state, resultContent
    });

    return key;
}


function completeToolActivity(callId, result, error = null) {
    const entry = activeToolActivities.get(callId);

    // If the backend omitted a call ID, complete the most recent pending tool.
    const target = entry || [...activeToolActivities.values()].reverse().find(
        item => item.state.textContent === "Running"
    );

    if (!target) return;

    const failed = Boolean(error) || (
        result && typeof result === "object" && result.ok === false
    );

    target.indicator.classList.remove("running");
    target.indicator.classList.add(failed ? "failed" : "completed");
    target.state.textContent = failed ? "Failed" : "Completed";
    target.resultContent.textContent = error
        ? String(error)
        : formatToolData(result);

    if (callId) activeToolActivities.delete(callId);
}


// --------------------------------------------------
// STREAMING CHAT
// --------------------------------------------------

async function sendMessage(text) {

    if (isGenerating || !text.trim()) return;

    isGenerating = true;

    $("send-button").disabled = true;

    appendMessage("user", text);

    const assistantBody = appendMessage("assistant", "");
    activeToolActivities = new Map();

    const model = $("model-selector").value;

    $("model-status").textContent = "Generating...";

    try {

        const response = await fetch(API + "/chat/stream", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                message: text,
                conversation_id: activeConversation,
                model: model
            })

        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        let buffer = "";

        while (true) {

            const { value, done } = await reader.read();

            if (done) break;

            buffer += decoder.decode(value, {
                stream: true
            });

            const events = buffer.split("\n\n");

            buffer = events.pop() || "";

            for (const event of events) {

                const line = event
                    .split("\n")
                    .find(item => item.startsWith("data:"));

                if (!line) continue;

                const data = JSON.parse(
                    line.slice(5).trim()
                );

                if (data.type === "start") {

                    activeConversation = data.conversation_id;

                } else if (
                    data.type === "tool_start" ||
                    (data.type === "tool" && data.phase === "start")
                ) {

                    appendToolActivity(
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

                    assistantBody.textContent += data.content;

                    $("messages").scrollTop =
                        $("messages").scrollHeight;

                } else if (data.type === "error") {

                    throw new Error(data.message);

                } else if (data.type === "done") {

                    $("model-status").textContent = "Ready";

                }

            }

        }

        await loadConversations();

    } catch (error) {

        assistantBody.textContent +=
            `\n\n[Error: ${error.message}]`;

        $("model-status").textContent = "Error";

    } finally {

        isGenerating = false;

        $("send-button").disabled = false;

        $("message-input").focus();

    }
}


// --------------------------------------------------
// INPUT
// --------------------------------------------------

$("chat-form").addEventListener("submit", async event => {

    event.preventDefault();

    const input = $("message-input");
    const text = input.value.trim();

    if (!text) return;

    input.value = "";
    input.style.height = "auto";

    await sendMessage(text);

});


$("message-input").addEventListener("keydown", event => {

    if (
        event.key === "Enter" &&
        !event.shiftKey &&
        !event.isComposing
    ) {

        event.preventDefault();
        $("chat-form").requestSubmit();

    }

});


$("message-input").addEventListener("input", event => {

    const input = event.target;

    input.style.height = "auto";

    input.style.height =
        Math.min(input.scrollHeight, 180) + "px";

});


// --------------------------------------------------
// MODEL SWITCHING
// --------------------------------------------------

$("model-selector").addEventListener("change", async event => {

    const model = event.target.value;

    $("model-status").textContent = "Switching...";

    try {

        await api("/models/switch", {
            method: "POST",
            body: JSON.stringify({ model })
        });

        $("model-status").textContent = "Ready";

    } catch (error) {

        $("model-status").textContent = "Error";
        alert(error.message);

    }

});


// --------------------------------------------------
// SYSTEM MONITORING
// --------------------------------------------------

function formatBytes(bytes) {

    if (bytes === null || bytes === undefined) {
        return "Unavailable";
    }

    const units = ["B", "KB", "MB", "GB", "TB"];

    let value = bytes;
    let index = 0;

    while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
    }

    return `${value.toFixed(2)} ${units[index]}`;
}


function setMetric(name, value, details) {

    $(`${name}-value`).textContent = value;

    $(`${name}-details`).textContent = details;
}


function setBar(name, value) {

    const bar = $(`${name}-bar`);

    if (value === null || value === undefined) {
        bar.style.width = "0%";
        return;
    }

    bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
}


async function refreshSystem() {

    try {

        const data = await api("/system");

        // CPU
        const cpu = data.cpu;

        setMetric(
            "cpu",
            `${cpu.usage_percent.toFixed(1)}%`,
            `${cpu.core_count} logical cores · ${
                cpu.physical_cores ?? "?"
            } physical cores · ${
                cpu.frequency_mhz
                    ? `${cpu.frequency_mhz.toFixed(0)} MHz`
                    : "Frequency unavailable"
            }`
        );

        setBar("cpu", cpu.usage_percent);


        // RAM
        const ram = data.memory;

        setMetric(
            "ram",
            `${ram.usage_percent.toFixed(1)}%`,
            `${formatBytes(ram.used_bytes)} used of ${
                formatBytes(ram.total_bytes)
            } · ${formatBytes(ram.available_bytes)} available`
        );

        setBar("ram", ram.usage_percent);


        // GPU
        const gpu = data.gpu[0];

        if (gpu) {

            setMetric(
                "gpu",
                gpu.utilization_percent === null
                    ? "N/A"
                    : `${gpu.utilization_percent}%`,
                `${gpu.name} · ${
                    gpu.temperature_c === null
                        ? "Temperature unavailable"
                        : `${gpu.temperature_c.toFixed(1)} °C`
                }`
            );

            setBar("gpu", gpu.utilization_percent);

            // VRAM / shared graphics memory
            if (gpu.vram_total_bytes) {

                const used = gpu.vram_used_bytes ?? 0;

                setMetric(
                    "vram",
                    `${(used / gpu.vram_total_bytes * 100).toFixed(1)}%`,
                    `${formatBytes(used)} / ${
                        formatBytes(gpu.vram_total_bytes)
                    }`
                );

                setBar(
                    "vram",
                    used / gpu.vram_total_bytes * 100
                );

            } else if (gpu.gtt_total_bytes) {

                const used = gpu.gtt_used_bytes ?? 0;

                setMetric(
                    "vram",
                    "Shared",
                    `${formatBytes(used)} / ${
                        formatBytes(gpu.gtt_total_bytes)
                    } GTT`
                );

                setBar(
                    "vram",
                    used / gpu.gtt_total_bytes * 100
                );

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
        const disk = data.disk;

        setMetric(
            "disk",
            `${disk.usage_percent.toFixed(1)}%`,
            `${formatBytes(disk.used_bytes)} used of ${
                formatBytes(disk.total_bytes)
            } · ${formatBytes(disk.free_bytes)} free`
        );

        setBar("disk", disk.usage_percent);


        // SWAP
        const swap = data.swap;

        setMetric(
            "swap",
            `${swap.usage_percent.toFixed(1)}%`,
            `${formatBytes(swap.used_bytes)} used of ${
                formatBytes(swap.total_bytes)
            }`
        );

        setBar("swap", swap.usage_percent);


        // NETWORK
        const network = data.network;

        $("download-value").textContent =
            `${formatBytes(network.download_bytes_per_sec)}/s`;

        $("upload-value").textContent =
            `${formatBytes(network.upload_bytes_per_sec)}/s`;


        // HARDWARE
        const info = $("hardware-info");
        info.replaceChildren();

        const rows = [
            ["CPU cores", cpu.core_count],
            ["CPU frequency", cpu.frequency_mhz
                ? `${cpu.frequency_mhz.toFixed(0)} MHz`
                : "Unavailable"],
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


        $("last-updated").textContent =
            `Updated ${new Date().toLocaleTimeString()}`;

    } catch (error) {

        $("last-updated").textContent =
            `Monitoring error: ${error.message}`;

    }

}


// --------------------------------------------------
// INITIALIZATION
// --------------------------------------------------

async function initialize() {

    try {

        await api("/health");

        $("connection-status").textContent = "Connected";

        await loadConversations();

        const status = await api("/models/status");

        if (status.inference.running) {
            $("model-status").textContent = "Loaded";
        } else {
            $("model-status").textContent = "Idle";
        }

    } catch (error) {

        $("connection-status").textContent = "Disconnected";

        console.error(error);

    }

}

initialize();