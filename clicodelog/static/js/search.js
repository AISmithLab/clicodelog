var searchResults = [];
var searchActiveIndex = -1;

function toggleSearch() {
    var modal = document.getElementById('search-modal');
    var btn = document.getElementById('search-btn');
    var active = modal.classList.toggle('active');
    if (btn) btn.classList.toggle('active', active);
    if (active) {
        document.getElementById('global-search-input').focus();
    } else if (searchAbort) {
        searchAbort.abort();          // stop work the user walked away from
        searchAbort = null;
    }
}

async function searchConversations(query) {
    var el = document.getElementById('search-results');
    if (!query || query.length < 2) {
        if (searchAbort) { searchAbort.abort(); searchAbort = null; }
        el.textContent = '';
        el.appendChild(noResults('Type at least 2 characters to search'));
        searchResults = [];
        return;
    }

    // Cancel the in-flight request. Without this a slow earlier query could
    // resolve after a fast later one and overwrite the correct results.
    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();
    var seq = ++searchRequestSeq;
    var signal = searchAbort.signal;

    el.textContent = '';
    el.appendChild(loadingSpinner('Searching...'));

    try {
        var url = '/api/search?q=' + encodeURIComponent(query) +
            '&source=' + encodeURIComponent(currentSource);
        var r = await fetch(url, { signal: signal });
        if (seq !== searchRequestSeq) return;          // superseded
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var data = await r.json();
        if (seq !== searchRequestSeq) return;
        renderSearchResults(data, query);
    } catch (e) {
        if (e.name === 'AbortError' || seq !== searchRequestSeq) return;
        el.textContent = '';
        el.appendChild(noResults('Search failed'));
    }
}

function renderSearchResults(data, query) {
    var el = document.getElementById('search-results');
    var results = (data && data.results) || [];
    searchResults = results;
    searchActiveIndex = -1;

    el.textContent = '';

    var status = document.createElement('div');
    status.className = 'search-status';
    if (results.length === 0) {
        el.appendChild(noResults('No results for "' + query + '"'));
        if (data && data.content_search === false) {
            var hint = document.createElement('div');
            hint.className = 'search-status';
            hint.textContent = 'Only titles and paths are searchable right now. ' +
                'Build the full-text index to search inside conversations.';
            el.appendChild(hint);
        }
        return;
    }
    var totalMatches = results.reduce(function(n, r) { return n + (r.match_count || 0); }, 0);
    status.textContent = results.length + (results.length === 1 ? ' session' : ' sessions') +
        (totalMatches ? ' · ' + totalMatches + ' matches' : '') +
        (data.content_search ? '' : ' · titles only');
    el.appendChild(status);

    results.forEach(function(result, i) {
        el.appendChild(buildSearchResult(result, i));
    });
}

function buildSearchResult(result, index) {
    var item = document.createElement('div');
    item.className = 'search-result-item';
    item.dataset.index = String(index);
    item.onclick = function() { openSearchResult(index); };

    var title = document.createElement('div');
    title.className = 'search-result-title';
    // The API returns a summary; the old UI showed an opaque UUID instead.
    title.textContent = result.summary || result.session_id;

    var meta = document.createElement('div');
    meta.className = 'search-result-meta';
    var bits = [];
    if (result.project_name) bits.push(result.project_name);
    if (result.last_ts) bits.push(formatTime(result.last_ts));
    if (result.match_count) bits.push(result.match_count + ' matches');
    meta.textContent = bits.join('  ·  ');

    item.appendChild(title);
    item.appendChild(meta);

    (result.matches || []).forEach(function(m) {
        item.appendChild(buildSnippet(m, index));
    });
    return item;
}

function buildSnippet(match, resultIndex) {
    var row = document.createElement('div');
    row.className = 'search-snippet';

    var role = document.createElement('span');
    role.className = 'search-snippet-role';
    role.textContent = match.role || '';
    row.appendChild(role);

    var text = document.createElement('span');
    // snippet_parts is an array of {t, hit}. Building <mark> nodes with
    // createElement keeps the no-innerHTML-with-dynamic-data rule intact.
    (match.snippet_parts || []).forEach(function(part) {
        if (part.hit) {
            var mark = document.createElement('mark');
            mark.textContent = part.t;
            text.appendChild(mark);
        } else {
            text.appendChild(document.createTextNode(part.t));
        }
    });
    row.appendChild(text);

    row.onclick = function(e) {
        e.stopPropagation();
        openSearchResult(resultIndex, match.uuid);   // jump straight to the message
    };
    return row;
}

function moveSearchSelection(delta) {
    if (!searchResults.length) return;
    var next = searchActiveIndex + delta;
    if (next < 0) next = searchResults.length - 1;
    if (next >= searchResults.length) next = 0;
    searchActiveIndex = next;

    var items = document.querySelectorAll('#search-results .search-result-item');
    items.forEach(function(node, i) {
        node.classList.toggle('active', i === searchActiveIndex);
        if (i === searchActiveIndex) node.scrollIntoView({ block: 'nearest' });
    });
}

function openSearchResult(index, anchorUuid) {
    var result = searchResults[index >= 0 ? index : searchActiveIndex];
    if (!result) return;
    toggleSearch();
    selectSearchResult(result.project_id, result.session_id, anchorUuid);
}

async function selectSearchResult(projectId, sessionId, anchorUuid) {
    currentProjectId = projectId;
    currentSessionId = sessionId;
    // The conversation load does not depend on the session list, so both run
    // concurrently instead of one after the other.
    await Promise.all([
        selectProject(projectId, { keepSession: true }),
        selectSession(sessionId, { anchor: anchorUuid }),
    ]);
}
