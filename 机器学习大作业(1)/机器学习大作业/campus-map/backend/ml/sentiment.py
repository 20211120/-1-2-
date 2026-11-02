# -*- coding: utf-8 -*-
"""
自动标签模块（情感分析已下线）
情感二分类模型因准确率不达标已移除（语料保留在 corpus.py / experiment.py 供课程实验使用）。
当前仅保留：多标签自动分类 = 关键词命中 + TF-IDF 余弦相似度兜底。
"""
import os
import sys

import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus import TAG_KEYWORDS

# 载入自定义校园词典，提高分词质量
CUSTOM_WORDS = ["自习区", "打菜", "手不抖", "手抖", "插座", "充电口", "通宵开放",
                "学生党", "考研党", "小组讨论", "自动售货机", "售货机", "借还书"]
for w in CUSTOM_WORDS:
    jieba.add_word(w)


def tokenize(text):
    """jieba 分词，返回空格连接的字符串（供 TfidfVectorizer 使用）"""
    return " ".join(w for w in jieba.cut(text) if w.strip())


class TagClassifier:
    """多标签分类：关键词命中为主，TF-IDF 余弦相似度兜底"""

    def __init__(self):
        self.tags = list(TAG_KEYWORDS.keys())
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2))
        # 每个标签的种子文本 = 关键词拼接
        seed_texts = [" ".join(TAG_KEYWORDS[t]) for t in self.tags]
        self.tag_vecs = self.vectorizer.fit_transform(tokenize(t) for t in seed_texts)

    def predict(self, text, sim_threshold=0.22):
        """返回命中的标签列表（保持 self.tags 顺序）"""
        hit = set()
        # 1) 关键词直接命中
        for tag, kws in TAG_KEYWORDS.items():
            if any(kw in text for kw in kws):
                hit.add(tag)
        # 2) TF-IDF 余弦相似度兜底（捕捉未覆盖关键词的表达）
        vec = self.vectorizer.transform([tokenize(text)])
        sims = cosine_similarity(vec, self.tag_vecs)[0]
        for i, s in enumerate(sims):
            if s >= sim_threshold:
                hit.add(self.tags[i])
        # 互斥标签处理：同时命中"好吃/难吃"、"安静/人多"等时，以关键词命中优先
        for a, b in [("好吃", "难吃"), ("价格实惠", "偏贵"), ("设备新", "设备旧")]:
            if a in hit and b in hit:
                a_kw = any(kw in text for kw in TAG_KEYWORDS[a])
                b_kw = any(kw in text for kw in TAG_KEYWORDS[b])
                if a_kw and not b_kw:
                    hit.discard(b)
                elif b_kw and not a_kw:
                    hit.discard(a)
        return [t for t in self.tags if t in hit]


_tagger = None


def get_tagger():
    global _tagger
    if _tagger is None:
        _tagger = TagClassifier()
    return _tagger


def analyze(text):
    """兼容壳：仅自动标签。情感字段给中性占位（情感分析已下线，各调用点无需改动）"""
    tags = get_tagger().predict(text)
    return {
        "sentiment": 1,           # 占位：前端已不再展示情感
        "sentiment_prob": 0.5,    # 占位
        "sentiment_label": "",
        "tags": tags,
    }


if __name__ == "__main__":
    for t in ["图书馆很安静，插座也多，自习圣地",
              "食堂菜难吃还贵，再也不来了",
              "体育馆器材不错就是人有点多"]:
        print(t, "->", analyze(t)["tags"])