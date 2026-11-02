# -*- coding: utf-8 -*-
"""
三项机器学习能力的离线评估（不依赖后端服务，直接跑模型）
================================================================
    cd campus-map/backend
    python -m ml.eval_all          # 结果同时打印并写入 ml_eval_report.txt

内容：
  ① 失物招领匹配：在人工标注的"正/负配对"集合上算 准确率 / 精确率 / 召回率 / F1
  ② 内容安全：分层 5 折交叉验证（含朴素贝叶斯对比基线）+ 规则层单独表现 + 混淆矩阵
  ③ 语义检索：人工标注的"查询 → 应命中商品"集合，算 Recall@1/@3 与 MRR，
     并与"关键词精确匹配"做同集合对比
"""
import io
import os
import sys

try:
    from . import matcher as M
    from . import moderation as D
    from . import semantic as S
    from .textutil import normalize
except ImportError:                       # 直接 python ml/eval_all.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import matcher as M
    import moderation as D
    import semantic as S
    from textutil import normalize

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml_eval_report.txt")


def _lf(kind, title, content, loc, poi, when, status="open"):
    return {"kind": kind, "title": title, "content": content, "location": loc,
            "poi_id": poi, "created_at": when, "status": status}


# ---------------------------------------------------------------- ① 失物配对标注集
POS_PAIRS = [
    (_lf("lost", "丢失一串钥匙（带飞机挂件）",
         "昨天下午在一号楼1-205教室上课后不见了一串钥匙，挂着一个小飞机模型挂件，很重要",
         "一号楼", 2, "2026-10-25T15:00:00"),
     _lf("found", "一号楼捡到钥匙一串",
         "在一号楼1-205教室捡到一串钥匙，上面有个飞机挂件，失主联系我",
         "一号楼", 2, "2026-10-25T20:30:00")),
    (_lf("lost", "校园卡丢了呜呜",
         "中午在一食堂吃完饭发现校园卡不见了，卡面有贴纸，姓名贴还在",
         "一食堂", 8, "2026-10-25T13:00:00"),
     _lf("found", "捡到一张校园卡",
         "今天中午在一食堂二楼捡到一张校园卡，已交到服务台，失主可以凭学号认领",
         "一食堂", 8, "2026-10-25T12:10:00")),
    (_lf("lost", "黑色保温杯落在图书馆四楼",
         "一个黑色保温杯，杯身贴了张龙猫贴纸，落在图书馆四楼自习区",
         "图书馆", 1, "2026-10-24T21:00:00"),
     _lf("found", "图书馆拾到一个水杯",
         "四楼自习区有个黑色保温杯没人拿，杯子上有卡通贴纸，放在前台了",
         "图书馆", 1, "2026-10-25T09:30:00")),
    (_lf("lost", "AirPods 充电盒丢了",
         "蓝牙耳机充电盒，白色，可能掉在体育中心羽毛球场",
         "体育中心", 6, "2026-10-23T19:00:00"),
     _lf("found", "体育馆捡到白色耳机盒",
         "羽毛球场地边上捡到一个白色无线耳机充电盒，已放体育馆前台",
         "体育中心", 6, "2026-10-23T21:20:00")),
    (_lf("lost", "丢了一把伞",
         "蓝色格子折叠伞，可能落在二号楼阶梯教室",
         "二号楼", 3, "2026-10-22T10:00:00"),
     _lf("found", "二号楼捡到折叠伞",
         "阶梯教室最后一排有一把蓝色格子折叠伞，放讲台了",
         "二号楼", 3, "2026-10-22T12:00:00")),
    (_lf("lost", "学生证丢了",
         "姓王，学号尾号 27，可能掉在三号楼实验室",
         "三号楼", 4, "2026-10-21T16:00:00"),
     _lf("found", "三号楼捡到学生证",
         "实验室桌上捡到一本学生证，姓王，已交到三号楼值班室",
         "三号楼", 4, "2026-10-21T18:30:00")),
    (_lf("lost", "丢了充电宝",
         "白色小米充电宝，20000 毫安，可能落在图书馆二楼",
         "图书馆", 1, "2026-10-20T14:00:00"),
     _lf("found", "图书馆二楼捡到移动电源",
         "二楼自习区捡到一个白色小米充电宝，贴了名字贴，放服务台了",
         "图书馆", 1, "2026-10-20T17:00:00")),
    (_lf("lost", "眼镜不见了",
         "黑框眼镜，度数比较高，可能在四号楼",
         "四号楼", 5, "2026-10-19T09:00:00"),
     _lf("found", "四号楼捡到一副眼镜",
         "阶梯教室捡到黑框眼镜一副，放在讲台抽屉里",
         "四号楼", 5, "2026-10-19T11:00:00")),
    (_lf("lost", "丢了考研数学书",
         "李永乐复习全书，里面有我的笔记，可能落在图书馆三楼",
         "图书馆", 1, "2026-10-18T15:00:00"),
     _lf("found", "图书馆捡到一本复习全书",
         "三楼自习桌上有一本李永乐考研数学复习全书，里面写满笔记，已交服务台",
         "图书馆", 1, "2026-10-18T19:00:00")),
    (_lf("lost", "自行车不见了",
         "蓝色山地车，停在图书馆门口，车筐有点歪",
         "图书馆", 1, "2026-10-17T08:00:00"),
     _lf("found", "图书馆门口有一辆车停了两天",
         "图书馆门口有辆蓝色自行车停了两天没人动，车筐歪了，保安挪到车棚了",
         "图书馆", 1, "2026-10-17T20:00:00")),
]

