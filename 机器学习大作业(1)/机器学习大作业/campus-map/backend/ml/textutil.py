# -*- coding: utf-8 -*-
"""
纯 Python 文本工具层
————————————————————————————————————————————
本模块刻意只依赖标准库（re / math / unicodedata / collections），不引入
jieba 与 scikit-learn，原因是：

  * 线上后端在 exe（PyInstaller --onedir）模式下运行，内置解释器里没有
    第三方科学计算包，引入 sklearn 会让 exe 体积暴涨且极易打包失败；
  * 课程平台的语料规模很小（几十~几百条），纯 Python 的稀疏 TF-IDF 与
    朴素贝叶斯在毫秒级就能算完，完全没必要上重型库。

提供的能力：
  1. normalize / 字 n-gram / 字符二元组 Jaccard —— 文本相似度基础；
  2. MaxMatchSegmenter —— 基于领域词典的逆向最大匹配切词（替代 jieba）；
  3. SparseTfidf —— 稀疏 TF-IDF 向量索引，支持查询加权（用于语义扩展）。
"""

import math
import re
import unicodedata
from collections import Counter

# ---------------------------------------------------------------- 归一化

# 只保留中日韩统一表意文字、英文字母、数字；其余（标点、空白、表情）当分隔符
_KEEP = re.compile(r"[^\u4e00-\u9fff\u3400-\u4dbfa-z0-9]+")


def normalize(text):
    """全角转半角 + 小写 + 去标点空白，返回适合做 n-gram 的紧凑串"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower()
    return _KEEP.sub("", s)


def normalize_keep_space(text):
    """归一化但保留空格分隔（用于把多个字段拼成一个可切词的串）"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower()
    return re.sub(r"[^\u4e00-\u9fff\u3400-\u4dbfa-z0-9]+", " ", s).strip()


def char_ngrams(text, ngram_range=(2, 3)):
    """返回字级 n-gram 计数字典；文本过短时退化为整串"""
    s = normalize(text)
    out = Counter()
    if not s:
        return out
    for n in ngram_range:
        if len(s) < n:
            continue
        for i in range(len(s) - n + 1):
            out[s[i:i + n]] += 1
    if not out:
        out[s] = 1
    return out


