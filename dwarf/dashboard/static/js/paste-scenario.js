// Paste-a-scenario tile on /operate/scenarios. POSTs raw YAML to
// /api/scenario/paste; surfaces validation result inline.

const textarea = document.querySelector("#paste-scenario-yaml");
const output = document.querySelector("[data-paste-output]");
const submit = document.querySelector("[data-paste-submit]");
const clear = document.querySelector("[data-paste-clear]");
const token = document.querySelector("#paste-scenario-token");

function showOutput(report, ok) {
  if (!output) return;
  output.hidden = false;
  output.dataset.state = ok ? "ok" : "error";
  if (typeof report === "string") {
    output.textContent = report;
  } else {
    output.textContent = JSON.stringify(report, null, 2);
  }
}

async function submitPaste() {
  if (!textarea) return;
  const yaml = textarea.value;
  if (!yaml.trim()) {
    showOutput("Paste some YAML first.", false);
    return;
  }
  submit.disabled = true;
  try {
    const endpoint = submit.dataset.endpoint;
    if (!endpoint) throw new Error("Paste endpoint is not configured.");
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: new URLSearchParams({
        token: token ? token.value : "",
        scenario_yaml: yaml,
      }),
    });
    let report;
    try { report = await response.json(); } catch { report = await response.text(); }
    showOutput(report, response.ok);
  } catch (err) {
    showOutput(`[error] ${String(err)}`, false);
  } finally {
    submit.disabled = false;
  }
}

if (submit) submit.addEventListener("click", submitPaste);
if (clear) clear.addEventListener("click", () => {
  if (textarea) textarea.value = "";
  if (output) { output.hidden = true; output.textContent = ""; }
});