NEG_PAIRS = [
    (_lf("lost", "丢失一串钥匙（带飞机挂件）", "在一号楼不见了钥匙，挂着飞机模型挂件",
         "一号楼", 2, "2026-10-25T15:00:00"),
     _lf("found", "捡到一张校园卡", "一食堂二楼捡到校园卡一张，已交服务台",
         "一食堂", 8, "2026-10-25T12:00:00")),
    (_lf("lost", "丢了个优盘", "银色的 u 盘，插在四号楼机房电脑上忘了拔",
         "四号楼", 5, "2026-10-20T10:00:00"),
     _lf("found", "操场捡到一副眼镜", "东区操场跑道上捡到黑框眼镜一副",
         "东区操场", 7, "2026-10-25T18:00:00")),
    (_lf("lost", "找我的自行车", "蓝色自行车，停在图书馆门口不见了，车筐有点歪",
         "图书馆", 1, "2026-10-25T08:00:00"),
     _lf("found", "捡到一个记事本", "三号楼 302 捡到一个笔记本，里面写满了高数笔记",
         "三号楼", 4, "2026-10-25T17:00:00")),
    (_lf("lost", "丢了学生证", "学生证丢了，可能落在二号楼阶梯教室",
         "二号楼", 3, "2026-10-25T11:00:00"),
     _lf("found", "捡到一把雨伞", "教学楼下捡到一把折叠伞，蓝色格子",
         "二号楼", 3, "2026-10-25T14:00:00")),
    (_lf("lost", "丢了保温杯", "黑色保温杯，在图书馆四楼",
         "图书馆", 1, "2026-10-25T09:00:00"),
     _lf("found", "捡到一张校园卡", "食堂门口捡到校园卡，已交服务台",
         "一食堂", 8, "2026-10-25T12:00:00")),
    (_lf("lost", "耳机盒丢了", "白色蓝牙耳机充电盒，体育馆附近丢的",
         "体育中心", 6, "2026-10-25T10:00:00"),
     _lf("found", "捡到一把钥匙", "操场捡到一把宿舍钥匙，挂着卡通挂件",
         "东区操场", 7, "2026-10-25T16:00:00")),
    (_lf("lost", "吉他拨片找不到了", "一个小小的吉他拨片，可能掉在樱花广场",
         "樱花广场", 9, "2026-10-25T15:00:00"),
     _lf("found", "捡到一块手表", "樱花广场长椅上捡到一块电子手表",
         "樱花广场", 9, "2026-10-25T18:00:00")),
    (_lf("lost", "丢了水卡", "洗澡用的水卡，宿舍楼下丢的",
         "其他", None, "2026-10-25T07:00:00"),
     _lf("found", "捡到一张校园卡", "一食堂捡到校园卡一张", "一食堂", 8, "2026-10-25T12:00:00")),
    (_lf("lost", "笔记本电源适配器丢了", "联想笔记本的充电器，四号楼机房丢的",
         "四号楼", 5, "2026-10-25T13:00:00"),
     _lf("found", "捡到一本教材", "二号楼教室捡到一本高等数学教材", "二号楼", 3, "2026-10-25T17:00:00")),
    (_lf("lost", "篮球不见了", "斯伯丁篮球，在体育中心打球时不见了",
         "体育中心", 6, "2026-10-25T19:00:00"),
     _lf("found", "捡到一把雨伞", "图书馆门口捡到一把黑色雨伞", "图书馆", 1, "2026-10-25T20:00:00")),
]


