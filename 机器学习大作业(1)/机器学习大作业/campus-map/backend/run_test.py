# -*- coding: utf-8 -*-
"""自检脚本：ML 模块 + Flask API 全链路测试，结果写入 UTF-8 日志文件"""
import io
import json
import os
import sys
import threading
import time
import traceback
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "..", "selftest.log")
sys.path.insert(0, BASE)

buf = io.StringIO()

def w(*a):
    print(*a, file=buf)

def get(path):
    with urllib.request.urlopen("http://127.0.0.1:5000" + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, data):
    req = urllib.request.Request(
        "http://127.0.0.1:5000" + path,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

def main():
    # 1. ML 模块测试
    from ml.sentiment import analyze, get_model
    m = get_model()
    w(f"[ML] train_acc={m.train_acc:.3f} test_acc={m.test_acc:.3f}")
    for t in ["图书馆很安静，插座也多，自习圣地",
              "食堂菜难吃还贵，再也不来了",
              "体育馆器材不错就是人有点多",
              "教室网速很快，适合上网课"]:
        w(f"[ML] {t} -> {json.dumps(analyze(t), ensure_ascii=False)}")

    # 2. 启动 Flask 并测 API
    import app as backend
    backend.init_db()
    srv = threading.Thread(target=lambda: backend.app.run(
        host="127.0.0.1", port=5000, debug=False, use_reloader=False), daemon=True)
    srv.start()
    time.sleep(2)

    pois = get("/api/pois")
    w(f"[API] pois={len(pois)} 第一个={pois[0]['name']} stats={pois[0]['stats']}")

    stats = get("/api/stats")
    w(f"[API] stats={json.dumps(stats, ensure_ascii=False)}")

    b1 = [p for p in pois if p["name"] == "一号楼"][0]
    detail = get(f"/api/pois/{b1['id']}")
    w(f"[API] 一号楼子级教室数={len(detail['children'])}")

    lib = [p for p in pois if p["name"] == "图书馆"][0]
    cmts = get(f"/api/pois/{lib['id']}/comments")
    w(f"[API] 图书馆评论数={len(cmts)} 首条tags={cmts[0]['tags']}")

    r = post(f"/api/pois/{lib['id']}/comments",
             {"author": "测试员", "content": "自习室很安静，插座充足，就是人有点多"})
    w(f"[API] 发表评论 -> {json.dumps(r, ensure_ascii=False)}")

    q = get("/api/pois?q=" + urllib.parse.quote("食堂"))
    w(f"[API] 搜索'食堂' -> {[p['name'] for p in q]}")

try:
    main()
    w("[OK] 全部测试通过")
except Exception:
    w("[FAIL]")
    w(traceback.format_exc())
finally:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
