const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
    const elements = new Map();
    const requests = [];
    function element() {
        return { style: {}, children: [], classList: { add() {}, remove() {} },
            appendChild(child) { this.children.push(child); } };
    }
    const context = vm.createContext({
        console,
        currentSource: 'copilot',
        document: {
            getElementById(id) {
                if (!elements.has(id)) elements.set(id, element());
                return elements.get(id);
            },
            createElement: element,
        },
        fetch(url) {
            return new Promise(resolve => requests.push({ url, resolve }));
        },
        setInterval() {},
        resetSessionFilters() {}, cacheClear() {}, setPanel() {}, emptyState() {},
        loadTagFilters: async () => {},
    });
    const root = path.join(__dirname, '../../clicodelog/static/js');
    vm.runInContext(fs.readFileSync(path.join(root, 'projects.js'), 'utf8'), context);
    return { context, requests, elements, root };
}

const sources = ['copilot', 'antigravity', 'claude-code'].map(id => ({
    id, name: id, available: true, session_count: 1,
}));

test('a source switch during source refresh survives the response', async () => {
    const { context, requests, elements } = harness();
    context.loadProjects = async () => {};
    context.fetchSyncStatus = async () => {};
    const refresh = context.loadSources(true);
    await context.changeSource('antigravity');
    requests[0].resolve({ json: async () => ({ current: 'claude-code', sources }) });
    await refresh;
    assert.equal(context.currentSource, 'antigravity');
    const selected = elements.get('source-select').children.filter(option => option.selected);
    assert.deepEqual(selected.map(option => option.value), ['antigravity']);
});

test('a sync completion cannot overwrite the newly selected source or status', async () => {
    const { context, requests, elements, root } = harness();
    context.loadProjects = async () => {};
    vm.runInContext(fs.readFileSync(path.join(root, 'sync.js'), 'utf8'), context);
    // sync.js starts a status request on load; complete it first.
    requests.shift().resolve({ json: async () => ({ last_sync: null }) });
    await new Promise(resolve => setImmediate(resolve));
    const syncing = context.manualSync();
    assert.equal(requests[0].url, '/api/sync?source=copilot');
    context.fetchSyncStatus = async () => {};
    await context.changeSource('antigravity');
    elements.get('sync-status').textContent = 'Antigravity status';
    requests.shift().resolve({ json: async () => ({ status: 'success', last_sync: '2026-10-20T12:00:00Z' }) });
    await new Promise(resolve => setImmediate(resolve));
    for (const request of requests.splice(0)) {
        request.resolve({ json: async () => ({ current: 'claude-code', sources }) });
    }
    await syncing;
    assert.equal(context.currentSource, 'antigravity');
    assert.equal(elements.get('sync-status').textContent, 'Antigravity status');
});

test('old project responses cannot replace the new source project list', async () => {
    const { context, requests } = harness();
    const rendered = [];
    context.renderProjects = projects => rendered.push(projects);
    context.fetchSyncStatus = async () => {};
    const previous = context.loadProjects();
    const changed = context.changeSource('antigravity');
    const currentProjects = [{ id: 'antigravity-project' }];
    requests[1].resolve({ json: async () => currentProjects });
    await changed;
    requests[0].resolve({ json: async () => [{ id: 'copilot-project' }] });
    await previous;
    assert.deepEqual(rendered, [currentProjects]);
    assert.deepEqual(context.projects, currentProjects);
});

test('old status responses cannot replace the new source status', async () => {
    const { context, requests, elements, root } = harness();
    vm.runInContext(fs.readFileSync(path.join(root, 'sync.js'), 'utf8'), context);
    context.currentSource = 'antigravity';
    elements.get('sync-status') || context.document.getElementById('sync-status');
    elements.get('sync-status').textContent = 'Antigravity status';
    requests[0].resolve({ json: async () => ({ last_sync: '2026-10-20T12:00:00Z' }) });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(elements.get('sync-status').textContent, 'Antigravity status');
});