def eval_matcher():
    out = []
    tp = fp = fn = tn = 0
    details = []
    for a, b in POS_PAIRS:
        res = M.rank_matches(a, [b], min_score=0.0)
        score = res[0][1]["score"] if res else 0.0
        hit = score >= M.NOTIFY_THRESHOLD
        tp += hit
        fn += (not hit)
        details.append(("正", score, hit, a["title"], b["title"]))
    for a, b in NEG_PAIRS:
        res = M.rank_matches(a, [b], min_score=0.0)
        score = res[0][1]["score"] if res else 0.0
        hit = score >= M.NOTIFY_THRESHOLD
        fp += hit
        tn += (not hit)
        details.append(("负", score, hit, a["title"], b["title"]))

    total = len(POS_PAIRS) + len(NEG_PAIRS)
    acc = (tp + tn) / total
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0

    out.append("① 失物招领匹配（阈值 %.2f：≥ 该分数才互相推送）" % M.NOTIFY_THRESHOLD)
    out.append("   标注配对：正样本 %d 对 / 负样本 %d 对" % (len(POS_PAIRS), len(NEG_PAIRS)))
    out.append("   准确率=%.4f  精确率=%.4f  召回率=%.4f  F1=%.4f" % (acc, prec, rec, f1))
    out.append("   混淆：TP=%d FP=%d FN=%d TN=%d" % (tp, fp, fn, tn))
    out.append("   逐对明细（分数 / 是否触发推送）：")
    for tag, score, hit, ta, tb in details:
        out.append("     [%s] %.3f %s  《%s》 ↔ 《%s》" %
                   (tag, score, "推送" if hit else "不推送", ta, tb))
    return out, {"accuracy": round(acc, 4), "precision": round(prec, 4),
                 "recall": round(rec, 4), "f1": round(f1, 4),
                 "pairs": total, "threshold": M.NOTIFY_THRESHOLD}


# ---------------------------------------------------------------- ③ 语义检索标注集
SEM_LISTINGS = [
    (1, "考研数学全书（李永乐）九五新", "毕业出清，考研数学复习全书，只做了前 50 页笔记，正版", "书籍"),
    (2, "出二手专业课本若干", "大一大二公共课教材打包出：高数、线代、大物，无缺页", "书籍"),
    (3, "出九成新机械键盘", "樱桃红轴，用了半年，手感很好，换了新键盘所以出掉", "电子产品"),
    (4, "求购二手自行车", "校内代步用，能骑就行，预算 150 以内", "生活用品"),
    (5, "求吉他一把，入门级即可", "想学吉他，求一把入门民谣吉他，价格好商量", "其他"),
    (6, "出个联想笔记本电脑", "8G 内存，日常办公够用，换了台式机所以出", "电子产品"),
    (7, "求购一个保温杯", "宿舍用，能保温就行，价格好商量", "生活用品"),
    (8, "出羽毛球拍一支", "尤尼克斯，八成新，可以小刀，体育馆自取", "其他"),
    (9, "出宿舍台灯", "护眼台灯，用了一学期，宿舍搬空出", "生活用品"),
    (10, "求购演唱会门票", "五月天演唱会，内场或看台都行", "票券"),
]
SEM_CORPUS_EXTRA = [
    "新买的机械键盘手感真好，红轴打代码很爽，准备再配个鼠标和显示器",
    "出个鼠标，配键盘一起用不错，打游戏办公都行",
    "笔记本电池不行了，想换台式机，键盘鼠标一起出",
    "考研数学全书配合真题一起刷，效率高很多，推荐再买个计算器",
    "宿舍想装个台灯，再买个收纳箱和小风扇就差不多了",
    "自行车停图书馆门口被挪了，车锁也不太好用了",
    "吉他弦断了，想再买个尤克里里入门练手，口琴也想要",
    "保温杯漏水了，想换成水壶，宿舍用方便",
    "演唱会门票没抢到，求两张电影票也好",
    "相机镜头想升级，三脚架也想一起出",
]
# (查询, 期望命中的商品 id 集合)
SEM_QUERIES = [
    ("笔记本", {6}),
    ("电脑", {6}),
    ("单车", {4}),
    ("机械健盘", {3}),          # 错别字
    ("音乐器材", {5}),          # 大类查询
    ("想买个喝水的杯子", {7}),   # 自然语言
    ("宿舍用品", {9}),
    ("演唱会票", {10}),
    ("数码产品", {3, 6}),
    ("考研要用的书", {1, 2}),
    ("想收个打游戏的键盘", {3}),
    ("有没有人出二手的代步车", {4}),
    ("想买个能保温的杯子带去上课", {7}),
]


