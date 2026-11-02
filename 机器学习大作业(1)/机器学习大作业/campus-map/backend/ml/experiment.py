# -*- coding: utf-8 -*-
"""对比不同特征/分类器组合在小语料上的测试准确率，选出最优配置"""
import io
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "..", "exp.log")
sys.path.insert(0, BASE)

import jieba  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.naive_bayes import MultinomialNB  # noqa: E402
from sklearn.model_selection import train_test_split, cross_val_score  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from corpus import POSITIVE, NEGATIVE  # noqa: E402

for w in ["自习区", "打菜", "手不抖", "手抖", "插座", "充电口"]:
    jieba.add_word(w)

texts = POSITIVE + NEGATIVE
labels = [1] * len(POSITIVE) + [0] * len(NEGATIVE)

def tok(t):
    return " ".join(w for w in jieba.cut(t) if w.strip())

tok_texts = [tok(t) for t in texts]

configs = {
    "word(1,2)+LR": Pipeline([("v", TfidfVectorizer(ngram_range=(1, 2))),
                              ("c", LogisticRegression(max_iter=1000))]),
    "word(1,1)+LR": Pipeline([("v", TfidfVectorizer()),
                              ("c", LogisticRegression(max_iter=1000))]),
    "char_wb(2,3)+LR": Pipeline([("v", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))),
                                 ("c", LogisticRegression(max_iter=1000))]),
    "char_wb(2,4)+LR": Pipeline([("v", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))),
                                 ("c", LogisticRegression(max_iter=1000, C=0.5))]),
    "char_wb(2,3)+NB": Pipeline([("v", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))),
                                 ("c", MultinomialNB(alpha=0.3))]),
    "word(1,1)+NB": Pipeline([("v", TfidfVectorizer()),
                              ("c", MultinomialNB(alpha=0.3))]),
}

buf = io.StringIO()
for name, pipe in configs.items():
    X_src = tok_texts if name.startswith("word") else texts
    X_tr, X_te, y_tr, y_te = train_test_split(
        X_src, labels, test_size=0.25, random_state=42, stratify=labels)
    pipe.fit(X_tr, y_tr)
    acc = pipe.score(X_te, y_te)
    cv = cross_val_score(pipe, X_src, labels, cv=5).mean()
    print(f"{name}: holdout={acc:.3f}  cv5={cv:.3f}", file=buf)

with open(LOG, "w", encoding="utf-8") as f:
    f.write(buf.getvalue())
