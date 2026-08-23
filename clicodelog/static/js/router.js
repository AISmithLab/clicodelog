// URL state: deep links, refresh-safe navigation, working back/forward.
// All navigation state used to live in JS globals, so a refresh dropped you
// back to the empty three-pane view and nothing was shareable.

function currentRoute() {
    var hash = location.hash.replace(/^#\/?/, '');
    if (!hash) return {};
    var parts = hash.split('/').map(decodeURIComponent);
    return { source: parts[0] || null, project: parts[1] || null, session: parts[2] || null };
}

function routeString() {
    var parts = [currentSource];
    if (currentProjectId) parts.push(currentProjectId);
    if (currentSessionId) parts.push(currentSessionId);
    return '#/' + parts.map(encodeURIComponent).join('/');
}

function pushRoute(replace) {
    var next = routeString();
    if (next === location.hash) return;
    try {
        if (replace) history.replaceState(null, '', next);
        else history.pushState(null, '', next);
    } catch (e) { /* file:// or a sandbox without history access */ }
}

// Replay a route without re-pushing it, so back/forward don't fight the app.
async function applyRoute(route) {
    suppressRouting = true;
    try {
        if (route.source && route.source !== currentSource) {
            var known = availableSources.some(function(s) { return s.id === route.source; });
            if (known) {
                var select = document.getElementById('source-select');
                if (select) select.value = route.source;
                await changeSource(route.source);
            }
        }
        if (route.project) {
            await selectProject(route.project, { keepSession: !!route.session });
            if (route.session) await selectSession(route.session);
        }
    } finally {
        suppressRouting = false;
    }
}

window.addEventListener('popstate', function() {
    var route = currentRoute();
    if (!route.source && !route.project) return;
    if (route.project === currentProjectId && route.session === currentSessionId) return;
    applyRoute(route);
});