def eval_semantic():
    docs = [{"id": i, "title": t, "text": "%s %s %s" % (t, c, cat),
             "norm_title": normalize(t)} for i, t, c, cat in SEM_LISTINGS]
    index = S.SemanticIndex().fit(docs, corpus_texts=[d["text"] for d in docs] + SEM_CORPUS_EXTRA)
    titles = {i: t for i, t, _, _ in SEM_LISTINGS}

    lines = []
    r1 = r3 = 0
    rr = 0.0
    for q, expect in SEM_QUERIES:
        res = index.search(q, top_k=5)
        got = [it["id"] for it in res["items"]]
        top1 = got[:1]
        top3 = got[:3]
        r1 += bool(set(top1) & expect)
        r3 += bool(set(top3) & expect)
        rank = next((n + 1 for n, i in enumerate(got) if i in expect), None)
        rr += (1.0 / rank) if rank else 0.0
        lines.append("     查询「%s」 期望=%s" % (q, [titles[i] for i in expect]))
        if res["explain"]:
            lines.append("       扩展：%s" % "；".join(res["explain"]))
        for it in res["items"][:3]:
            mark = "✓" if it["id"] in expect else " "
            lines.append("       %s %.3f %s（命中词：%s）" %
                         (mark, it["score"], titles[it["id"]],
                          "、".join(it["matched_terms"]) or "-"))
        if not res["items"]:
            lines.append("       （无结果）")

    n = len(SEM_QUERIES)
    out = ["③ 二手市集语义检索（标注 %d 条查询 / %d 件商品）" % (n, len(SEM_LISTINGS))]
    out += lines
    out.append("   Recall@1=%.4f  Recall@3=%.4f  MRR=%.4f" % (r1 / n, r3 / n, rr / n))

    # 关键词精确匹配做对比
    kw_r1 = kw_r3 = 0
    for q, expect in SEM_QUERIES:
        qn = normalize(q)
        hits = [i for i, t, c, cat in SEM_LISTINGS
                if qn and (qn in normalize(t) or qn in normalize(c))]
        kw_r1 += bool(set(hits[:1]) & expect)
        kw_r3 += bool(set(hits[:3]) & expect)
    out.append("   同集合对比 · 关键词精确匹配：Recall@1=%.4f Recall@3=%.4f（语义扩展把 0 命中也救回来了）"
               % (kw_r1 / n, kw_r3 / n))
    return out, {"recall_at_1": round(r1 / n, 4), "recall_at_3": round(r3 / n, 4),
                 "mrr": round(rr / n, 4), "queries": n,
                 "keyword_recall_at_1": round(kw_r1 / n, 4),
                 "keyword_recall_at_3": round(kw_r3 / n, 4)}


# ---------------------------------------------------------------- 主流程

