var projectRequestSeq = 0;

async function loadSources() {
    try {
        const data = await fetch('/api/sources').then(r => r.json());
        availableSources = data.sources;
        currentSource = data.current;
        const select = document.getElementById('source-select');
        select.textContent = '';
        availableSources.forEach(function(s) {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.selected = s.id === currentSource;
            opt.disabled = !s.available;
            var label = s.name;
            if (!s.available) label += ' (not found)';
            else if (s.warning) label += ' (unreadable)';
            else if (s.session_count) label += ' (' + s.session_count + ')';
            opt.textContent = label;
            select.appendChild(opt);
        });
        // A source with files on disk but nothing readable means the vendor
        // changed format. Say so rather than showing an empty list, which is
        // how Gemini stayed broken unnoticed.
        var broken = availableSources.filter(function(s) { return s.warning; });
        if (broken.length) {
            console.warn('Sources present but unreadable:',
                broken.map(function(s) { return s.id; }).join(', '));
            showSourceWarning(broken);
        }
    } catch (e) { console.error('Error loading sources:', e); }
}

function showSourceWarning(broken) {
    var bar = document.getElementById('source-warning');
    if (!bar) return;
    bar.textContent = broken.map(function(s) { return s.name; }).join(', ') +
        ': files found but none could be read. The log format may have changed.';
    bar.style.display = 'block';
}

async function changeSource(sourceId) {
    if (sourceId === currentSource) return;
    currentSource = sourceId;
    currentProjectId = null;
    currentSessionId = null;
    document.getElementById('export-btn').disabled = true;
    document.getElementById('copy-btn').disabled = true;
    currentSessions = [];
    document.getElementById('session-filters').style.display = 'none';
    resetSessionFilters();
    activeTagFilter = null;
    cacheClear();
    document.getElementById('conv-filter-bar').style.display = 'none';
    setPanel('sessions-list', emptyState('📁', 'Select a project to view sessions'));
    setPanel('conversation-content', emptyState('💬', 'Select a session to view the conversation', 'AI Conversation History'));
    await loadProjects();
    await loadTagFilters();
    await fetchSyncStatus();
}

async function loadProjects() {
    try {
        projects = await fetch('/api/projects?source=' + currentSource).then(r => r.json());
        renderProjects(projects);
    } catch (e) { setPanel('projects-list', emptyState('', 'Error loading projects')); }
}

function changeProjectSort(mode) {
    projectSort = mode;
    renderProjects(projects);
}

function fmtProjectDate(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleDateString([], { year: '2-digit', month: 'short', day: 'numeric' });
}

function renderProjects(projectsList) {
    const container = document.getElementById('projects-list');
    var filtered = activeTagFilter
        ? projectsList.filter(function(p) { return p.tags && p.tags.includes(activeTagFilter); })
        : projectsList.slice();

    filtered.sort(function(a, b) {
        if (projectSort === 'recent') {
            return (Date.parse(b.last_modified) || 0) - (Date.parse(a.last_modified) || 0);
        }
        if (projectSort === 'sessions') {
            return (b.session_count || 0) - (a.session_count || 0);
        }
        return (a.custom_name || a.name).localeCompare(b.custom_name || b.name);
    });

    container.textContent = '';
    if (filtered.length === 0) { container.appendChild(emptyState('', 'No projects found')); return; }
    filtered.forEach(function(project) {
        const item = document.createElement('div');
        item.className = 'list-item' + (project.id === currentProjectId ? ' active' : '');
        item.dataset.projectId = project.id;
        item.onclick = function() { selectProject(project.id); };
        const editBtn = document.createElement('button');
        editBtn.className = 'edit-btn';
        editBtn.title = 'Edit name & tags';
        editBtn.textContent = '\u270E';
        editBtn.onclick = function(e) { e.stopPropagation(); openEditProject(project.id); };
        const title = document.createElement('div');
        title.className = 'list-item-title';
        title.textContent = project.custom_name || project.name;
        const meta = document.createElement('div');
        meta.className = 'list-item-meta';
        var dateStr = fmtProjectDate(project.last_modified);
        meta.textContent = project.session_count + ' sessions' + (dateStr ? '  ·  updated ' + dateStr : '') + ' ';
        (project.tags || []).forEach(function(t) {
            const chip = document.createElement('span');
            chip.className = 'tag-chip';
            chip.style.background = tagColor(t);
            chip.textContent = t;
            meta.appendChild(chip);
        });
        item.appendChild(editBtn);
        item.appendChild(title);
        item.appendChild(meta);
        container.appendChild(item);
    });
}

