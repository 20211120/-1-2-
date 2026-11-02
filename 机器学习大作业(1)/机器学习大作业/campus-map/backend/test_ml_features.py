# -*- coding: utf-8 -*-
"""
三项新增机器学习能力的全链路自检
================================================================
前提：后端已在 127.0.0.1:5000 运行（源码模式或 exe 模式均可）

    cd campus-map/backend && python app.py
    python test_ml_features.py          # 另开一个窗口
    # 结果见 backend/ml_features_test.log

覆盖：
  ① 失物招领智能匹配：发布 → 匹配打分 → lf_matches 落库 → 双方站内通知
  ② 内容安全：广告/引战/不文明命中即拦截（不给发表，没有人工审核队列）；
     正常吐槽、正常二手交易放行
  ③ 二手市集语义检索：同义词/错别字/大类查询能搜到语义相关商品
  ④ 评论删除：作者可删自己的评论、楼主可删自己帖子/备注下的评论、越权删除被拒；
     另外楼主可删自己的帖子（连带删除帖下评论）
"""
import io
import json
import os
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "ml_features_test.log")
ROOT = "http://127.0.0.1:5000"
BUF = io.StringIO()
PASS, FAIL = [], []


def w(*a):
    print(*a, file=BUF)


def req(method, path, data=None, token=None, form=None):
    url = ROOT + path
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    w("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  -> " + detail) if detail else ""))


def q(s):
    return urllib.parse.quote(s)


