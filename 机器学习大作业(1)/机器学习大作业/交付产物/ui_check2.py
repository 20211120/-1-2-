# -*- coding: utf-8 -*-
"""
评论删除 + 删帖 + 内容拦截（不给发表）的浏览器端到端验证
================================================================
真实鼠标点击 / 真实文本输入 / 真实截图，走 Edge DevTools 协议。

    python ui_check2.py

注：删除前页面会弹 confirm，脚本把 window.confirm 包一层记录提示语并自动确认
（既验证了"确实调用了二次确认"，又不让模态框卡住 CDP）。
"""
import base64
import json
import os
import subprocess
import time
import urllib.request

import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "verify_ui2.log")
SHOTS = os.path.join(HERE, "shots")
ROOT = "http://127.0.0.1:5000"
PORT = 52007
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]

buf, results = [], []


def log(*a):
    line = " ".join(str(x) for x in a)
    buf.append(line)
    print(line)


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    log("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  -> " + str(detail)) if detail else ""))


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
            if "method" in msg:
                self.events.append(msg)

    def js(self, expr, await_promise=True):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise)
        return r.get("result", {}).get("value")

    def navigate(self, url, settle=1.6):
        self.send("Page.navigate", url=url)
        time.sleep(settle)
        for _ in range(40):
            if self.js("document.readyState") == "complete":
                break
            time.sleep(0.25)
        time.sleep(0.5)

    def box(self, selector):
        return self.js("""(() => {
            const el = document.querySelector(%s);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.left + r.width/2, y: r.top + r.height/2, w: r.width};
        })()""" % json.dumps(selector))

    def click(self, selector, settle=0.8):
        b = self.box(selector)
        if not b or b["w"] == 0:
            raise RuntimeError("元素不可点击: " + selector)
        for t in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=t, x=b["x"], y=b["y"],
                      button="left", clickCount=1)
        time.sleep(settle)
        return b

    def type_into(self, selector, text, settle=0.7):
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

    def js_errors(self):
        inpage = self.js("(window.__errs || []).slice(0, 5)") or []
        cdp = [e["params"]["exceptionDetails"].get("text") for e in self.events
               if e.get("method") == "Runtime.exceptionThrown"]
        return list(inpage) + cdp


