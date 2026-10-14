// /operate/compare runner — scenario picker + SSE-streamed run + result panel.
//
// Pure DOM wiring. No external deps. The SSE endpoint accepts a query-free
// body-authenticated POST so dashboard credentials stay out of URLs.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function setSelected(row) {
  $$(".scenario-picker__row").forEach((r) => r.setAttribute("aria-selected", "false"));
  row.setAttribute("aria-selected", "true");
  const id = row.dataset.id;
  const path = row.dataset.path;
  const family = row.dataset.family || "—";
  const runtime = row.dataset.runtime || "—";
  $("[data-run-card-id]").textContent = id;
  $("[data-run-card-family]").textContent = family;
  $("[data-run-card-runtime]").textContent = runtime;
  const cmd = `cardano-profile compare ${path}`;
  const pre = $("#compare-command");
  pre.textContent = cmd;
  pre.dataset.commandTemplate = cmd;
  const available = row.dataset.available === "true";
  const availability = $("[data-run-availability]");
  if (availability) {
    availability.textContent = available
      ? "Requirements available. Ready to compare."
      : row.dataset.unavailableReason || "This scenario is unavailable.";
  }
  $("[data-run-compare]").disabled = !available;
  $("[data-run-compare]").dataset.path = path;
}

function applyFilters() {
  const activePill = $(".scenario-picker .pill[aria-pressed='true']");
  const family = activePill ? activePill.dataset.family : "";
  const term = ($("[data-picker-search]").value || "").trim().toLowerCase();
  let visible = 0;
  $$(".scenario-picker__row").forEach((row) => {
    const familyMatch = !family || row.dataset.family === family;
    const text = `${row.dataset.id} ${row.dataset.title || ""}`.toLowerCase();
    const termMatch = !term || text.includes(term);
    const show = familyMatch && termMatch;
    row.hidden = !show;
    if (show) visible += 1;
  });
  const counter = $("[data-picker-count]");
  if (counter) counter.textContent = `${visible} scenarios`;
}

function bindPickerFilters() {
  $$(".scenario-picker .pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      $$(".scenario-picker .pill").forEach((p) => p.setAttribute("aria-pressed", "false"));
      pill.setAttribute("aria-pressed", "true");
      applyFilters();
    });
  });
  const search = $("[data-picker-search]");
  if (search) search.addEventListener("input", applyFilters);
}

function bindPickerSelection() {
  $$(".scenario-picker__row").forEach((row) => {
    row.addEventListener("click", () => setSelected(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        setSelected(row);
      }
    });
  });
}

function bindCommandToggle() {
  const button = $("[data-toggle-command]");
  const pre = $("#compare-command");
  if (!button || !pre) return;
  button.addEventListener("click", () => {
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", expanded ? "false" : "true");
    pre.hidden = expanded;
    button.textContent = expanded ? "Show command" : "Hide command";
  });
}

function setOutputState(state) {
  const el = $("[data-output-state]");
  if (!el) return;
  el.textContent = state;
  el.dataset.state = state;
}

export function comparisonTerminalState(result, exitCode) {
  if (result === "AGREED" || result === "DIVERGED") return "done";
  if (result === "INCOMPLETE") return "incomplete";
  return exitCode === 0 ? "done" : "incomplete";
}

function appendOutput(line) {
  const code = $("[data-output-code]");
  if (!code) return;
  if (code.dataset.fresh !== "1") {
    code.textContent = "";
    code.dataset.fresh = "1";
  }
  code.textContent += line + "\n";
  const pre = $("[data-output-pre]");
  if (pre) pre.scrollTop = pre.scrollHeight;
}

function showResult(payload) {
  const section = $("[data-run-result]");
  if (!section) return;
  section.hidden = false;
  const pill = $("[data-result-pill]");
  if (pill) {
    const result = payload.result || (payload.exit_code === 0 ? "AGREED" : "INCOMPLETE");
    if (result === "AGREED") {
      pill.className = "result-pill result-pill--pass";
      pill.textContent = "AGREED";
    } else if (result === "DIVERGED") {
      pill.className = "result-pill result-pill--fail";
      pill.textContent = "DIVERGED";
    } else {
      pill.className = "result-pill result-pill--error";
      pill.textContent = "INCOMPLETE";
    }
  }
  const link = $("[data-result-bundle]");
  if (link && payload.bundle_url) {
    link.href = payload.bundle_url;
    link.textContent = payload.bundle_url;
  }
}

function parseTrailerEvent(line) {
  // Server sends `event: done\ndata: {"agreed":true,"bundle_url":"/runs/..."}`
  // We track the most-recent event tag and apply it to the next data line.
  const trimmed = line.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("event: ")) return { type: "event", value: trimmed.slice(7) };
  if (trimmed.startsWith("data: ")) return { type: "data", value: trimmed.slice(6) };
  return null;
}

async function streamRun(button) {
  const path = button.dataset.path;
  if (!path) return;
  setOutputState("running");
  const code = $("[data-output-code]");
  if (code) {
    code.textContent = "";
    code.dataset.fresh = "1";
  }
  const result = $("[data-run-result]");
  if (result) result.hidden = true;
  button.disabled = true;

  const token = $("[data-compare-token]")?.value || "";
  if (!token) {
    appendOutput("Dashboard token is unavailable. Reload the page and try again.");
    setOutputState("error");
    button.disabled = false;
    return;
  }
  const body = new URLSearchParams({ token, path });
  let pendingEvent = null;
  let observedResult = null;
  try {
    const response = await fetch("/api/scenario/compare", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok) {
      appendOutput(`HTTP ${response.status}: ${await response.text()}`);
      setOutputState("error");
      button.disabled = false;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\n")) {
          const parsed = parseTrailerEvent(line);
          if (!parsed) continue;
          if (parsed.type === "event") {
            pendingEvent = parsed.value;
            continue;
          }
          if (parsed.type === "data") {
            if (pendingEvent === "done") {
              try {
                const donePayload = JSON.parse(parsed.value);
                showResult({ ...donePayload, result: observedResult });
                setOutputState(comparisonTerminalState(observedResult, donePayload.exit_code));
              } catch {
                appendOutput("[done]");
              }
              pendingEvent = null;
              continue;
            }
            const resultMatch = parsed.value.match(/^Result: (AGREED|DIVERGED|INCOMPLETE)$/);
            if (resultMatch) observedResult = resultMatch[1];
            appendOutput(parsed.value);
          }
        }
      }
    }
    if ($("[data-output-state]")?.dataset.state === "running") setOutputState("done");
  } catch (err) {
    appendOutput(`[error] ${String(err)}`);
    setOutputState("error");
  } finally {
    button.disabled = false;
  }
}

function bindRunner() {
  const button = $("[data-run-compare]");
  if (!button) return;
  button.addEventListener("click", () => streamRun(button));
}

if (typeof document !== "undefined") {
  bindPickerFilters();
  bindPickerSelection();
  bindCommandToggle();
  bindRunner();
}
