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
            <div class="flex items-center gap-3">
                <div id="connection-status" class="flex items-center gap-2 text-sm text-slate-400">
                    <span class="w-2 h-2 rounded-full bg-green-500"></span>
                    Daemon Connected
                </div>
                <form method="post" action="/dashboard/logout">
                    <button type="submit" title="Sign out" class="text-slate-500 hover:text-red-400 transition">
                        <i data-lucide="log-out" class="w-4 h-4"></i>
                    </button>
                </form>
            </div>
        </header>

        <!-- Knowledge Graph -->
        <div class="card p-5 mb-8">
            <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-2 text-slate-400">
                    <i data-lucide="network" class="w-4 h-4"></i>
                    <h2 class="text-sm font-semibold uppercase tracking-wider">Memory Graph</h2>
                </div>
                <div class="flex items-center gap-2">
                    <select id="graph-type-filter" onchange="applyFilters()"
                        class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-indigo-500 cursor-pointer">
                        <option value="">All types</option>
                        <option value="extracted">Entity (extracted)</option>
                        <option value="concept">Concept</option>
                        <option value="file">File</option>
                        <option value="sem_episodic">Episodic memory</option>
                        <option value="sem_semantic">Semantic memory</option>
                    </select>
                    <input id="graph-search" type="text" placeholder="Search nodes…"
                        oninput="applyFilters()"
                        class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-white w-36 focus:outline-none focus:border-indigo-500">
                    <button onclick="togglePhysics()" id="physics-btn"
                        class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition">⏸ Freeze</button>
                    <button onclick="refreshGraph()" class="text-slate-500 hover:text-white transition ml-1">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>
            <!-- legend -->
            <div class="flex flex-wrap gap-3 mb-3 text-[10px] text-slate-400">
                <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full inline-block" style="background:#6366f1"></span>Entity (extracted)</span>
                <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full inline-block" style="background:#8b5cf6"></span>Concept</span>
                <span class="flex items-center gap-1"><span class="w-2 h-2 rounded" style="background:#475569;transform:rotate(45deg);display:inline-block"></span> File</span>
                <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full inline-block" style="background:#f59e0b"></span>Episodic memory</span>
                <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full inline-block" style="background:#10b981"></span>Semantic memory</span>
            </div>
            <div class="flex gap-3">
                <div id="knowledge-graph" class="flex-1 h-[480px] bg-slate-900/50 rounded-lg border border-slate-700/50"></div>
                <!-- side panel -->
                <div id="graph-panel" class="hidden w-64 flex-shrink-0 bg-slate-900/70 rounded-lg border border-slate-700/50 p-4 text-sm overflow-y-auto">
                    <div class="flex justify-between items-start mb-3">
                        <span class="font-semibold text-white" id="panel-title">Node</span>
                        <button onclick="document.getElementById('graph-panel').classList.add('hidden')" class="text-slate-500 hover:text-white text-xs">✕</button>
                    </div>
                    <div id="panel-body" class="space-y-2 text-xs text-slate-300"></div>
                    <button id="panel-forget" onclick="forgetNode()"
                        class="mt-4 w-full text-xs bg-red-900/50 hover:bg-red-800 text-red-300 border border-red-800 rounded px-2 py-1.5 transition hidden">
                        🗑 Forget this entity
                    </button>
                </div>
            </div>
        </div>

        <div class="card p-5 mb-8">
            <div class="flex items-center gap-2 mb-4 text-slate-400">
                <i data-lucide="settings" class="w-4 h-4"></i>
                <h2 class="text-sm font-semibold uppercase tracking-wider">Agent Routing</h2>
            </div>
            <div id="agent-list" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <!-- Agents injected here -->
            </div>
        </div>

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
                
                updateAgentList(data.components.agents);
            } catch (err) {
                console.error('Failed to fetch health:', err);
                document.getElementById('connection-status').innerHTML = '<span class="w-2 h-2 rounded-full bg-red-500"></span> Disconnected';
            }
        }

        let network = null;
        let allNodes = [], allEdges = [];
        let physicsOn = true;
        let selectedNodeId = null;

        async function refreshGraph() {
            try {
                const resp = await fetch('/memory/graph/d3');
                const data = await resp.json();
                allNodes = data.nodes;
                allEdges = data.edges;
                renderGraph(allNodes, allEdges);
            } catch (err) {
                console.error('Graph fetch failed:', err);
            }
        }

        function renderGraph(nodes, edges) {
            const container = document.getElementById('knowledge-graph');
            const options = {
                nodes: {
                    shape: 'dot', size: 14,
                    font: { size: 11, color: '#cbd5e1' },
                    borderWidth: 2, shadow: true,
                },
                edges: {
                    width: 1.2,
                    color: { inherit: 'from', opacity: 0.6 },
                    arrows: { to: { enabled: true, scaleFactor: 0.4 } },
                    smooth: { type: 'dynamic' },
                    font: { size: 9, color: '#64748b', align: 'middle' },
                },
                groups: {
                    extracted:   { color: { background: '#6366f1', border: '#4338ca' } },
                    concept:     { color: { background: '#8b5cf6', border: '#6d28d9' } },
                    entity:      { color: { background: '#6366f1', border: '#4338ca' } },
                    file:        { color: { background: '#475569', border: '#334155' }, shape: 'diamond' },
                    sem_episodic:{ color: { background: '#f59e0b', border: '#d97706' }, shape: 'square' },
                    sem_semantic:{ color: { background: '#10b981', border: '#059669' }, shape: 'square' },
                },
                physics: {
                    enabled: physicsOn,
                    barnesHut: { gravitationalConstant: -4000, springLength: 120 },
                    stabilization: { iterations: 150 },
                },
                interaction: { hover: true, tooltipDelay: 150 },
            };
            if (network) network.destroy();
            network = new vis.Network(container, { nodes, edges }, options);

            network.on('click', params => {
                if (params.nodes.length > 0) showNodePanel(params.nodes[0]);
                else document.getElementById('graph-panel').classList.add('hidden');
            });

            network.on('doubleClick', params => {
                if (params.nodes.length > 0) {
                    const n = allNodes.find(x => x.id === params.nodes[0]);
                    if (n && n.source === 'kuzu' && n.group !== 'file') {
                        if (confirm(`Forget entity "${n.label}" from memory?`)) forgetNode(n.id);
                    }
                }
            });
        }

        function showNodePanel(nodeId) {
            selectedNodeId = nodeId;
            const n = allNodes.find(x => x.id === nodeId);
            if (!n) return;
            const panel = document.getElementById('graph-panel');
            panel.classList.remove('hidden');
            document.getElementById('panel-title').textContent = n.label;
            const connected = allEdges
                .filter(e => e.from === nodeId || e.to === nodeId)
                .map(e => {
                    const otherId = e.from === nodeId ? e.to : e.from;
                    const other = allNodes.find(x => x.id === otherId);
                    return `<span class="inline-block bg-slate-700 rounded px-1.5 py-0.5 mr-1 mb-1">${other ? other.label : otherId}</span>`;
                }).join('');
            document.getElementById('panel-body').innerHTML = `
                <div><span class="text-slate-500">Source:</span> ${n.source === 'agentmemory' ? '🟡 AgentMemory' : '🔵 KuzuDB'}</div>
                <div><span class="text-slate-500">Type:</span> ${n.group}</div>
                ${connected ? `<div class="mt-2"><div class="text-slate-500 mb-1">Connected to:</div>${connected}</div>` : '<div class="text-slate-500">No connections</div>'}
            `;
            const forgetBtn = document.getElementById('panel-forget');
            if (n.source === 'kuzu' && n.group !== 'file') forgetBtn.classList.remove('hidden');
            else forgetBtn.classList.add('hidden');
        }

        async function forgetNode(nodeId) {
            const id = nodeId || selectedNodeId;
            if (!id) return;
            const n = allNodes.find(x => x.id === id);
            if (!n) return;
            if (!nodeId && !confirm(`Forget entity "${n.label}" from memory?`)) return;
            try {
                await fetch('/memory/forget', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ entity_name: id }),
                });
                document.getElementById('graph-panel').classList.add('hidden');
                await refreshGraph();
            } catch (err) {
                alert('Failed to forget node: ' + err.message);
            }
        }

        function applyFilters() {
            if (!network) return;
            const typeFilter = document.getElementById('graph-type-filter').value;
            const q = document.getElementById('graph-search').value.trim().toLowerCase();

            // Step 1: type filter — if set, only nodes of that group are "in scope"
            const typeMatch = n => !typeFilter || n.group === typeFilter;

            // Step 2: search filter — within type-scoped nodes, highlight by label
            const searchMatch = n => !q || n.label.toLowerCase().includes(q);

            const activeIds = new Set(allNodes.filter(n => typeMatch(n) && searchMatch(n)).map(n => n.id));
            const dimIds   = new Set(allNodes.filter(n => typeMatch(n) && !searchMatch(n)).map(n => n.id));
            // nodes outside type filter are hidden entirely when a type is selected
            const hiddenIds = new Set(allNodes.filter(n => !typeMatch(n)).map(n => n.id));

            const noFilter = !typeFilter && !q;

            const styled = allNodes.map(n => {
                if (noFilter) return { ...n, opacity: 1, font: { color: '#cbd5e1' } };
                if (hiddenIds.has(n.id)) return { ...n, opacity: 0.05, font: { color: '#1e293b' } };
                if (activeIds.has(n.id)) return { ...n, opacity: 1,    font: { color: '#f8fafc' } };
                return { ...n, opacity: 0.12, font: { color: '#334155' } };
            });

            const styledEdges = allEdges.map(e => ({
                ...e,
                color: (noFilter || (activeIds.has(e.from) && activeIds.has(e.to)))
                    ? undefined
                    : { color: '#1e293b', opacity: 0.08 },
            }));

            network.setData({ nodes: styled, edges: styledEdges });
        }

        function togglePhysics() {
            physicsOn = !physicsOn;
            if (network) network.setOptions({ physics: { enabled: physicsOn } });
            document.getElementById('physics-btn').textContent = physicsOn ? '⏸ Freeze' : '▶ Unfreeze';
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
                grok: '#e8a840',
                ollama: '#64748b'
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
