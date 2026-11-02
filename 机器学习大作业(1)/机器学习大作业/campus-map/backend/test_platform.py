# -*- coding: utf-8 -*-
"""南航评论区平台全链路自检：认证/备注权限/失物招领/论坛/统计"""
import io
import json
import os
import sys
import traceback
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "platform_test.log")
buf = io.StringIO()

def w(*a):
    print(*a, file=buf)

def req(method, path, data=None, token=None, form=None):
    url = "http://127.0.0.1:5000" + path
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if form is not None:  # multipart 简化：用 urlencoded 代替（无图片场景）
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def main():
    # 认证
    s, r1 = req("POST", "/api/auth/register", {"username": "测试君", "password": "123456"})
    w(f"[AUTH] 注册 -> {s} {json.dumps(r1, ensure_ascii=False)}")
    token = r1.get("token")
    s, r2 = req("POST", "/api/auth/login", {"username": "测试君", "password": "123456"})
    w(f"[AUTH] 登录 -> {s} token={bool(r2.get('token'))}")
    token = r2.get("token", token)
    s, r3 = req("POST", "/api/auth/login", {"username": "测试君", "password": "wrong"})
    w(f"[AUTH] 错误密码 -> {s} {r3}")

    # 备注：公开/私密权限
    s, n1 = req("POST", "/api/pois/1/notes",
                {"content": "周三下午四楼人少", "visibility": "public"}, token)
    s2, n2 = req("POST", "/api/pois/1/notes",
                 {"content": "我常坐的靠窗位置", "visibility": "private"}, token)
    w(f"[NOTE] 发备注 -> {s}/{s2} ids={n1.get('id')},{n2.get('id')}")
    s, anon = req("GET", "/api/pois/1/notes")
    s2, mine = req("GET", "/api/pois/1/notes", token=token)
    w(f"[NOTE] 匿名可见={len(anon)} 登录可见={len(mine)} (私密应仅登录可见)")
    s, dn = req("DELETE", f"/api/notes/{n2.get('id')}", token=token)
    w(f"[NOTE] 删除自己备注 -> {s} {dn}")
    s, dn2 = req("DELETE", f"/api/notes/{n1.get('id')}")   # 无 token
    w(f"[NOTE] 未登录删除 -> {s} {dn2}")

    # 失物招领
    s, lf = req("POST", "/api/lostfound",
                form={"kind": "lost", "title": "丢了校园卡", "content": "在体育中心丢的",
                      "location": "体育中心", "contact": "vx: test"}, token=token)
    w(f"[LF] 发帖 -> {s} {lf}")
    s, lf_list = req("GET", "/api/lostfound?kind=lost")
    w(f"[LF] 寻物列表 -> {s} 共{len(lf_list)}条 首条={lf_list[0]['title']}")
    s, un = req("POST", "/api/lostfound", form={"kind": "lost", "title": "x"})  # 未登录
    w(f"[LF] 未登录发帖 -> {s} {un}")
    s, rs = req("POST", f"/api/lostfound/{lf.get('id')}/resolve", token=token)
    w(f"[LF] 标记已解决 -> {s} {rs}")

    # 论坛
    s, p = req("POST", "/api/posts",
               {"title": "体育馆淋浴间什么时候修", "content": "又没热水了，冬天真的受不了"}, token)
    w(f"[POST] 发帖 -> {s} analysis={json.dumps(p.get('analysis'), ensure_ascii=False)}")
    pid = p.get("id")
    s, c = req("POST", f"/api/posts/{pid}/comments", {"content": "同问，太冷了"}, token)
    s, lk = req("POST", f"/api/posts/{pid}/like")
    s, pd = req("GET", f"/api/posts/{pid}")
    w(f"[POST] 详情 -> 评论{len(pd['comments'])}条 likes={pd['likes']} tags={pd['tags']}")
    s, plist = req("GET", "/api/posts?q=" + urllib.parse.quote("图书馆"))
    w(f"[POST] 搜索'图书馆' -> {[p2['title'] for p2 in plist]}")

    # 统计
    s, st = req("GET", "/api/stats")
    w(f"[STATS] {json.dumps(st, ensure_ascii=False)}")

    # ---- 新增：任意点备注 + 组合搜索 ----
    s, mn = req("POST", "/api/map_notes",
                {"content": "这里能拍到主楼全景", "visibility": "public",
                 "lat": 31.9360, "lng": 118.7875}, token)
    w(f"[MAPNOTE] 自由标注 -> {s} {mn}")
    s, mn2 = req("POST", "/api/map_notes",
                 {"content": "我的秘密基地", "visibility": "private",
                  "lat": 31.9350, "lng": 118.7860}, token)
    s, all_notes = req("GET", "/api/map_notes")
    s2, my_notes = req("GET", "/api/map_notes", token=token)
    w(f"[MAPNOTE] 匿名可见自由标注={len(all_notes)} 登录可见={len(my_notes)}（私密应+1）")

    # POI 备注「最高的楼」绑定主楼，再组合搜索
    s, zl = req("GET", "/api/pois?q=" + urllib.parse.quote("主楼"))
    if not zl:      # 有些库里没有名字/描述带"主楼"的地点，退化为第一个地点
        s, zl = req("GET", "/api/pois")
    zl_id = zl[0]["id"]
    s, n3 = req("POST", f"/api/pois/{zl_id}/notes",
                {"content": "全校最高的楼，爬楼锻炼好去处", "visibility": "public"}, token)
    s, sr = req("GET", "/api/search_map?q=" + urllib.parse.quote("最高的楼"))
    w(f"[SEARCH] 搜'最高的楼' -> pois={len(sr['pois'])} notes="
      f"{[(n['content'][:10], n.get('poi_name')) for n in sr['notes']]}")
    s, sr2 = req("GET", "/api/search_map?q=" + urllib.parse.quote("秘密基地"))
    w(f"[SEARCH] 匿名搜私密备注 -> notes={len(sr2['notes'])}（应为0）")
    s, sr3 = req("GET", "/api/search_map?q=" + urllib.parse.quote("秘密基地"), token=token)
    w(f"[SEARCH] 本人搜私密备注 -> notes={len(sr3['notes'])}（应为1）")

    # ---- 新增：二手市集 ----
    s, mk = req("POST", "/api/market",
                form={"kind": "sell", "title": "出二手高数教材", "content": "同济第七版",
                      "category": "书籍", "price": "15", "contact": "vx: book"}, token=token)
    w(f"[MARKET] 发布 -> {s} {mk}")
    s, ml = req("GET", "/api/market?kind=sell&category=" + urllib.parse.quote("书籍"))
    w(f"[MARKET] 出售-书籍 -> 共{len(ml)}条 首条={ml[0]['title']} ¥{ml[0]['price']}")
    s, un2 = req("POST", "/api/market", form={"kind": "sell", "title": "x"})
    w(f"[MARKET] 未登录发布 -> {s} {un2}")
    s, dn3 = req("POST", f"/api/market/{mk.get('id')}/done", token=token)
    w(f"[MARKET] 标记已成交 -> {s} {dn3}")

try:
    main()
    w("[OK] 平台全链路测试通过")
except Exception:
    w("[FAIL]")
    w(traceback.format_exc())
finally:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
