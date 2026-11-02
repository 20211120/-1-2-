/* 南航评论区 平台公共脚本：登录态、导航头、API 封装 */
const API = 'http://127.0.0.1:5000/api';
const UPLOAD_BASE = 'http://127.0.0.1:5000/uploads/';

/* ---------- 登录态（localStorage） ---------- */
function getToken() { return localStorage.getItem('nuaa_token') || ''; }
function getUser() { return localStorage.getItem('nuaa_user') || ''; }
function setAuth(token, user) {
  localStorage.setItem('nuaa_token', token);
  localStorage.setItem('nuaa_user', user);
}
function clearAuth() {
  localStorage.removeItem('nuaa_token');
  localStorage.removeItem('nuaa_user');
}

/* ---------- API 封装 ---------- */
async function api(path, opts = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  const token = getToken();
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const res = await fetch(API + path, Object.assign({}, opts, { headers }));
  if (res.status === 401 || res.status === 403) {
    throw Object.assign(new Error('需要登录'), { needAuth: true });
  }
  if (res.status >= 400 && res.status !== 401 && res.status !== 403) {
    let msg = '请求失败', mod = null;
    try { const j = await res.json(); msg = j.error || msg; mod = j.moderation || null; }
    catch (e) { /* 忽略 */ }
    // 内容安全拦截：把判定详情挂到错误上，页面可以就地展示命中项
    throw Object.assign(new Error(msg), { moderation: mod, status: res.status });
  }
  return res.status === 204 ? null : res.json();
}

/* ---------- 轻提示 ---------- */
function showToast(msg, type) {
  let el = document.getElementById('pfToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'pfToast';
    el.className = 'pf-toast';
    document.body.appendChild(el);
  }
  el.className = 'pf-toast ' + (type || '');
  el.textContent = msg;
  requestAnimationFrame(() => el.classList.add('on'));
  clearTimeout(el._tm);
  el._tm = setTimeout(() => el.classList.remove('on'), 2600);
}

/* ---------- 内容安全提示（拦截 / 待复核） ---------- */
function renderModeration(el, mod) {
  if (!el) return;
  if (!mod || mod.action === 'allow') { el.className = 'mnotice'; el.innerHTML = ''; return; }
  const hits = (mod.matched && mod.matched.length)
    ? `<div>命中特征：<span class="m-hit">${mod.matched.join('、')}</span></div>` : '';
  const sug = mod.suggestion ? `<div>${esc(mod.suggestion)}</div>` : '';
  if (mod.action === 'block') {
    el.className = 'mnotice block show';
    el.innerHTML = `<b>内容已被拦截</b>（${esc(mod.label_name)}，风险 ${(mod.risk * 100).toFixed(0)}%）`
      + hits + sug + '<div>请修改后重新提交。</div>';
  } else {
    el.className = 'mnotice warn show';
    el.innerHTML = `<b>已发布，内容正在复核</b>（${esc(mod.label_name)}，风险 ${(mod.risk * 100).toFixed(0)}%）`
      + hits + sug;
  }
}

/* 统一处理写操作失败：被拦截时就地展示原因 */
function handleWriteError(e, noticeEl) {
  if (e && e.moderation) {
    renderModeration(noticeEl, e.moderation);
    showToast(e.message || '内容已被拦截', 'error');
  } else {
    showToast(e && e.message ? e.message : '操作失败', 'error');
  }
}

/* ---------- 站内通知中心（失物招领智能匹配推送） ---------- */
let notifyTimer = null;

function injectNotifyModal() {
  const d = document.createElement('div');
  d.className = 'notify-modal';
  d.id = 'notifyModal';
  d.innerHTML = `
    <div class="notify-box">
      <div class="notify-head">
        <h3>🔔 消息通知</h3>
        <span class="n-read" onclick="readAllNotify()">全部已读</span>
        <span class="n-close" onclick="closeNotifyModal()">✕</span>
      </div>
      <div class="notify-list" id="notifyList"></div>
    </div>`;
  d.addEventListener('click', (ev) => { if (ev.target === d) closeNotifyModal(); });
  document.body.appendChild(d);
}

async function refreshNotify() {
  const bell = document.getElementById('notifyBell');
  if (!bell) return;
  if (!getUser() || !getToken()) { bell.style.display = 'none'; return; }
  bell.style.display = 'flex';
  try {
    const r = await api('/notifications');
    const dot = bell.querySelector('.dot');
    const n = r.unread || 0;
    dot.textContent = n > 99 ? '99+' : n;
    dot.classList.toggle('on', n > 0);
    bell.title = n > 0 ? `你有 ${n} 条未读通知` : '消息通知';
  } catch (e) { /* 静默失败，不打扰用户 */ }
}

