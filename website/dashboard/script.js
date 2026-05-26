const Dashboard = {
    token: localStorage.getItem('prime_session_token'),
    user: null,
    guilds: [],

    async init() {
        const params = new URLSearchParams(window.location.search);
        const newToken = params.get('session_token');
        if (newToken) {
            this.token = newToken;
            localStorage.setItem('prime_session_token', newToken);
            window.history.replaceState({}, document.title, window.location.pathname);
        }

        this.startClock();
        this.bindNav();

        if (this.token) {
            await this.boot();
        } else {
            this.logout();
        }
    },

    async boot() {
        try {
            const res = await fetch(`/api/me`, {
                headers: { 'X-Session-Token': this.token }
            });
            if (!res.ok) throw new Error();
            const data = await res.json();
            if (data.authenticated) {
                this.user = data.user;
                this.guilds = data.guilds;
                this.renderBase();
                this.renderServers();
                this.fetchSystemStats();
                document.body.classList.add('authenticated');
                document.body.classList.remove('loading');
                
                // Show modal to new users to choose notification channel preference
                if (this.user.is_new_user) {
                    setTimeout(() => {
                        const modal = document.getElementById('preferenceModal');
                        if (modal) modal.classList.add('active');
                    }, 800);
                }
                return;
            }
        } catch (e) {
            console.error("Boot sequence failed.");
        }
        this.logout();
    },

    logout() {
        localStorage.removeItem('prime_session_token');
        this.token = null;
        document.body.classList.remove('authenticated');
        document.body.classList.remove('loading');
    },

    renderBase() {
        document.getElementById('userName').textContent = this.user.name;
        document.getElementById('welcomeName').textContent = this.user.name;
        if (this.user.avatar) {
            document.getElementById('userAvatar').src = `https://cdn.discordapp.com/avatars/${this.user.id}/${this.user.avatar}.png`;
        } else {
            document.getElementById('userAvatar').src = `https://cdn.discordapp.com/embed/avatars/0.png`;
        }
    },

    renderServers() {
        const grid = document.getElementById('guildGrid');
        grid.innerHTML = '';
        const managed = this.guilds.filter(g => (g.permissions & 0x8) || (g.permissions & 0x20));
        if (managed.length === 0) {
            grid.innerHTML = '<p style="opacity:0.3">No managed servers found.</p>';
            return;
        }
        managed.forEach(g => {
            const icon = g.icon ? `https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png` : 'https://cdn.discordapp.com/embed/avatars/0.png';
            const card = document.createElement('div');
            card.className = `guild-card ${g.bot_present ? '' : 'missing'}`;
            card.innerHTML = `
                <img src="${icon}">
                <div class="g-meta">
                    <strong>${g.name}</strong>
                    <div class="tag">${g.bot_present ? 'ACTIVE' : 'INVITE REQUIRED'}</div>
                </div>
            `;
            card.onclick = () => {
                if (g.bot_present) this.openConfig(g);
                else this.invite(g.id);
            };
            grid.appendChild(card);
        });
    },

    async fetchSystemStats() {
        try {
            const res = await fetch(`/api/dashboard/stats`, {
                headers: { 'X-Session-Token': this.token }
            });
            const data = await res.json();
            const formatNum = (num) => {
                if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
                if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
                return num;
            };
            document.getElementById('statUsers').textContent = formatNum(data.users || 0);
            document.getElementById('statMsgs').textContent = formatNum(data.messages || 0);

            if (data.leaderboard) {
                const list = document.getElementById('leaderboardList');
                if (!list) return;
                list.innerHTML = data.leaderboard.map((u, i) => `
                    <div class="rank-row">
                        <div class="u-info">
                            <b style="color: ${i === 0 ? '#ffaa00' : 'var(--p)'}">#${i + 1}</b>
                            <span>${u.username}</span>
                        </div>
                        <div class="u-info">
                            <b style="font-size: 0.7rem; opacity: 0.6;">LVL ${u.level}</b>
                            <span style="font-size: 0.7rem;">${formatNum(u.xp)} XP</span>
                        </div>
                    </div>
                `).join('');
            }
        } catch (e) { }
    },

    async fetchAnalytics() {
        try {
            const res = await fetch(`/api/analytics/summary`, {
                headers: { 'X-Session-Token': this.token }
            });
            const data = await res.json();
            document.getElementById('anaAvgLevel').textContent = data.avg_level || '--';
            document.getElementById('anaCommands').textContent = data.commands_total || '--';
        } catch (e) { }
    },

    async openConfig(guild) {
        this.activeGuild = guild;
        this.switchTab('customization');
        document.getElementById('custTitle').textContent = guild.name.toUpperCase();
        document.getElementById('custFormContainer').style.display = 'block';
        document.getElementById('aiArchitectSection').style.display = 'block';
        document.getElementById('noGuildSelected').style.display = 'none';

        try {
            const res = await fetch(`/api/guilds/${guild.id}/settings`, {
                headers: { 'X-Session-Token': this.token }
            });
            if (res.ok) {
                const s = await res.json();
                document.getElementById('mCfgPrefix').value = s.prefix || '!';
                document.getElementById('mCfgVibe').value = s.vibe || 'chill';
                document.getElementById('mCfgWelcomeChan').value = s.welcome_channel || '';
                document.getElementById('mCfgLogChan').value = s.log_channel || '';
                document.getElementById('mCfgRulesChan').value = s.rules_channel || '';
                document.getElementById('mCfgRoleReqChan').value = s.role_request_channel || '';
                document.getElementById('mCfgVerifyChan').value = s.verification_channel || '';
                document.getElementById('mCfgLevelChan').value = s.leveling_channel || '';
                document.getElementById('mCfgGeneralChan').value = s.general_channel || '';
                document.getElementById('mCfgVerifiedRole').value = s.verified_role || '';
                document.getElementById('mCfgUnverifiedRole').value = s.unverified_role || '';
                document.getElementById('mCfgMutedRole').value = s.muted_role || '';
                document.getElementById('mCfgAesthetic').value = s.aesthetic_overlay || '';
                document.getElementById('mCfgPrompt').value = s.custom_system_prompt || '';
                document.getElementById('mCfgRoleChan').value = s.roles_channel || '';
            }
        } catch (e) { }
    },

    switchTab(tabId) {
        document.querySelectorAll('.nav-item').forEach(btn => {
            if (btn.getAttribute('data-tab') === tabId) btn.classList.add('active');
            else btn.classList.remove('active');
        });
        document.querySelectorAll('.tab').forEach(el => {
            if (el.id === `tab-${tabId}`) el.classList.add('active');
            else el.classList.remove('active');
        });
        if (tabId === 'logs') this.runLogSimulation();
        if (tabId === 'analytics') this.fetchAnalytics();
    },

    async invite(id) {
        try {
            const res = await fetch(`/api/invite-url?guild_id=${id}`);
            const data = await res.json();
            window.open(data.url, '_blank');
        } catch (e) { }
    },

    bindNav() {
        document.querySelectorAll('.nav-item').forEach(btn => {
            btn.onclick = () => this.switchTab(btn.getAttribute('data-tab'));
        });
    },

    runLogSimulation() {
        const consoleEl = document.getElementById('logConsole');
        if (!consoleEl) return;
        const logs = ["[BRAIN] Scout active...", "[DB] Syncing...", "[AI] Thinking...", "[SECURITY] Monitoring..."];
        let i = 0;
        const interval = setInterval(() => {
            if (document.querySelector('#tab-logs.active')) {
                const entry = document.createElement('div');
                entry.className = 'log-entry';
                entry.innerHTML = `<b>[${new Date().toLocaleTimeString()}]</b> ${logs[i % logs.length]}`;
                consoleEl.appendChild(entry);
                consoleEl.scrollTop = consoleEl.scrollHeight;
                i++;
                if (consoleEl.children.length > 20) consoleEl.removeChild(consoleEl.firstChild);
            } else clearInterval(interval);
        }, 3000);
    },

    startClock() {
        setInterval(() => {
            const el = document.getElementById('osClock');
            if (el) el.textContent = new Date().toLocaleTimeString();
        }, 1000);
    }
};

