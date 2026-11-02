# -*- coding: utf-8 -*-
"""
二手市集「语义检索」
================================================================
纯本地、零下载、可解释的混合语义检索，三条通道加权融合：

  ① 词级通道（权重 0.55）
     用领域词典做逆向最大匹配切词，再算词级 TF-IDF 余弦。
     查询会先做两步"查询扩展"：
       · 同义词表：笔记本 → 电脑 / 台式机 / 一体机 / 主机 …
       · 共现学习：从平台全站文本（商品、帖子、评论、失物招领）里统计
         词语共现的 PMI（点互信息），自动把"数据驱动"的相关词也加进来，
         例如 键盘 → 鼠标 / 显示器 / 键帽（这是从语料里学到的，不是写死的）。
     所以搜"笔记本"能搜出"电脑"，搜"单车"能搜出"自行车"。

  ② 字级通道（权重 0.35）
     字级 2-3gram TF-IDF 余弦，负责兜住错别字、缩写和词表没覆盖的写法
     （"机械健盘""airpod" 这类）。

  ③ 字面命中加成（最多 +0.15）
     查询串完整出现在标题里再加分，避免语义扩展把明显精确的结果挤下去。

最终得分 = 0.55·词级 + 0.35·字级 + 字面加成，返回结果带 score 与
matched_terms（告诉用户"为什么这条被搜出来"）。
"""

import math
from itertools import combinations

try:                                    # 作为包导入（app.py / exe 打包）
    from .textutil import (MaxMatchSegmenter, SparseTfidf, char_ngrams,
                           normalize)
except ImportError:                     # 直接 python semantic.py 跑自检
    from textutil import MaxMatchSegmenter, SparseTfidf, char_ngrams, normalize

# ---------------------------------------------------------------- 领域词表
# 覆盖二手市集的高频商品名。切词只认这张表里的词，表外的连续汉字按单字处理。
LEXICON_WORDS = [
    # 数码 / 电子
    "笔记本电脑", "游戏本", "轻薄本", "台式机", "一体机", "主机", "电脑", "笔记本",
    "平板电脑", "平板", "ipad", "macbook", "surface", "显示器", "屏幕", "机械键盘",
    "键盘", "键帽", "鼠标", "鼠标垫", "扩展坞", "路由器", "网线", "网卡", "u盘",
    "优盘", "移动硬盘", "硬盘", "内存条", "显卡", "cpu", "散热器", "充电宝",
    "移动电源", "充电器", "数据线", "充电线", "耳机", "蓝牙耳机", "降噪耳机",
    "airpods", "耳麦", "音箱", "蓝牙音箱", "麦克风", "摄像头", "手柄", "游戏手柄",
    "手机", "智能手机", "iphone", "安卓", "华为", "小米", "红米", "oppo", "vivo",
    "手机壳", "钢化膜", "相机", "单反", "微单", "镜头", "三脚架", "稳定器",
    "打印机", "扫描仪", "电子书", "kindle", "录音笔", "投影仪", "台灯", "护眼台灯",
    "电风扇", "小风扇", "加湿器", "小冰箱", "吹风机", "电吹风", "洗衣机", "剃须刀",
    "电动牙刷", "热水壶", "电煮锅", "电饭煲", "微波炉", "体重秤", "智能手环",
    "智能手表", "手表", "运动手环", "充电座", "插线板", "插座", "台灯灯管",
    # 书籍 / 学习
    "教材", "课本", "教科书", "专业书", "二手书", "书籍", "复习全书", "考研数学",
    "考研英语", "考研政治", "专业课", "真题", "模拟题", "四六级", "六级", "四级",
    "单词书", "笔记", "讲义", "资料", "试卷", "答案", "计算器", "科学计算器",
    "文具", "笔袋", "钢笔", "中性笔", "笔记本子", "活页本", "打印纸", "文件夹",
    "书架", "书立", "小提琴", "吉他", "民谣吉他", "电吉他", "尤克里里", "电子琴",
    "口琴", "笛子", "乐谱", "画材", "颜料", "马克笔", "绘图板",
    # 出行 / 运动
    "自行车", "单车", "脚踏车", "山地车", "公路车", "折叠车", "代步车", "电动车",
    "电瓶车", "滑板车", "平衡车", "滑板", "长板", "头盔", "车锁", "车灯", "打气筒",
    "篮球", "足球", "排球", "羽毛球拍", "网球拍", "乒乓球拍", "球拍", "球鞋",
    "运动鞋", "跑鞋", "瑜伽垫", "哑铃", "拉力器", "跳绳", "护腕", "护膝", "泳镜",
    "泳帽", "健身手套", "登山包", "帐篷", "睡袋", "钓具",
    # 生活
    "保温杯", "水杯", "杯子", "水壶", "水瓶", "咖啡杯", "饭盒", "餐具", "筷子",
    "收纳箱", "收纳盒", "置物架", "衣架", "晾衣架", "床帘", "蚊帐", "凉席", "床垫",
    "被子", "枕头", "抱枕", "毛毯", "电热毯", "锁", "门锁", "挂锁", "雨伞",
    "折叠伞", "雨衣", "口罩", "洗手液", "纸巾", "洗衣液", "沐浴露", "洗发水",
    "化妆品", "口红", "面膜", "香水", "化妆包", "镜子", "吹风机风嘴", "指甲刀",
    "药盒", "创可贴", "眼药水", "温度计", "行李箱", "拉杆箱", "背包", "双肩包",
    "书包", "手提包", "帆布包", "钱包", "卡包", "钥匙扣", "眼镜", "墨镜", "隐形眼镜",
    "帽子", "围巾", "手套", "外套", "卫衣", "羽绒服", "毛衣", "T恤", "牛仔裤",
    "裙子", "拖鞋", "皮鞋", "帆布鞋", "正装", "西装", "领带", "演出服",
    # 票券 / 其他
    "门票", "票券", "演唱会门票", "电影票", "话剧票", "车票", "机票", "会员卡",
    "健身卡", "游泳卡", "餐券", "代金券", "优惠券", "校园卡", "水卡", "电卡",
    "考研资料", "考证资料", "公考资料", "雅思", "托福", "实习", "教材全套",
]

