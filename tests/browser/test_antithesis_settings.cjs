const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dwarf-settings-test-'));
const server = spawn('python3', ['-u', '-c', `
from http.server import ThreadingHTTPServer
from profile_manager import dashboard
from profile_manager.config import DeploymentConfig, save_config
def forbidden(*args, **kwargs):
    raise AssertionError('Settings must not execute commands')
dashboard._cli_command = forbidden
save_config(DeploymentConfig.from_dict({'host': 'preserved-host', 'moog': {
    'enabled': True, 'github_repo': 'example/original', 'antithesis_user': 'stored-user',
    'github_pat': 'test-existing-pat', 'antithesis_password': 'test-existing-password'}}))
server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.serve_dashboard_handler_factory('settings-test-token'))
print(server.server_port, flush=True)
server.serve_forever()
`], {cwd: root, env: {...process.env, PYTHONPATH: '.:dwarf',
  ADA2_PROFILE_MANAGER_CONFIG: path.join(stateDir, 'config.yaml'),
  ADA2_DWARF_STATE_DIR: stateDir, ADA2_DWARF_RUNS_DIR: path.join(stateDir, 'runs'),
  ADA2_DWARF_BUNDLES_DIR: path.join(stateDir, 'bundles'), MOOG_ANTITHESIS_USER: 'environment-user'},
  stdio: ['ignore', 'pipe', 'pipe']});
