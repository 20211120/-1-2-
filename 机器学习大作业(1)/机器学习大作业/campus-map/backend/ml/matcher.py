# -*- coding: utf-8 -*-
"""
失物招领「寻物 ↔ 招领」智能匹配引擎
================================================================
思路（混合特征打分，全部可解释，无第三方依赖）：

    最终相似度 = 0.40·文本相似度   （字级 2-3gram TF-IDF 余弦，抗错别字/改写）
               + 0.26·物品类别一致度（领域词典抽取物品类型，「钥匙」对「校园卡」直接判否）
               + 0.14·地点一致度    （优先比对地图 POI，其次地点串的字符二元组 Jaccard）
               + 0.08·时间接近度    （1/(1+Δt/36h) 的衰减函数）
               + 0.12·特征词重合度  （颜色/挂件/图案等区分性特征，如"飞机挂件""蓝色"）
               + 证据加成：类别互相印证 +0.06、类别与地点同时吻合 +0.06、
                            区分性特征吻合 +0.04（多条线索交叉验证，避免只靠文本）

    两条信息必须一个是 lost、一个是 found，否则相似度直接为 0；
    双方都识别出物品类型且类型不同时，总分在加成之后再乘以 0.40 的否决系数。

阈值：score ≥ 0.60 → 高置信匹配，立即互相推送通知；
      score ≥ 0.38 → 存入 lf_matches 作为"可能相关"，不打扰用户。
"""

try:                                    # 作为包导入（app.py / exe 打包）
    from .textutil import (SparseTfidf, _cosine, bigrams, contains_any,
                           jaccard, normalize, parse_time)
except ImportError:                     # 直接 python matcher.py 跑自检
    from textutil import (SparseTfidf, _cosine, bigrams, contains_any,
                          jaccard, normalize, parse_time)

# ---------------------------------------------------------------- 领域词典

# 物品类别 -> 别名（命中任意别名即认为属于该类）
ITEM_TYPES = {
    "钥匙": ["钥匙串", "宿舍钥匙", "车钥匙", "门钥匙", "钥匙扣", "钥匙"],
    "校园卡": ["校园卡", "一卡通", "学生卡", "消费卡", "饭卡", "校卡"],
    "证件": ["身份证", "学生证", "团员证", "驾驶证", "护照", "考试证", "证件"],
    "手机": ["苹果手机", "智能手机", "手机", "iphone", "华为", "小米", "oppo", "vivo"],
    "钱包": ["钱包", "卡包", "皮夹", "钱夹"],
    "耳机": ["蓝牙耳机", "无线耳机", "airpods", "耳机", "耳麦", "耳塞"],
    "电脑": ["笔记本电脑", "电脑包", "平板电脑", "macbook", "surface", "电脑",
             "笔记本", "ipad", "平板"],
    "充电设备": ["充电宝", "移动电源", "充电器", "数据线", "充电线", "充电头"],
    "雨伞": ["遮阳伞", "折叠伞", "雨伞", "雨具", "伞"],
    "水杯": ["保温杯", "咖啡杯", "水杯", "水壶", "水瓶", "杯子"],
    "眼镜": ["隐形眼镜", "眼镜盒", "眼镜", "镜框", "墨镜"],
    "包": ["双肩包", "手提包", "帆布包", "行李袋", "书包", "背包", "袋子"],
    "手表": ["运动手环", "电子表", "手表", "手环", "腕表"],
    "代步工具": ["电动车", "电瓶车", "滑板车", "平衡车", "山地车", "自行车", "单车"],
    "衣物": ["羽绒服", "外套", "卫衣", "围巾", "手套", "帽子", "校服", "衣服"],
    "书籍资料": ["复习资料", "教材", "课本", "笔记", "讲义", "试卷", "真题", "资料", "书"],
    "存储设备": ["内存卡", "存储卡", "读卡器", "硬盘", "优盘", "u盘"],
    "乐器": ["尤克里里", "小提琴", "电子琴", "口琴", "吉他", "笛子"],
    "运动器材": ["羽毛球拍", "瑜伽垫", "哑铃", "球拍", "篮球", "足球", "滑板", "护膝", "护腕"],
    "化妆品": ["化妆包", "眼影", "口红", "粉饼", "气垫", "香水", "面膜"],
    "药品": ["感冒药", "创可贴", "眼药水", "药盒", "药品"],
    "卡类": ["洗澡卡", "热水卡", "充值卡", "水卡", "电卡"],
    "电脑配件": ["扩展坞", "鼠标垫", "显示器", "路由器", "键盘", "鼠标", "网线"],
    "文具": ["笔袋", "钢笔", "计算器", "尺子"],
    "快递": ["快递", "包裹", "菜鸟"],
    "照片": ["证件照", "照片", "相片"],
}

