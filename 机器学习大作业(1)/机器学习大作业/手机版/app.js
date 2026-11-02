/* 南航评论区 · 手机版 逻辑（自包含，复用原后端 API，不改后端） */
(function () {
  'use strict';

  /* ---------- 基础 ---------- */
  const HOST = location.hostname || '127.0.0.1';
  const API = 'http://' + HOST + ':5000/api';
  const UPLOAD = 'http://' + HOST + ':5000/uploads/';
  const TMAP_KEY = '3NIBZ-IW5EJ-55OFI-XTXNT-LJZE3-MUBFS';

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = (s) => String(s || '').replace('T', ' ').slice(0, 16);

  const getToken = () => localStorage.getItem('nuaa_token') || '';
  const getUser = () => localStorage.getItem('nuaa_user') || '';
  const setAuth = (t, u) => { localStorage.setItem('nuaa_token', t); localStorage.setItem('nuaa_user', u); };
  const clearAuth = () => { localStorage.removeItem('nuaa_token'); localStorage.removeItem('nuaa_user'); };

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (opts.body !== undefined && !(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
    if (getToken()) headers['Authorization'] = 'Bearer ' + getToken();
    const res = await fetch(API + path, Object.assign({}, opts, { headers }));
    if (res.status === 401 || res.status === 403) throw new Error('需要登录');
    if (res.status >= 400) {
      let msg = '请求失败', mod = null;
      try { const j = await res.json(); msg = j.error || msg; mod = j.moderation || null; } catch (e) {}
      throw Object.assign(new Error(msg), { moderation: mod });
    }
    return res.status === 204 ? null : res.json();
  }

  /* 写操作失败：被内容安全拦截时给出明确原因（ML②） */
  function writeError(e) {
    if (e && e.moderation && e.moderation.message) {
      const hits = (e.moderation.matched || []).join('、');
      toast(e.moderation.message + (hits ? '（命中：' + hits + '）' : ''));
    } else {
      toast(e && e.message ? e.message : '操作失败');
    }
  }

  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('on');
    clearTimeout(t._tm);
    t._tm = setTimeout(() => t.classList.remove('on'), 2200);
  }
  function requireLogin() {
    if (getUser()) return true;
    M.auth.open('login');
    toast('请先登录');
    return false;
  }

  /* ---------- 抽屉 sheet ---------- */
  const sheet = {
    open(title, html) {
      $('sheetTitle').textContent = title || '';
      $('sheetBody').innerHTML = html;
      $('sheet').classList.add('open');
      $('sheetMask').classList.add('open');
    },
    close() {
      $('sheet').classList.remove('open');
      $('sheetMask').classList.remove('open');
    },
  };

  /* ---------- 全屏表单 ---------- */
  const form = {
    open(title, html) {
      $('formTitle').textContent = title;
      $('formBody').innerHTML = html;
      $('formModal').classList.add('open');
    },
    close() { $('formModal').classList.remove('open'); },
  };

  /* ---------- Tab 切换 ---------- */
  function switchTab(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    $('page-' + name).classList.add('active');
    document.querySelectorAll('.tabbar .tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    window.scrollTo(0, 0);
    // 悬浮发布按钮（只属于失物/市集/论坛三个模块）
    $('fabLost').style.display = name === 'lost' ? 'block' : 'none';
    $('fabMarket').style.display = name === 'market' ? 'block' : 'none';
    $('fabForum').style.display = name === 'forum' ? 'block' : 'none';
    if (name === 'home') M.home.load();
    if (name === 'map') M.map.ensure();
  }

  /* ---------- 登录 / 注册 ---------- */
  let authMode = 'login';
  const auth = {
    open(mode) {
      authMode = mode || 'login';
      form.open(authMode === 'login' ? '登录' : '注册', `
        <div class="field"><label>用户名</label><input id="auUser" placeholder="给自己起个昵称"></div>
        <div class="field"><label>密码</label><input id="auPwd" type="password" placeholder="至少 4 位"></div>
        <div class="auth-err" id="auErr"></div>
        <button class="btn block" onclick="M.auth.submit()">${authMode === 'login' ? '登 录' : '注 册'}</button>
        <button class="btn ghost block" onclick="M.auth.toggle()">${authMode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}</button>`);
    },
    toggle() {
      authMode = authMode === 'login' ? 'register' : 'login';
      M.auth.open(authMode);
    },
    async submit() {
      const username = $('auUser').value.trim();
      const password = $('auPwd').value;
      const err = $('auErr');
      if (!username || !password) { err.textContent = '请填写用户名和密码'; return; }
      try {
        const r = await fetch(API + '/auth/' + authMode, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        }).then(x => x.json());
        if (r.token) {
          setAuth(r.token, r.username);
          form.close();
          M.refreshUser();
          toast('欢迎，' + r.username);
        } else err.textContent = r.error || '操作失败';
      } catch (e) { err.textContent = '无法连接后端'; }
    },
    logout() {
      clearAuth();
      M.refreshUser();
      toast('已退出登录');
    },
  };
  function refreshUser() {
    const u = getUser();
    $('userChip').textContent = u || 'ewe';
    $('authBtn').textContent = u ? '退出' : '登录';
    $('authBtn').onclick = u ? () => auth.logout() : () => auth.open('login');
    notify.refresh();          // 登录态变化后同步未读通知红点
  }

  /* ---------- 首页 ---------- */
  const home = {
    async load() {
      try {
        const s = await api('/stats');
        $('homeStats').innerHTML = [
          [s.poi_count, '校园地点', '校园', 'badge-purple'],
          [s.lostfound_open, '失物待处理', '失物', 'badge-orange'],
          [s.market_open, '市集在售', '市集', 'badge-green'],
          [s.post_count, '论坛帖子', '论坛', 'badge-blue'],
          [s.comment_count, '地点评论', '地点', 'badge-indigo'],
        ].map(([n, t, b, cls]) => `
          <div class="stat-card">
            <span class="badge ${cls}">${b}</span>
            <div class="n">${n}</div>
            <div class="t">${t}</div>
          </div>`).join('');
        // 三项机器学习能力的实时数据（答辩时可直接指着看）
        const acc = s.moderation_acc != null ? (s.moderation_acc * 100).toFixed(1) + '%' : '-';
        $('homeMl').innerHTML = `
          <div class="ml-line"><b>🎒 失物智能匹配</b>
            <span>已匹配 ${s.lf_match_count || 0} 组，推送通知 ${s.notification_count || 0} 条</span></div>
          <div class="ml-line"><b>🛡️ 内容安全识别</b>
            <span>已拦截 ${s.moderation_blocked || 0} 条（命中即拦截、不允许发表），模型准确率 ${acc}</span></div>
          <div class="ml-line"><b>🔍 市集语义检索</b>
            <span>搜「笔记本」也能找到「电脑」</span></div>`;
      } catch (e) { /* 忽略 */ }
    },
  };

  /* ---------- 地图 ---------- */
  const map = {
    tmap: null, markers: null, pois: [], ready: false, pickMode: false,
    async ensure() { if (!this.tried) { this.tried = true; await this.init(); } },
    async init() {
      try {
        if (TMAP_KEY) await this._loadScript(`https://map.qq.com/api/gljs?v=1.exp&libraries=service&key=${TMAP_KEY}`);
        else await this._loadScript('https://map.qq.com/api/gljs?v=1.exp&libraries=service');
      } catch (e) { this.fallback(); return; }
      try {
        this.tmap = new TMap.Map('mMap', { zoom: 16, center: new TMap.LatLng(31.9387, 118.7917) });
        // 地图上不显示地点图标（保持干净），"点到哪看哪"直接在地图上点选
        this.tmap.on('click', (ev) => {
          if (!this.pickMode) return;
          this.pickMode = false;
          const latlng = ev.latLng;
          let best = null, bestD = 1e9;
          for (const p of this.pois) {
            if (p.lat == null) continue;
            const d = Math.hypot(p.lat - latlng.lat, p.lng - latlng.lng);
            if (d < bestD) { bestD = d; best = p; }
          }
          if (best && bestD < 0.004) this.openPoi(best.id);
          else toast('附近没有地点（试试从下方列表选择）');
        });
        try { this.ready = true; } catch (e) {}
      } catch (e) { this.fallback(); return; }
      // 8 秒后仍无中心 = 鉴权失败，启用卡片模式
      setTimeout(() => { if (!this.ready && this.tmap && !this.tmap.getCenter()) this.fallback(); }, 8000);
      await this.loadPois();
    },
    _loadScript(src) {
      return new Promise((res, rej) => {
        const s = document.createElement('script');
        s.src = src;
        s.onload = res;
        s.onerror = () => rej(new Error('script load fail'));
        document.head.appendChild(s);
      });
    },
    fallback() { $('mapFallback').classList.add('on'); },
    async loadPois() {
      try { this.pois = await api('/pois'); } catch (e) { this.pois = []; }
      this.renderList();
    },
    renderList(kw) {
      kw = (kw || '').trim();
      const shown = this.pois.filter(p => !kw || (p.name + (p.description || '')).includes(kw));
      $('mPoiList').innerHTML = shown.map(p => `
        <div class="poi-row" onclick="M.map.openPoi(${p.id})">
          <div class="dot">📌</div>
          <div class="grow">
            <div style="font-size:14.5px;font-weight:600">${esc(p.name)}</div>
            <div class="muted ellipsis">${esc(p.description || '')}</div>
          </div>
          <span class="badge gray">💬 ${p.stats ? p.stats.total : 0}</span>
        </div>`).join('') || '<div class="empty">没有匹配的地点</div>';
    },
    async openPoi(id) {
      try {
        const [d, cmts] = await Promise.all([
          api('/pois/' + id),
          api('/pois/' + id + '/comments').catch(() => []),
        ]);
        // 地图不显示图标，改为把地图中心挪到该地点（地图可用时）
        if (this.tmap && d.lat != null && d.lng != null) {
          try { this.tmap.setCenter(new TMap.LatLng(d.lat, d.lng)); } catch (e) {}
        }
        const s = d.stats || {};
        const me = getUser();
        sheet.open(d.name, `
          <div class="muted">${esc(d.description || '')}</div>
          <div class="kv">💬 共 ${s.total || 0} 条评论</div>
          <button class="btn block" onclick="M.map.navTo(${d.lat}, ${d.lng}, '${esc(d.name)}')">🧭 导航到这里</button>
          <div class="sec-title">评论</div>
          <div id="poiCmts">${cmts.map(c => `
            <div class="comment"><div class="c-head"><b style="color:#4a5a75">${esc(c.author)}</b>
            ${(me && c.author === me) ? `<span class="c-del" onclick="M.map.delCmt(${id}, ${c.id})">删除</span>` : ''}
            <span style="margin-left:auto">${fmt(c.created_at)}</span></div>
            <div class="c-body">${esc(c.content)}</div></div>`).join('') || '<div class="muted" style="padding:10px 0">还没有评论，来抢沙发</div>'}</div>
          <div class="input-row">
            <input id="poiCmtInput" placeholder="说说这个地方…">
            <button class="btn" onclick="M.map.submitCmt(${id})">发送</button>
          </div>`);
      } catch (e) { toast(e.message); }
    },
    async submitCmt(id) {
      const v = $('poiCmtInput').value.trim();
      if (!v) return;
      if (!requireLogin()) return;
      try {
        await api('/pois/' + id + '/comments', { method: 'POST', body: JSON.stringify({ content: v }) });
        toast('评论已发布');
        M.map.openPoi(id);
      } catch (e) { writeError(e); }
    },
    /* 删评：地点评论只能删自己发的 */
    async delCmt(pid, cid) {
      if (!confirm('确定删除这条评论吗？删除后不可恢复。')) return;
      try {
        await api('/pois/' + pid + '/comments/' + cid, { method: 'DELETE' });
        toast('评论已删除');
        M.map.openPoi(pid);
      } catch (e) { writeError(e); }
    },
    async locate() {
      if (!navigator.geolocation) return toast('浏览器不支持定位');
      toast('定位中…');
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          if (!this.tmap) return toast('地图未加载');
          // 浏览器定位是 WGS84，转成腾讯地图的 GCJ02，避免蓝点偏移
          const [lat, lng] = this.wgs84ToGcj02(pos.coords.latitude, pos.coords.longitude);
          // 在地图上画出"我的位置"蓝色圆点
          if (this.myLoc) { this.myLoc.setMap(null); this.myLoc = null; }
          this.myLoc = new TMap.MultiMarker({
            map: this.tmap,
            styles: { me: new TMap.MarkerStyle({ width: 22, height: 22,
              src: this.myLocIcon(), anchor: { x: 11, y: 11 } }) },
            geometries: [{ id: 'me', styleId: 'me', position: new TMap.LatLng(lat, lng) }],
          });
          this.tmap.setCenter(new TMap.LatLng(lat, lng));
          this.tmap.setZoom(16);
          toast('已定位：蓝点就是你的位置');
        },
        () => toast('定位失败（需授予定位权限）'),
        { timeout: 8000 });
    },
    /* 浏览器 WGS84 → 腾讯 GCJ02（与电脑版一致的标准转换） */
    wgs84ToGcj02(lat, lng) {
      const PI = Math.PI, A = 6378245.0, EE = 0.00669342162296594323;
      const outOfChina = (la, lo) => lo < 72.004 || lo > 137.8347 || la < 0.8293 || la > 55.8271;
      const tLat = (x, y) => {
        let r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
        r += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0;
        r += (20.0 * Math.sin(y * PI) + 40.0 * Math.sin(y / 3.0 * PI)) * 2.0 / 3.0;
        r += (160.0 * Math.sin(y / 12.0 * PI) + 320 * Math.sin(y * PI / 30.0)) * 2.0 / 3.0;
        return r;
      };
      const tLng = (x, y) => {
        let r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
        r += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0;
        r += (20.0 * Math.sin(x * PI) + 40.0 * Math.sin(x / 3.0 * PI)) * 2.0 / 3.0;
        r += (150.0 * Math.sin(x / 12.0 * PI) + 300.0 * Math.sin(x / 30.0 * PI)) * 2.0 / 3.0;
        return r;
      };
      if (outOfChina(lat, lng)) return [lat, lng];
      let dlat = tLat(lng - 105.0, lat - 35.0);
      let dlng = tLng(lng - 105.0, lat - 35.0);
      const radlat = lat / 180.0 * PI;
      let magic = Math.sin(radlat);
      magic = 1 - EE * magic * magic;
      const sm = Math.sqrt(magic);
      dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sm) * PI);
      dlng = (dlng * 180.0) / (A / sm * Math.cos(radlat) * PI);
      return [lat + dlat, lng + dlng];
    },
    /* “我的位置”蓝点：外圈光晕 + 白边实心蓝 */
    myLocIcon() {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
        <circle cx="11" cy="11" r="10" fill="#1e6fd9" fill-opacity="0.18"/>
        <circle cx="11" cy="11" r="6" fill="#1e6fd9" stroke="#fff" stroke-width="2.5"/></svg>`;
      return 'data:image/svg+xml,' + encodeURIComponent(svg);
    },
    pick() {
      this.pickMode = true;
      toast('点一下地图，找最近的地点');
    },
    navTo(lat, lng, name) {
      const uri = `https://apis.map.qq.com/uri/v1/routeplan?type=walk&to=${encodeURIComponent(name)}&tocoord=${lat},${lng}&referer=nuaacampus`;
      location.href = uri;
    },
  };

  /* ---------- 站内通知中心（ML① 失物匹配推送） ---------- */
  const notify = {
    timer: null,
    async refresh() {
      if (!getUser() || !getToken()) { $('notifyDot').classList.remove('on'); return; }
      try {
        const r = await api('/notifications');
        const n = r.unread || 0;
        $('notifyDot').textContent = n > 99 ? '99+' : n;
        $('notifyDot').classList.toggle('on', n > 0);
      } catch (e) { /* 静默 */ }
    },
    start() {
      if (this.timer) return;
      this.refresh();
      this.timer = setInterval(() => this.refresh(), 30000);
    },
    async open() {
      if (!getUser()) { M.auth.open('login'); toast('请先登录'); return; }
      sheet.open('🔔 消息通知', '<div class="empty">加载中…</div>');
      try {
        const r = await api('/notifications');
        if (!r.items.length) {
          sheet.open('🔔 消息通知',
            '<div class="empty">暂时没有通知<br>发布寻物 / 招领信息后，系统匹配到相似信息会在这里提醒你</div>');
          return;
        }
        const rows = r.items.map(n => {
          const rel = n.related
            ? `<span class="badge ${n.related.kind === 'lost' ? 'red' : 'green'}">`
              + `${n.related.kind === 'lost' ? '寻物' : '招领'}</span>
               <span>${esc(n.related.title)}</span>` : '';
          return `<div class="notify-row ${n.is_read ? '' : 'unread'}"
                       onclick="M.notify.open2(${n.id}, ${n.related_id || 0})">
            <div class="nt">${esc(n.title)}</div>
            <div class="nb">${esc(n.body)}</div>
            <div class="nm">
              ${n.score ? `<span class="sem-score">相似度 ${(n.score * 100).toFixed(0)}%</span>` : ''}
              ${rel}
              <span style="margin-left:auto">${fmt(n.created_at)}</span>
            </div></div>`;
        }).join('');
        sheet.open('🔔 消息通知',
          `<div class="notify-head"><b>共 ${r.items.length} 条</b>
             <span class="all" onclick="M.notify.readAll()">全部已读</span></div>${rows}`);
      } catch (e) { toast(e.message || '加载失败'); }
    },
    async readAll() {
      try { await api('/notifications/read', { method: 'POST', body: '{}' }); }
      catch (e) { /* 忽略 */ }
      this.refresh();
      this.open();
    },
    async open2(id, relatedId) {
      try { await api('/notifications/read', { method: 'POST', body: JSON.stringify({ ids: [id] }) }); }
      catch (e) { /* 忽略 */ }
      this.refresh();
      sheet.close();
      if (relatedId) {
        switchTab('lost');
        if (!lost.items.length) await lost.load();
        lost.open(relatedId);
      }
    },
  };

  /* ---------- 失物招领 ---------- */
  const lost = {
    items: [], kind: '', kw: '',
    init() {
      $('lfSearch').addEventListener('input', (e) => { this.kw = e.target.value.trim(); this.render(); });
      $('lfChips').addEventListener('click', (e) => {
        const c = e.target.closest('.chip'); if (!c) return;
        this.kind = c.dataset.k;
        $('lfChips').querySelectorAll('.chip').forEach(x => x.classList.toggle('active', x === c));
        this.render();
      });
    },
    async load() {
      try { this.items = await api('/lostfound'); } catch (e) { this.items = []; toast(e.message); }
      this.render();
    },
    render() {
      const kw = this.kw.toLowerCase();
      const shown = this.items.filter(it =>
        (!this.kind || it.kind === this.kind) &&
        (!kw || (it.title + it.content + (it.location || '')).toLowerCase().includes(kw)));
      $('lfGrid').innerHTML = shown.map(it => `
        <div class="gcard" onclick="M.lost.open(${it.id})">
          <div class="thumb">${it.image ? `<img src="${UPLOAD}${it.image}" onerror="this.outerHTML='<div class=&quot;ph&quot;>🎒</div>'">` : '<div class="ph">🎒</div>'}</div>
          <div class="info">
            <div class="t clamp2">${esc(it.title)}</div>
            <span class="badge ${it.kind === 'lost' ? 'red' : 'green'}">${it.kind === 'lost' ? '寻物' : '招领'}</span>
            <span class="badge gray">📍${esc((it.location || '未填写').slice(0, 8))}</span>
            <div class="muted" style="margin-top:4px">${it.status === 'resolved' ? '✅ 已解决' : esc(it.author) + ' · ' + fmt(it.created_at)}</div>
          </div>
        </div>`).join('') || '<div class="empty">暂无相关信息</div>';
    },
    open(id) {
      const it = this.items.find(x => x.id === id);
      if (!it) return;
      sheet.open(it.title, `
        ${it.image ? `<img class="big-img" src="${UPLOAD}${it.image}">` : ''}
        <div class="kv"><span class="badge ${it.kind === 'lost' ? 'red' : 'green'}">${it.kind === 'lost' ? '🔍 寻物启事' : '🎁 失物招领'}</span>
          ${it.status === 'resolved' ? '<span class="badge green">✅ 已解决</span>' : ''}</div>
        <div class="kv">📍 地点：${esc(it.location || '未填写')}</div>
        <div class="kv">👤 发布人：${esc(it.author)}</div>
        <div class="kv">🕒 时间：${fmt(it.created_at)}</div>
        ${it.contact ? `<div class="kv">📞 联系方式：${esc(it.contact)}</div>` : ''}
        <div class="kv" style="color:#333c4e">${esc(it.content)}</div>
        ${it.author === getUser() && it.status === 'open'
          ? `<button class="btn red block" onclick="M.lost.resolve(${it.id})">✅ 标记已解决</button>` : ''}`);
    },
    async resolve(id) {
      try { await api('/lostfound/' + id + '/resolve', { method: 'POST' }); toast('已标记解决'); sheet.close(); this.load(); }
      catch (e) { toast(e.message); }
    },
    openCreate() {
      if (!requireLogin()) return;
      form.open('发布失物信息', `
        <div class="field"><label>类型</label>
          <div class="radio-row">
            <label class="on" id="lfKindLost">🔍 寻物<input type="radio" name="lfk" value="lost" checked></label>
            <label id="lfKindFound">🎁 招领<input type="radio" name="lfk" value="found"></label>
          </div></div>
        <div class="field"><label>标题</label><input id="lfTitle" placeholder="如：丢失一串钥匙"></div>
        <div class="field"><label>详细描述</label><textarea id="lfContent" placeholder="时间、特征、细节…"></textarea></div>
        <div class="field"><label>地点</label><input id="lfLoc" placeholder="如：一食堂二楼"></div>
        <div class="field"><label>联系方式</label><input id="lfContact" placeholder="QQ / 微信 / 手机号"></div>
        <div class="field"><label>照片（选填）</label>
          <label class="file-pick"><span>📷</span><span id="lfImgName">选择图片</span>
          <input type="file" accept="image/*" style="display:none" id="lfImg" onchange="M.lost.pickImg(this)"></label></div>
        <button class="btn block red" onclick="M.lost.create()">发布</button>`);
      document.querySelector('input[name=lfk]').addEventListener('change', () => {
        document.getElementById('lfKindLost').classList.toggle('on', document.querySelector('input[name=lfk]:checked').value === 'lost');
        document.getElementById('lfKindFound').classList.toggle('on', document.querySelector('input[name=lfk]:checked').value === 'found');
      });
    },
    pickImg(inp) { if (inp.files[0]) $('lfImgName').textContent = '已选：' + inp.files[0].name; },
    async create() {
      const kind = document.querySelector('input[name=lfk]:checked').value;
      const title = $('lfTitle').value.trim();
      if (!title) return toast('请填写标题');
      const fd = new FormData();
      fd.append('kind', kind); fd.append('title', title);
      fd.append('content', $('lfContent').value.trim());
      fd.append('location', $('lfLoc').value.trim());
      fd.append('contact', $('lfContact').value.trim());
      const f = $('lfImg').files[0];
      if (f) fd.append('image', f);
      try {
        const r = await api('/lostfound', { method: 'POST', body: fd });
        form.close();
        toast('发布成功');
        // ML① 发布后刷新通知红点：匹配到相似信息时铃铛立刻有提示
        if (r.match_count) await notify.refresh();
        this.load();
      } catch (e) { writeError(e); }
    },
  };

  /* ---------- 二手市集 ---------- */
  const market = {
    items: [], kind: '', kw: '', sem: true, semItems: null, timer: null,
    init() {
      $('mkSearch').addEventListener('input', () => {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.search(), 340);
      });
      $('mkChips').addEventListener('click', (e) => {
        const c = e.target.closest('.chip'); if (!c) return;
        this.kind = c.dataset.k;
        $('mkChips').querySelectorAll('.chip').forEach(x => x.classList.toggle('active', x === c));
        if (this.semItems) this.search(); else this.render();
      });
    },
    toggleSem() {
      this.sem = !this.sem;
      const sw = $('mkSemSwitch');
      sw.className = 'sem-switch' + (this.sem ? ' on' : '');
      sw.textContent = this.sem ? '语义检索' : '精确匹配';
      $('mkSemTip').textContent = this.sem
        ? '支持同义词、错别字与相关词扩展'
        : '只看标题 / 描述里完全相同的字';
      this.search();
    },
    async load() {
      try { this.items = await api('/market'); } catch (e) { this.items = []; toast(e.message); }
      this.render();
    },
    /* ML③ 语义检索：走后端 /api/market/search */
    async search() {
      this.kw = $('mkSearch').value.trim();
      if (!this.kw || !this.sem) {
        this.semItems = null;
        $('mkSemTip').textContent = this.sem
          ? '支持同义词、错别字与相关词扩展'
          : '只看标题 / 描述里完全相同的字';
        this.render();
        return;
      }
      try {
        const r = await api('/market/search?q=' + encodeURIComponent(this.kw));
        if (this.kw !== $('mkSearch').value.trim()) return;   // 丢弃过期响应
        this.semItems = r.items;
        const ex = (r.explain || []).join('；');
        $('mkSemTip').innerHTML = ex ? esc(ex) : '（本次查询无需扩展）';
      } catch (e) { this.semItems = null; toast(e.message); }
      this.render();
    },
    render() {
      const kw = this.kw.toLowerCase();
      let shown;
      if (this.sem && kw && this.semItems) {
        shown = this.semItems.filter(it => !this.kind || it.kind === this.kind);
      } else {
        shown = this.items.filter(it =>
          (!this.kind || it.kind === this.kind) &&
          (!kw || (it.title + it.content).toLowerCase().includes(kw)));
      }
      $('mkGrid').innerHTML = shown.map(it => `
        <div class="gcard ${it.status === 'done' ? 'op' : ''}" onclick="M.market.open(${it.id})">
          <div class="thumb">${it.image ? `<img src="${UPLOAD}${it.image}" onerror="this.outerHTML='<div class=&quot;ph&quot;>📚</div>'">` : '<div class="ph">📚</div>'}</div>
          <div class="info">
            <div class="t clamp2">${esc(it.title)}</div>
            <div style="color:#d43d3d;font-weight:700;font-size:15px">${it.price != null ? '¥' + it.price : '面议'}</div>
            <span class="badge ${it.kind === 'sell' ? 'red' : 'green'}">${it.kind === 'sell' ? '出售' : '求购'}</span>
            <span class="badge gray">${esc(it.category)}</span>
            ${it.score_pct ? `<span class="sem-score">相关度 ${it.score_pct}%</span>` : ''}
            ${it.status === 'done' ? '<div class="muted" style="margin-top:4px">✅ 已完成</div>' : ''}
            ${(it.matched_terms && it.matched_terms.length)
              ? `<div class="sem-terms">语义命中：<b>${esc(it.matched_terms.join('、'))}</b></div>` : ''}
          </div>
        </div>`).join('') || '<div class="empty">暂无商品</div>';
    },
    open(id) {
      const it = this.items.find(x => x.id === id);
      if (!it) return;
      sheet.open(it.title, `
        ${it.image ? `<img class="big-img" src="${UPLOAD}${it.image}">` : ''}
        <div class="kv"><span class="badge ${it.kind === 'sell' ? 'red' : 'green'}">${it.kind === 'sell' ? '出售' : '求购'}</span>
          <span class="badge gray">${esc(it.category)}</span>
          ${it.status === 'done' ? '<span class="badge green">✅ 已完成</span>' : ''}</div>
        <div class="kv" style="font-size:18px;font-weight:700;color:#d43d3d">${it.price != null ? '¥' + it.price : '价格面议'}</div>
        <div class="kv">👤 ${esc(it.author)} · 🕒 ${fmt(it.created_at)}</div>
        ${it.contact ? `<div class="kv">📞 联系：${esc(it.contact)}</div>` : ''}
        <div class="kv" style="color:#333c4e">${esc(it.content)}</div>
        ${it.author === getUser() && it.status === 'open'
          ? `<button class="btn red block" onclick="M.market.done(${it.id})">🤝 标记已成交</button>` : ''}`);
    },
    async done(id) {
      try { await api('/market/' + id + '/done', { method: 'POST' }); toast('已标记成交'); sheet.close(); this.load(); }
      catch (e) { toast(e.message); }
    },
    openCreate() {
      if (!requireLogin()) return;
      form.open('发布二手信息', `
        <div class="field"><label>类型</label>
          <div class="radio-row">
            <label class="on" id="mkKindSell">💰 出售<input type="radio" name="mkk" value="sell" checked></label>
            <label id="mkKindWant">🛒 求购<input type="radio" name="mkk" value="want"></label>
          </div></div>
        <div class="field"><label>标题</label><input id="mkTitle" placeholder="如：考研数学全书（九五新）"></div>
        <div class="field"><label>价格（元，求购可不填）</label><input id="mkPrice" type="number" inputmode="decimal" placeholder="如：25"></div>
        <div class="field"><label>分类</label><select id="mkCat">
          <option>书籍</option><option>电子产品</option><option>生活用品</option><option>票券</option><option>其他</option></select></div>
        <div class="field"><label>描述</label><textarea id="mkContent" placeholder="成色、交易方式、自取地点…"></textarea></div>
        <div class="field"><label>联系方式</label><input id="mkContact" placeholder="QQ / 微信 / 手机号"></div>
        <div class="field"><label>照片（选填）</label>
          <label class="file-pick"><span>📷</span><span id="mkImgName">选择图片</span>
          <input type="file" accept="image/*" style="display:none" id="mkImg" onchange="if(this.files[0])document.getElementById('mkImgName').textContent='已选：'+this.files[0].name"></label></div>
        <button class="btn block red" onclick="M.market.create()">发布</button>`);
      document.querySelector('input[name=mkk]').addEventListener('change', () => {
        document.getElementById('mkKindSell').classList.toggle('on', document.querySelector('input[name=mkk]:checked').value === 'sell');
        document.getElementById('mkKindWant').classList.toggle('on', document.querySelector('input[name=mkk]:checked').value === 'want');
      });
    },
    async create() {
      const title = $('mkTitle').value.trim();
      if (!title) return toast('请填写标题');
      const fd = new FormData();
      fd.append('kind', document.querySelector('input[name=mkk]:checked').value);
      fd.append('title', title);
      fd.append('content', $('mkContent').value.trim());
      fd.append('category', $('mkCat').value);
      fd.append('contact', $('mkContact').value.trim());
      const price = $('mkPrice').value;
      if (price) fd.append('price', price);
      const f = $('mkImg').files[0];
      if (f) fd.append('image', f);
      try {
        await api('/market', { method: 'POST', body: fd });
        form.close();
        toast('发布成功');
        this.load();
        if (this.semItems) this.search();
      } catch (e) { writeError(e); }
    },
  };

  /* ---------- 校园评论（论坛） ---------- */
  const forum = {
    posts: [], kw: '', cur: null,
    init() {
      $('fmSearch').addEventListener('input', (e) => { this.kw = e.target.value.trim(); this.render(); });
    },
    async load() {
      try { this.posts = await api('/posts'); } catch (e) { this.posts = []; toast(e.message); }
      this.render();
    },
    render() {
      const kw = this.kw.toLowerCase();
      const shown = this.posts.filter(p => !kw || (p.title + p.content).toLowerCase().includes(kw));
      $('fmList').innerHTML = shown.map(p => `
        <div class="card fcard" onclick="M.forum.open(${p.id})">
          <div class="t">${esc(p.title)}</div>
          <div class="ex clamp2">${esc(p.content)}</div>
          ${p.image ? `<img class="thumb" src="${UPLOAD}${p.image}" onerror="this.style.display='none'">` : ''}
          <div class="meta"><span>👤 ${esc(p.author)}</span><span>${fmt(p.created_at)}</span>
            <span>💬 ${p.comment_count}</span><span>👍 ${p.likes}</span></div>
        </div>`).join('') || '<div class="empty">还没有帖子，来发第一个</div>';
    },
    async open(id) {
      try {
        const p = await api('/posts/' + id);
        this.cur = p;
        const me = getUser();
        sheet.open(p.title, `
          <div class="muted">👤 ${esc(p.author)} · ${fmt(p.created_at)}</div>
          ${p.image ? `<img class="big-img" src="${UPLOAD}${p.image}">` : ''}
          <div class="kv" style="color:#333c4e">${esc(p.content)}</div>
          <button class="btn like" onclick="M.forum.like(${p.id})">👍 点赞 <b id="likeN">${p.likes}</b></button>
          ${(me && p.author === me)
            ? `<button class="btn red block" onclick="M.forum.delPost(${p.id})">🗑 删除帖子</button>` : ''}
          <div class="sec-title">评论</div>
          <div id="fmCmts">${(p.comments || []).map(c => `
            <div class="comment"><div class="c-head"><b style="color:#4a5a75">${esc(c.author)}</b>
            ${(me && (c.author === me || p.author === me))
              ? `<span class="c-del" onclick="M.forum.delComment(${p.id}, ${c.id})">删除</span>` : ''}
            <span style="margin-left:auto">${fmt(c.created_at)}</span></div>
            <div class="c-body">${esc(c.content)}</div></div>`).join('') || '<div class="muted" style="padding:10px 0">还没有评论</div>'}</div>
          <div class="input-row">
            <input id="fmCmtInput" placeholder="写下你的看法…">
            <button class="btn" onclick="M.forum.cmt(${p.id})">发送</button>
          </div>`);
      } catch (e) { toast(e.message); }
    },
    /* 删评：评论作者本人，或帖子楼主 */
    async delComment(pid, cid) {
      if (!confirm('确定删除这条评论吗？删除后不可恢复。')) return;
      try {
        await api('/posts/' + pid + '/comments/' + cid, { method: 'DELETE' });
        toast('评论已删除');
        M.forum.open(pid);
        this.load();
      } catch (e) { writeError(e); }
    },
    /* 删帖：只有楼主本人可以删，连带删除该帖下所有评论 */
    async delPost(pid) {
      if (!confirm('确定删除这条帖子吗？帖子下的所有评论也会一起删除，且不可恢复。')) return;
      try {
        const r = await api('/posts/' + pid, { method: 'DELETE' });
        toast('帖子已删除' + (r && r.comments_deleted ? '（含 ' + r.comments_deleted + ' 条评论）' : ''));
        sheet.close();
        this.load();
      } catch (e) { writeError(e); }
    },
    async like(id) {
      try {
        const r = await api('/posts/' + id + '/like', { method: 'POST' });
        const el = $('likeN'); if (el) el.textContent = r.likes;
        const p = this.posts.find(x => x.id === id); if (p) p.likes = r.likes;
        this.render();
      } catch (e) { toast(e.message); }
    },
    async cmt(id) {
      const v = $('fmCmtInput').value.trim();
      if (!v) return;
      if (!requireLogin()) return;
      try {
        await api('/posts/' + id + '/comments', { method: 'POST', body: JSON.stringify({ content: v }) });
        toast('评论已发布');
        M.forum.open(id);
        this.load();
      } catch (e) { writeError(e); }
    },
    openCreate() {
      if (!requireLogin()) return;
      form.open('发帖', `
        <div class="field"><label>标题</label><input id="fTitle" placeholder="如：期末周图书馆占座现象你怎么看"></div>
        <div class="field"><label>正文</label><textarea id="fContent" placeholder="想聊什么都行…"></textarea></div>
        <div class="field"><label>配图（选填）</label>
          <label class="file-pick"><span>📷</span><span id="fmImgName">选择图片</span>
          <input type="file" accept="image/*" style="display:none" id="fmImg" onchange="if(this.files[0])document.getElementById('fmImgName').textContent='已选：'+this.files[0].name"></label></div>
        <button class="btn block red" onclick="M.forum.create()">发布</button>`);
    },
    async create() {
      const title = $('fTitle').value.trim();
      const content = $('fContent').value.trim();
      if (!title || !content) return toast('标题和正文不能为空');
      const fd = new FormData();
      fd.append('title', title); fd.append('content', content);
      const f = $('fmImg').files[0];
      if (f) fd.append('image', f);
      try {
        await api('/posts', { method: 'POST', body: fd });
        form.close();
        toast('发布成功');
        this.load();
      } catch (e) { writeError(e); }
    },
  };

  /* ---------- 挂载 & 启动 ---------- */
  window.M = { switchTab, sheet, form, auth, refreshUser, home, map, lost, market, forum, notify };
  document.querySelectorAll('.tabbar .tab').forEach(t =>
    t.addEventListener('click', () => switchTab(t.dataset.tab)));
  $('mSearch').addEventListener('input', (e) => map.renderList(e.target.value));
  // 顶栏搜索：跳到论坛并聚焦搜索框
  $('topSearchBtn').onclick = () => {
    switchTab('forum');
    setTimeout(() => { try { $('fmSearch').focus(); } catch (e) {} }, 120);
  };
  // 顶栏铃铛：站内通知中心（失物匹配推送）
  $('notifyBell').onclick = () => notify.open();

  refreshUser();
  lost.init(); market.init(); forum.init();
  notify.start();
  home.load();
  lost.load(); market.load(); forum.load();
})();