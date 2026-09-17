#!/usr/bin/env node

/* Read-only visual contract for the DWARF dashboard.
 *
 * Usage:
 *   node tools/dashboard_visual_audit.js [base-url] [screenshot-directory]
 *
 * The crawler performs read-only navigation and UI interactions, downloads
 * exported assets, and verifies that selected write endpoints reject requests
 * without an operator token. It never submits an authenticated mutation, starts
 * a scenario, deploys a target, removes an asset, or launches Antithesis.
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const baseUrl = (process.argv[2] || process.env.DWARF_DASHBOARD_URL || 'http://127.0.0.1:8787').replace(/\/$/, '');
const screenshotDir = process.argv[3] || '';

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
];

const ROUTES = [
  '/', '/tests', '/scenarios',
  '/operate', '/operate/audit', '/operate/bundles',
  '/operate/antithesis', '/operate/compare', '/operate/compare/runs',
  '/operate/config', '/operate/config/edit', '/operate/contract',
  '/operate/coverage', '/operate/crashes', '/operate/notifications', '/operate/plugins',
  '/operate/primitives', '/operate/primitives/new', '/operate/profiles', '/operate/profiles/new',
  '/operate/profile-templates', '/operate/runs', '/operate/testcases', '/operate/testcase-buckets', '/operate/corpora', '/operate/grammars', '/operate/risk-packages',
  '/operate/scenarios', '/operate/scenarios/new', '/operate/schedule',
  '/operate/static-analysis', '/operate/status', '/operate/targets', '/operate/targets/new',
  '/operate/timeline',
  '/learn', '/learn/api', '/learn/architecture', '/learn/attack-cost', '/learn/cli',
  '/learn/concepts', '/learn/consensus', '/learn/coverage', '/learn/developer-onboarding',
  '/learn/examples', '/learn/faq', '/learn/getting-started', '/learn/glossary',
  '/learn/operator-runbook', '/learn/overview', '/learn/plugin-authoring', '/learn/primitives',
  '/learn/profile-templates', '/learn/status', '/learn/testcases', '/learn/corpora', '/learn/grammars', '/learn/risk-packages',
  '/learn/threat-coverage', '/learn/troubleshooting', '/learn/walkthroughs',
];

const EXPECTED_STATUS_ROUTES = [
  '/operate/scenarios/visual-audit-missing',
  '/operate/targets/visual-audit-missing',
  '/operate/profiles/visual-audit-missing',
  '/operate/primitives/visual-audit-missing',
  '/operate/profile-templates/visual-audit-missing',
  '/operate/testcases/visual-audit-missing',
  '/operate/testcase-buckets/visual-audit-missing',
  '/operate/corpora/visual-audit-missing',
  '/operate/grammars/visual-audit-missing',
  '/operate/risk-packages/visual-audit-missing',
  '/operate/plugins/visual-audit-missing',
].map(route => ({ route, expectedStatus: 404 }));

const FILTER_ROUTES = [
  '/operate/primitives', '/operate/profile-templates', '/operate/testcases',
  '/operate/testcase-buckets', '/operate/corpora', '/operate/grammars',
  '/operate/risk-packages', '/operate/plugins',
];

const BUILDER_ROUTES = [
  '/operate/scenarios/new', '/operate/targets/new', '/operate/profiles/new',
];

const TOKEN_GATES = [
  { path: '/api/catalog/scenarios/__visual-audit__/save', expectedStatus: 403 },
  { path: '/api/corpora/runtime--overlay/actions', expectedStatus: 403 },
  { path: '/api/grammars/runtime--missing/actions', expectedStatus: 403 },
];

function safeName(route, viewport) {
  const slug = route.replace(/^\//, '').replace(/[^a-zA-Z0-9]+/g, '-') || 'root';
  return `${viewport}-${slug}.png`;
}

function sameOriginPath(href) {
  try {
    const url = new URL(href, baseUrl);
    if (url.origin !== new URL(baseUrl).origin) return '';
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/static/')) return '';
    if (url.pathname.endsWith('/tail') || url.pathname === '/metrics') return '';
    return url.pathname + url.search;
  } catch (_) {
    return '';
  }
}

function sameOriginApiPath(href) {
  try {
    const url = new URL(href, baseUrl);
    if (url.origin !== new URL(baseUrl).origin) return '';
    return url.pathname + url.search;
  } catch (_) {
    return '';
  }
}

async function representativeDynamicRoutes(page) {
  const representatives = [];
  const definitionDetail = /^\/operate\/(scenarios|targets|profiles)\/[^/?]+$/;
  const definitionEdit = /^\/operate\/(scenarios|targets|profiles)\/[^/?]+\/edit$/;
  for (const source of ['/operate/runs', '/operate/scenarios', '/operate/targets', '/operate/profiles', '/operate/primitives', '/operate/profile-templates', '/operate/testcases', '/operate/testcase-buckets', '/operate/corpora', '/operate/grammars', '/operate/risk-packages', '/operate/plugins']) {
    try {
      await page.goto(baseUrl + source, { waitUntil: 'domcontentloaded', timeout: 15000 });
      const hrefs = await page.locator('a[href]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
      const paths = hrefs.map(sameOriginPath).filter(Boolean);
      if (source.endsWith('/runs')) {
        const run = paths.find(item => /^\/operate\/runs\/[^/?]+$/.test(item));
        if (run) representatives.push(run, `${run}/live`);
      } else if (source.endsWith('/primitives')) {
        const detail = paths.find(item => /^\/operate\/primitives\/[^/?]+$/.test(item) && item !== '/operate/primitives/new');
        if (detail) representatives.push(detail);
      } else if (source.endsWith('/profile-templates')) {
        const detail = paths.find(item => /^\/operate\/profile-templates\/[^/?]+$/.test(item));
        if (detail) representatives.push(detail);
      } else if (source.endsWith('/testcases')) {
        const detail = paths.find(item => /^\/operate\/testcases\/[^/?]+$/.test(item));
        if (detail) representatives.push(detail);
      } else if (source.endsWith('/testcase-buckets')) {
        const detail = paths.find(item => /^\/operate\/testcase-buckets\/[^/?]+$/.test(item));
        if (detail) representatives.push(detail);
      } else if (source.endsWith('/corpora')) {
        const detail = paths.find(item => /^\/operate\/corpora\/[^/?]+$/.test(item));
        if (detail) representatives.push(detail);
      } else if (source.endsWith('/grammars')) {
        const detail = paths.find(item => /^\/operate\/grammars\/[^/?]+$/.test(item));
        if (detail) representatives.push(detail);
      } else if (source.endsWith('/risk-packages')) {
        const detail = paths.find(item => /^\/operate\/risk-packages\/[^/?]+$/.test(item));
        if (detail) representatives.push(detail);
      } else if (source.endsWith('/plugins')) {
        const detail = paths.find(item => /^\/operate\/plugins\/[^/?]+$/.test(item));
        if (detail) representatives.push(detail);
      } else {
        const detail = paths.find(item => definitionDetail.test(item) && item !== `${source}/new`);
        const edit = paths.find(item => definitionEdit.test(item));
        if (detail) representatives.push(detail);
        if (edit) representatives.push(edit);
      }
    } catch (_) {
      // The static source route will report the actionable failure later.
    }
  }
  return representatives;
}

async function inspectRoute(page, route, viewport, expectedStatus = 200) {
  const browserErrors = [];
  const onConsole = message => {
    if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
  };
  const onPageError = error => browserErrors.push(`page: ${error.message}`);
  page.on('console', onConsole);
  page.on('pageerror', onPageError);

  const problems = [];
  try {
    const response = await page.goto(baseUrl + route, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForLoadState('load', { timeout: 10000 });
    const status = response ? response.status() : 0;
    if (status !== expectedStatus) {
      problems.push(`HTTP ${status || 'no response'} (expected ${expectedStatus})`);
    }

    const metrics = await page.evaluate(async viewportName => {
      const root = document.documentElement;
      const isVisible = element => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && rect.width > 0
          && rect.height > 0;
      };
      const identify = element => element.id
        || element.getAttribute('name')
        || element.getAttribute('aria-label')
        || (element.textContent || '').trim().slice(0, 48)
        || element.tagName.toLowerCase();
      const controls = [...document.querySelectorAll('input, select, textarea, button')]
        .filter(element => isVisible(element) && element.type !== 'hidden');
      const whiteControls = controls.filter(element => {
        const color = getComputedStyle(element).backgroundColor;
        return color === 'rgb(255, 255, 255)' || color === 'rgba(255, 255, 255, 1)';
      }).length;
      const brokenImages = [...document.images].filter(image => {
        const style = getComputedStyle(image);
        return style.display !== 'none' && (!image.complete || image.naturalWidth === 0);
      }).map(image => image.currentSrc || image.src);
      const unlabeledControls = [...document.querySelectorAll('input, select, textarea')]
        .filter(element => element.type !== 'hidden' && getComputedStyle(element).display !== 'none')
        .filter(element => {
          if (element.getAttribute('aria-label') || element.getAttribute('aria-labelledby')) return false;
          if (element.closest('label')) return false;
          return !element.id || !document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
        })
        .map(element => element.id || element.name || element.tagName.toLowerCase());
      const unnamedButtons = [...document.querySelectorAll('button')]
        .filter(element => getComputedStyle(element).display !== 'none')
        .filter(element => !(element.textContent || '').trim() && !element.getAttribute('aria-label'))
        .length;
      const missingHeadings = document.querySelectorAll('h1').length === 0 ? 1 : 0;
      const missingImageAlt = [...document.images].filter(image => !image.hasAttribute('alt')).length;
      const editor = document.querySelector('.definition-editor');
      const escapedEditorControls = [];
      const stretchedSingleLineControls = [];
      if (editor && isVisible(editor)) {
        const editorRect = editor.getBoundingClientRect();
        for (const element of controls.filter(control => editor.contains(control))) {
          const rect = element.getBoundingClientRect();
          if (rect.left < editorRect.left - 1 || rect.right > editorRect.right + 1) {
            escapedEditorControls.push(identify(element));
          }
          if (element.matches('select, input:not([type="checkbox"]):not([type="radio"])')
              && rect.height > 56) {
            stretchedSingleLineControls.push(`${identify(element)}:${Math.round(rect.height)}px`);
          }
        }
      }
      const undersizedMobileControls = viewportName === 'mobile'
        ? controls
          .filter(element => element.closest('form'))
          .filter(element => !element.matches('input[type="checkbox"], input[type="radio"]'))
          .filter(element => element.getBoundingClientRect().height < 44)
          .map(element => `${identify(element)}:${Math.round(element.getBoundingClientRect().height)}px`)
        : [];
      const profileRowOverflow = [];
      for (const row of document.querySelectorAll('.profile-catalog__row')) {
        if (!isVisible(row)) continue;
        const rowRect = row.getBoundingClientRect();
        const escaped = [...row.children]
          .filter(isVisible)
          .filter(element => {
            const rect = element.getBoundingClientRect();
            return rect.left < rowRect.left - 1 || rect.right > rowRect.right + 1;
          });
        if (escaped.length) profileRowOverflow.push(row.id || identify(row));
      }
      const builderHelpFailures = [];
      if (editor && isVisible(editor)) {
        for (const name of editor.querySelectorAll('.definition-field__name')) {
          if (!name.matches('[data-field-help]') || !name.dataset.fieldHelp || name.hasAttribute('title') || name.tabIndex < 0) {
            builderHelpFailures.push(`field:${identify(name)}`);
          }
        }
        for (const select of editor.querySelectorAll('select')) {
          if (!isVisible(select)) continue;
          const tooltip = [...select.parentElement.children]
            .find(element => element.classList.contains('definition-select-tooltip'));
          if (!select.matches('[data-selected-option-help]')
              || !select.dataset.selectedOptionHelp
              || select.hasAttribute('title')
              || !tooltip
              || tooltip.textContent.trim() !== select.dataset.selectedOptionHelp.trim()) {
            builderHelpFailures.push(`select:${identify(select)}`);
          }
        }
      }
      const fragmentLinks = [...document.querySelectorAll('a[href*="#"]')]
        .map(anchor => anchor.href)
        .filter(href => {
          const target = new URL(href, location.href);
          return target.origin === location.origin && target.hash.length > 1;
        });
      const documents = new Map();
      const brokenFragments = [];
      for (const href of [...new Set(fragmentLinks)]) {
        const target = new URL(href, location.href);
        const documentKey = target.pathname + target.search;
        let targetDocument;
        if (documentKey === location.pathname + location.search) {
          targetDocument = document;
        } else if (documents.has(documentKey)) {
          targetDocument = documents.get(documentKey);
        } else {
          try {
            const response = await fetch(documentKey, { credentials: 'same-origin' });
            targetDocument = response.ok
              ? new DOMParser().parseFromString(await response.text(), 'text/html')
              : null;
          } catch (_) {
            targetDocument = null;
          }
          documents.set(documentKey, targetDocument);
        }
        let fragment;
        try {
          fragment = decodeURIComponent(target.hash.slice(1));
        } catch (_) {
          fragment = target.hash.slice(1);
        }
        if (!targetDocument || !targetDocument.getElementById(fragment)) {
          brokenFragments.push(target.pathname + target.hash);
        }
      }
      const contextualLinkFailures = [...document.querySelectorAll('a[href^="/learn/"]')]
        .filter(anchor => (anchor.textContent || '').includes('Authoring guide'))
        .filter(anchor => {
          const rel = new Set((anchor.getAttribute('rel') || '').split(/\s+/).filter(Boolean));
          return anchor.getAttribute('target') !== '_blank'
            || !rel.has('noopener')
            || !rel.has('noreferrer');
        })
        .map(anchor => anchor.getAttribute('href'));
      return {
        clientWidth: root.clientWidth,
        scrollWidth: root.scrollWidth,
        whiteControls,
        brokenImages,
        unlabeledControls,
        unnamedButtons,
        missingHeadings,
        missingImageAlt,
        escapedEditorControls,
        undersizedMobileControls,
        stretchedSingleLineControls,
        profileRowOverflow,
        builderHelpFailures,
        brokenFragments,
        contextualLinkFailures,
      };
    }, viewport.name);

    if (metrics.scrollWidth > metrics.clientWidth + 2) {
      problems.push(`document overflow ${metrics.scrollWidth}px > ${metrics.clientWidth}px`);
    }
    if (metrics.whiteControls) problems.push(`${metrics.whiteControls} browser-white controls`);
    if (metrics.brokenImages.length) problems.push(`broken images: ${metrics.brokenImages.join(', ')}`);
    if (metrics.unlabeledControls.length) problems.push(`unlabeled controls: ${metrics.unlabeledControls.join(', ')}`);
    if (metrics.unnamedButtons) problems.push(`${metrics.unnamedButtons} unnamed buttons`);
    if (metrics.missingHeadings) problems.push('missing h1');
    if (metrics.missingImageAlt) problems.push(`${metrics.missingImageAlt} images missing alt text`);
    if (metrics.escapedEditorControls.length) problems.push(`editor controls escaped content well: ${metrics.escapedEditorControls.join(', ')}`);
    if (metrics.undersizedMobileControls.length) problems.push(`undersized mobile form controls: ${metrics.undersizedMobileControls.join(', ')}`);
    if (metrics.stretchedSingleLineControls.length) problems.push(`stretched single-line controls: ${metrics.stretchedSingleLineControls.join(', ')}`);
    if (metrics.profileRowOverflow.length) problems.push(`profile row overflow: ${metrics.profileRowOverflow.join(', ')}`);
    if (metrics.builderHelpFailures.length) problems.push(`builder help failures: ${metrics.builderHelpFailures.join(', ')}`);
    if (metrics.brokenFragments.length) problems.push(`broken fragment links: ${metrics.brokenFragments.join(', ')}`);
    if (metrics.contextualLinkFailures.length) problems.push(`contextual links do not open safely: ${metrics.contextualLinkFailures.join(', ')}`);
    problems.push(...browserErrors.filter(problem => !(
      expectedStatus >= 400
      && problem.startsWith('console: Failed to load resource: the server responded with a status of')
    )));

    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, safeName(route, viewport.name)), fullPage: true });
    }
  } catch (error) {
    problems.push(`navigation: ${error.message}`);
  } finally {
    page.off('console', onConsole);
    page.off('pageerror', onPageError);
  }
  return { route, viewport: viewport.name, problems };
}

async function exerciseReadOnlyInteractions(page) {
  const problems = [];
  let checks = 0;
  for (const route of FILTER_ROUTES) {
    await page.goto(baseUrl + route, { waitUntil: 'load', timeout: 20000 });
    const search = page.locator('input[type="search"]').first();
    if (!await search.count()) {
      problems.push(`${route}: missing search control`);
      continue;
    }
    const rows = page.locator('[data-search]');
    const baseline = await rows.count();
    if (baseline === 0) {
      checks += 1;
      if (!await page.locator('.empty-section-statement').count()) {
        problems.push(`${route}: empty catalog has no explicit empty state`);
      }
      continue;
    }
    await search.fill('__dwarf_visual_audit_no_match__');
    checks += 1;
    if (await page.locator('[data-search]:visible').count()) {
      problems.push(`${route}: no-match filter left rows visible`);
    }
    await search.fill('');
    checks += 1;
    if (await page.locator('[data-search]:visible').count() !== baseline) {
      problems.push(`${route}: clearing filter did not restore ${baseline} rows`);
    }
  }

  for (const route of BUILDER_ROUTES) {
    await page.goto(baseUrl + route, { waitUntil: 'load', timeout: 20000 });
    const raw = page.locator('[data-editor-tab="raw"]');
    const structured = page.locator('[data-editor-tab="structured"]');
    if (!await raw.count() || !await structured.count()) {
      problems.push(`${route}: structured/raw tabs missing`);
      continue;
    }
    await raw.click();
    checks += 1;
    if (await raw.getAttribute('aria-selected') !== 'true'
        || !await page.locator('[data-editor-panel="raw"]:visible').count()) {
      problems.push(`${route}: raw view did not become active`);
    }
    const rawBeforeReturn = await page.locator('[data-editor-raw]').inputValue();
    await structured.click();
    checks += 1;
    await page.waitForFunction(() => {
      const selected = document.querySelector('[data-editor-tab="structured"]')?.getAttribute('aria-selected') === 'true';
      const report = document.querySelector('[data-editor-report]')?.textContent || '';
      return selected || report.startsWith('Cannot switch to Structured:');
    }, null, { timeout: 10000 });
    if (route === '/operate/targets/new') {
      const report = (await page.locator('[data-editor-report]').textContent()).trim();
      if (await raw.getAttribute('aria-selected') !== 'true'
          || !report.startsWith('Cannot switch to Structured: upstream_commit must be a non-empty string')
          || await page.locator('[data-editor-raw]').inputValue() !== rawBeforeReturn) {
        problems.push(`${route}: invalid required provenance was not contained in raw view`);
      }
    } else if (await structured.getAttribute('aria-selected') !== 'true'
        || !await page.locator('[data-editor-panel="structured"]:visible').count()) {
      problems.push(`${route}: structured view did not restore after validation`);
    }
  }
  return { checks, problems };
}

function safeAttachmentFilename(disposition) {
  const match = /filename="?([^";]+)"?/i.exec(disposition || '');
  if (!match) return false;
  const filename = match[1];
  return filename.length > 0
    && !/[\\/\r\n\0]/.test(filename)
    && filename !== '.'
    && filename !== '..';
}

async function verifyDownloads(page, dynamicRoutes) {
  const problems = [];
  const urls = new Set();
  const detailRoutes = dynamicRoutes.filter(route => /^\/operate\/(scenarios|targets|profiles|primitives|profile-templates|testcases|testcase-buckets|corpora|grammars|risk-packages|plugins)\/[^/?]+$/.test(route));
  for (const route of [...FILTER_ROUTES, ...detailRoutes]) {
    await page.goto(baseUrl + route, { waitUntil: 'load', timeout: 20000 });
    const hrefs = await page.locator('a[href]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
    for (const href of hrefs.map(sameOriginApiPath).filter(Boolean)) {
      if (/^\/api\/(assets|catalog|corpora|grammars|risk-packages|plugins)\//.test(href)
          && /\/(download|export)(?:\?|$)/.test(href)) {
        urls.add(href);
      }
    }
  }

  let checks = 0;
  for (const url of urls) {
    const response = await page.request.get(baseUrl + url, { timeout: 30000 });
    checks += 1;
    if (response.status() !== 200) {
      problems.push(`${url}: download returned ${response.status()}`);
      continue;
    }
    const headers = response.headers();
    const contentType = (headers['content-type'] || '').toLowerCase();
    if (!contentType || contentType.includes('text/html')) {
      problems.push(`${url}: unsafe content-type ${contentType || '(missing)'}`);
    }
    if (!safeAttachmentFilename(headers['content-disposition'])) {
      problems.push(`${url}: missing or unsafe content-disposition filename`);
    }
  }
  if (!checks) problems.push('no catalog downloads were discovered');
  return { checks, problems };
}

async function verifyTokenGates(page) {
  const problems = [];
  let checks = 0;
  for (const gate of TOKEN_GATES) {
    const response = await page.request.post(baseUrl + gate.path, {
      data: { action: 'audit-denied' },
      headers: { 'Content-Type': 'application/json' },
      timeout: 20000,
    });
    checks += 1;
    if (response.status() !== gate.expectedStatus) {
      problems.push(`${gate.path}: returned ${response.status()}, expected ${gate.expectedStatus}`);
    }
  }
  return { checks, problems };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const discoveryPage = await browser.newPage({ viewport: VIEWPORTS[0], reducedMotion: 'reduce' });
  const dynamicRoutes = await representativeDynamicRoutes(discoveryPage);
  await discoveryPage.close();
  const routeSpecs = [
    ...[...new Set([...ROUTES, ...dynamicRoutes])].map(route => ({ route, expectedStatus: 200 })),
    ...EXPECTED_STATUS_ROUTES,
  ];
  const results = [];

  for (const viewport of VIEWPORTS) {
    const page = await browser.newPage({ viewport, reducedMotion: 'reduce' });
    for (const spec of routeSpecs) {
      results.push(await inspectRoute(page, spec.route, viewport, spec.expectedStatus));
    }
    await page.close();
  }

  const interactionPage = await browser.newPage({ viewport: VIEWPORTS[0], reducedMotion: 'reduce' });
  const interactions = await exerciseReadOnlyInteractions(interactionPage);
  const downloads = await verifyDownloads(interactionPage, dynamicRoutes);
  const gates = await verifyTokenGates(interactionPage);
  await interactionPage.close();
  await browser.close();

  const failures = [
    ...results.filter(result => result.problems.length),
    ...interactions.problems.map(problem => ({ phase: 'interactions', problems: [problem] })),
    ...downloads.problems.map(problem => ({ phase: 'downloads', problems: [problem] })),
    ...gates.problems.map(problem => ({ phase: 'token-gates', problems: [problem] })),
  ];
  console.log(JSON.stringify({
    baseUrl,
    routeCount: routeSpecs.length,
    checks: results.length,
    interactionChecks: interactions.checks,
    downloadChecks: downloads.checks,
    gateChecks: gates.checks,
    failures,
  }, null, 2));
  if (failures.length) process.exitCode = 1;
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
