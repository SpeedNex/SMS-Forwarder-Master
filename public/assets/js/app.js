let authToken = localStorage.getItem('masterToken') || '';
let lastMessageId = 0;

const loginOverlay = document.getElementById('loginOverlay');
const appShell = document.getElementById('appShell');

function showNotification(message, type = 'info') {
    const existing = document.querySelector('.notification');
    if (existing) existing.remove();
    const el = document.createElement('div');
    el.className = `notification ${type}`;
    el.innerHTML = `<div class="notification-content"><i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle'}"></i><span>${message}</span></div>`;
    el.style.cssText = `
        position: fixed; top: 24px; right: 24px; z-index: 3000;
        background: ${type === 'success' ? 'rgba(74,222,128,0.9)' : type === 'error' ? 'rgba(239,68,68,0.9)' : 'rgba(59,130,246,0.9)'};
        color: #fff; padding: 12px 16px; border-radius: 10px;
        box-shadow: 0 8px 20px rgba(0,0,0,0.35);
        border-left: 4px solid ${type === 'success' ? '#22c55e' : type === 'error' ? '#ef4444' : '#3b82f6'};
    `;
    document.body.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 200);
    }, 2600);
}

async function apiFetch(url, options = {}) {
    const headers = options.headers || {};
    if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
    options.headers = headers;
    const res = await fetch(url, options);
    if (res.status === 401) {
        authToken = '';
        localStorage.removeItem('masterToken');
        showLogin();
        throw new Error('未登录或登录已过期');
    }
    return res;
}

function showLogin() {
    loginOverlay.classList.remove('hidden');
    appShell.classList.add('hidden');
    document.getElementById('loginUser').focus();
}

function hideLogin() {
    loginOverlay.classList.add('hidden');
    appShell.classList.remove('hidden');
}

async function doLogin() {
    const username = document.getElementById('loginUser').value.trim();
    const password = document.getElementById('loginPass').value;
    if (!username || !password) {
        showNotification('请输入用户名和密码', 'error');
        return;
    }
    const btn = document.getElementById('loginBtn');
    const original = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 登录中';
    btn.disabled = true;
    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '登录失败');
        authToken = data.token;
        localStorage.setItem('masterToken', authToken);
        hideLogin();
        await loadProfile();
        await loadAgents(true);
        await loadMessages(true);
        await loadTasks();
        showNotification('登录成功', 'success');
    } catch (e) {
        showNotification('登录失败: ' + e.message, 'error');
    } finally {
        btn.innerHTML = original;
        btn.disabled = false;
    }
}

async function loadProfile() {
    const res = await apiFetch('/api/profile');
    const data = await res.json();
    document.getElementById('profileName').textContent = data.username;
}

function updateSendStatusTag() {
    const tag = document.getElementById('sendStatusTag');
    const select = document.getElementById('agentSelect');
    if (!select.value) {
        tag.textContent = '选择 Agent 后可发送';
        tag.classList.remove('warn');
    } else {
        const text = select.options[select.selectedIndex].text || ('Agent #' + select.value);
        tag.textContent = '当前选择: ' + text;
    }
}