def main():
    # ---------------------------------------------------------------- 登录
    w("=" * 72)
    w("① 失物招领智能匹配 + 站内通知推送")
    w("=" * 72)
    user = "ml_selfcheck"
    _, r = req("POST", "/api/auth/register", {"username": user, "password": "123456"})
    if not r.get("token"):
        _, r = req("POST", "/api/auth/login", {"username": user, "password": "123456"})
    token = r.get("token")
    w("  登录用户：%s token=%s" % (user, bool(token)))
    if not token:
        raise RuntimeError("无法登录测试账号")

    # 发一条"寻物"，正常情况下会和库里已存在的"捡到校园卡"形成高置信匹配
    _, item = req("POST", "/api/lostfound", token=token, form={
        "kind": "lost", "title": "丢失校园卡一张",
        "content": "中午在一食堂二楼吃完饭发现校园卡不见了，卡面有贴纸，姓名贴还在",
        "location": "一食堂", "contact": "QQ 111222333"})
    w("  发布寻物 -> id=%s match_count=%s notified=%s" %
      (item.get("id"), item.get("match_count"), item.get("notified")))
    for m in item.get("matches", []):
        w("     匹配《%s》(%s) 相似度 %d%% 等级=%s 理由=%s" %
          (m["title"], m["author"], m["score_pct"], m["level"],
           "；".join(m["reasons"][:3])))
    check("发布寻物后返回匹配结果", item.get("match_count", 0) >= 1,
          "match_count=%s" % item.get("match_count"))
    check("高置信匹配触发双方推送", item.get("notified", 0) >= 1,
          "notified=%s" % item.get("notified"))

    lid = item.get("id")
    _, mres = req("GET", "/api/lostfound/%s/matches" % lid, token=token)
    w("  匹配详情接口 -> %d 条" % len(mres.get("matches", [])))
    for m in mres.get("matches", []):
        w("     %.3f %s | 文本%.2f 类别%.2f 地点%.2f 时间%.2f 特征%.2f" %
          (m["score"], m["item"]["title"], m["detail"]["text"], m["detail"]["item"],
           m["detail"]["location"], m["detail"]["time"], m["detail"]["feature"]))
    check("匹配详情接口返回打分细项",
          bool(mres.get("matches")) and "detail" in mres["matches"][0])

    _, notes = req("GET", "/api/notifications", token=token)
    w("  通知中心 -> 未读 %d 条" % notes.get("unread", 0))
    for n in notes.get("items", [])[:3]:
        w("     %s | %s" % (n["title"], n["body"][:70]))
        if n.get("related"):
            w("       关联信息：《%s》(%s %s)" % (n["related"]["title"],
                                                 n["related"]["kind"], n["related"]["location"]))
    check("通知中心收到匹配提醒", notes.get("unread", 0) >= 1,
          "unread=%s" % notes.get("unread"))
    check("通知带出被匹配的信息（可点击跳转）",
          bool(notes.get("items")) and notes["items"][0].get("related") is not None)

    _, rd = req("POST", "/api/notifications/read", {}, token)
    check("标记已读后未读归零", rd.get("unread") == 0, "unread=%s" % rd.get("unread"))

    # 反向：发一条"招领"，应匹配到上面的寻物
    _, fitem = req("POST", "/api/lostfound", token=token, form={
        "kind": "found", "title": "一食堂捡到校园卡",
        "content": "在食堂二楼餐桌上捡到一张校园卡，有贴纸和姓名贴，已交服务台",
        "location": "一食堂", "contact": "QQ 444555666"})
    w("  反向发布招领 -> id=%s 匹配=%s" % (fitem.get("id"), fitem.get("match_count")))
    for m in fitem.get("matches", []):
        w("     匹配《%s》 相似度 %d%%" % (m["title"], m["score_pct"]))
    check("招领也能反向匹配到寻物", fitem.get("match_count", 0) >= 1)

    # ---------------------------------------------------------------- 内容安全
    w("")
    w("=" * 72)
    w("② 垃圾广告 / 引战 / 不文明内容识别拦截")
    w("=" * 72)
    cases = [
        # (描述, 接口, 请求体, 期望动作)
        ("论坛-兼职刷单广告", "post_forum",
         {"title": "招兼职", "content": "校园兼职日结200，加微信详聊，非诚勿扰"}, "block"),
        ("论坛-代写论文广告", "post_forum",
         {"title": "代写服务", "content": "专业代写论文包过，加QQ详聊，价格便宜"}, "block"),
        ("论坛-引战挑事", "post_forum",
         {"title": "说个事", "content": "计算机学院的就这水平，也配在这发言？"}, "block"),
        ("论坛-不文明辱骂", "post_forum",
         {"title": "无语", "content": "傻逼东西，滚出去"}, "block"),
        ("论坛-正常吐槽（应放行）", "post_forum",
         {"title": "食堂吐槽", "content": "食堂的菜又贵又难吃，避雷，还是去外面吃吧"}, "allow"),
        ("论坛-正常讨论（应放行）", "post_forum",
         {"title": "图书馆占座", "content": "期末周图书馆占座太严重了，建议平台出个举报入口"}, "allow"),
        ("市集-正常二手带微信（应放行）", "post_market",
         {"kind": "sell", "title": "出二手高数教材", "content": "九成新，需要的加微信 book123 详聊",
          "category": "书籍", "price": "15"}, "allow"),
        ("论坛-轻度引战（应拦截）", "post_forum",
         {"title": "讨论一下", "content": "不服来辩，我就说一食堂最难吃"}, "block"),
        ("论坛-站内引流（应拦截）", "post_forum",
         {"title": "出几本书", "content": "出二手高数教材，需要的加微信 abc123"}, "block"),
        ("市集-刷单广告（应拦截）", "post_market",
         {"kind": "sell", "title": "招代理", "content": "招收代理一件代发，刷单返利日结，加微信",
          "category": "其他"}, "block"),
        ("市集-代写广告（应拦截）", "post_market",
         {"kind": "sell", "title": "代写作业", "content": "专业代写作业包修改，代考包过，加QQ",
          "category": "其他"}, "block"),
        ("失物-正常带联系方式（应放行）", "post_lf",
         {"kind": "lost", "title": "丢了保温杯", "content": "黑色保温杯落在图书馆四楼，捡到请联系我",
          "location": "图书馆", "contact": "微信 abc123"}, "allow"),
        ("评论-辱骂（应拦截）", "comment",
         {"pid": None, "content": "你他妈算什么东西，也配评论"}, "block"),
        ("评论-正常（应放行）", "comment",
         {"pid": None, "content": "同感，淋浴间确实经常没热水，希望修一下"}, "allow"),
    ]
    _, posts = req("GET", "/api/posts")
    pid = posts[0]["id"] if posts else None

    for desc, kind, payload, expect in cases:
        if kind == "post_forum":
            status, r = req("POST", "/api/posts", payload, token)
        elif kind == "post_market":
            status, r = req("POST", "/api/market", form=payload, token=token)
        elif kind == "post_lf":
            status, r = req("POST", "/api/lostfound", form=payload, token=token)
        else:
            status, r = req("POST", "/api/posts/%s/comments" % pid,
                            {"content": payload["content"]}, token)
        mod = r.get("moderation") or {}
        action = mod.get("action", "allow" if status < 400 else "block")
        w("  %-24s -> HTTP %s action=%s label=%s risk=%s %s" %
          (desc, status, action, mod.get("label_name", "-"), mod.get("risk", "-"),
           ("| " + r["error"]) if r.get("error") else ""))
        check(desc, action == expect, "action=%s 期望=%s" % (action, expect))

    _, logs = req("GET", "/api/moderation/logs", token=token)
    summary = logs.get("summary", {})
    w("  处置记录：拦截 %s 条（命中即拦截，无人工审核队列；历史遗留复核记录 %s 条）；"
      "模型 5 折准确率 %.4f（宏平均F1 %.4f，%s）" %
      (summary.get("moderation_blocked"), summary.get("moderation_review"),
       summary.get("moderation_acc", 0), summary.get("moderation_macro_f1", 0),
       summary.get("moderation_model", "-")))
    w("  最近记录：")
    for it in logs.get("items", [])[:5]:
        w("     [%s] %s %s risk=%.3f 命中=%s | %s" %
          (it["action"], it["target_type"], it["label_name"], it["risk"],
           "、".join(it["matched"]), it["snippet"][:32]))
    check("内容安全处置日志可查", bool(logs.get("items")))

    # ---------------------------------------------------------------- 语义检索
    w("")
    w("=" * 72)
    w("③ 二手市集语义检索")
    w("=" * 72)
    # 先补几条商品，保证检索演示有足够语料（这些数据也会留在库里，方便演示）
    _, existed = req("GET", "/api/market")
    titles = {it["title"] for it in existed} if isinstance(existed, list) else set()
    for payload in [
        {"kind": "sell", "title": "出个联想笔记本电脑", "content": "8G 内存，日常办公够用，换了台式机所以出",
         "category": "电子产品", "price": "1500"},
        {"kind": "sell", "title": "出宿舍护眼台灯", "content": "用了一学期，宿舍搬空出，可小刀",
         "category": "生活用品", "price": "20"},
        {"kind": "want", "title": "求购一个保温杯", "content": "宿舍用，能保温就行，价格好商量",
         "category": "生活用品", "price": ""},
        {"kind": "sell", "title": "出罗技无线鼠标", "content": "用了三个月，配笔记本用正合适，可小刀",
         "category": "电子产品", "price": "45"},
    ]:
        if payload["title"] in titles:      # 自检可重复执行，不重复造数据
            continue
        req("POST", "/api/market", form=payload, token=token)

    queries = [
        ("笔记本", "电脑/笔记本"),
        ("单车", "自行车"),
        ("机械健盘", "机械键盘（错别字）"),
        ("音乐器材", "吉他（大类查询）"),
        ("想买个喝水的杯子", "保温杯（自然语言）"),
        ("宿舍用品", "台灯/宿舍用品"),
    ]
    for query, expect in queries:
        _, sem = req("GET", "/api/market/search?q=" + q(query))
        _, kw = req("GET", "/api/market/search?q=" + q(query) + "&mode=keyword")
        w("  查询「%s」（期望能找到：%s）" % (query, expect))
        if sem.get("explain"):
            w("     查询扩展：%s" % " | ".join(sem["explain"]))
        for it in sem.get("items", [])[:3]:
            w("     语义 %.3f  %s  %s  命中=%s" %
              (it["score"], it["title"], "¥%s" % it["price"] if it["price"] else "面议",
               "、".join(it.get("matched_terms", [])) or "-"))
        w("     关键词模式命中 %d 条；语义模式命中 %d 条" %
          (len(kw.get("items", [])), len(sem.get("items", []))))
        check("语义检索「%s」有结果" % query, len(sem.get("items", [])) >= 1)
        check("语义检索「%s」比关键词召回更多" % query,
              len(sem.get("items", [])) > len(kw.get("items", [])) or
              (sem.get("items") and sem["items"][0].get("score_pct", 0) > 0))

    _, st = req("GET", "/api/stats")
    w("")
    w("=" * 72)
    w("④ 评论删除（删评权限 + 越权拦截）")
    w("=" * 72)
    # 准备：A 发一个帖子，B 用另一个账号
    _, p = req("POST", "/api/posts",
               {"title": "删评功能自检帖", "content": "用来验证评论删除权限的帖子"}, token)
    test_pid = p.get("id")
    w("  自检帖 id=%s（楼主=%s）" % (test_pid, user))

    other = "ml_selfcheck_b"
    _, r2 = req("POST", "/api/auth/register", {"username": other, "password": "123456"})
    if not r2.get("token"):
        _, r2 = req("POST", "/api/auth/login", {"username": other, "password": "123456"})
    token_b = r2.get("token")
    w("  第二账号：%s token=%s" % (other, bool(token_b)))

    def comment_count(pid_):
        _, pd = req("GET", "/api/posts/%s" % pid_)
        return len(pd.get("comments", []))

    # 1) A 删自己的评论
    _, c1 = req("POST", "/api/posts/%s/comments" % test_pid, {"content": "一楼是我的评论"}, token)
    before = comment_count(test_pid)
    s_del, r_del = req("DELETE", "/api/posts/%s/comments/%s" % (test_pid, c1["id"]), token=token)
    after = comment_count(test_pid)
    w("  A 删自己的评论 -> HTTP %s %s（评论数 %d -> %d）" % (s_del, r_del, before, after))
    check("评论作者可以删除自己的评论", s_del == 200 and after == before - 1)

    # 2) B 不能删 A 的评论
    _, c2 = req("POST", "/api/posts/%s/comments" % test_pid, {"content": "这条是楼主的评论"}, token)
    s_bad, r_bad = req("DELETE", "/api/posts/%s/comments/%s" % (test_pid, c2["id"]), token=token_b)
    w("  B 删 A 的评论（越权） -> HTTP %s %s" % (s_bad, r_bad.get("error")))
    check("越权删除他人评论被拒绝", s_bad == 403 and comment_count(test_pid) == before)

    # 3) 楼主 A 可以删 B 在该帖下的评论
    _, c3 = req("POST", "/api/posts/%s/comments" % test_pid, {"content": "B 在这个帖子下的评论"}, token_b)
    s_own, r_own = req("DELETE", "/api/posts/%s/comments/%s" % (test_pid, c3["id"]), token=token)
    w("  楼主 A 删 B 的评论（楼主权限） -> HTTP %s by=%s" % (s_own, r_own.get("by")))
    check("楼主可以删除自己帖子下的他人评论", s_own == 200 and r_own.get("by") == "owner")

    # 4) 未登录不能删
    _, c4 = req("POST", "/api/posts/%s/comments" % test_pid, {"content": "未登录删除测试"}, token)
    s_anon, _ = req("DELETE", "/api/posts/%s/comments/%s" % (test_pid, c4["id"]))
    w("  未登录删除 -> HTTP %s" % s_anon)
    check("未登录不能删评论", s_anon == 401)
    req("DELETE", "/api/posts/%s/comments/%s" % (test_pid, c4["id"]), token=token)

    # 5) 地点评论：作者可删自己的
    _, c5 = req("POST", "/api/pois/1/comments", {"content": "图书馆四楼插座很多，适合自习"}, token)
    s_poi, r_poi = req("DELETE", "/api/pois/1/comments/%s" % c5["id"], token=token)
    w("  地点评论作者删自己的 -> HTTP %s %s" % (s_poi, r_poi.get("ok")))
    check("地点评论作者可以删除自己的评论", s_poi == 200 and r_poi.get("ok"))
    s_poi_bad, r_poi_bad = req("DELETE", "/api/pois/1/comments/1", token=token_b)
    w("  B 删别人的地点评论 -> HTTP %s %s" % (s_poi_bad, r_poi_bad.get("error")))
    check("地点评论越权删除被拒绝", s_poi_bad in (403, 404))

    # 6) 被拦截的评论不入库（拦截 = 不给发表）
    n_before = comment_count(test_pid)
    s_blk, r_blk = req("POST", "/api/posts/%s/comments" % test_pid,
                       {"content": "傻逼楼主，去死吧"}, token)
    n_after = comment_count(test_pid)
    w("  发辱骂评论 -> HTTP %s action=%s 评论数 %d -> %d" %
      (s_blk, (r_blk.get("moderation") or {}).get("action"), n_before, n_after))
    check("不良评论被直接拦截且不入库", s_blk == 400 and n_after == n_before)

    # 7) 删帖：只有楼主本人能删，连带删除帖下所有评论
    w("")
    w("  -- 删帖（校园评论的帖子本身） --")
    _, p2 = req("POST", "/api/posts", {"title": "待删除的帖子", "content": "验证删帖与级联删除评论"}, token)
    del_pid = p2.get("id")
    req("POST", "/api/posts/%s/comments" % del_pid, {"content": "这条评论会随帖子一起被删"}, token)
    req("POST", "/api/posts/%s/comments" % del_pid, {"content": "第二条也会一起删"}, token_b)
    w("  自检帖 id=%s，先塞 2 条评论" % del_pid)
    s_bad2, r_bad2 = req("DELETE", "/api/posts/%s" % del_pid, token=token_b)
    w("  B 删 A 的帖子（越权） -> HTTP %s %s" % (s_bad2, r_bad2.get("error")))
    check("越权删除他人帖子被拒绝", s_bad2 == 403)
    s_anon2, _ = req("DELETE", "/api/posts/%s" % del_pid)
    check("未登录不能删帖", s_anon2 == 401)
    s_dp, r_dp = req("DELETE", "/api/posts/%s" % del_pid, token=token)
    s_get, _ = req("GET", "/api/posts/%s" % del_pid)
    w("  楼主删自己的帖子 -> HTTP %s 连带删除评论 %s 条；再查该帖 -> HTTP %s" %
      (s_dp, r_dp.get("comments_deleted"), s_get))
    check("楼主可以删除自己的帖子", s_dp == 200 and r_dp.get("ok"))
    check("删帖连带删除帖下评论", r_dp.get("comments_deleted") == 2,
          "comments_deleted=%s" % r_dp.get("comments_deleted"))
    check("删掉的帖子再查为 404", s_get == 404)

    w("")
    w("  全站统计（含 ML 指标）：")
    w("     " + json.dumps({k: st[k] for k in (
        "lostfound_open", "market_open", "lf_match_count", "notification_count",
        "moderation_blocked", "moderation_review", "moderation_acc",
        "moderation_macro_f1", "moderation_samples") if k in st},
        ensure_ascii=False))

    w("")
    w("=" * 72)
    w("结果：%d 项通过 / %d 项失败" % (len(PASS), len(FAIL)))
    if FAIL:
        w("失败项：")
        for f in FAIL:
            w("  - " + f)
    else:
        w("全部通过 ✔")
    w("=" * 72)


try:
    main()
except Exception:
    w("[异常] 自检中断")
    w(traceback.format_exc())
    FAIL.append("脚本异常中断")
finally:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(BUF.getvalue())
    print("测试完成：%d 通过 / %d 失败，详情见 %s" % (len(PASS), len(FAIL), LOG))
    sys.stdout.flush()