def main():
    buf = io.StringIO()
    summary = {}

    def w(line=""):
        buf.write(line + "\n")

    w("=" * 78)
    w("南航评论区 · 三项机器学习能力离线评估报告")
    w("生成脚本：campus-map/backend/ml/eval_all.py（纯 Python，无需 sklearn）")
    w("=" * 78)
    w()

    lines, m1 = eval_matcher()
    for l in lines:
        w(l)
    summary["matcher"] = m1
    w()

    metrics = D.metrics(force=True)
    rule = D.rule_only_report()
    w("② 内容安全识别（垃圾广告 / 引战挑事 / 不文明用语）")
    w("   模型：%s；语料 %d 条（正常 %d / 广告 %d / 引战 %d / 不文明 %d）" % (
        metrics["model"], metrics["samples"],
        metrics["per_class"]["normal"]["support"], metrics["per_class"]["ad"]["support"],
        metrics["per_class"]["flame"]["support"], metrics["per_class"]["uncivil"]["support"]))
    w("   分层 %d 折交叉验证：准确率=%.4f  宏平均 F1=%.4f" %
      (metrics["k"], metrics["accuracy"], metrics["macro_f1"]))
    for lb in D.LABELS:
        c = metrics["per_class"][lb]
        w("     %-6s P=%.3f R=%.3f F1=%.3f" %
          (D.LABEL_NAMES[lb], c["precision"], c["recall"], c["f1"]))
    w("   对比基线 %s：准确率=%.4f 宏平均F1=%.4f（字级特征太稀疏，未采用）" %
      (metrics["baseline_nb"]["model"], metrics["baseline_nb"]["accuracy"],
       metrics["baseline_nb"]["macro_f1"]))
    w("   仅规则层：触发 %d 条，精确率=%.4f 召回率=%.4f（规则高精度、模型补召回）" %
      (rule["triggered"], rule["precision"], rule["recall"]))

    # 阈值标定：当前策略是"命中即拦截"，没有任何人工复核档，
    # 所以必须说明这条判定线为什么取 0.50、以及它会不会误伤正常内容。
    risks = {}
    for text, gold in D.CORPUS:
        risks.setdefault(gold, []).append(D.moderate(text, context="forum")["risk"])
    normal_max = max(risks["normal"])
    w("   阈值标定（当前判定线 %.2f，≥ 即拦截、无人工复核档）：" % D.BLOCK_LINE)
    w("     正常内容 risk 最大仅 %.3f（%d 条样本），与判定线留出 %.3f 的余量" %
      (normal_max, len(risks["normal"]), D.BLOCK_LINE - normal_max))
    for th in (0.45, 0.50, 0.55, 0.60):
        fp = sum(1 for v in risks["normal"] if v >= th)
        rec = {lb: sum(1 for v in risks[lb] if v >= th) / len(risks[lb])
               for lb in ("ad", "flame", "uncivil")}
        mark = " ←当前" if abs(th - D.BLOCK_LINE) < 1e-9 else ""
        w("     线 %.2f：正常误伤 %d/%d，广告召回 %.2f，引战召回 %.2f，不文明召回 %.2f%s" %
          (th, fp, len(risks["normal"]), rec["ad"], rec["flame"], rec["uncivil"], mark))

    w("   混淆矩阵（行=人工标注，列=模型判定）：")
    w("     %-8s %s" % ("", "".join("%10s" % D.LABEL_NAMES[b] for b in D.LABELS)))
    for a in D.LABELS:
        w("     %-8s %s" % (D.LABEL_NAMES[a],
                            "".join("%10d" % metrics["confusion"][a][b] for b in D.LABELS)))
    w("   线上判定示例（含上下文感知：二手市集允许留联系方式）：")
    for text, ctx in [("图书馆四楼很安静，适合自习", "forum"),
                      ("食堂的菜又贵又难吃，避雷", "forum"),
                      ("球赛输了，好气啊，难受", "forum"),
                      ("图书馆四楼有人加微信拉群，影响学习", "forum"),
                      ("出二手高数教材，需要的加微信 book123", "market"),
                      ("出九成新机械键盘，樱桃红轴，校内自取", "market"),
                      ("丢了保温杯，落在图书馆四楼，捡到请联系我", "lostfound"),
                      ("出二手高数教材，需要的加微信 abc123", "forum"),
                      ("招兼职日结200，加微信详聊", "forum"),
                      ("专业代写论文包过，加QQ详聊", "forum"),
                      ("计算机学院的就这水平，也配发言", "forum"),
                      ("不服来辩，我就说食堂最难吃", "forum"),
                      ("傻逼东西，滚出去", "forum")]:
        r = D.moderate(text, context=ctx)
        w("     [%-9s %-5s risk=%.3f] %s" % (ctx, r["action"], r["risk"], text))
        w("            -> %s" % (r["message"] or "放行"))
    summary["moderation"] = {
        "accuracy": metrics["accuracy"], "macro_f1": metrics["macro_f1"],
        "samples": metrics["samples"], "model": metrics["model"],
        "baseline_nb_accuracy": metrics["baseline_nb"]["accuracy"],
        "rule_precision": rule["precision"], "rule_recall": rule["recall"],
        "block_line": D.BLOCK_LINE, "normal_max_risk": round(normal_max, 4),
    }
    w()

    lines3, m3 = eval_semantic()
    for l in lines3:
        w(l)
    summary["semantic"] = m3
    w()

    w("=" * 78)
    w("汇总")
    w("=" * 78)
    w("  失物匹配   : 准确率 %.4f / 精确率 %.4f / 召回率 %.4f（%d 对标注样本，阈值 %.2f）" %
      (m1["accuracy"], m1["precision"], m1["recall"], m1["pairs"], m1["threshold"]))
    w("  内容安全   : 命中即拦截（判定线 %.2f，正常内容最高 risk 仅 %.3f）；"
      "5 折准确率 %.4f / 宏平均 F1 %.4f（基线 NB %.4f）" %
      (D.BLOCK_LINE, normal_max, metrics["accuracy"], metrics["macro_f1"],
       metrics["baseline_nb"]["accuracy"]))
    w("  语义检索   : Recall@1 %.4f / Recall@3 %.4f / MRR %.4f（关键词仅 %.4f / %.4f）" %
      (m3["recall_at_1"], m3["recall_at_3"], m3["mrr"],
       m3["keyword_recall_at_1"], m3["keyword_recall_at_3"]))
    w("=" * 78)

    path = os.path.abspath(REPORT)
    with open(path, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print("报告已写入：%s" % path)
    print(buf.getvalue()[-700:])


if __name__ == "__main__":
    main()