let stderr = '';
server.stderr.on('data', data => { stderr += data; });
(async () => {
  let browser;
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Server startup timeout')), 20000);
      server.once('error', error => {clearTimeout(timer); reject(error);});
      server.once('exit', code => {clearTimeout(timer); reject(new Error(`Server exited ${code}: ${stderr}`));});
      server.stdout.once('data', data => {clearTimeout(timer); resolve(Number(data.toString().trim()));});
    });
    const origin = `http://127.0.0.1:${port}`;
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], mutations = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() !== 'GET') mutations.push(new URL(request.url()).pathname); });
    const form = page.locator('#antithesis-settings-form');
    const field = name => form.locator(`[name="${name}"]`);
    const button = page.locator('#antithesis-save-settings');
    const waitState = expected => page.waitForFunction(expected => document.querySelector('#antithesis-settings-state').dataset.state === expected, expected);
    const config = () => JSON.parse(fs.readFileSync(path.join(stateDir, 'config.yaml')));
    const response = await page.goto(origin + '/run/antithesis');
    const html = await response.text();
    assert.equal(await form.isVisible(), false);
    await page.locator('#antithesis-settings-heading').click();
    assert.equal(await form.isVisible(), true);
    assert.ok(!html.includes('test-existing-password') && !html.includes('test-existing-pat'));
    assert.equal(await field('antithesis_user').inputValue(), 'environment-user');
    assert.equal(await field('antithesis_user').isDisabled(), true);
    assert.equal(await field('antithesis_password').inputValue(), '');
    assert.match(await field('antithesis_password').getAttribute('placeholder'), /configured/);
    await form.getByText('GitHub target', {exact: true}).click();
    assert.equal(await field('github_repo').inputValue(), 'example/original');
    await page.locator('#antithesis-policy').selectOption('exact');
    await page.locator('#antithesis-duration').fill('7');
    const scenario = await page.locator('#antithesis-scenario option').nth(1).getAttribute('value');
    await page.locator('#antithesis-scenario').selectOption(scenario);
    await field('github_repo').fill('example/updated');
    await field('antithesis_password').fill('test-replacement-password');
    await waitState('unsaved');
    const savedResponse = page.waitForResponse(response => response.url().endsWith('/api/moog/setup'));
    await button.click();
    await waitState('saved');
    assert.ok(!(await (await savedResponse).text()).includes('test-replacement-password'));
    assert.equal(new URL(page.url()).pathname, '/run/antithesis');
    assert.equal(await page.locator('#antithesis-policy').inputValue(), 'exact');
    assert.equal(await page.locator('#antithesis-duration').inputValue(), '7');
    assert.equal(await page.locator('#antithesis-scenario').inputValue(), scenario);
    assert.equal(await field('antithesis_password').inputValue(), '');
    assert.equal(await button.isDisabled(), true);
    assert.equal(config().host, 'preserved-host');
    assert.equal(config().moog.enabled, true);
    assert.equal(config().moog.github_pat, 'test-existing-pat');
    assert.equal(config().moog.antithesis_password, 'test-replacement-password');
    assert.equal(config().moog.antithesis_user, 'stored-user');
    assert.equal(config().moog.github_repo, 'example/updated');
    // Errors preserve the editor; retry saves successfully.
    await page.route('**/api/moog/setup', route => route.fulfill({status: 503, json: {ok: false, error: 'Test outage'}}));
    await field('github_repo').fill('example/retried');
    await button.click();
    await waitState('error');
    assert.equal(await field('github_repo').inputValue(), 'example/retried');
    assert.equal(await button.isEnabled(), true);
    await page.unroute('**/api/moog/setup');
    await button.click();
    await waitState('saved');
    await form.getByText('Moog runtime', {exact: true}).click();
    await field('enabled').uncheck();
    await button.click();
    await waitState('saved');
    assert.equal(config().moog.enabled, false);
    await page.reload();
    assert.equal(await form.isVisible(), false);
    await page.locator('#antithesis-settings-heading').click();
    await form.getByText('GitHub target', {exact: true}).click();
    assert.equal(await field('github_repo').inputValue(), 'example/retried');
    for (const width of [1440, 390, 720]) {
      await page.setViewportSize({width, height: 1000});
      assert.equal(await page.locator('#run-stage-review').evaluate(node => node.scrollWidth > node.clientWidth), false);
    }
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('.antithesis-settings').screenshot({path: '/tmp/dwarf-antithesis-inline-settings.png'});
    assert.equal(await page.locator('#antithesis-prepare').isDisabled(), true);
    assert.equal(await page.locator('#antithesis-submit').isDisabled(), true);
    // Authentication failure must not change persisted settings.
    const before = config();
    const rejected = await page.request.post(origin + '/api/moog/setup', {
      headers: {Accept: 'application/json'}, form: {token: 'incorrect', github_repo: 'bad/change'}});
    assert.equal(rejected.status(), 403);
    assert.deepEqual(config(), before);
    // Existing Settings form keeps its redirect contract and blank-secret semantics.
    const legacy = await page.request.post(origin + '/api/moog/setup', {maxRedirects: 0,
      form: {token: 'settings-test-token', enabled: 'true', github_repo: 'legacy/saved', github_pat: ''}});
    assert.equal(legacy.status(), 303);
    assert.equal(legacy.headers().location, '/operate/config?saved=moog');
    assert.equal(config().moog.github_pat, 'test-existing-pat');
    // Actual file errors return a safe JSON failure, not raw configuration content.
    const configFile = path.join(stateDir, 'config.yaml');
    const original = fs.readFileSync(configFile);
    fs.writeFileSync(configFile, 'test-invalid-config-with-secret');
    const failed = await page.request.post(origin + '/api/moog/setup', {
      headers: {Accept: 'application/json'}, form: {token: 'settings-test-token', github_repo: 'not/saved'}});
    assert.equal(failed.status(), 500);
    assert.equal((await failed.json()).ok, false);
    assert.ok(!(await failed.text()).includes('test-invalid-config-with-secret'));
    fs.writeFileSync(configFile, original);
    assert.deepEqual(errors, []);
    assert.ok(mutations.every(url => url === '/api/moog/setup'));
    console.log('PASS: inline settings load/save/retry, masked secrets, environment precedence, partial updates, preserved wizard selections, persistence, auth, legacy redirect, mobile layout, no execution');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    fs.rmSync(stateDir, {recursive: true, force: true});
  }
})().catch(error => {console.error(error, stderr); process.exitCode = 1;});