# 区分性特征词：颜色、图案、挂饰、材质、标记
FEATURE_WORDS = [
    "挂件", "挂饰", "钥匙扣", "贴纸", "贴画", "卡通", "龙猫", "小熊", "飞机", "星星",
    "月亮", "爱心", "蝴蝶结", "挂绳", "链子", "拉链", "图案", "logo", "限量", "定制",
    "刻字", "名字贴", "标签", "贴膜", "保护壳", "礼盒", "包装盒",
    "蓝色", "黑色", "白色", "红色", "粉色", "绿色", "黄色", "紫色", "灰色", "银色",
    "透明", "磨砂", "金属", "皮质", "编织", "毛绒", "木质", "玻璃",
]

# 归一化后的别名索引，避免每次重复计算
_ALIAS_INDEX = []
for _t, _aliases in ITEM_TYPES.items():
    for _a in _aliases:
        _ALIAS_INDEX.append((normalize(_a), _t))
_ALIAS_INDEX.sort(key=lambda x: -len(x[0]))


def extract_item_types(text):
    """返回按匹配别名长度降序的物品类别列表（可能多个，如"钥匙扣挂件"）"""
    s = normalize(text)
    if not s:
        return []
    hits = {}
    for alias, tname in _ALIAS_INDEX:
        if alias and alias in s:
            hits[tname] = max(hits.get(tname, 0), len(alias))
    return [t for t, _ in sorted(hits.items(), key=lambda x: -x[1])]


def primary_item_type(text):
    ts = extract_item_types(text)
    return ts[0] if ts else ""


def extract_features(text):
    return set(contains_any(normalize(text), FEATURE_WORDS))


# ---------------------------------------------------------------- 单项打分

def text_similarity(vec_a, vec_b):
    """两条信息的字级 TF-IDF 余弦相似度（复用同一个索引，避免重复训练）"""
    return _cosine(vec_a["vec"], vec_a["norm"], vec_b["vec"], vec_b["norm"])


def location_similarity(a, b):
    la = normalize(a.get("location") or "")
    lb = normalize(b.get("location") or "")
    pa, pb = a.get("poi_id"), b.get("poi_id")
    if pa and pb and pa == pb:
        return 1.0, "地点相同（同一校园地点）"
    if not la or not lb:
        return 0.4, "地点信息不全"
    if la in lb or lb in la:
        return 0.95, "地点高度重合"
    j = jaccard(bigrams(la), bigrams(lb))
    if j >= 0.5:
        return 0.8, "地点相近"
    if j >= 0.25:
        return 0.55, "地点部分重合"
    return max(0.05, j), "地点不同"


def time_similarity(a, b):
    ta, tb = parse_time(a.get("created_at")), parse_time(b.get("created_at"))
    if not ta or not tb:
        return 0.5, None
    hours = abs((ta - tb).total_seconds()) / 3600.0
    sim = 1.0 / (1.0 + hours / 36.0)
    return sim, hours


def item_similarity(ta, tb):
    if ta and tb:
        return (1.0 if ta == tb else 0.0), True
    if ta or tb:
        return 0.45, False
    return 0.5, False


def feature_similarity(fa, fb):
    if not fa and not fb:
        return 0.5
    if not fa or not fb:
        return 0.4
    return 0.3 + 0.7 * jaccard(fa, fb)


def _fmt_hours(h):
    if h is None:
        return "时间未知"
    if h < 1:
        return "相差 %d 分钟" % max(1, int(h * 60))
    if h < 24:
        return "相差 %.1f 小时" % h
    return "相差 %.1f 天" % (h / 24.0)