async function sendRelayMessage() {
    const cid = document.getElementById('relayChannelId').value;
    const content = document.getElementById('relayContent').value;
    const btn = document.getElementById('btnRelay');
    if (!cid || !content) return alert("Missing data.");
    btn.textContent = "TRANSMITTING...";
    btn.disabled = true;
    try {
        const res = await fetch(`/api/guilds/0/message`, {
            method: 'POST',
            body: JSON.stringify({ channel_id: cid, content: content }),
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': Dashboard.token }
        });
        if (res.ok) {
            btn.textContent = "✓ DELIVERED";
            document.getElementById('relayContent').value = "";
        } else btn.textContent = "❌ FAILED";
    } catch (e) { btn.textContent = "❌ ERROR"; }
    setTimeout(() => { btn.textContent = "AUTHORIZE TRANSMISSION"; btn.disabled = false; }, 2000);
}

document.addEventListener('DOMContentLoaded', () => Dashboard.init());

async function triggerAction(action) {
    if (!Dashboard.activeGuild) return;
    const res = await fetch(`/api/guilds/${Dashboard.activeGuild.id}/trigger?token=${Dashboard.token}`, {
        method: 'POST',
        body: JSON.stringify({ action }),
        headers: { 'Content-Type': 'application/json' }
    });
    alert(res.ok ? "Action Triggered!" : "Failed to trigger.");
}

