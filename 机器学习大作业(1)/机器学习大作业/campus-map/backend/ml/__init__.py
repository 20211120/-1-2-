# -*- coding: utf-8 -*-
"""
南航评论区 · 机器学习模块包
================================================================
三个线上能力全部用纯 Python 实现（只用标准库），因此：

  * 源码模式（python campus-map/backend/app.py）直接可用；
  * PyInstaller 打包的 exe 里也能跑，不需要 sklearn / jieba / numpy，
    exe 体积不增加，换电脑不用装任何环境。

  matcher.py     失物招领「寻物 ↔ 招领」智能匹配（多特征加权打分）
  moderation.py  垃圾广告 / 引战挑事 / 不文明用语识别（规则 + TF-IDF 类质心）
  semantic.py    二手市集语义检索（同义词 + 主题词 + PMI 共现 + 三通道融合）
  textutil.py    共用文本工具（归一化、字 n-gram、TF-IDF、词典切词）

目录里的 corpus.py / sentiment.py / experiment.py 是早期课程实验素材
（sklearn + jieba 版本），只用于对比实验与答辩讲解，线上不加载。
"""

__all__ = ["matcher", "moderation", "semantic", "textutil"]