# ---------------------------------------------------------------- 同义词表
# 同一组内的词视为"语义等价"，查询时互相扩展。
SYNONYM_GROUPS = [
    {"电脑", "笔记本", "笔记本电脑", "台式机", "主机", "一体机", "macbook",
     "surface", "游戏本", "轻薄本"},
    {"平板", "平板电脑", "ipad", "电子书", "kindle"},
    {"手机", "智能手机", "iphone", "安卓", "华为", "小米", "红米", "oppo", "vivo"},
    {"键盘", "机械键盘", "键帽", "无线键盘", "外接键盘"},
    {"鼠标", "无线鼠标", "有线鼠标", "鼠标垫"},
    {"显示器", "屏幕", "外接屏", "曲面屏"},
    {"耳机", "蓝牙耳机", "无线耳机", "降噪耳机", "airpods", "耳麦", "耳塞"},
    {"充电宝", "移动电源", "充电器", "充电头", "数据线", "充电线"},
    {"u盘", "优盘", "移动硬盘", "硬盘", "内存卡", "存储卡"},
    {"自行车", "单车", "脚踏车", "山地车", "公路车", "折叠车", "代步车"},
    {"电动车", "电瓶车", "电动自行车", "滑板车", "平衡车"},
    {"教材", "课本", "教科书", "专业书", "二手书", "书籍", "书"},
    {"考研数学", "复习全书", "数学全书", "真题", "模拟题", "考研资料"},
    {"四六级", "六级", "四级", "英语资料", "单词书"},
    {"吉他", "民谣吉他", "木吉他", "古典吉他", "电吉他", "尤克里里", "小吉他",
     "乌克丽丽"},
    {"门票", "票券", "演唱会门票", "电影票", "演出票", "话剧票"},
    {"保温杯", "水杯", "杯子", "水壶", "水瓶", "咖啡杯"},
    {"台灯", "护眼台灯", "学习灯", "小台灯"},
    {"电风扇", "小风扇", "风扇", "循环扇"},
    {"羽毛球拍", "网球拍", "乒乓球拍", "球拍"},
    {"运动鞋", "跑鞋", "球鞋", "帆布鞋"},
    {"自行车锁", "车锁", "门锁", "挂锁", "锁"},
    {"背包", "双肩包", "书包", "电脑包", "书包袋"},
    {"行李箱", "拉杆箱", "旅行箱"},
    {"化妆品", "口红", "面膜", "香水", "化妆包", "护肤品"},
    {"打印", "打印机", "扫描仪", "打印纸"},
]

