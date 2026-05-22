DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LeadAgent Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
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
    </div>

    <script>
        async function fetchData() {
            try {
                const resp = await fetch('/health');
                const data = await resp.json();
                
                // document.getElementById('uptime').textContent = formatSeconds(data.uptime_seconds);
                // document.getElementById('entity-count').textContent = data.components.database.entity_count;
                
                updateQuotaGrid(data.components.agents, data.quotas);
                updateAgentList(data.components.agents);
                updateROI();
            } catch (err) {
                console.error('Failed to fetch health:', err);
                document.getElementById('connection-status').innerHTML = '<span class="w-2 h-2 rounded-full bg-red-500"></span> Disconnected';
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
                    
                    const lines = item.content.split('\\n');
                    const userLine = lines[0].replace('User: ', '');
                    const agent = item.metadata.agent || 'unknown';
                    const sid = item.metadata.session_id || 'default';
                    
                    entry.innerHTML = `
                        <div class="flex justify-between mb-1">
                            <span class="text-indigo-400 font-semibold text-xs uppercase">${agent}</span>
                            <button onclick="toggleAudit('${sid}', this)" class="text-[10px] text-slate-500 hover:text-indigo-400 transition">Audit Trace</button>
                        </div>
                        <div class="text-slate-300 truncate mb-2">${userLine}</div>
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

        // Init
        lucide.createIcons();
        fetchData();
        refreshHistory();
        setInterval(fetchData, 5000);
        setInterval(refreshHistory, 30000);
    </script>
</body>
</html>
"""