async function saveActiveSettings() {
    if (!Dashboard.activeGuild) return alert("No server selected.");
    const data = {
        prefix: document.getElementById('mCfgPrefix').value,
        vibe: document.getElementById('mCfgVibe').value,
        welcome_channel: document.getElementById('mCfgWelcomeChan').value,
        log_channel: document.getElementById('mCfgLogChan').value,
        rules_channel: document.getElementById('mCfgRulesChan').value,
        role_request_channel: document.getElementById('mCfgRoleReqChan').value,
        verification_channel: document.getElementById('mCfgVerifyChan').value,
        leveling_channel: document.getElementById('mCfgLevelChan').value,
        general_channel: document.getElementById('mCfgGeneralChan').value,
        verified_role: document.getElementById('mCfgVerifiedRole').value,
        unverified_role: document.getElementById('mCfgUnverifiedRole').value,
        muted_role: document.getElementById('mCfgMutedRole').value,
        aesthetic_overlay: document.getElementById('mCfgAesthetic').value,
        custom_system_prompt: document.getElementById('mCfgPrompt').value,
        roles_channel: document.getElementById('mCfgRoleChan').value
    };
    const res = await fetch(`/api/guilds/${Dashboard.activeGuild.id}/settings`, {
        method: 'POST',
        body: JSON.stringify(data),
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': Dashboard.token }
    });
    alert(res.ok ? "Settings Synced!" : "Sync Failed.");
}

async function triggerAiBuild() {
    const prompt = document.getElementById('aiArchPrompt').value;
    const res = await fetch(`/api/guilds/${Dashboard.activeGuild.id}/ai-plan`, {
        method: 'POST',
        body: JSON.stringify({ prompt }),
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': Dashboard.token }
    });
    const data = await res.json();
    if (data.status === "success") {
        window.activeAiPlan = data.plan;
        document.getElementById('aiArchPlanReview').style.display = 'block';
        document.getElementById('aiPlanList').innerHTML = data.plan.map(p => `<div>${p.icon} ${p.name}</div>`).join('');
    }
}

async function executeAiBuild() {
    const res = await fetch(`/api/guilds/${Dashboard.activeGuild.id}/ai-execute`, {
        method: 'POST',
        body: JSON.stringify({ plan: window.activeAiPlan }),
        headers: { 'Content-Type': 'application/json', 'X-Session-Token': Dashboard.token }
    });
    alert(res.ok ? "Architecture Manifested!" : "Execution Failed.");
}

