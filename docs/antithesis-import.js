/* Optional Antithesis adapter. No network, node execution or changes to source files. */
(function (root) {
  'use strict';
  const VERSION = 'dwarf.antithesis-import.v1';
  const MAX_TARGETS = 16, MAX_METRICS = 2000;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  function fail(message) { const error = new Error(message); error.code = 'antithesis-format'; throw error; }

  // These snapshot reports are produced by DWARF's native collectors and
  // report merger. Keep their IDs (including collision-qualified IDs) identical
  // to Local/Actions output. Handler and queue durations are not block_fetch.
  function metricName(name) { return name; }
  // Presentation concepts do not change metric IDs or assert equivalent work.
  // The boundary strings describe the existing collector contracts.
  const concepts = {
    amaru: {
      'amaru-patched-header-validation.header_validation': ['header_validation_time', 'Header validation time', 'Header validation verdict'],
      protocol_decode: ['protocol_receive_decode', 'Protocol receive/decode time', 'Protocol message decode'],
      handler: ['blockfetch_handler_time', 'BlockFetch handler time', 'BlockFetch handler event'],
      keepalive_round_trip: ['keepalive_round_trip', 'KeepAlive round-trip time', 'KeepAlive round trip'],
      queue_residence: ['queue_residence', 'BlockFetch queue residence — exact', 'Exact queue residence'],
      block_decode: ['block_decode', 'Block decoding time', 'BlockFetch block decode']
    },
    'cardano-node': {
      'cardano-patched-header-validation.header_validation': ['header_validation_time', 'Header validation time', 'Consensus header validation verdict'],
      protocol_receive_decode: ['protocol_receive_decode', 'Protocol receive/decode time', 'Protocol receive and decode'],
      handler_service: ['blockfetch_handler_time', 'BlockFetch handler time', 'addFetchedBlock service in a merged request'],
      keepalive_rtt: ['keepalive_round_trip', 'KeepAlive round-trip time', 'KeepAlive round trip'],
      queue_residence_lower_bound: ['queue_residence_lower_bound', 'BlockFetch queue residence — lower bound', 'Lower bound around STM calls'],
      queue_residence_upper_bound: ['queue_residence_upper_bound', 'BlockFetch queue residence — upper bound', 'Upper bound around STM calls']
    }
  };
  function comparisonMetric(record, id, metric) {
    if (record.runtime !== 'antithesis' || !/^[0-9a-f]{64}$/.test(record.derived_import_identity || '')) return null;
    const implementation = own(concepts, record.implementation) ? concepts[record.implementation] : {};
    const match = own(implementation, id) ? implementation[id] : null;
    if (!match || !metric || metric.unit !== 'us') return null;
    return {id: match[0], title: match[1], boundary: match[2], category: 'latency-processing',
      key: JSON.stringify(['antithesis-concept-v1', record.scenario_id, record.mode, match[0], metric.unit]),
      note: 'Grouped by measurement concept. Separate run samples and node-specific boundaries; no performance ranking.'};
  }
  function normalize(value, depth = 0) {
    if (depth > 16) fail('Measurement nesting exceeds the import limit.');
    if (typeof value === 'number' && !Number.isFinite(value)) fail('Measurement contains a non-finite number.');
    if (Array.isArray(value)) return value.map(item => normalize(item, depth + 1));
    if (!object(value)) return value;
    const result = Object.create(null);
    for (const key of Object.keys(value)) result[key] = key === 'unit' && value[key] === 'microseconds' ? 'us' : normalize(value[key], depth + 1);
    return result;
  }
  function parse(entry, bytes, api) {
    if (!entry || entry.type !== 'file') fail('Missing Antithesis snapshot/report file.');
    if (entry.size > api.MAX_JSON_BYTES) fail('Antithesis JSON exceeds the existing viewer size limit.');
    let value;
    try { value = JSON.parse(api.entryText(bytes, entry)); } catch (_) { fail('Malformed Antithesis JSON: ' + entry.path); }
    if (!object(value)) fail('Expected an object in ' + entry.path);
    return value;
  }
  async function convert(parsed, bytes, api, raw) {
    const entries = new Map(parsed.entries.map(entry => [entry.path, entry]));
    // An ordinary run manifest always wins. Never reinterpret a damaged DWARF
    // run as Antithesis, or use a filename alone to opt into conversion.
    if (parsed.entries.some(entry => /(?:^|\/)manifest\.json$/.test(entry.path))) return null;
    const snapshots = parsed.entries.filter(entry => entry.type === 'file' && /\/snapshot\.json$/.test(entry.path));
    if (!snapshots.length) return null;
    if (snapshots.length > MAX_TARGETS) fail('Choose an archive with at most 16 Antithesis targets.');
    const sourceHash = await api.sha256Hex(raw);
    const models = [];
    for (const entry of snapshots.sort((a, b) => a.path.localeCompare(b.path))) {
      const prefix = entry.path.slice(0, -'snapshot.json'.length);
      const snapshot = parse(entry, bytes, api);
      const reportEntry = entries.get(prefix + 'measurements/report.json');
      const report = parse(reportEntry, bytes, api);
      const target = report.target_identity;
      if (!object(target) || target.engine !== 'antithesis' || !['amaru', 'cardano-node'].includes(target.implementation)
          || !['stock', 'patched'].includes(target.mode) || typeof target.version !== 'string'
          || !target.version || typeof snapshot.service !== 'string'
          || prefix.split('/').filter(Boolean).pop() !== snapshot.service
          || report.schema_version !== 'v1' || typeof report.scenario !== 'string' || !report.scenario
          || !object(report.measurements) || !object(snapshot.summary)) fail('Snapshot and report do not identify a supported Antithesis target.');
      const keys = Object.keys(report.measurements);
      if (keys.length > MAX_METRICS) fail('Too many measurement rows for one target.');
      const metrics = Object.create(null), mapping = [], warnings = [];
      for (const name of keys) {
        const id = metricName(name, target.implementation), raw = report.measurements[name];
        if (!object(raw)) fail('Invalid measurement object: ' + name);
        if (own(metrics, id)) fail('Conflicting metric names map to ' + id);
        metrics[id] = normalize(raw);
        mapping.push({source: name, metric_id: id, source_unit: raw.unit || null, unit: metrics[id].unit || null});
      }
      const collectors = object(snapshot.summary.collector_results) ? snapshot.summary.collector_results : {};
      for (const [id, collector] of Object.entries(collectors)) {
        if (!object(collector)) fail('Invalid collector evidence: ' + id);
        if (collector.export && collector.export.incomplete) warnings.push(id + ': source export incomplete');
        if (collector.exercise && collector.exercise.measurement_complete === false) warnings.push(id + ': ' + collector.exercise.status);
      }
      const reportHash = await api.sha256Hex(api.entryBytes(bytes, reportEntry));
      const snapshotHash = await api.sha256Hex(api.entryBytes(bytes, entry));
      const targetHash = await api.sha256Hex(new TextEncoder().encode(prefix));
      const runId = 'antithesis-' + sourceHash.slice(0, 20) + '-' + targetHash.slice(0, 12);
      const provenance = {status: 'derived-antithesis-import', converter: VERSION,
        source_archive_sha256: sourceHash, source_service: snapshot.service,
        source_snapshot_sha256: snapshotHash, source_report_sha256: reportHash,
        original_run_id: snapshot.run_id || null, original_target_identity: target,
        metric_mapping: mapping, collector_warnings: warnings,
        window: snapshot.window || null, source_window: snapshot.source_window || null,
        collector_errors: snapshot.summary.collector_errors || [],
        performance_authority: snapshot.performance_authority || 'Antithesis virtualized execution; not hardware benchmarking',
        source_collection_duration_seconds: report.duration_seconds == null ? null : report.duration_seconds,
        note: 'Browser-derived measurement view. Source bytes are unchanged. The derived view digest identifies this conversion, not an original run hash chain or signature. Run timestamps, control evidence and verdict are not inferred.'};
      const manifest = {schema_version: 'v1', run_id: runId, runtime: 'antithesis',
        scenario: {id: report.scenario, title: 'Antithesis · ' + report.scenario}, target: target,
        started_at: null, ended_at: null, exit_status: 'unknown', execution: {security_verdict: 'unknown'}, provenance};
      const hash = await api.sha256Hex(new TextEncoder().encode(JSON.stringify(manifest)));
      provenance.derived_view_sha256 = hash;
      const convertedReport = Object.assign({}, report, {measurements: metrics, duration_seconds: null});
      const compactRows = Object.entries(metrics).map(([metric, raw]) => {
        const row = object(raw.all) ? raw.all : raw;
        return {metric, status: row.status || 'unavailable', unit: row.unit || null,
          sample_count: row.sample_count == null ? 0 : row.sample_count,
          mean: row.mean, median: row.median, p95: row.p95, p99: row.p99};
      });
      const targetEntries = parsed.entries.filter(e => e.path.startsWith(prefix));
      models.push({runId, rootName: prefix.slice(0, -1), manifest,
        chain: {}, antithesisImport: {identity: hash},
        identityConsistent: null, manifestHashMatches: null, assertions: [], assertionsTotal: 0,
        measurementReport: convertedReport, measurementSelection: {resolution: snapshot.summary.resolution || {}},
        summary: {schema_version: 'v1', metric_count: compactRows.length,
          available_count: compactRows.filter(r => r.status === 'available').length,
          unavailable_count: compactRows.filter(r => r.status !== 'available').length},
        compactRows, compactRowsTotal: compactRows.length, entries: targetEntries,
        evidenceFiles: {'assertions.json': {present: false, valid: null, status: 'missing'},
          'measurements/report.json': {present: true, valid: true, status: 'valid'},
          'measurements/summary.json': {present: false, valid: null, status: 'missing'},
          'measurements/compact-table.json': {present: false, valid: null, status: 'missing'}},
        logTail: [{event: 'antithesis-import', payload: provenance.note},
          ...warnings.slice(0, 50).map(payload => ({event: 'collector-evidence', payload}))],
        errors: [], archiveManifest: null, archiveManifestCheck: null,
        retainedComparisonHashes: {'measurements/report.json': reportHash, 'snapshot.json': snapshotHash},
        scenarioDocument: null, scenarioHashMatches: false, scenarioCanonicalSafe: true});
    }
    // Every file must belong to one of the recognized service directories.
    const prefixes = snapshots.map(e => e.path.slice(0, -'snapshot.json'.length));
    if (parsed.entries.some(e => e.type === 'file' && !prefixes.some(p => e.path.startsWith(p)))) fail('Archive mixes Antithesis targets with unrelated files.');
    return models;
  }
  root.DwarfAntithesisImport = {convert, metricName, comparisonMetric, version: VERSION};
})(typeof window !== 'undefined' ? window : globalThis);
