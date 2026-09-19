const root = document.querySelector('.product-landing__status');
const wordmark = document.querySelector('[data-landing-wordmark]');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

async function animateWordmark() {
  if (!wordmark || reducedMotion.matches) return;
  const letters = 'DWARF';
  while (!reducedMotion.matches) {
    wordmark.textContent = '';
    wordmark.dataset.phase = 'typing';
    for (const letter of letters) {
      wordmark.textContent += letter;
      await wait(80);
    }
    await wait(1200);
    wordmark.dataset.phase = 'dim';
    await wait(600);
    await wait(2000);
    wordmark.dataset.phase = 'fade';
    await wait(400);
    wordmark.textContent = '';
    wordmark.dataset.phase = 'reset';
    await wait(500);
  }
  wordmark.textContent = letters;
  wordmark.dataset.phase = 'static';
}

function updatePill(name, state, value, detail) {
  const pill = root?.querySelector(`[data-status="${name}"]`);
  if (!pill) return;
  pill.dataset.state = state;
  const strong = pill.querySelector('strong');
  const small = pill.querySelector('small');
  if (strong) strong.textContent = value;
  if (small) small.textContent = detail;
}

async function refreshFramework() {
  try {
    const response = await fetch(root.dataset.frameworkUrl, {headers: {'Accept': 'application/json'}});
    const payload = await response.json();
    const ready = response.ok && payload.status === 'ok';
    updatePill('framework', ready ? 'ready' : 'unhealthy', ready ? 'Ready' : 'Needs attention', 'Dashboard service');
  } catch (error) {
    updatePill('framework', 'unknown', 'Unknown', 'Health request failed');
  }
}

async function fetchTopology(url) {
  const response = await fetch(url, {headers: {'Accept': 'application/json'}});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function refreshTopology() {
  try {
    let payload = await fetchTopology(root.dataset.topologyUrl);
    updatePill('topology', payload.state, payload.state === 'checking' ? 'Checking' : payload.state, 'Cardano + Amaru');
    while (payload.state === 'checking') {
      await wait(500);
      payload = await fetchTopology('/api/topology/health');
    }
    const detail = payload.cached
      ? `Cached · ${payload.age_seconds ?? 'unknown'}s old`
      : (payload.reason_code || 'Cardano + Amaru');
    updatePill('topology', payload.state, payload.state, detail);
  } catch (error) {
    updatePill('topology', 'unknown', 'Unknown', 'Topology check failed');
  }
}

if (root) {
  refreshFramework();
  refreshTopology();
}
animateWordmark();