async function aiAutoLink() {
    if (!Dashboard.activeGuild) return alert("Select a server first.");
    const btn = event.target;
    btn.textContent = "AUDITING SERVER...";
    btn.disabled = true;

    try {
        const res = await fetch(`/api/guilds/${Dashboard.activeGuild.id}/ai-suggest`, {
            method: 'POST', headers: { 'X-Session-Token': Dashboard.token }
        });
        const data = await res.json();
        if (data.status === "success") {
            Dashboard.activeSuggestions = data.suggestions;
            document.getElementById('aiSuggestModal').classList.add('active');

            const list = document.getElementById('aiSuggestList');
            let html = `<p style="color:var(--p); font-size: 0.8rem; margin-bottom: 1.5rem;">${data.suggestions.reasoning}</p>`;

            Object.entries(data.suggestions.mappings).forEach(([k, v]) => {
                if (v) html += `<div class="plan-item"><b>${k}</b> <span>➔ ${v}</span></div>`;
            });

            if (data.suggestions.creation_suggestions.length > 0) {
                html += `<h4 style="color:#ffaa00; margin-top:1rem; font-size:0.7rem;">CREATION SUGGESTIONS</h4>`;
                data.suggestions.creation_suggestions.forEach(s => {
                    html += `<div class="plan-item" style="border-left: 2px solid #ffaa00;"><b>${s.key}</b> <span>Create "${s.recommended_name}"</span></div>`;
                });
            }
            list.innerHTML = html;
        }
    } catch (e) { }
    btn.textContent = "AI AUTO-LINK SYSTEM";
    btn.disabled = false;
}

function applyAiSuggestions() {
    const sug = Dashboard.activeSuggestions;
    if (!sug) return;
    const idMap = {
        'welcome_channel': 'mCfgWelcomeChan', 'log_channel': 'mCfgLogChan',
        'rules_channel': 'mCfgRulesChan', 'roles_channel': 'mCfgRoleChan',
        'verification_channel': 'mCfgVerifyChan', 'leveling_channel': 'mCfgLevelChan',
        'general_channel': 'mCfgGeneralChan', 'verified_role': 'mCfgVerifiedRole',
        'unverified_role': 'mCfgUnverifiedRole', 'muted_role': 'mCfgMutedRole'
    };
    Object.entries(sug.mappings).forEach(([k, v]) => {
        const el = document.getElementById(idMap[k]);
        if (el && v) el.value = v;
    });
    closeSuggestModal();
    alert("Suggestions loaded into fields. Click SYNC ALL CHANGES to finalize.");
}

async function executeTerminalCommand() {
    const input = document.getElementById('terminalInput');
    const cmd = input.value.trim();
    if (!cmd) return;

    const consoleEl = document.getElementById('logConsole');
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<b style="color:var(--p)">[AUTH#${Dashboard.user.id.slice(-4)}]</b> EXECUTING: ${cmd}`;
    consoleEl.appendChild(entry);

    input.value = "";
    setTimeout(() => {
        const resEntry = document.createElement('div');
        resEntry.className = 'log-entry';
        resEntry.innerHTML = `<b style="color:#ffaa00">[SHELL]</b> Command "${cmd}" registered. Processing background task...`;
        consoleEl.appendChild(resEntry);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }, 800);
}

function closeSuggestModal() { document.getElementById('aiSuggestModal').classList.remove('active'); }
function closeModal() { document.getElementById('configModal').classList.remove('active'); }

async function saveSignupPreference() {
    const selected = document.querySelector('input[name="notif_pref"]:checked');
    if (!selected) return alert("Select a preference option.");
    
    const val = selected.value;
    const btn = document.getElementById('btnPrefSave');
    btn.textContent = "SAVING CONFIGURATION...";
    btn.disabled = true;
    
    try {
        const res = await fetch('/api/user/preference', {
            method: 'POST',
            body: JSON.stringify({ preference: val }),
            headers: {
                'Content-Type': 'application/json',
                'X-Session-Token': Dashboard.token
            }
        });
        if (res.ok) {
            document.getElementById('preferenceModal').classList.remove('active');
        } else {
            alert("Could not update preference. Please try again.");
        }
    } catch (e) {
        alert("Network error. Please try again.");
    }
    btn.textContent = "SAVE PREFERENCES";
    btn.disabled = false;
}