function renderAgents(agents) {
    const grid = document.getElementById('agentGrid');
    grid.innerHTML = '';
    const select = document.getElementById('agentSelect');
    select.innerHTML = '<option value="">请选择 Agent</option>';
    let online = 0;
    agents.forEach(a => {
        const card = document.createElement('div');
        card.className = 'small-card';
        const lastSeen = a.last_seen || '--';
        const isOnline = !!a.online || (a.status && a.status.toLowerCase().includes('online'));
        if (isOnline) online += 1;
        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <div style="font-size:1.05rem; color:#e0e7ff; font-weight:600;">${a.name || '未命名'}</div>
                <span class="tag ${isOnline ? '' : 'warn'}">${a.status || 'unknown'}</span>
            </div>
            <p style="color:#94a3b8;">${a.description || '无描述'}</p>
            <p style="color:#cbd5e1; margin-top:6px; font-size:0.9rem;">上次心跳：${lastSeen}</p>
            <div style="margin-top:8px; display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                <span class="tag" style="cursor:pointer;" data-key="${a.id}"><i class="fas fa-key"></i> 显示 API Key</span>
                <button class="btn btn-danger" data-del="${a.id}" style="padding:6px 10px;"><i class="fas fa-trash"></i> 删除</button>
            </div>
        `;
        card.querySelector('[data-key]').addEventListener('click', () => showAgentKey(a));
        card.querySelector('[data-del]').addEventListener('click', () => deleteAgent(a.id, a.name));
        grid.appendChild(card);
        select.insertAdjacentHTML('beforeend', `<option value="${a.id}">${a.name || ('Agent #' + a.id)}</option>`);
    });
    document.getElementById('agentCount').textContent = `${online} / ${agents.length}`;
    updateSendStatusTag();
}

async function loadAgents(showNotify = false) {
    const res = await apiFetch('/api/agents');
    const data = await res.json();
    renderAgents(data);
    if (showNotify) showNotification('Agent 列表已刷新', 'success');
}

function showAgentModal() {
    document.getElementById('agentModal').classList.remove('hidden');
    document.getElementById('agentFormSection').classList.remove('hidden');
    document.getElementById('agentResultSection').classList.add('hidden');
    document.getElementById('agentNameInput').focus();
}

function hideAgentModal() {
    document.getElementById('agentModal').classList.add('hidden');
}

function resetAgentModal() {
    document.getElementById('agentNameInput').value = '';
    document.getElementById('agentDescInput').value = '';
    document.getElementById('agentResultName').textContent = '';
    document.getElementById('agentResultKey').textContent = '';
    document.getElementById('agentFormSection').classList.remove('hidden');
    document.getElementById('agentResultSection').classList.add('hidden');
    document.getElementById('agentNameInput').focus();
}

async function submitNewAgent() {
    const name = document.getElementById('agentNameInput').value.trim();
    const desc = document.getElementById('agentDescInput').value.trim();
    if (!name) {
        showNotification('请填写 Agent 名称', 'error');
        return;
    }
    const btns = document.querySelectorAll('#agentFormSection button');
    btns.forEach(b => b.disabled = true);
    try {
        const res = await apiFetch('/api/agents', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc })
        });
        const data = await res.json();
        if (!res.ok) {
            showNotification('创建失败: ' + (data.detail || res.status), 'error');
            return;
        }
        document.getElementById('agentResultName').textContent = `#${data.id} ${data.name || ''}`;
        document.getElementById('agentResultKey').textContent = data.api_key;
        document.getElementById('agentFormSection').classList.add('hidden');
        document.getElementById('agentResultSection').classList.remove('hidden');
        showNotification('Agent 创建成功，已生成 API Key', 'success');
        await loadAgents();
    } catch (e) {
        showNotification('创建失败: ' + e.message, 'error');
    } finally {
        btns.forEach(b => b.disabled = false);
    }
}

function copyText(elementId) {
    const text = document.getElementById(elementId).textContent;
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
        showNotification('已复制到剪贴板', 'success');
    }).catch(() => {
        showNotification('复制失败', 'error');
    });
}