# 同义组反查表
_SYN_INDEX = {}
for _gi, _group in enumerate(SYNONYM_GROUPS):
    for _w in _group:
        _SYN_INDEX.setdefault(_w, _gi)

# ---------------------------------------------------------------- 上位词（主题）表
# 用户常搜"大类"而不是具体商品（"想买个数码产品""求个乐器"）。命中主题词时，
# 把该主题下的典型商品词一起加进查询向量（权重低于同义词），让大类查询也有结果。
TOPIC_MAP = {
    "数码": ["电脑", "手机", "键盘", "鼠标", "显示器", "耳机", "充电宝", "平板",
             "相机", "u盘", "路由器", "手柄"],
    "电子": ["电脑", "手机", "耳机", "键盘", "充电宝", "显示器", "平板", "相机"],
    "电脑": ["键盘", "鼠标", "显示器", "内存条", "摄像头", "鼠标垫", "路由器"],
    "乐器": ["吉他", "尤克里里", "电子琴", "口琴", "笛子", "小提琴"],
    "音乐": ["吉他", "尤克里里", "电子琴", "口琴", "笛子", "音箱", "麦克风"],
    "书": ["教材", "课本", "真题", "复习全书", "笔记", "资料", "专业书"],
    "学习": ["教材", "课本", "真题", "笔记", "台灯", "计算器", "文具"],
    "考研": ["考研数学", "考研英语", "复习全书", "真题", "专业课", "笔记"],
    "运动": ["球拍", "羽毛球拍", "篮球", "足球", "瑜伽垫", "哑铃", "运动鞋", "头盔"],
    "健身": ["哑铃", "瑜伽垫", "跳绳", "拉力器", "运动手环", "健身手套"],
    "出行": ["自行车", "电动车", "头盔", "车锁", "滑板", "行李箱"],
    "代步": ["自行车", "电动车", "滑板车", "平衡车"],
    "生活用品": ["保温杯", "台灯", "收纳箱", "衣架", "床帘", "风扇", "雨伞", "水壶"],
    "宿舍": ["床帘", "蚊帐", "收纳箱", "台灯", "小风扇", "凉席", "衣架", "插线板"],
    "票": ["门票", "电影票", "演唱会门票", "车票", "餐券", "代金券"],
    "礼物": ["香水", "口红", "手表", "音箱", "保温杯", "手环"],
    "摄影": ["相机", "单反", "微单", "镜头", "三脚架", "稳定器"],
}