def start_edge():
    exe = next(p for p in EDGE if os.path.isfile(p))
    prof = os.path.join(HERE, "edge-profile-ui2")
    proc = subprocess.Popen(
        [exe, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", "--remote-debugging-port=%d" % PORT,
         "--user-data-dir=" + prof, "--window-size=1280,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json/version" % PORT, timeout=2)
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Edge 调试端口未就绪")


def open_tab(url):
    req = urllib.request.Request("http://127.0.0.1:%d/json/new?%s" % (PORT, url), method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def api_json(method, path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    rq = urllib.request.Request(ROOT + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(rq, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    auth = None
    body = json.dumps({"username": "ml_selfcheck", "password": "123456"}).encode()
    for path in ("/api/auth/login", "/api/auth/register"):
        try:
            rq = urllib.request.Request(ROOT + path, data=body,
                                        headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(rq, timeout=10) as r:
                auth = json.loads(r.read().decode())
            if auth.get("token"):
                break
        except Exception:
            continue
    if not auth or not auth.get("token"):
        raise RuntimeError("无法登录测试账号")

    proc = start_edge()
    try:
        cdp = CDP(open_tab("about:blank")["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source="""
            window.__errs = [];
            window.addEventListener('error', e => window.__errs.push(String(e.message)));
            window.addEventListener('unhandledrejection',
                e => window.__errs.push('rejection: ' + String(e.reason)));
        """)
        cdp.navigate(ROOT + "/forum.html")
        cdp.js("localStorage.setItem('nuaa_token', %s); localStorage.setItem('nuaa_user', %s); 'ok'"
               % (json.dumps(auth["token"]), json.dumps(auth["username"])))
        cdp.navigate(ROOT + "/forum.html")

        # 让 confirm 自动确认，同时记录提示语（验证确实弹了二次确认）
        cdp.js("""window.__confirmMsg = null;
                  window.confirm = (m) => { window.__confirmMsg = m; return true; }; 'ok'""")

        # ---------------------------------------------------------- 发帖
        log("=" * 70)
        log("① 发一个用于验证删评的帖子")
        log("=" * 70)
        title = "删评UI验证帖"
        cdp.click("button.btn-primary", settle=0.6)
        cdp.type_into("#fTitle", title)
        cdp.type_into("#fContent", "这条帖子用来验证评论删除按钮与拦截提示。")
        cdp.click("#createModal .actions .btn-primary", settle=1.4)
        cdp.type_into("#searchInput", title, settle=0.6)
        cards = cdp.js("document.querySelectorAll('#list .post').length")
        log("  搜索到帖子卡片 %s 张" % cards)
        check("帖子已发布并出现在列表中", cards >= 1, "cards=%s" % cards)

        # ---------------------------------------------------------- 评论 + 删评
        log("")
        log("=" * 70)
        log("② 发表评论 → 出现「删除」→ 点删除 → 评论消失")
        log("=" * 70)
        cdp.click("#list .post", settle=1.2)
        detail_open = cdp.js("document.getElementById('detailModal').classList.contains('open')")
        check("帖子详情弹窗可打开", detail_open)
        cdp.type_into("#cmtInput", "这是一条待删除的测试评论")
        cdp.click(".cmt-form .btn-primary", settle=1.4)
        n_before = cdp.js("document.querySelectorAll('#dComments .citem').length")
        has_del = cdp.js("!!document.querySelector('#dComments .c-del')")
        del_text = cdp.js("(document.querySelector('#dComments .c-del')||{}).textContent")
        log("  评论数=%s，出现删除入口=%s（文案：%s）" % (n_before, has_del, del_text))
        check("自己发的评论出现「删除」入口", has_del and del_text == "删除")
        cdp.shot("13-forum-comment-delete-btn")

        cdp.click("#dComments .c-del", settle=1.5)
        confirm_msg = cdp.js("window.__confirmMsg")
        n_after = cdp.js("document.querySelectorAll('#dComments .citem').length")
        log("  删除前二次确认提示：%s" % confirm_msg)
        log("  评论数 %s -> %s" % (n_before, n_after))
        check("删除前弹二次确认", bool(confirm_msg) and "删除" in (confirm_msg or ""))
        check("点击删除后评论真的没了", n_after == n_before - 1,
              "%s -> %s" % (n_before, n_after))
        cdp.shot("14-forum-comment-deleted")

        # ---------------------------------------------------------- 拦截不给发表
        log("")
        log("=" * 70)
        log("③ 不良评论：直接拦截、不给发表（没有审核队列）")
        log("=" * 70)
        n0 = cdp.js("document.querySelectorAll('#dComments .citem').length")
        cdp.type_into("#cmtInput", "傻逼楼主，去死吧")
        cdp.click(".cmt-form .btn-primary", settle=1.5)
        cls = cdp.js("document.getElementById('cmtNotice').className")
        txt = cdp.js("document.getElementById('cmtNotice').innerText")
        n1 = cdp.js("document.querySelectorAll('#dComments .citem').length")
        log("  提示区 class=%s" % cls)
        log("  提示内容：%s" % (txt or "").replace("\n", " | "))
        log("  评论数 %s -> %s（应不变）" % (n0, n1))
        check("辱骂评论被拦截并就地提示",
              "block" in (cls or "") and "拦截" in (txt or ""))
        check("被拦截的评论不入库（列表条数不变）", n1 == n0, "%s -> %s" % (n0, n1))
        cdp.shot("15-forum-comment-blocked")

        # ---------------------------------------------------------- 删帖
        log("")
        log("=" * 70)
        log("⑤ 删除自己发的帖子（详情弹窗里的「删除帖子」）")
        log("=" * 70)
        cdp.send("Emulation.clearDeviceMetricsOverride")
        cdp.navigate(ROOT + "/forum.html")
        cdp.js("window.confirm = (m) => { window.__confirmMsg = m; return true; }; 'ok'")
        title2 = "待删除UI验证帖"
        cdp.click("button.btn-primary", settle=0.6)
        cdp.type_into("#fTitle", title2)
        cdp.type_into("#fContent", "这个帖子用来验证删帖按钮，稍后会被删除。")
        cdp.click("#createModal .actions .btn-primary", settle=1.5)
        cdp.type_into("#searchInput", title2, settle=0.6)
        cards2 = cdp.js("document.querySelectorAll('#list .post').length")
        check("待删帖子已发布", cards2 >= 1, "cards=%s" % cards2)
        cdp.click("#list .post", settle=1.2)
        btn_post = cdp.js("""(() => {const b = document.getElementById('dDelPost');
            return b && getComputedStyle(b).display !== 'none' ? b.textContent : null;})()""")
        log("  详情弹窗里的删帖按钮：%s" % btn_post)
        check("自己的帖子出现「删除帖子」按钮", btn_post == "删除帖子")
        cdp.shot("18-forum-post-delete-btn")

        cdp.click("#dDelPost", settle=1.8)
        confirm2 = cdp.js("window.__confirmMsg")
        left = cdp.js("""[...document.querySelectorAll('#list .post .title')]
            .filter(e => e.textContent === %s).length""" % json.dumps(title2))
        log("  删帖二次确认：%s" % confirm2)
        log("  列表中同名帖子剩余：%s 条" % left)
        check("删帖有二次确认", bool(confirm2) and "帖子" in (confirm2 or ""))
        check("帖子已从列表消失", left == 0, "剩余 %s" % left)
        cdp.shot("19-forum-post-deleted")

        # ---------------------------------------------------------- 手机版
        log("")
        log("=" * 70)
        log("⑥ 手机版：评论删除 + 删帖")
        log("=" * 70)
        cdp.send("Emulation.setDeviceMetricsOverride", width=414, height=860,
                 deviceScaleFactor=2, mobile=True)
        cdp.navigate(ROOT + "/m/")
        time.sleep(0.8)
        pid = cdp.js("""(async () => {
            const r = await fetch('http://127.0.0.1:5000/api/posts').then(x => x.json());
            const hit = r.find(p => p.title === %s);
            return hit ? hit.id : null;
        })()""" % json.dumps(title))
        log("  手机版目标帖子 id=%s" % pid)
        check("手机版能取到目标帖子", pid is not None)
        cdp.js("window.confirm = (m) => { window.__confirmMsg = m; return true; }; 'ok'")
        cdp.js("M.forum.open(%s); 'ok'" % pid)
        time.sleep(1.4)
        cdp.type_into("#fmCmtInput", "手机版待删除评论")
        cdp.click("#sheetBody .input-row .btn", settle=1.5)
        m_del = cdp.js("!!document.querySelector('#sheetBody .c-del')")
        m_n = cdp.js("document.querySelectorAll('#sheetBody .comment').length")
        log("  手机版出现删除入口=%s，评论数=%s" % (m_del, m_n))
        check("手机版评论出现「删除」入口", m_del)
        cdp.shot("16-mobile-comment-delete")
        cdp.click("#sheetBody .c-del", settle=1.6)
        m_n2 = cdp.js("document.querySelectorAll('#sheetBody .comment').length")
        log("  手机版删除后评论数=%s" % m_n2)
        check("手机版删评生效", m_n2 == m_n - 1, "%s -> %s" % (m_n, m_n2))
        cdp.shot("17-mobile-comment-deleted")

        # 手机版删帖
        pnew = api_json("POST", "/api/posts",
                        {"title": "手机版待删帖", "content": "验证手机版删帖按钮"}, auth["token"])
        mpid = pnew.get("id")
        cdp.js("M.forum.open(%s); 'ok'" % mpid)
        time.sleep(1.4)
        m_btn = cdp.js("""(() => {const b = document.querySelector('#sheetBody .btn.red.block');
            return b ? b.textContent.trim() : null;})()""")
        log("  手机版帖子 id=%s 删除按钮=%s" % (mpid, m_btn))
        check("手机版自己的帖子出现删帖按钮", bool(m_btn) and "删除帖子" in m_btn)
        cdp.shot("20-mobile-post-delete")
        cdp.click("#sheetBody .btn.red.block", settle=1.8)
        still = cdp.js("""(async () => {
            const r = await fetch('http://127.0.0.1:5000/api/posts').then(x => x.json());
            return r.filter(p => p.id === %s).length;
        })()""" % mpid)
        log("  手机版删帖后该帖剩余：%s 条" % still)
        check("手机版删帖生效", still == 0, "剩余 %s" % still)

        # ---------------------------------------------------------- JS 异常
        log("")
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
    for n, o, d in results:
        if not o:
            log("  FAIL: %s (%s)" % (n, d))
    log("=" * 70)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))
    print("done: %d/%d" % (ok, len(results)))


if __name__ == "__main__":
    main()