def bigrams(text):
    """字符二元组集合（做 Jaccard 用）"""
    s = normalize(text)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a, b):
    """两个集合的 Jaccard 相似度"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def contains_any(text, words):
    """text 中命中哪些关键词（返回命中列表，按 words 顺序）"""
    if not text:
        return []
    return [w for w in words if w in text]


# ---------------------------------------------------------------- 切词

class MaxMatchSegmenter:
    """
    逆向最大匹配切词：只认词典里的词，词典外的连续汉字按单字输出。

    校园/二手场景的词汇高度固定（"机械键盘""考研数学""保温杯"…），
    用领域词典做最大匹配的结果，在这类短文本上并不比通用分词差，
    而且完全可控、可解释（答辩时可以指着词典说明覆盖范围）。
    """

    def __init__(self, words=()):
        self.words = set()
        self.max_len = 1
        for w in words:
            self.add(w)

    def add(self, word):
        w = normalize(word)
        if len(w) >= 2:
            self.words.add(w)
            self.max_len = max(self.max_len, len(w))

    def cut(self, text):
        """返回词列表（保序，含单字兜底 token）"""
        s = normalize(text)
        tokens = []
        i = 0
        n = len(s)
        while i < n:
            matched = None
            upper = min(self.max_len, n - i)
            for size in range(upper, 1, -1):
                piece = s[i:i + size]
                if piece in self.words:
                    matched = piece
                    break
            if matched:
                tokens.append(matched)
                i += len(matched)
            else:
                # 词典外：英数连续串整体保留，汉字单字输出
                j = i
                if s[i].isascii() and s[i].isalnum():
                    while j < n and s[j].isascii() and s[j].isalnum():
                        j += 1
                    tokens.append(s[i:j])
                    i = j
                else:
                    tokens.append(s[i])
                    i += 1
        return tokens

    def index_map(self, text):
        """切词并给出每个词在归一化串中的起始位置（用于命中解释）"""
        s = normalize(text)
        out = []
        i = 0
        n = len(s)
        while i < n:
            matched = None
            upper = min(self.max_len, n - i)
            for size in range(upper, 1, -1):
                piece = s[i:i + size]
                if piece in self.words:
                    matched = piece
                    break
            if matched:
                out.append((matched, i))
                i += len(matched)
            else:
                out.append((s[i], i))
                i += 1
        return out


# ---------------------------------------------------------------- TF-IDF

class SparseTfidf:
    """
    稀疏 TF-IDF 索引（纯 Python 字典实现）

    * analyze="char"：字级 n-gram（抗错别字、抗未登录词）
    * analyze="word"：先用 MaxMatchSegmenter 切词，再做词级 TF-IDF

    fit()        用文档集合建索引（df / idf）
    vectorize()  把任意文本转成 (权重字典, L2 范数)
    search()     查询检索，支持 query_weights 对查询词加权（语义扩展用）
    """

    def __init__(self, analyze="char", ngram_range=(2, 3), segmenter=None):
        self.analyze = analyze
        self.ngram_range = ngram_range
        self.segmenter = segmenter
        self.df = Counter()
        self.n_docs = 0
        self.idf = {}
        self.docs = []            # [{"vec": {...}, "norm": float}]

    # ---- 内部：文本 -> 计数字典
    def _terms(self, text):
        if self.analyze == "word":
            if self.segmenter is None:
                raise ValueError("word 模式需要 segmenter")
            toks = [t for t in self.segmenter.cut(text) if len(t) >= 1]
            return Counter(toks)
        return char_ngrams(text, self.ngram_range)

    def fit(self, docs):
        self.docs = []
        self.df = Counter()
        self.n_docs = max(1, len(docs))
        counters = [self._terms(d) for d in docs]
        for c in counters:
            self.df.update(c.keys())
        # 平滑 idf：log((1+N)/(1+df)) + 1
        self.idf = {t: math.log((1.0 + self.n_docs) / (1.0 + df)) + 1.0
                    for t, df in self.df.items()}
        for c in counters:
            vec = self._weight(c)
            self.docs.append({"vec": vec, "norm": _norm(vec)})
        return self

    def add_doc(self, text):
        """增量加入一条文档（idf 按当前 df 近似，不重建已入索引项）"""
        c = self._terms(text)
        for t in c:
            self.df[t] += 1
            if t not in self.idf:
                self.idf[t] = math.log((1.0 + self.n_docs) / (1.0 + self.df[t])) + 1.0
        self.n_docs += 1
        vec = self._weight(c)
        self.docs.append({"vec": vec, "norm": _norm(vec)})
        return len(self.docs) - 1

    def _weight(self, counter):
        vec = {}
        for t, tf in counter.items():
            w = (1.0 + math.log(tf)) * self.idf.get(
                t, math.log(1.0 + self.n_docs) + 1.0)
            vec[t] = w
        return vec

    def vectorize(self, text, query_weights=None):
        """
        query_weights: {term: multiplier}，用于把同义词/相关词的权重压进查询向量
        """
        c = self._terms(text)
        if query_weights:
            merged = {}
            for t, tf in c.items():
                merged[t] = merged.get(t, 0.0) + tf * query_weights.get(t, 1.0)
            for t, mult in query_weights.items():
                if t not in c and mult > 0:
                    merged[t] = merged.get(t, 0.0) + mult
            vec = self._weight_from_weights(merged)
        else:
            vec = self._weight(c)
        return vec, _norm(vec)

    def _weight_from_weights(self, weights):
        vec = {}
        for t, w in weights.items():
            if w <= 0:
                continue
            idf = self.idf.get(t)
            if idf is None:
                continue          # 查询词不在训练词表里 -> 无区分度，跳过
            vec[t] = w * idf
        return vec

    def search(self, text, top_k=None, query_weights=None, min_score=0.0):
        """返回 [(doc_index, score)]，按分数降序"""
        qvec, qnorm = self.vectorize(text, query_weights)
        if not qvec or qnorm <= 0:
            return []
        out = []
        for i, d in enumerate(self.docs):
            if d["norm"] <= 0:
                continue
            score = _cosine(qvec, qnorm, d["vec"], d["norm"])
            if score > min_score:
                out.append((i, score))
        out.sort(key=lambda x: -x[1])
        return out[:top_k] if top_k else out

    def top_terms(self, text, k=8, query_weights=None):
        """查询命中的高权重词（用于前端展示"为什么搜出这条"）"""
        qvec, _ = self.vectorize(text, query_weights)
        return [t for t, _ in sorted(qvec.items(), key=lambda x: -x[1])[:k]]


def _norm(vec):
    return math.sqrt(sum(v * v for v in vec.values()))


def _cosine(v1, n1, v2, n2):
    if n1 <= 0 or n2 <= 0:
        return 0.0
    if len(v1) > len(v2):
        v1, v2 = v2, v1
    dot = 0.0
    for t, w in v1.items():
        w2 = v2.get(t)
        if w2:
            dot += w * w2
    return dot / (n1 * n2)


def softmax(scores):
    """数值稳定的 softmax，输入 dict -> dict"""
    if not scores:
        return {}
    m = max(scores.values())
    exps = {k: math.exp(v - m) for k, v in scores.items()}
    total = sum(exps.values()) or 1.0
    return {k: v / total for k, v in exps.items()}


def parse_time(s):
    """解析 'YYYY-MM-DDTHH:MM:SS' / 'YYYY-MM-DD HH:MM:SS'，失败返回 None"""
    from datetime import datetime

    if not s:
        return None
    txt = str(s).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(txt)
    except ValueError:
        return None