class SemanticIndex:
    """二手市集语义检索索引（内存态，数据量小，随写随建）"""

    def __init__(self):
        self.segmenter = MaxMatchSegmenter(LEXICON_WORDS)
        self.word_index = SparseTfidf(analyze="word", segmenter=self.segmenter)
        self.char_index = SparseTfidf(analyze="char", ngram_range=(2, 3))
        self.docs = []            # [{"id":..,"text":..,"norm_title":..}]
        self.doc_grams = []       # 每篇文档的字 n-gram 集合（算查询覆盖率）
        self.doc_words = []       # 每篇文档的词集合
        self.related_map = {}     # 词 -> [(相关词, pmi)]
        self.corpus_docs = 0

    # ---------------------------------------------------------- 建索引

    def fit(self, docs, corpus_texts=None):
        """
        docs: [{"id": int, "text": str, "title": str}]（text 为标题+描述+分类的拼接）
        corpus_texts: 用于学习共现关系的全站文本（缺省用 docs）
        """
        self.docs = list(docs)
        texts = [d["text"] for d in self.docs]
        self.word_index.fit(texts)
        self.char_index.fit(texts)
        self.doc_grams = [set(char_ngrams(t, (2, 3))) for t in texts]
        self.doc_words = [set(self.segmenter.cut(t)) for t in texts]
        self.learn_expansion(corpus_texts if corpus_texts else texts)
        return self

    def learn_expansion(self, texts):
        """
        从语料里学共现：PMI(x,y) = log( p(x,y) / (p(x)·p(y)) )

        语料只有几十篇时，标准做法（共现 ≥ 2 次）几乎筛不出词对，
        因此阈值随语料规模自适应：文档少时放宽共现次数、抬高 PMI 门槛。
        """
        from collections import Counter

        doc_terms = []
        for t in texts:
            toks = {w for w in self.segmenter.cut(t) if len(w) >= 2}
            toks = {w for w in toks if w in self.segmenter.words}
            if toks:
                doc_terms.append(toks)
        n = len(doc_terms)
        self.corpus_docs = n
        self.related_map = {}
        if n < 4:
            return self
        min_co = 2 if n >= 25 else 1
        min_pmi = 0.8 if n >= 25 else 1.2
        df = Counter()
        pairs = Counter()
        for toks in doc_terms:
            df.update(toks)
            for a, b in combinations(sorted(toks), 2):
                pairs[(a, b)] += 1
        for (a, b), c in pairs.items():
            if c < min_co:
                continue
            pa, pb, pab = df[a] / n, df[b] / n, c / n
            if pa <= 0 or pb <= 0:
                continue
            pmi = math.log(pab / (pa * pb))
            if pmi < min_pmi:
                continue
            self.related_map.setdefault(a, []).append((b, pmi))
            self.related_map.setdefault(b, []).append((a, pmi))
        for k in self.related_map:
            self.related_map[k].sort(key=lambda x: -x[1])
            self.related_map[k] = self.related_map[k][:8]
        return self

    # ---------------------------------------------------------- 查询扩展

    def expand_query(self, query, syn_weight=0.75, pmi_weight=0.5,
                     topic_weight=0.6):
        """
        返回 (原始词, 可替换词权重, 共现相关词权重, 扩展说明)

        · 可替换词（alt）：同义词 + 主题上位词。它们和原词"能互相替代"，
          因此既进查询向量，也参与"查询词覆盖率"计算。
        · 共现相关词（rel）：从平台语料用 PMI 学出来的相关词，多半是
          "搭着一起买"的互补商品（键盘↔鼠标），只用来丰富查询向量，
          不参与覆盖率，避免把精确命中的结果稀释掉。
        """
        raw_terms = [w for w in self.segmenter.cut(query) if len(w) >= 2]
        alt, rel = {}, {}
        syn_terms, topic_terms, pmi_terms = [], [], []

        for term in raw_terms:
            gi = _SYN_INDEX.get(term)
            if gi is not None:
                for w in SYNONYM_GROUPS[gi]:
                    if w != term and w not in raw_terms:
                        alt[w] = max(alt.get(w, 0.0), syn_weight)
                        syn_terms.append(w)
            for cand, _pmi in self.related_map.get(term, []):
                if cand in raw_terms or cand in alt:
                    continue
                if gi is not None and _SYN_INDEX.get(cand) == gi:
                    continue
                rel[cand] = max(rel.get(cand, 0.0), pmi_weight)
                pmi_terms.append(cand)

        # 主题上位词：整串查询里出现主题词就展开，并对成员再做一跳同义扩展
        # （"数码产品" → 键盘 + 键盘的同义组；→ 电脑 + 电脑的同义组 笔记本/台式机）
        for key, members in TOPIC_MAP.items():
            if key not in query:
                continue
            for w in members:
                if w not in raw_terms and w not in alt:
                    alt[w] = max(alt.get(w, 0.0), topic_weight)
                    topic_terms.append(w)
                gi2 = _SYN_INDEX.get(w)
                if gi2 is None:
                    continue
                for s in SYNONYM_GROUPS[gi2]:
                    if s in raw_terms or s in alt:
                        continue
                    alt[s] = max(alt.get(s, 0.0), topic_weight * 0.8)
                    topic_terms.append(s)

        # 整串查询本身也可能就是一个词（如"机械键盘"）
        whole = normalize(query)
        if whole and whole not in raw_terms and len(whole) >= 2:
            if whole in self.segmenter.words:
                raw_terms.append(whole)

        explain = []
        if syn_terms:
            explain.append("同义词：" + "、".join(_uniq(syn_terms)[:6]))
        if topic_terms:
            explain.append("同类商品：" + "、".join(_uniq(topic_terms)[:6]))
        if pmi_terms:
            explain.append("语料共现相关词：" + "、".join(_uniq(pmi_terms)[:6]))
        return raw_terms, alt, rel, explain

    # ---------------------------------------------------------- 检索

    def _coverage(self, qgrams, doc_grams):
        """
        查询字 n-gram 在文档中的覆盖率（IDF 加权，未登录 n-gram 降权）

        比余弦更适合"错别字"场景：查询"机械健盘"里"健盘"不在语料词表里，
        只占 0.5 权重，命中"机械"就能拿到很高的覆盖率。
        """
        if not qgrams:
            return 0.0
        total = 0.0
        hit = 0.0
        for t in qgrams:
            w = self.char_index.idf.get(t, 0.5)
            total += w
            if t in doc_grams:
                hit += w
        return hit / total if total else 0.0

    def search(self, query, top_k=None, min_score=0.08):
        q = (query or "").strip()
        if not q:
            return {"items": [], "expanded": [], "explain": [], "query_terms": []}
        raw_terms, alt, rel, explain = self.expand_query(q)
        query_weights = dict(alt)
        for t, w in rel.items():
            query_weights[t] = max(query_weights.get(t, 0.0), w)

        # ① 词级通道：TF-IDF 余弦，查询向量里塞入同义词/相关词
        word_hits = dict(self.word_index.search(
            q, query_weights=query_weights, min_score=0.0))
        # ② 字级通道：字 2-3gram 余弦
        char_hits = dict(self.char_index.search(q, min_score=0.0))
        # ③ 查询词覆盖率：可替换词里有多少被文档命中（分母只算语料词表里有的词）
        cand = {t: 1.0 for t in raw_terms}
        for t, w in alt.items():
            cand[t] = max(cand.get(t, 0.0), w)
        known = {t: w for t, w in cand.items() if t in self.word_index.idf}
        known_total = sum(known.values())
        qgrams = set(char_ngrams(q, (2, 3)))
        qn = normalize(q)

        out = []
        for i, d in enumerate(self.docs):
            w_score = word_hits.get(i, 0.0)
            c_score = char_hits.get(i, 0.0)
            words = self.doc_words[i]
            w_cov = (sum(w for t, w in known.items() if t in words) / known_total
                     if known_total else 0.0)
            c_cov = self._coverage(qgrams, self.doc_grams[i])
            score = (0.30 * w_score + 0.32 * w_cov
                     + 0.22 * c_cov + 0.06 * c_score)
            text_norm = normalize(d.get("text") or "")
            title = d.get("norm_title") or normalize(d.get("title") or "")
            if len(qn) >= 2 and qn in title:
                score += 0.12
            elif len(qn) >= 2 and qn in text_norm:
                score += 0.06
            if score <= min_score:
                continue
            pool = list(query_weights.keys()) + raw_terms
            hits = _uniq([t for t in pool if t in text_norm])
            out.append({
                "index": i,
                "id": d.get("id"),
                "score": round(min(1.0, score), 4),
                "word": round(w_score, 4),
                "word_coverage": round(w_cov, 4),
                "char": round(c_score, 4),
                "char_coverage": round(c_cov, 4),
                "matched_terms": hits[:6],
            })
        out.sort(key=lambda x: -x["score"])
        if top_k:
            out = out[:top_k]
        expanded = _uniq(list(alt.keys()) + list(rel.keys()))
        return {"items": out, "expanded": expanded, "explain": explain,
                "query_terms": raw_terms}


