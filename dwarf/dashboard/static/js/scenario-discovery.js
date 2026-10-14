const facetGroups = [
  'implementation', 'execution', 'goal', 'recommended',
  'compatibility', 'capability', 'evidence', 'threat_model', 'risk_register',
];

function values(value) {
  return String(value || '').split(/\s+/).filter(Boolean);
}

export function scenarioMatchesDiscovery(metadata, state) {
  if (!state.showMaintenance && metadata.maintenance === 'hidden-by-default') return false;
  const query = String(state.query || '').trim().toLowerCase();
  if (query && !String(metadata.search || '').toLowerCase().includes(query)) return false;
  return facetGroups.every((group) => {
    const selected = state.facets?.[group] || [];
    if (!selected.length) return true;
    const available = values(metadata.facets?.[group]);
    return selected.some((value) => available.includes(value));
  });
}

export function familyShouldOpen({query, currentlyOpen, manuallyClosed = false}) {
  if (manuallyClosed) return false;
  return Boolean(String(query || '').trim() || currentlyOpen);
}

function savedState(scope) {
  try {
    const value = JSON.parse(sessionStorage.getItem(`dwarf:scenario-discovery:${scope}`) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch (_error) {
    return {};
  }
}

function persist(scope, state) {
  try {
    sessionStorage.setItem(`dwarf:scenario-discovery:${scope}`, JSON.stringify(state));
  } catch (_error) {
    // Discovery remains functional when browser storage is unavailable.
  }
}

function cardMetadata(card) {
  return {
    maintenance: card.dataset.maintenance,
    search: card.dataset.search,
    facets: Object.fromEntries(facetGroups.map((group) => [group, card.dataset[datasetFacetKey(group)]])),
  };
}

function datasetFacetKey(group) {
  return `facet${group.split('_').map((part) => part[0].toUpperCase() + part.slice(1)).join('')}`;
}

function disclosureKey(disclosure) {
  return disclosure.dataset.scenarioDisclosure || disclosure.dataset.scenarioFamilyGroup || '';
}

export function mountScenarioDiscovery(root) {
  if (!root || root._scenarioDiscoveryController) return root?._scenarioDiscoveryController || null;
  const scope = root.dataset.discoveryScope || 'catalog';
  const prior = savedState(scope);
  const state = {
    query: typeof prior.query === 'string' ? prior.query : '',
    showMaintenance: prior.showMaintenance === true,
    openFamilies: Array.isArray(prior.openFamilies) ? prior.openFamilies : [],
    closedFamilies: Array.isArray(prior.closedFamilies) ? prior.closedFamilies : [],
    facets: Object.fromEntries(facetGroups.map((group) => [group, Array.isArray(prior.facets?.[group]) ? prior.facets[group] : []])),
  };
  const query = root.querySelector('[data-discovery-query]');
  const maintenance = root.querySelector('[data-discovery-show-maintenance]');
  const cards = [...root.querySelectorAll('[data-scenario-card]')];
  const programmaticOpen = new WeakMap();
  const disclosures = [...root.querySelectorAll('[data-scenario-disclosure], [data-scenario-family-group]')];
  if (query) query.value = state.query;
  if (maintenance) maintenance.checked = state.showMaintenance;

  function setFamilyOpen(family, open) {
    if (family.open === open) return;
    programmaticOpen.set(family, open);
    family.open = open;
  }

  function render() {
    const currentSelected = cards.find((card) => card.getAttribute('aria-selected') === 'true');
    let visibleVariants = 0;
    cards.forEach((card) => {
      const filterMatches = scenarioMatchesDiscovery(cardMetadata(card), state);
      const matches = card === currentSelected || filterMatches;
      card.dataset.discoveryFilterMatch = String(filterMatches);
      card.dataset.discoveryMatch = String(matches);
      if (filterMatches) visibleVariants += 1;
    });
    let visibleFamilies = 0;
    disclosures.forEach((family) => {
      const familyCards = [...family.querySelectorAll('[data-scenario-card]')];
      const visible = familyCards.some((card) => card.dataset.discoveryMatch === 'true');
      const hasFilterMatch = familyCards.some((card) => card.dataset.discoveryFilterMatch === 'true');
      family.hidden = !visible;
      if (visible) {
        if (hasFilterMatch && (
          family.dataset.scenarioFamilyGroup
          || String(family.dataset.scenarioDisclosure || '').startsWith('family:')
        )) visibleFamilies += 1;
        const key = disclosureKey(family);
        const rememberedOpen = state.openFamilies.includes(key);
        const manuallyClosed = state.closedFamilies.includes(key);
        setFamilyOpen(family, familyShouldOpen({
          query: hasFilterMatch ? state.query : '',
          currentlyOpen: rememberedOpen,
          manuallyClosed,
        }));
      }
    });
    cards.forEach((card) => {
      const ancestors = disclosures.filter((disclosure) => disclosure.contains(card));
      card.hidden = card.dataset.discoveryMatch !== 'true' || ancestors.some((disclosure) => !disclosure.open);
    });
    root.querySelectorAll('[data-facet-group][data-facet-value]').forEach((button) => {
      const selectedValues = state.facets[button.dataset.facetGroup] || [];
      button.setAttribute('aria-pressed', String(selectedValues.includes(button.dataset.facetValue)));
    });
    root.querySelectorAll('[data-facet-clear]').forEach((button) => {
      button.setAttribute('aria-pressed', String(!(state.facets[button.dataset.facetClear] || []).length));
    });
    const count = root.querySelector('[data-discovery-count]');
    if (count) count.textContent = `${visibleFamilies} families · ${visibleVariants} of ${cards.length} exact variants`;
    persist(scope, state);
    root.dispatchEvent(new CustomEvent('scenario-discovery:changed', {detail: {visibleFamilies, visibleVariants}}));
  }

  root.querySelectorAll('[data-facet-group][data-facet-value]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = button.dataset.facetGroup;
      const value = button.dataset.facetValue;
      const selectedValues = new Set(state.facets[group] || []);
      if (selectedValues.has(value)) selectedValues.delete(value);
      else selectedValues.add(value);
      state.facets[group] = [...selectedValues];
      render();
    });
  });
  root.querySelectorAll('[data-facet-clear]').forEach((button) => {
    button.addEventListener('click', () => { state.facets[button.dataset.facetClear] = []; render(); });
  });
  query?.addEventListener('input', () => { state.query = query.value; render(); });
  maintenance?.addEventListener('change', () => { state.showMaintenance = maintenance.checked; render(); });
  disclosures.forEach((family) => {
    family.addEventListener('toggle', () => {
      if (programmaticOpen.get(family) === family.open) {
        programmaticOpen.delete(family);
        render();
        return;
      }
      programmaticOpen.delete(family);
      const openFamilies = new Set(state.openFamilies);
      const closedFamilies = new Set(state.closedFamilies);
      if (family.open) {
        openFamilies.add(disclosureKey(family));
        closedFamilies.delete(disclosureKey(family));
      } else {
        openFamilies.delete(disclosureKey(family));
        closedFamilies.add(disclosureKey(family));
      }
      state.openFamilies = [...openFamilies];
      state.closedFamilies = [...closedFamilies];
      cards.forEach((card) => {
        const ancestors = disclosures.filter((disclosure) => disclosure.contains(card));
        card.hidden = card.dataset.discoveryMatch !== 'true' || ancestors.some((disclosure) => !disclosure.open);
      });
      persist(scope, state);
    });
  });
  root.querySelectorAll('[data-facet-option-search]').forEach((input) => {
    input.addEventListener('input', () => {
      const group = input.dataset.facetOptionSearch;
      const needle = input.value.trim().toLowerCase();
      root.querySelectorAll(`[data-facet-group="${group}"]`).forEach((button) => {
        button.hidden = Boolean(needle) && !String(button.dataset.facetLabel || '').includes(needle);
      });
    });
  });
  root.querySelector('[data-discovery-clear]')?.addEventListener('click', () => {
    state.query = '';
    state.showMaintenance = false;
    state.openFamilies = [];
    state.closedFamilies = [];
    facetGroups.forEach((group) => { state.facets[group] = []; });
    if (query) query.value = '';
    if (maintenance) maintenance.checked = false;
    root.querySelectorAll('[data-facet-option-search]').forEach((input) => { input.value = ''; });
    root.querySelectorAll('[data-facet-group][data-facet-label]').forEach((button) => { button.hidden = false; });
    disclosures.forEach((family) => {
      setFamilyOpen(family, false);
    });
    render();
  });
  const controller = {state, refresh: render};
  root._scenarioDiscoveryController = controller;
  render();
  return controller;
}

export function mountScenarioDiscoveryRoots(container = document) {
  return [...container.querySelectorAll('[data-scenario-discovery]')].map(mountScenarioDiscovery);
}

if (typeof document !== 'undefined') mountScenarioDiscoveryRoots();
