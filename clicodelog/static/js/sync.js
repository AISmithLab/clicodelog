async function fetchSyncStatus() {
    const sourceAtStart = currentSource;
    try {
        const data = await fetch('/api/status?source=' + sourceAtStart).then(r => r.json());
        if (currentSource === sourceAtStart) updateSyncStatus(data.last_sync);
    } catch (e) {
        if (currentSource === sourceAtStart) document.getElementById('sync-status').textContent = 'Status unavailable';
    }
}

function updateSyncStatus(lastSync) {
    const el = document.getElementById('sync-status');
    if (lastSync) {
        const d = new Date(lastSync);
        el.textContent = 'Last sync: ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        el.title = 'Last sync: ' + d.toLocaleString();
    } else {
        el.textContent = 'Not synced';
    }
}

async function manualSync() {
    const sourceAtStart = currentSource;
    const btn = document.getElementById('sync-btn');
    btn.classList.add('syncing');
    btn.disabled = true;
    document.getElementById('sync-status').textContent = 'Syncing...';
    try {
        const data = await fetch('/api/sync?source=' + sourceAtStart, { method: 'POST' }).then(r => r.json());
        if (data.status === 'success') {
            if (currentSource === sourceAtStart) updateSyncStatus(data.last_sync);
            conversationCache = {};
            if (typeof loadProjects === 'function') await loadProjects();
            if (typeof loadSources === 'function') await loadSources(true);
        } else {
            if (currentSource === sourceAtStart) document.getElementById('sync-status').textContent = 'Sync failed';
        }
    } catch (e) {
        if (currentSource === sourceAtStart) document.getElementById('sync-status').textContent = 'Sync error';
    } finally {
        btn.classList.remove('syncing');
        btn.disabled = false;
    }
}

fetchSyncStatus();
setInterval(fetchSyncStatus, 60000);
