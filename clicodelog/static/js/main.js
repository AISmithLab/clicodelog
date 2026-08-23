document.getElementById('project-search').addEventListener('input', function(e) {
    var query = e.target.value.toLowerCase();
    renderProjects(projects.filter(function(p) {
        return p.name.toLowerCase().includes(query) ||
            (p.custom_name && p.custom_name.toLowerCase().includes(query)) ||
            (p.tags && p.tags.some(function(t) { return t.toLowerCase().includes(query); }));
    }));
});

document.getElementById('global-search-input').addEventListener('input', function(e) {
    clearTimeout(searchTimeout);
    var query = e.target.value;
    // 150ms rather than 300ms: queries are indexed now, so the debounce is the
    // dominant term in perceived latency.
    searchTimeout = setTimeout(function() { searchConversations(query); }, 150);
});

document.getElementById('global-search-input').addEventListener('keydown', function(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveSearchSelection(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveSearchSelection(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); openSearchResult(-1); }
});

document.addEventListener('keydown', function(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        toggleSearch();
    }
    if (e.key === 'Escape') {
        var modal = document.getElementById('search-modal');
        if (modal && modal.classList.contains('active')) toggleSearch();
        var stats = document.getElementById('stats-modal');
        if (stats && stats.classList.contains('active')) toggleStats();
    }
});

async function init() {
    await loadSources();

    // loadTagFilters and fetchSyncStatus only need currentSource, so they no
    // longer queue behind the projects fetch.
    await Promise.all([
        loadProjects(),
        typeof loadTagFilters === 'function' ? loadTagFilters() : Promise.resolve(),
        typeof fetchSyncStatus === 'function' ? fetchSyncStatus() : Promise.resolve(),
    ]);

    var route = currentRoute();
    if (route.project) {
        await applyRoute(route);
    } else {
        pushRoute(true);
    }

    if (typeof pollSearchIndexStatus === 'function') pollSearchIndexStatus();
}

init();
