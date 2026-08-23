function serverExportUrl(fmt) {
    return '/api/projects/' + encodeURIComponent(currentProjectId) +
        '/sessions/' + encodeURIComponent(currentSessionId) +
        '/export?source=' + encodeURIComponent(currentSource) +
        '&fmt=' + encodeURIComponent(fmt);
}

// Exports go through the server, which re-reads the file with tool outputs
// included. The browser payload deliberately omits them — they are the bulk of
// a large session and the viewer never renders them.
function downloadVia(url, filename) {
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

function exportConversation() {
    if (!currentProjectId || !currentSessionId) return;
    var filtered = (dateFromFilter || dateToFilter);
    if (filtered && currentConversation) {
        // A date-filtered export is a view of what is on screen, so build it
        // client-side from the messages actually in scope.
        var msgs = (typeof getChronologicalMessages === 'function')
            ? getChronologicalMessages() : currentConversation.messages;
        var blob = new Blob([buildConversationText(currentConversation, msgs)],
            { type: 'text/plain' });
        var url = URL.createObjectURL(blob);
        downloadVia(url, (currentSessionId || 'conversation') + '-filtered.txt');
        setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
        return;
    }
    downloadVia(serverExportUrl('txt'), (currentSessionId || 'conversation') + '.txt');
}

function exportMarkdown() {
    if (!currentProjectId || !currentSessionId) return;
    downloadVia(serverExportUrl('md'), (currentSessionId || 'conversation') + '.md');
}

function exportRawConversation() {
    if (!currentProjectId || !currentSessionId) return;
    // Server streams the exact source file — the complete raw record.
    var url = '/api/projects/' + encodeURIComponent(currentProjectId) +
        '/sessions/' + encodeURIComponent(currentSessionId) +
        '/export-raw?source=' + encodeURIComponent(currentSource);
    var a = document.createElement('a');
    a.href = url;
    a.download = (currentSessionId || 'conversation') + '.raw.jsonl';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

async function copyConversation() {
    if (!currentConversation) return;
    var msgs = (typeof getChronologicalMessages === 'function') ? getChronologicalMessages() : currentConversation.messages;
    try {
        var text;
        if (!dateFromFilter && !dateToFilter && currentProjectId && currentSessionId) {
            // Prefer the server rendering so tool outputs are included.
            try {
                var r = await fetch(serverExportUrl('txt'));
                if (r.ok) text = await r.text();
            } catch (e) { /* fall through to the local build */ }
        }
        if (!text) text = buildConversationText(currentConversation, msgs);
        await navigator.clipboard.writeText(text);
        var btn = document.getElementById('copy-btn');
        if (btn) {
            var label = btn.querySelector('span:last-child');
            if (label) {
                var orig = label.textContent;
                label.textContent = 'Copied!';
                setTimeout(function() { label.textContent = orig; }, 2000);
            }
        }
    } catch (e) { alert('Failed to copy to clipboard'); }
}

function buildConversationText(conv, messages) {
    var sep60 = '============================================================';
    var sep40 = '----------------------------------------';
    var sep60dash = '------------------------------------------------------------';
    var lines = [sep60, 'Session: ' + conv.session_id, 'Source: ' + currentSource, sep60, ''];
    if (conv.summaries && conv.summaries.length > 0) {
        lines.push('SUMMARIES:');
        conv.summaries.forEach(function(s) { lines.push('  \u2022 ' + s); });
        lines.push('', sep60dash, '');
    }
    var msgList = messages || conv.messages;
    msgList.forEach(function(msg) {
        lines.push('[' + msg.role.toUpperCase() + '] ' + (msg.timestamp || ''));
        if (msg.model) lines.push('Model: ' + msg.model);
        lines.push(sep40);
        if (msg.content) lines.push(msg.content);
        if (msg.thinking) lines.push('', '--- THINKING ---', msg.thinking, '--- END THINKING ---');
        if (msg.tool_uses && msg.tool_uses.length > 0) {
            lines.push('');
            msg.tool_uses.forEach(function(tool) {
                lines.push('[TOOL: ' + tool.name + ']');
                // Full, untruncated input. Objects rendered as pretty JSON so
                // every field/value is preserved exactly, bit for bit.
                lines.push('INPUT:');
                if (tool.input && typeof tool.input === 'object') {
                    lines.push(JSON.stringify(tool.input, null, 2));
                } else {
                    lines.push(String(tool.input == null ? '' : tool.input));
                }
                if (tool.result != null && String(tool.result).length) {
                    lines.push('OUTPUT:');
                    lines.push(String(tool.result));
                }
            });
        }
        if (msg.usage) lines.push('\n[Tokens: ' + totalTokens(msg.usage) + ']');
        lines.push('', sep60, '');
    });
    return lines.join('\n');
}