def _uniq(seq):
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


if __name__ == "__main__":
    # 直接运行本文件即可看检索效果（完整三模块评估见 python -m ml.eval_all）
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    LISTINGS = [
        (1, "考研数学全书（李永乐）九五新", "毕业出清，考研数学复习全书，只做了前 50 页笔记，正版，将军路校区自取", "书籍"),
        (2, "出二手专业课本若干", "大一大二公共课教材打包出：高数、线代、大物，无缺页，便宜甩", "书籍"),
        (3, "出九成新机械键盘", "樱桃红轴，用了半年，手感很好，换了新键盘所以出掉", "电子产品"),
        (4, "求购二手自行车", "校内代步用，能骑就行，预算 150 以内，有出的同学联系我", "生活用品"),
        (5, "求吉他一把，入门级即可", "想学吉他，求一把入门民谣吉他，价格好商量", "其他"),
        (6, "出个联想笔记本，8G 内存", "大三换台式机了，笔记本闲置，日常办公够用，可小刀", "电子产品"),
        (7, "求购一个保温杯", "宿舍用，能保温就行，价格好商量", "生活用品"),
        (8, "出羽毛球拍一支", "尤尼克斯，八成新，可以小刀，体育馆自取", "其他"),
        (9, "出宿舍台灯", "护眼台灯，用了一学期，宿舍搬空出", "生活用品"),
        (10, "求购演唱会门票", "五月天演唱会，内场或看台都行，价格好说", "票券"),
    ]
    docs = [{"id": i, "title": t, "text": t + " " + c + " " + cat, "norm_title": normalize(t)}
            for i, t, c, cat in LISTINGS]
    # 模拟平台其它文本（帖子/评论/失物招领），用于学习共现关系
    EXTRA = [
        "新买的机械键盘手感真好，红轴打代码很爽，准备再配个鼠标和显示器",
        "出个鼠标，配键盘一起用不错，打游戏办公都行",
        "笔记本电池不行了，想换台式机，键盘鼠标一起出",
        "考研数学全书配合真题一起刷，效率高很多，推荐再买个计算器",
        "考研英语单词书和真题一起背，四六级也够用了",
        "宿舍想装个台灯，再买个收纳箱和小风扇就差不多了",
        "自行车停图书馆门口被挪了，车锁也不太好用了",
        "羽毛球拍和运动鞋都放在体育馆柜子里了",
        "吉他弦断了，想再买个尤克里里入门练手，口琴也想要",
        "保温杯漏水了，想换成水壶，宿舍用方便",
        "演唱会门票没抢到，求两张电影票也好",
        "相机镜头想升级，三脚架也想一起出",
    ]
    idx = SemanticIndex().fit(docs, corpus_texts=[d["text"] for d in docs] + EXTRA)

    print("共现学习到的相关词（部分）：")
    for k in ["电脑", "键盘", "自行车", "教材", "吉他", "保温杯", "门票", "相机"]:
        print("  %s -> %s" % (k, [w for w, _ in idx.related_map.get(k, [])]))
    print()
    for q in ["笔记本", "电脑", "单车", "机械健盘", "书", "想买个喝水的杯子",
              "演唱会票", "音乐器材", "尤克里里", "数码产品", "宿舍用品",
              "有没有便宜的代步工具", "考研要用的书"]:
        r = idx.search(q, top_k=3)
        print("查询「%s」 %s" % (q, " | ".join(r["explain"]) or "（无扩展）"))
        for it in r["items"]:
            title = next(d["title"] for d in docs if d["id"] == it["id"])
            print("    %.3f  %s  命中=%s  词覆盖=%.2f 字覆盖=%.2f" %
                  (it["score"], title, it["matched_terms"],
                   it["word_coverage"], it["char_coverage"]))
        if not r["items"]:
            print("    （无结果）")