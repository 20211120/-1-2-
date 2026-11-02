# -*- coding: utf-8 -*-
"""
真实浏览器端到端验证（Edge + CDP）
============================================================
不用 puppeteer，用标准库 + websocket-client 直接驱动 Edge 的 DevTools 协议：
真实鼠标点击、真实文本输入、真实截图，全部落在 127.0.0.1:5000 的真页面上。

输出：控制台日志 + verify_ui.log + PNG 截图
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "verify_ui.log")
SHOTS = os.path.join(HERE, "shots")
ROOT = "http://127.0.0.1:5000"
DEFAULT_PORT = 51999
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

buf = []
results = []


def log(*a):
    line = " ".join(str(x) for x in a)
    buf.append(line)
    print(line)


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    log("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  -> " + str(detail)) if detail else ""))


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=40)
        self.id = 0
        self.events = []

    def send(self, method, **params):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})
            if "method" in msg:                      # 事件先攒起来（异常检查用）
                self.events.append(msg)

    def js_errors(self):
        """页面内 window.__errs + CDP 抛出的未捕获异常"""
        inpage = self.js("(window.__errs || []).slice(0, 5)") or []
        cdp_errs = [e["params"]["exceptionDetails"].get("text")
                    for e in self.events if e.get("method") == "Runtime.exceptionThrown"]
        return list(inpage) + cdp_errs

    def js(self, expr, await_promise=True):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise)
        return r.get("result", {}).get("value")

    def navigate(self, url, settle=1.6):
        self.send("Page.navigate", url=url)
        time.sleep(settle)
        for _ in range(40):
            state = self.js("document.readyState")
            if state == "complete":
                break
            time.sleep(0.25)
        time.sleep(0.5)

    def box(self, selector):
        return self.js("""(() => {
            const el = document.querySelector(%s);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.left + r.width / 2, y: r.top + r.height / 2,
                    w: r.width, h: r.height, text: (el.textContent || '').slice(0, 40)};
        })()""" % json.dumps(selector))

    def click(self, selector, settle=0.7):
        b = self.box(selector)
        if not b or b["w"] == 0:
            raise RuntimeError("元素不可点击: " + selector)
        for t in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=t, x=b["x"], y=b["y"],
                      button="left", clickCount=1)
        time.sleep(settle)
        return b

    def type_into(self, selector, text, settle=0.8):
        self.click(selector, settle=0.2)
        self.send("Input.insertText", text=text)
        time.sleep(settle)

    def shot(self, name):
        os.makedirs(SHOTS, exist_ok=True)
        data = self.send("Page.captureScreenshot", format="png")
        path = os.path.join(SHOTS, name + ".png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data["data"]))
        log("    截图 -> %s" % path)
        return path


def start_edge(port):
    exe = next((p for p in EDGE_CANDIDATES if os.path.isfile(p)), None)
    if not exe:
        raise RuntimeError("找不到 msedge.exe")
    profile = os.path.join(HERE, "edge-profile")
    proc = subprocess.Popen(
        [exe, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", "--remote-debugging-port=%d" % port,
         "--user-data-dir=" + profile, "--window-size=1280,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as r:
                json.loads(r.read().decode())
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Edge 调试端口未就绪")


def open_tab(port, url="about:blank"):
    req = urllib.request.Request("http://127.0.0.1:%d/json/new?%s" % (port, url), method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=5) as r:
            tabs = json.loads(r.read().decode())
        return tabs[0]


def api_login(user="ml_selfcheck", pwd="123456"):
    body = json.dumps({"username": user, "password": pwd}).encode()
    req = urllib.request.Request(ROOT + "/api/auth/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        body = json.dumps({"username": user, "password": pwd}).encode()
        req = urllib.request.Request(ROOT + "/api/auth/register", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())


def main():
    proc = start_edge(DEFAULT_PORT)
    try:
        tab = open_tab(DEFAULT_PORT, ROOT + "/")
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Log.enable")
        # 每个新页面都挂上错误收集器，用于最后的"无 JS 异常"检查
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source="""
            window.__errs = [];
            window.addEventListener('error', e => window.__errs.push(String(e.message)));
            window.addEventListener('unhandledrejection',
                e => window.__errs.push('rejection: ' + String(e.reason)));
        """)

        pid = None
        posts = json.loads(urllib.request.urlopen(ROOT + "/api/posts", timeout=10).read().decode())
        if posts:
            pid = posts[0]["id"]

        # ---------------------------------------------------------- 首页
        log("=" * 70)
        log("① 首页：ML 能力面板（真实渲染）")
        log("=" * 70)
        cdp.navigate(ROOT + "/")
        title = cdp.js("document.title")
        cards = cdp.js("document.querySelectorAll('#mlPanel .ml-card').length")
        texts = cdp.js("[...document.querySelectorAll('#mlPanel .ml-card')].map(e => e.innerText.replace(/\\n/g,' | '))")
        log("  title=%s  ML 卡片数=%s" % (title, cards))
        for t in (texts or []):
            log("    - %s" % t[:150])
        check("首页 ML 能力面板渲染 3 张卡", cards == 3, "cards=%s" % cards)
        check("面板含真实统计数字（非占位）",
              bool(texts) and any("已拦截" in t for t in texts))
        cdp.shot("01-home-ml")

        # ---------------------------------------------------------- 登录
        log("")
        log("=" * 70)
        log("② 登录 + 失物招领通知中心（真实点击铃铛）")
        log("=" * 70)
        auth = api_login()
        cdp.js("localStorage.setItem('nuaa_token', %s); localStorage.setItem('nuaa_user', %s); 'ok'"
               % (json.dumps(auth["token"]), json.dumps(auth["username"])))
        cdp.navigate(ROOT + "/lostfound.html")
        bell_visible = cdp.js("(() => {const b=document.getElementById('notifyBell');"
                             "return b && getComputedStyle(b).display !== 'none';})()")
        time.sleep(1.2)
        unread = cdp.js("document.querySelector('#notifyBell .dot').classList.contains('on')")
        unread_txt = cdp.js("document.querySelector('#notifyBell .dot').textContent")
        log("  铃铛可见=%s 有红点=%s 未读数=%s" % (bell_visible, unread, unread_txt))
        check("登录后顶栏出现通知铃铛", bell_visible)
        check("存在未读匹配通知（红点）", unread, "unread=%s" % unread_txt)
        cdp.shot("02-lostfound-bell")

        cdp.click("#notifyBell", settle=0.9)
        opened = cdp.js("document.getElementById('notifyModal').classList.contains('open')")
        items = cdp.js("document.querySelectorAll('#notifyList .notify-item').length")
        first = cdp.js("(() => {const e=document.querySelector('#notifyList .notify-item');"
                       "return e ? e.innerText.replace(/\\n/g,' | ') : '';})()")
        log("  通知弹窗打开=%s 条数=%s" % (opened, items))
        log("    首条：%s" % (first or "")[:170])
        check("点击铃铛弹出通知中心", opened)
        check("通知列表有内容且带相似度", bool(first) and "相似度" in (first or ""))
        cdp.shot("03-notify-modal")

        # 点第一条通知 → 跳到匹配的那条失物信息
        cdp.click("#notifyList .notify-item", settle=1.8)
        url_now = cdp.js("location.pathname + location.search")
        modal_open = cdp.js("document.getElementById('detailModal').classList.contains('open')")
        detail_title = cdp.js("document.getElementById('dTitle').textContent")
        log("  跳转后 URL=%s 详情弹窗=%s 标题=%s" % (url_now, modal_open, detail_title))
        check("点击通知能打开被匹配的信息", modal_open, "title=%s" % detail_title)
        cdp.shot("04-notify-jump")
        cdp.js("closeDetail && closeDetail()")

        # ---------------------------------------------------------- 内容拦截
        log("")
        log("=" * 70)
        log("③ 内容安全：在论坛真实输入广告并点发布，应被拦截")
        log("=" * 70)
        cdp.navigate(ROOT + "/forum.html")
        cdp.click("button.btn-primary", settle=0.7)          # 打开发帖弹窗
        form_open = cdp.js("document.getElementById('createModal').classList.contains('open')")
        check("发帖弹窗可打开", form_open)
        cdp.type_into("#fTitle", "招兼职")
        cdp.type_into("#fContent", "校园兼职日结200，加微信详聊，非诚勿扰")
        cdp.click("#createModal .actions .btn-primary", settle=1.5)
        notice_cls = cdp.js("document.getElementById('postNotice').className")
        notice_txt = cdp.js("document.getElementById('postNotice').innerText")
        modal_still = cdp.js("document.getElementById('createModal').classList.contains('open')")
        log("  提示区 class=%s" % notice_cls)
        log("  提示内容：%s" % (notice_txt or "").replace("\n", " | "))
        check("广告被拦截并就地提示", "block" in (notice_cls or "") and "拦截" in (notice_txt or ""))
        check("被拦截时弹窗保留（未误关）", modal_still)
        cdp.shot("05-forum-blocked")

        # 正常内容应放行
        cdp.js("document.getElementById('fTitle').value='';document.getElementById('fContent').value='';'ok'")
        cdp.type_into("#fTitle", "图书馆座位吐槽")
        cdp.type_into("#fContent", "期末周图书馆座位太紧张了，八点半到就没位置，大家有什么办法吗")
        cdp.click("#createModal .actions .btn-primary", settle=1.8)
        closed = cdp.js("!document.getElementById('createModal').classList.contains('open')")
        check("正常帖子正常发布（弹窗关闭）", closed)
        cdp.shot("06-forum-normal")

        # ---------------------------------------------------------- 语义检索
        log("")
        log("=" * 70)
        log("④ 二手市集语义检索（真实输入「笔记本」「单车」）")
        log("=" * 70)
        cdp.navigate(ROOT + "/market.html")
        cdp.type_into("#searchInput", "笔记本", settle=1.6)
        hint = cdp.js("document.getElementById('semHint').innerText")
        first_card = cdp.js("(() => {const e=document.querySelector('#grid .item');"
                            "return e ? e.innerText.replace(/\\n/g,' | ') : '';})()")
        score_badge = cdp.js("document.querySelectorAll('#grid .sem-score').length")
        log("  语义提示：%s" % (hint or "").replace("\n", " | "))
        log("  首条结果：%s" % (first_card or ""))
        check("「笔记本」语义扩展提示出现", "同义词" in (hint or "") or "语义" in (hint or ""))
        check("「笔记本」搜到电脑类商品", "笔记本" in (first_card or "") or "电脑" in (first_card or ""))
        check("结果带相关度徽标", score_badge >= 1, "badges=%s" % score_badge)
        cdp.shot("07-market-semantic-notebook")

        # 关键词模式对比
        cdp.click("#semMode", settle=1.2)
        kw_cards = cdp.js("document.querySelectorAll('#grid .item').length")
        kw_hint = cdp.js("document.getElementById('semHint').innerText")
        log("  切到精确匹配：结果 %s 条，提示=%s" % (kw_cards, (kw_hint or "")[:60]))
        cdp.click("#semMode", settle=1.2)

        cdp.js("document.getElementById('searchInput').value='';'ok'")
        cdp.type_into("#searchInput", "单车", settle=1.6)
        bike = cdp.js("(() => {const e=document.querySelector('#grid .item');"
                      "return e ? e.innerText.replace(/\\n/g,' | ') : '';})()")
        log("  「单车」首条：%s" % (bike or ""))
        check("「单车」能搜到自行车", "自行车" in (bike or ""))
        cdp.shot("08-market-semantic-bike")

        # ---------------------------------------------------------- 手机版
        log("")
        log("=" * 70)
        log("⑤ 手机版：通知铃铛 + 语义检索")
        log("=" * 70)
        cdp.send("Emulation.setDeviceMetricsOverride", width=414, height=860,
                 deviceScaleFactor=2, mobile=True)
        cdp.navigate(ROOT + "/m/")
        time.sleep(0.6)
        m_bell = cdp.js("!!document.getElementById('notifyBell')")
        m_dot = cdp.js("document.getElementById('notifyDot').classList.contains('on')")
        m_ml = cdp.js("document.getElementById('homeMl').innerText.replace(/\\n/g,' | ')")
        log("  手机版铃铛=%s 红点=%s" % (m_bell, m_dot))
        log("  ML 摘要：%s" % (m_ml or "")[:150])
        check("手机版顶栏有通知铃铛", m_bell)
        check("手机版首页显示 ML 能力摘要", "失物智能匹配" in (m_ml or ""))
        cdp.shot("09-mobile-home")

        cdp.js("M.switchTab('market'); 'ok'")
        time.sleep(0.6)
        cdp.js("(() => {const el=document.getElementById('mkSearch'); el.value='笔记本';"
               "el.dispatchEvent(new Event('input',{bubbles:true})); return 'ok';})()")
        time.sleep(1.8)
        m_hint = cdp.js("document.getElementById('mkSemTip').innerText")
        m_card = cdp.js("(() => {const e=document.querySelector('#mkGrid .gcard');"
                        "return e ? e.innerText.replace(/\\n/g,' | ') : '';})()")
        log("  手机版语义提示：%s" % (m_hint or ""))
        log("  手机版首条：%s" % (m_card or ""))
        check("手机版语义检索生效", bool(m_card) and ("笔记本" in m_card or "电脑" in m_card))
        cdp.shot("10-mobile-market-semantic")

        # 手机版通知抽屉
        cdp.js("M.notify.open(); 'ok'")
        time.sleep(1.4)
        m_sheet = cdp.js("document.getElementById('sheet').classList.contains('open')")
        m_items = cdp.js("document.querySelectorAll('#sheetBody .notify-row').length")
        log("  手机版通知抽屉打开=%s 条数=%s" % (m_sheet, m_items))
        check("手机版通知中心可打开且有记录", m_sheet and m_items >= 1, "items=%s" % m_items)
        cdp.shot("11-mobile-notify")

        # ---------------------------------------------------------- JS 报错检查
        log("")
        log("=" * 70)
        log("⑥ 控制台错误检查")
        log("=" * 70)
        errs = cdp.js_errors()
        check("页面无未捕获 JS 异常", not errs, str(errs))

    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    ok = sum(1 for _, o, _ in results if o)
    log("")
    log("=" * 70)
    log("浏览器验证结果：%d 项通过 / %d 项失败" % (ok, len(results) - ok))
    for name, o, d in results:
        if not o:
            log("  FAIL: %s (%s)" % (name, d))
    log("=" * 70)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))
    print("done: %d/%d" % (ok, len(results)))


if __name__ == "__main__":
    main()