async function openEditProject(projectId) {
    const project = projects.find(function(p) { return p.id === projectId; });
    if (!project) return;
    const newName = prompt('Custom project name (leave empty to use default):', project.custom_name || '');
    if (newName === null) return;
    const newTagsStr = prompt('Tags (comma-separated):', (project.tags || []).join(', '));
    if (newTagsStr === null) return;
    const newTags = newTagsStr.split(',').map(function(t) { return t.trim(); }).filter(Boolean);
    try {
        await fetch('/api/projects/' + projectId + '/meta?source=' + currentSource, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ custom_name: newName, tags: newTags }),
        });
        await loadProjects();
        await loadTagFilters();
    } catch (e) { console.error('Error saving project metadata:', e); }
}

// Moving the highlight used to re-render the whole project list.
function markActiveProject(projectId) {
    var container = document.getElementById('projects-list');
    if (!container) return;
    container.querySelectorAll('.list-item.active').forEach(function(el) {
        el.classList.remove('active');
    });
    var next = container.querySelector('.list-item[data-project-id="' + CSS.escape(projectId) + '"]');
    if (next) next.classList.add('active');
}

async function selectProject(projectId, opts) {
    opts = opts || {};
    currentProjectId = projectId;
    if (!opts.keepSession) currentSessionId = null;
    markActiveProject(projectId);
    if (!suppressRouting && !opts.keepSession && typeof pushRoute === 'function') pushRoute();

    document.getElementById('export-btn').disabled = true;
    document.getElementById('copy-btn').disabled = true;
    setPanel('sessions-list', loadingSpinner('Loading sessions...'));

    var seq = ++projectRequestSeq;
    try {
        var r = await fetch('/api/projects/' + encodeURIComponent(projectId) +
            '/sessions?source=' + encodeURIComponent(currentSource) +
            '&limit=' + SESSION_PAGE);
        if (seq !== projectRequestSeq) return;      // a newer project was clicked
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var data = await r.json();
        if (seq !== projectRequestSeq) return;
        // Older builds returned a bare array; accept both shapes.
        var list = Array.isArray(data) ? data : data.sessions;
        if (!Array.isArray(list)) throw new Error('bad payload');
        currentSessions = list;
        sessionTotal = Array.isArray(data) ? list.length : (data.total || list.length);
        document.getElementById('session-filters').style.display = 'flex';
        applySessionFilters();
    } catch (e) {
        if (seq !== projectRequestSeq) return;
        setPanel('sessions-list', errorState('Could not load sessions.', function() {
            selectProject(projectId, opts);
        }));
    }
}

// Fetch the rest of a large project's sessions on demand. Loading all 10,581
// up front cost ~21 MB of server RAM and a 7 MB response for a list nobody
// scrolls to the end of.
async function loadMoreSessions() {
    if (currentSessions.length >= sessionTotal) return;
    var btn = document.getElementById('load-more-sessions');
    if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
    var seq = projectRequestSeq;
    try {
        var r = await fetch('/api/projects/' + encodeURIComponent(currentProjectId) +
            '/sessions?source=' + encodeURIComponent(currentSource) +
            '&limit=' + SESSION_PAGE + '&offset=' + currentSessions.length);
        if (seq !== projectRequestSeq) return;
        var data = await r.json();
        var list = Array.isArray(data) ? data : data.sessions;
        if (Array.isArray(list) && list.length) {
            currentSessions = currentSessions.concat(list);
            applySessionFilters();
        }
    } catch (e) { /* the button is restored below */ }
    if (btn) { btn.disabled = false; }
}
