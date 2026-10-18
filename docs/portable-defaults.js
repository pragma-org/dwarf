/* Optional GitHub Pages bootstrap for preselected portable run archives.
 * This file stays external to the self-contained viewer: file:// exports
 * remain network-free, while a static host can opt in with one script tag. */
(function (root) {
  "use strict";

  var SCHEMA = "dwarf.default-run-archives.v1";
  var MAX_ARCHIVES = 10;
  var MAX_ARCHIVE_BYTES = 10 * 1024 * 1024;
  var MAX_TOTAL_BYTES = 25 * 1024 * 1024;

  function fail(message) { throw new Error("Default run archives: " + message); }

  function validateManifest(value, manifestUrl, expectedOrigin) {
    if (!value || value.schema_version !== SCHEMA) fail("unsupported manifest schema");
    if (!Array.isArray(value.archives) || !value.archives.length) fail("manifest must list at least one archive");
    if (value.archives.length > MAX_ARCHIVES) fail("manifest may contain at most " + MAX_ARCHIVES + " archives");
    var total = 0, seen = Object.create(null);
    return value.archives.map(function (entry) {
      if (!entry || typeof entry.url !== "string") fail("archive URL is missing");
      var url = new URL(entry.url, manifestUrl);
      if (url.origin !== expectedOrigin) fail("archive URL must use the same origin as the viewer");
      if (!/\.(tar\.gz|tgz)$/i.test(url.pathname)) fail("archive URL must end in .tar.gz or .tgz");
      if (seen[url.href]) fail("archive URL is duplicated");
      seen[url.href] = true;
      if (!Number.isSafeInteger(entry.size_bytes) || entry.size_bytes <= 0 || entry.size_bytes > MAX_ARCHIVE_BYTES) fail("archive size is outside the allowed range");
      if (typeof entry.sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(entry.sha256)) fail("archive digest must be a SHA-256 hex value");
      total += entry.size_bytes;
      if (total > MAX_TOTAL_BYTES) fail("archives exceed the total download limit");
      return {url: url.href, name: decodeURIComponent(url.pathname.split("/").pop()), size: entry.size_bytes, sha256: entry.sha256.toLowerCase()};
    });
  }

  function hex(buffer) {
    return Array.prototype.map.call(new Uint8Array(buffer), function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
  }

  async function loadDefaultRuns(win, manifestUrl, fetcher) {
    if (!win || !win.location || !/^https?:$/.test(win.location.protocol)) return null;
    if (!win.DwarfPortableImportInstance || typeof win.DwarfPortableImportInstance.importFiles !== "function") fail("portable importer is unavailable");
    var get = fetcher || win.fetch.bind(win);
    var manifestResponse = await get(manifestUrl, {credentials: "same-origin", cache: "no-cache"});
    if (!manifestResponse.ok) fail("manifest request failed");
    var entries = validateManifest(await manifestResponse.json(), manifestUrl, win.location.origin);
    var files = [];
    for (var i = 0; i < entries.length; i += 1) {
      var entry = entries[i], response = await get(entry.url, {credentials: "same-origin", cache: "default"});
      if (!response.ok) fail("archive request failed for " + entry.name);
      var bytes = await response.arrayBuffer();
      if (bytes.byteLength !== entry.size) fail("size mismatch for " + entry.name);
      var digest = hex(await win.crypto.subtle.digest("SHA-256", bytes));
      if (digest !== entry.sha256) fail("digest mismatch for " + entry.name);
      files.push(new win.File([bytes], entry.name, {type: "application/gzip"}));
    }
    return win.DwarfPortableImportInstance.importFiles(files);
  }

  function showError(doc, error) {
    var status = doc && doc.querySelector("[data-import-status]");
    if (!status) return;
    status.hidden = false;
    status.textContent = error && error.message ? error.message : String(error);
    status.setAttribute("data-import-tone", "error");
  }

  var api = {validateManifest: validateManifest, loadDefaultRuns: loadDefaultRuns, SCHEMA: SCHEMA};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root && root.document) {
    root.DwarfPortableDefaults = api;
    var script = root.document.currentScript;
    var manifest = script && script.getAttribute("data-dwarf-default-runs");
    if (manifest) {
      var start = function () { loadDefaultRuns(root, new URL(manifest, root.location.href).href).catch(function (error) { showError(root.document, error); }); };
      if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", start);
      else start();
    }
  }
})(typeof window !== "undefined" ? window : null);