async function openNotifyModal() {
  if (!getUser()) { openAuthModal('login'); return; }
  document.getElementById('notifyModal').classList.add('open');
  const list = document.getElementById('notifyList');
  list.innerHTML = '<div class="notify-empty">加载中…</div>';
  try {
    const r = await api('/notifications');
    if (!r.items.length) {
      list.innerHTML = '<div class="notify-empty">暂时没有通知。<br>发布寻物 / 招领信息后，'
        + '系统匹配到相似信息会在这里提醒你。</div>';
      return;
    }
    list.innerHTML = r.items.map(n => {
      const rel = n.related
        ? `<span class="badge-${n.related.kind === 'lost' ? 'red' : 'green'}">`
          + `${n.related.kind === 'lost' ? '寻物' : '招领'}</span>
           <span>${esc(n.related.title)}</span>`
        : '';
      return `<div class="notify-item ${n.is_read ? '' : 'unread'}"
                   onclick="openNotify(${n.id}, ${n.related_id || 0})">
        <div class="n-title">${esc(n.title)}</div>
        <div class="n-body">${esc(n.body)}</div>
        <div class="n-meta">
          ${n.score ? `<span class="sem-score">相似度 ${(n.score * 100).toFixed(0)}%</span>` : ''}
          ${rel}
          <span style="margin-left:auto">${fmtTime(n.created_at)}</span>
        </div></div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div class="notify-empty">${esc(e.message || '加载失败')}</div>`;
  }
}

function closeNotifyModal() {
  document.getElementById('notifyModal').classList.remove('open');
}

async function readAllNotify() {
  try {
    await api('/notifications/read', { method: 'POST', body: '{}' });
    refreshNotify();
    openNotifyModal();
  } catch (e) { showToast(e.message || '操作失败', 'error'); }
}

/* 点通知：标记已读 + 跳到对应的失物招领详情 */
async function openNotify(id, relatedId) {
  try {
    await api('/notifications/read', { method: 'POST', body: JSON.stringify({ ids: [id] }) });
  } catch (e) { /* 忽略 */ }
  refreshNotify();
  closeNotifyModal();
  if (!relatedId) return;
  const cur = location.pathname.split('/').pop();
  if (cur === 'lostfound.html' && typeof openDetail === 'function') openDetail(relatedId);
  else location.href = 'lostfound.html?open=' + relatedId;
}

function startNotifyPolling() {
  if (notifyTimer) return;
  refreshNotify();
  notifyTimer = setInterval(refreshNotify, 30000);
}

/* ---------- 平台导航头 ---------- */
const NAV = [
  ['index.html', '首页'],
  ['map.html', '校园地图'],
  ['lostfound.html', '失物招领'],
  ['market.html', '二手市集'],
  ['forum.html', '校园评论'],
];

function renderHeader(active) {
  const el = document.getElementById('header-root');
  if (!el) return;
  const user = getUser();
  el.className = 'pf-header';
  el.innerHTML =
    `<a class="pf-brand" href="index.html">✈ 南航评论区</a>
     <div class="pf-nav">${NAV.map(([href, name]) =>
       `<a href="${href}" class="${href === active ? 'active' : ''}">${name}</a>`).join('')}</div>
     <div class="pf-user">
       <div class="pf-bell" id="notifyBell" onclick="openNotifyModal()" title="消息通知">
         <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
              stroke-linecap="round" stroke-linejoin="round"><path d="M18 7.5a6 6 0 1 0-12 0c0 6-2.5 7.5-2.5 7.5h17S18 13.5 18 7.5z"/><path d="M13.7 20a2 2 0 0 1-3.4 0"/></svg>
         <span class="dot"></span>
       </div>
       ${user
       ? `<span class="chip">👤 ${user}</span><span class="logout" onclick="doLogout()">退出</span>`
       : `<button class="login-btn" onclick="openAuthModal('login')">登录 / 注册</button>`}</div>`;
  startNotifyPolling();
}

function doLogout() { clearAuth(); location.reload(); }

/* ---------- 登录/注册弹窗 ---------- */
let authMode = 'login';
function openAuthModal(mode) {
  authMode = mode || 'login';
  document.getElementById('authModal').classList.add('open');
  renderAuthModal();
}
function closeAuthModal() { document.getElementById('authModal').classList.remove('open'); }
function renderAuthModal() {
  document.getElementById('authTitle').textContent = authMode === 'login' ? '登录' : '注册';
  document.getElementById('authSubmit').textContent = authMode === 'login' ? '登 录' : '注 册';
  document.getElementById('authSwitch').textContent =
    authMode === 'login' ? '还没有账号？点我注册' : '已有账号？点我登录';
  document.getElementById('authErr').textContent = '';
}
function toggleAuthMode() {
  authMode = authMode === 'login' ? 'register' : 'login';
  renderAuthModal();
}
async function submitAuth() {
  const username = document.getElementById('authUser').value.trim();
  const password = document.getElementById('authPwd').value;
  const err = document.getElementById('authErr');
  if (!username || !password) { err.textContent = '请填写用户名和密码'; return; }
  try {
    const r = await fetch(API + '/auth/' + authMode, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then(x => x.json());
    if (r.token) {
      setAuth(r.token, r.username);
      closeAuthModal();
      location.reload();
    } else {
      err.textContent = r.error || '操作失败';
    }
  } catch (e) {
    err.textContent = '无法连接后端，请先启动 backend/app.py';
  }
}

/* 通用：注入弹窗 DOM（登录弹窗 + 站内通知中心，每个页面调用一次） */
function injectAuthModal() {
  const d = document.createElement('div');
  d.className = 'auth-modal';
  d.id = 'authModal';
  d.innerHTML = `
    <div class="auth-box">
      <h3 id="authTitle">登录</h3>
      <input id="authUser" placeholder="用户名">
      <input id="authPwd" type="password" placeholder="密码（至少 4 位）">
      <div class="auth-err" id="authErr"></div>
      <button class="auth-submit" id="authSubmit" onclick="submitAuth()">登 录</button>
      <div class="auth-switch" id="authSwitch" onclick="toggleAuthMode()"></div>
      <div style="text-align:center;margin-top:10px">
        <span class="muted" style="cursor:pointer" onclick="closeAuthModal()">关闭</span>
      </div>
    </div>`;
  document.body.appendChild(d);
  if (!document.getElementById('notifyModal')) injectNotifyModal();
}

function requireLogin() {
  if (getUser()) return true;
  openAuthModal('login');
  return false;
}

/* 时间格式化 */
function fmtTime(s) { return (s || '').replace('T', ' '); }
function esc(s) {
  return String(s || '').replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