async function sendTask() {
    const agentId = document.getElementById('agentSelect').value;
    const phone = document.getElementById('sendPhone').value.trim();
    const text = document.getElementById('sendText').value.trim();
    if (!agentId) return showNotification('请选择 Agent', 'error');
    if (!phone || !text) return showNotification('请输入号码和内容', 'error');
    const btn = document.getElementById('sendTaskBtn');
    const original = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 下发中';
    btn.disabled = true;
    try {
        const res = await apiFetch('/api/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent_id: Number(agentId), phone, text })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        showNotification('任务已下发，等待 Agent 拉取', 'success');
        await loadTasks();
    } catch (e) {
        showNotification('下发失败: ' + e.message, 'error');
    } finally {
        btn.innerHTML = original;
        btn.disabled = false;
    }
}

function createMessageRow(msg) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td>${msg.id}</td>
        <td>${msg.agent_name || ('#' + msg.agent_id)}</td>
        <td class="mono">${msg.modem_port || '--'}</td>
        <td>${msg.sender}</td>
        <td>${msg.timestamp}</td>
        <td style="max-width:420px; white-space:normal; word-break:break-all;">${msg.text}</td>
        <td>${msg.created_at}</td>
    `;
    return tr;
}

function renderMessageTableInitial(messages) {
    const tbody = document.getElementById('messageTable');
    tbody.innerHTML = '';
    lastMessageId = 0;
    messages.forEach(m => {
        const row = createMessageRow(m);
        tbody.appendChild(row);
        if (m.id > lastMessageId) lastMessageId = m.id;
    });
}

function appendMessages(messages) {
    if (!messages || !messages.length) return;
    const tbody = document.getElementById('messageTable');
    for (let i = messages.length - 1; i >= 0; i--) {
        const row = createMessageRow(messages[i]);
        tbody.insertBefore(row, tbody.firstChild);
        if (messages[i].id > lastMessageId) lastMessageId = messages[i].id;
    }
}

async function loadMessages(initial = false) {
    let url = '/api/messages?limit=200';
    if (!initial && lastMessageId) url += '&after_id=' + lastMessageId;
    const res = await apiFetch(url);
    const data = await res.json();
    if (initial || !lastMessageId) {
        renderMessageTableInitial(data);
    } else {
        appendMessages(data);
    }
}

function renderTasks(tasks) {
    const tbody = document.getElementById('taskTable');
    tbody.innerHTML = '';
    tasks.forEach(t => {
        const statusTag = `<span class="tag ${t.status === 'error' ? 'danger' : t.status === 'pending' ? 'warn' : ''}">${t.status}</span>`;
        const resultTag = t.result ? `<span class="tag neutral">${t.result}</span>` : '';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${t.id}</td>
            <td>${t.agent_name || ('#' + t.agent_id)}</td>
            <td>${t.phone}</td>
            <td style="max-width:320px; white-space:normal; word-break:break-all;">${t.text}</td>
            <td>${statusTag}</td>
            <td>${t.created_at || ''}</td>
            <td>${t.delivered_at || ''}</td>
            <td>${t.done_at || ''}</td>
            <td>${resultTag}</td>
        `;
        tbody.appendChild(tr);
    });
    const pending = tasks.filter(t => t.status === 'pending').length;
    document.getElementById('pendingTasks').textContent = pending;
}

async function loadTasks(showNotify = false) {
    const res = await apiFetch('/api/tasks');
    const data = await res.json();
    renderTasks(data);
    if (showNotify) showNotification('任务列表已刷新', 'success');
}

async function loadLogs(showNotify = false) {
    const res = await apiFetch('/api/logs?lines=300');
    const data = await res.json();
    const content = data.lines && data.lines.length ? data.lines.join('\n') : '(暂无日志)';
    document.getElementById('logContent').textContent = content;
    if (showNotify) showNotification('日志已刷新');
}

function showAgentKey(agent) {
    showNotification(`Agent: ${agent.name || agent.id} | API Key: ${agent.api_key || '请在数据库查看'}`, 'info');
}

async function deleteAgent(agentId, name) {
    if (!confirm(`确定删除 Agent ${name || ('#' + agentId)} 吗？相关任务和短信会被清理。`)) return;
    try {
        const res = await apiFetch(`/api/agents/${agentId}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        showNotification('Agent 已删除', 'success');
        await loadAgents(true);
        await loadTasks();
        await loadMessages(true);
    } catch (e) {
        showNotification('删除失败: ' + e.message, 'error');
    }
}

document.getElementById('loginBtn').addEventListener('click', doLogin);
document.getElementById('newAgentBtn').addEventListener('click', showAgentModal);
document.getElementById('refreshAgentsBtn').addEventListener('click', () => loadAgents(true));
document.getElementById('agentSelect').addEventListener('change', updateSendStatusTag);
document.getElementById('sendTaskBtn').addEventListener('click', sendTask);
document.getElementById('refreshMsgBtn').addEventListener('click', () => loadMessages(true));
document.getElementById('refreshTaskBtn').addEventListener('click', () => loadTasks(true));
document.getElementById('toggleLogBtn').addEventListener('click', async function() {
    const panel = document.getElementById('logPanel');
    if (panel.style.display === 'none' || !panel.style.display) {
        panel.style.display = 'block';
        await loadLogs();
        this.innerHTML = '<i class="fas fa-eye-slash"></i> 收起日志';
    } else {
        panel.style.display = 'none';
        this.innerHTML = '<i class="fas fa-eye"></i> 查看日志';
    }
});
document.getElementById('refreshLogBtn').addEventListener('click', () => loadLogs(true));

setInterval(() => {
    if (!authToken) return;
    loadMessages(false);
    loadTasks();
}, 5000);

(async () => {
    if (!authToken) {
        showLogin();
        return;
    }
    try {
        await loadProfile();
        hideLogin();
        await loadAgents(true);
        await loadMessages(true);
        await loadTasks();
    } catch (e) {
        showLogin();
    }
})();
