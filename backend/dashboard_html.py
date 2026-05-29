DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LeadAgent Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Fira+Code:wght@400;500&display=swap');
        body { font-family: 'Inter', sans-serif; background-color: #0f172a; color: #f8fafc; }
        .mono { font-family: 'Fira Code', monospace; }
        .card { background-color: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; }
        .badge { padding: 0.125rem 0.5rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }
        .progress-bg { background-color: #334155; height: 0.5rem; border-radius: 9999px; overflow: hidden; }
        .progress-fill { height: 100%; transition: width 0.5s ease-out; }
    </style>
</head>
<body class="p-6">
    <div class="max-w-6xl mx-auto">
        <header class="flex justify-between items-center mb-8">
            <div class="flex items-center gap-3">
                <div class="bg-indigo-600 p-2 rounded-lg">
                    <i data-lucide="brain" class="text-white w-6 h-6"></i>
                </div>
                <h1 class="text-2xl font-bold tracking-tight">LeadAgent <span class="text-indigo-400">Dashboard</span></h1>
            </div>
            <div id="connection-status" class="flex items-center gap-2 text-sm text-slate-400">
                <span class="w-2 h-2 rounded-full bg-green-500"></span>
                Daemon Connected
            </div>
        </header>

        <!-- Knowledge Graph Map (Phase 2) -->
        <div class="card p-5 mb-8">
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2 text-slate-400">
                    <i data-lucide="network" class="w-4 h-4"></i>
                    <h2 class="text-sm font-semibold uppercase tracking-wider">Knowledge Graph (Real-time)</h2>
                </div>
                <button onclick="refreshGraph()" class="text-slate-500 hover:text-white transition">
                    <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                </button>
            </div>
            <div id="knowledge-graph" class="h-[400px] w-full bg-slate-900/50 rounded-lg border border-slate-700/50"></div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div class="card p-5">
                <div class="flex items-center gap-2 mb-4 text-slate-400">
                    <i data-lucide="bar-chart-3" class="w-4 h-4"></i>
                    <h2 class="text-sm font-semibold uppercase tracking-wider">Agent ROI</h2>
                </div>
                <div id="roi-stats" class="space-y-4">
                    <!-- ROI injected here -->
                </div>
            </div>

            <div class="md:col-span-2 card p-5">
                <div class="flex items-center gap-2 mb-4 text-slate-400">
                    <i data-lucide="zap" class="w-4 h-4"></i>
                    <h2 class="text-sm font-semibold uppercase tracking-wider">Active Quotas</h2>
                </div>
                <div id="quota-grid" class="grid grid-cols-1 sm:grid-cols-2 gap-6">
                    <!-- Quotas injected here -->
                </div>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div class="card p-5">
                <div class="flex items-center justify-between mb-4">
                    <div class="flex items-center gap-2 text-slate-400">
                        <i data-lucide="list" class="w-4 h-4"></i>
                        <h2 class="text-sm font-semibold uppercase tracking-wider">Recent Activity</h2>
                    </div>
                    <button onclick="refreshHistory()" class="text-slate-500 hover:text-white transition">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
                <div id="activity-list" class="space-y-4">
                    <!-- History injected here -->
                </div>
            </div>

            <div class="card p-5">
                <div class="flex items-center gap-2 mb-4 text-slate-400">
                    <i data-lucide="settings" class="w-4 h-4"></i>
                    <h2 class="text-sm font-semibold uppercase tracking-wider">Agent Routing</h2>
                </div>
                <div id="agent-list" class="space-y-4">
                    <!-- Agents injected here -->
                </div>
            </div>
        </div>

        <!-- MCP Rules -->
        <div class="card p-5 mt-6">
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2 text-slate-400">
                    <i data-lucide="shield" class="w-4 h-4"></i>
                    <h2 class="text-sm font-semibold uppercase tracking-wider">MCP Tool Rules</h2>
                </div>
                <button onclick="refreshRules()" class="text-slate-500 hover:text-white transition">
                    <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                </button>
            </div>
            <p class="text-xs text-slate-500 mb-4">Structural enforcement — rules are evaluated before any tool reaches the agent. Higher priority wins.</p>
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead>
                        <tr class="text-left text-xs uppercase text-slate-500 border-b border-slate-700">
                            <th class="pb-2 pr-4">Tool</th>
                            <th class="pb-2 pr-4">Scope</th>
                            <th class="pb-2 pr-4">Reason</th>
                            <th class="pb-2 pr-4">Priority</th>
                            <th class="pb-2 pr-4">Action</th>
                            <th class="pb-2"></th>
                        </tr>
                    </thead>
                    <tbody id="rules-table" class="divide-y divide-slate-800">
                        <!-- Rules injected here -->
                    </tbody>
                </table>
            </div>
            <!-- Add rule form -->
            <div class="mt-4 pt-4 border-t border-slate-700">
                <div class="flex flex-wrap gap-2 items-end">
                    <div>
                        <label class="block text-xs text-slate-500 mb-1">Tool pattern</label>
                        <input id="new-tool" type="text" placeholder="e.g. Bash, write_file, *" class="bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm text-white w-40 focus:outline-none focus:border-indigo-500">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-500 mb-1">Action</label>
                        <select id="new-action" class="bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500">
                            <option value="ask">ask</option>
                            <option value="allow">allow</option>
                            <option value="deny">deny</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs text-slate-500 mb-1">Scope</label>
                        <div class="flex gap-1">
                            <select id="new-scope-type" onchange="onScopeTypeChange()" class="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500">
                                <option value="global">global</option>
                                <option value="agent">agent:</option>
                                <option value="session">session:</option>
                            </select>
                            <input id="new-scope-value" type="text" placeholder="name or id" class="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-sm text-white w-28 focus:outline-none focus:border-indigo-500 hidden">
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs text-slate-500 mb-1">Priority</label>
                        <input id="new-priority" type="number" placeholder="0" value="10" class="bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm text-white w-20 focus:outline-none focus:border-indigo-500">
                    </div>
                    <div class="flex-1">
                        <label class="block text-xs text-slate-500 mb-1">Reason (optional)</label>
                        <input id="new-reason" type="text" placeholder="Why this rule exists" class="bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm text-white w-full focus:outline-none focus:border-indigo-500">
                    </div>
                    <button onclick="addRule()" class="bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold px-4 py-1.5 rounded transition">
                        + Add Rule
                    </button>
                </div>
                <div id="rules-feedback" class="mt-2 text-xs hidden"></div>
            </div>
        </div>
    </div>

    <script>
        async function fetchData() {
            try {
                const resp = await fetch('/health');
                const data = await resp.json();
                
                updateQuotaGrid(data.components.agents, data.quotas);
                updateAgentList(data.components.agents);
                updateROI();
            } catch (err) {
                console.error('Failed to fetch health:', err);
                document.getElementById('connection-status').innerHTML = '<span class="w-2 h-2 rounded-full bg-red-500"></span> Disconnected';
            }
        }

        let network = null;
        async function refreshGraph() {
            try {
                const resp = await fetch('/memory/graph/d3');
                const data = await resp.json();
                
                const container = document.getElementById('knowledge-graph');
                const options = {
                    nodes: {
                        shape: 'dot',
                        size: 16,
                        font: { size: 12, color: '#f8fafc' },
                        borderWidth: 2,
                        shadow: true
                    },
                    edges: {
                        width: 1,
                        color: { inherit: 'from' },
                        arrows: { to: { enabled: true, scaleFactor: 0.5 } },
                        smooth: { type: 'continuous' }
                    },
                    groups: {
                        file: { color: { background: '#64748b', border: '#475569' }, shape: 'diamond' },
                        entity: { color: { background: '#6366f1', border: '#4338ca' } }
                    },
                    physics: {
                        enabled: true,
                        stabilization: { iterations: 100 }
                    }
                };
                
                if (network) network.destroy();
                network = new vis.Network(container, data, options);
            } catch (err) {
                console.error('Graph fetch failed:', err);
            }
        }

        async function updateROI() {
            try {
                const resp = await fetch('/v1/roi');
                const data = await resp.json();
                const container = document.getElementById('roi-stats');
                container.innerHTML = '';
                
                Object.entries(data).forEach(([agent, stats]) => {
                    const div = document.createElement('div');
                    div.className = 'space-y-1';
                    const rate = (stats.success_rate * 100).toFixed(0);
                    div.innerHTML = `
                        <div class="flex justify-between text-[10px] uppercase font-bold text-slate-500">
                            <span>${agent}</span>
                            <span>${rate}% success</span>
                        </div>
                        <div class="progress-bg h-1">
                            <div class="progress-fill bg-indigo-500" style="width: ${rate}%"></div>
                        </div>
                    `;
                    container.appendChild(div);
                });
            } catch (err) {}
        }

        function updateQuotaGrid(agents, quotas) {
            const grid = document.getElementById('quota-grid');
            grid.innerHTML = '';
            
            ['claude', 'gemini'].forEach(key => {
                const q = quotas[key];
                const ag = agents[key];
                if (!ag || !ag.installed) return;

                const pct = q.real_daily_pct || 0;
                const colorClass = pct > 80 ? 'bg-red-500' : (pct > 50 ? 'bg-yellow-500' : 'bg-green-500');
                
                const card = document.createElement('div');
                card.className = 'space-y-2';
                card.innerHTML = `
                    <div class="flex justify-between items-center text-xs">
                        <span class="font-bold uppercase" style="color: ${getAgentColor(key)}">${key}</span>
                        <span class="text-slate-400">${pct.toFixed(0)}%</span>
                    </div>
                    <div class="progress-bg">
                        <div class="progress-fill ${colorClass}" style="width: ${pct}%"></div>
                    </div>
                    <div class="text-[10px] text-slate-500">Daily subscription limit</div>
                `;
                grid.appendChild(card);
            });
        }

        function updateAgentList(agents) {
            const list = document.getElementById('agent-list');
            list.innerHTML = '';
            
            Object.entries(agents).forEach(([key, ag]) => {
                const item = document.createElement('div');
                item.className = 'flex items-center justify-between p-3 bg-slate-800/50 rounded-lg border border-slate-700/50';
                
                let statusBadge = '<span class="badge bg-slate-700 text-slate-400">Offline</span>';
                if (ag.available) statusBadge = '<span class="badge bg-green-900 text-green-300">Available</span>';
                else if (ag.exhausted) statusBadge = '<span class="badge bg-yellow-900 text-yellow-300">Exhausted</span>';
                else if (ag.installed && ag.signed_in === false) statusBadge = '<span class="badge bg-orange-900 text-orange-300">Sign In Req</span>';

                item.innerHTML = `
                    <div class="flex items-center gap-3">
                        <div class="w-2 h-2 rounded-full" style="background-color: ${getAgentColor(key)}"></div>
                        <span class="font-semibold capitalize">${key}</span>
                    </div>
                    ${statusBadge}
                `;
                list.appendChild(item);
            });
        }

        async function refreshHistory() {
            const list = document.getElementById('activity-list');
            list.innerHTML = '<div class="text-slate-500 text-sm italic">Loading history...</div>';
            
            try {
                const resp = await fetch('/v1/history?limit=5');
                const data = await resp.json();
                
                list.innerHTML = '';
                if (data.length === 0) {
                    list.innerHTML = '<div class="text-slate-500 text-sm italic">No recent activity.</div>';
                }
                
                for (const item of data) {
                    const entry = document.createElement('div');
                    entry.className = 'p-3 bg-slate-800/30 rounded-lg border border-slate-700/30 text-sm';
                    
                    // Strip Go CLI conversation history — take last "User:" segment
                    let userLine = item.content;
                    const lastUser = userLine.lastIndexOf('\\nUser:');
                    if (lastUser !== -1) userLine = userLine.slice(lastUser + 6).trim();
                    else userLine = userLine.replace(/^User:\s*/, '').split('\\n')[0];
                    userLine = userLine.replace(/=== YOUR TASK ===/g, '').replace(/=== END TASK ===/g, '').trim();
                    const agent = item.metadata.agent || 'unknown';
                    const sid = item.metadata.session_id || 'default';
                    
                    const preview = item.metadata.answer_preview || '';
                    entry.innerHTML = `
                        <div class="flex justify-between mb-1">
                            <span class="text-indigo-400 font-semibold text-xs uppercase">${agent}</span>
                            <button onclick="toggleAudit('${sid}', this)" class="text-[10px] text-slate-500 hover:text-indigo-400 transition">Audit Trace</button>
                        </div>
                        <div class="text-slate-300 truncate mb-1">${userLine}</div>
                        ${preview ? `<div class="text-slate-500 text-xs truncate">${preview}</div>` : ''}
                        <div class="audit-details hidden space-y-2 border-t border-slate-700 pt-2 mt-2 text-[10px] text-slate-400">
                            <div class="italic">Loading causal narrative...</div>
                        </div>
                    `;
                    list.appendChild(entry);
                }
            } catch (err) {
                list.innerHTML = '<div class="text-red-400 text-sm">Error loading activity.</div>';
            }
        }

        async function toggleAudit(sid, btn) {
            const entry = btn.closest('div').parentElement;
            const details = entry.querySelector('.audit-details');
            details.classList.toggle('hidden');
            
            if (!details.classList.contains('hidden')) {
                try {
                    const resp = await fetch(`/v1/audit/${sid}`);
                    const data = await resp.json();
                    
                    if (data.length > 0) {
                        const rationale = data[0].rationale;
                        details.innerHTML = `
                            <div class="flex justify-between"><span>Task Type:</span><span class="mono">${rationale.task_type}</span></div>
                            <div class="flex justify-between"><span>Complexity:</span><span class="mono">${rationale.complexity}</span></div>
                            <div class="flex justify-between"><span>Historical Affinity:</span><span class="mono">${rationale.historical_affinity.toFixed(2)}</span></div>
                            <div class="text-slate-500 mt-1">Known Risks: ${Object.entries(rationale.known_failure_risks).map(([e, c]) => `${e}(${c})`).join(', ') || 'None'}</div>
                        `;
                    } else {
                        details.innerHTML = 'No audit data found.';
                    }
                } catch (err) {
                    details.innerHTML = 'Failed to load audit.';
                }
            }
        }

        function formatSeconds(s) {
            const h = Math.floor(s / 3600);
            const m = Math.floor((s % 3600) / 60);
            const sec = Math.floor(s % 60);
            if (h > 0) return `${h}h ${m}m ${sec}s`;
            if (m > 0) return `${m}m ${sec}s`;
            return `${sec}s`;
        }

        function getAgentColor(key) {
            const colors = {
                claude: '#a78cf7',
                gemini: '#5e9cf5',
                codex: '#5dba6e',
                grok: '#e8a840'
            };
            return colors[key] || '#64748b';
        }

        // ── MCP Rules ────────────────────────────────────────────────────────

        const ACTION_STYLES = {
            allow: 'bg-green-900 text-green-300',
            deny:  'bg-red-900 text-red-300',
            ask:   'bg-yellow-900 text-yellow-300',
        };

        async function refreshRules() {
            const tbody = document.getElementById('rules-table');
            tbody.innerHTML = '<tr><td colspan="6" class="py-3 text-slate-500 text-xs italic">Loading...</td></tr>';
            try {
                const resp = await fetch('/rules');
                const rules = await resp.json();
                tbody.innerHTML = '';
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" class="py-3 text-slate-500 text-xs italic">No rules defined. Add one below.</td></tr>';
                    return;
                }
                rules.forEach(r => {
                    const tr = document.createElement('tr');
                    tr.className = 'text-slate-300 text-sm';
                    const style = ACTION_STYLES[r.action] || 'bg-slate-700 text-slate-300';
                    tr.innerHTML = `
                        <td class="py-2 pr-4 mono font-medium text-white">${r.tool_pattern}</td>
                        <td class="py-2 pr-4 text-xs text-slate-400">${r.scope}</td>
                        <td class="py-2 pr-4 text-xs text-slate-400 max-w-[180px] truncate" title="${r.reason || ''}">${r.reason || '—'}</td>
                        <td class="py-2 pr-4 text-xs text-slate-400">${r.priority}</td>
                        <td class="py-2 pr-4">
                            <select onchange="updateRuleAction('${r.id}', this.value, this)" class="bg-slate-800 border border-slate-700 rounded px-2 py-0.5 text-xs font-semibold ${style} focus:outline-none cursor-pointer">
                                <option value="allow" ${r.action==='allow'?'selected':''}>allow</option>
                                <option value="ask"   ${r.action==='ask'  ?'selected':''}>ask</option>
                                <option value="deny"  ${r.action==='deny' ?'selected':''}>deny</option>
                            </select>
                        </td>
                        <td class="py-2">
                            <button onclick="deleteRule('${r.id}', this)" class="text-slate-600 hover:text-red-400 transition" title="Delete rule">
                                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                            </button>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
                lucide.createIcons();
            } catch (err) {
                tbody.innerHTML = '<tr><td colspan="6" class="py-3 text-red-400 text-xs">Failed to load rules.</td></tr>';
            }
        }

        async function updateRuleAction(ruleId, newAction, selectEl) {
            // Delete the old rule and recreate with the new action, preserving other fields
            try {
                const resp = await fetch('/rules');
                const rules = await resp.json();
                const rule = rules.find(r => r.id === ruleId);
                if (!rule) return;

                await fetch(`/rules/${ruleId}`, { method: 'DELETE' });
                await fetch('/rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tool_pattern: rule.tool_pattern,
                        action: newAction,
                        scope: rule.scope,
                        reason: rule.reason || '',
                        input_match: rule.input_match || '',
                        priority: rule.priority,
                    }),
                });
                // Update select style
                selectEl.className = selectEl.className.replace(/bg-\S+ text-\S+/, '');
                const style = ACTION_STYLES[newAction] || 'bg-slate-700 text-slate-300';
                selectEl.classList.add(...style.split(' '));
                showFeedback(`Rule updated: ${rule.tool_pattern} → ${newAction}`, 'green');
                refreshRules();
            } catch (err) {
                showFeedback('Failed to update rule.', 'red');
            }
        }

        async function deleteRule(ruleId, btn) {
            try {
                await fetch(`/rules/${ruleId}`, { method: 'DELETE' });
                btn.closest('tr').remove();
                showFeedback('Rule deleted.', 'slate');
            } catch (err) {
                showFeedback('Failed to delete rule.', 'red');
            }
        }

        function onScopeTypeChange() {
            const type = document.getElementById('new-scope-type').value;
            const valInput = document.getElementById('new-scope-value');
            if (type === 'global') {
                valInput.classList.add('hidden');
                valInput.value = '';
            } else {
                valInput.classList.remove('hidden');
                valInput.placeholder = type === 'agent' ? 'claude / gemini / grok' : 'session id';
            }
        }

        async function addRule() {
            const tool = document.getElementById('new-tool').value.trim();
            const action = document.getElementById('new-action').value;
            const scopeType = document.getElementById('new-scope-type').value;
            const scopeVal = document.getElementById('new-scope-value').value.trim();
            const scope = scopeType === 'global' ? 'global' : `${scopeType}:${scopeVal}`;
            const priority = parseInt(document.getElementById('new-priority').value) || 0;
            const reason = document.getElementById('new-reason').value.trim();

            if (scopeType !== 'global' && !scopeVal) {
                showFeedback('Enter a value for the scope (agent name or session id).', 'red');
                return;
            }

            if (!tool) { showFeedback('Tool pattern is required.', 'red'); return; }

            try {
                const resp = await fetch('/rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tool_pattern: tool, action, scope, priority, reason }),
                });
                if (!resp.ok) throw new Error(await resp.text());
                document.getElementById('new-tool').value = '';
                document.getElementById('new-reason').value = '';
                document.getElementById('new-priority').value = '10';
                showFeedback(`Rule added: ${tool} → ${action}`, 'green');
                refreshRules();
            } catch (err) {
                showFeedback('Failed to add rule: ' + err.message, 'red');
            }
        }

        function showFeedback(msg, color) {
            const el = document.getElementById('rules-feedback');
            const colors = { green: 'text-green-400', red: 'text-red-400', slate: 'text-slate-400' };
            el.className = `mt-2 text-xs ${colors[color] || 'text-slate-400'}`;
            el.textContent = msg;
            el.classList.remove('hidden');
            setTimeout(() => el.classList.add('hidden'), 3000);
        }

        // Init
        lucide.createIcons();
        fetchData();
        refreshHistory();
        refreshGraph();
        refreshRules();
        setInterval(fetchData, 5000);
        setInterval(refreshHistory, 30000);
        setInterval(refreshGraph, 60000);
        setInterval(refreshRules, 15000);
    </script>
</body>
</html>
"""