def match_pair(a, b, vec_a, vec_b):
    """
    a / b: dict，需含 kind,title,content,location,poi_id,created_at,status
    vec_a / vec_b: 由 build_vectors() 生成的文本向量
    返回打分明细 dict
    """
    if a.get("kind") == b.get("kind"):
        return None

    text_a = (a.get("title") or "") + " " + (a.get("content") or "")
    text_b = (b.get("title") or "") + " " + (b.get("content") or "")

    t_sim = text_similarity(vec_a, vec_b)
    ta, tb = primary_item_type(text_a), primary_item_type(text_b)
    i_sim, both_known = item_similarity(ta, tb)
    l_sim, l_reason = location_similarity(a, b)
    tm_sim, hours = time_similarity(a, b)
    fa, fb = extract_features(text_a), extract_features(text_b)
    f_sim = feature_similarity(fa, fb)

    score = (0.40 * t_sim + 0.26 * i_sim + 0.14 * l_sim
             + 0.08 * tm_sim + 0.12 * f_sim)

    reasons = [l_reason]
    if hours is not None:
        reasons.append(_fmt_hours(hours) + "发布")
    shared = sorted(fa & fb)
    if shared:
        reasons.append("共同特征：" + "、".join(shared))

    # ---- 多线索交叉验证的加成 ----
    if ta and tb and ta == tb:
        score += 0.06
        reasons.append("同属「%s」类物品，类别相互印证" % ta)
    if ta and tb and ta == tb and l_sim >= 0.9:
        score += 0.06
        reasons.append("物品类别与地点同时吻合")
    if shared:
        score += 0.04
        reasons.append("区分性特征吻合")
    if ta and tb and ta != tb:
        reasons.append("物品类别不同（%s / %s）" % (ta, tb))
    elif (ta or tb) and not (ta and tb):
        reasons.append("其中一条识别到物品类型：%s" % (ta or tb))

    # 类别冲突否决：加成之后再压低总分
    if ta and tb and ta != tb:
        score *= 0.40

    # 标题高度重合的小加成
    na, nb = normalize(a.get("title") or ""), normalize(b.get("title") or "")
    if na and na == nb:
        score += 0.08
        reasons.append("标题完全一致")

    # 「捡到」早于「丢失」发布时间：可能性略降
    lost, found = (a, b) if a.get("kind") == "lost" else (b, a)
    tl, tf = parse_time(lost.get("created_at")), parse_time(found.get("created_at"))
    if tl and tf and (tl - tf).total_seconds() > 6 * 3600:
        score *= 0.92
        reasons.append("发布先后顺序相反")

    # 已解决的信息降权
    if a.get("status") == "resolved" and b.get("status") == "resolved":
        score *= 0.85
    elif a.get("status") == "resolved" or b.get("status") == "resolved":
        score *= 0.95

    score = max(0.0, min(1.0, score))
    if score >= 0.60:
        level = "高"
    elif score >= 0.45:
        level = "中"
    else:
        level = "低"

    return {
        "score": round(score, 4),
        "level": level,
        "reasons": reasons,
        "detail": {
            "text": round(t_sim, 4), "item": round(i_sim, 4),
            "location": round(l_sim, 4), "time": round(tm_sim, 4),
            "feature": round(f_sim, 4),
        },
        "item_type": ta or tb,
        "shared_features": shared,
        "hours": None if hours is None else round(hours, 2),
        "both_type_known": both_known,
    }


# ---------------------------------------------------------------- 批量匹配

NOTIFY_THRESHOLD = 0.60      # 高置信：互相推送
STORE_THRESHOLD = 0.38       # 可能相关：写入 lf_matches 备查


def build_vectors(rows):
    """对一批失物招领记录建字级 TF-IDF 向量（一次性 fit，避免 O(n²) 重复训练）"""
    idx = SparseTfidf(analyze="char", ngram_range=(2, 3))
    texts = [((r.get("title") or "") + " " + (r.get("content") or "") + " "
              + (r.get("location") or "")) for r in rows]
    idx.fit(texts)
    return idx.docs


def rank_matches(target, candidates, top_k=5, min_score=STORE_THRESHOLD):
    """
    target: 待匹配的记录（dict）
    candidates: 反向类型的候选记录列表
    返回按分数降序的 [(candidate, detail), ...]
    """
    if not candidates:
        return []
    rows = [target] + list(candidates)
    vecs = build_vectors(rows)
    tvec = vecs[0]
    out = []
    for i, cand in enumerate(candidates, start=1):
        detail = match_pair(target, cand, tvec, vecs[i])
        if detail and detail["score"] >= min_score:
            out.append((cand, detail))
    out.sort(key=lambda x: -x[1]["score"])
    return out[:top_k]


if __name__ == "__main__":
    lost = {"kind": "lost", "title": "丢失一串钥匙（带飞机挂件）",
            "content": "昨天下午在一号楼1-205上课后不见了一串钥匙，挂着飞机模型挂件",
            "location": "一号楼", "poi_id": 2, "created_at": "2026-10-25T15:00:00",
            "status": "open"}
    found = {"kind": "found", "title": "一号楼捡到钥匙一串",
             "content": "在一号楼1-205教室捡到一串钥匙，上面有个飞机挂件，失主联系我",
             "location": "一号楼", "poi_id": 2, "created_at": "2026-10-25T20:30:00",
             "status": "open"}
    other = {"kind": "found", "title": "捡到一张校园卡",
             "content": "一食堂二楼捡到校园卡一张，已交服务台",
             "location": "一食堂", "poi_id": 8, "created_at": "2026-10-25T12:00:00",
             "status": "open"}
    for cand, d in rank_matches(lost, [found, other]):
        print(cand["title"], "->", d["score"], d["level"], d["reasons"])