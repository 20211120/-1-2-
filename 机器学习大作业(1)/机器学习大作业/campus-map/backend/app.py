# -*- coding: utf-8 -*-
"""
南航评论区 平台后端（原校园地图评论系统升级版）
模块：校园地图（POI/评论/备注/导航数据）+ 失物招领 + 校园论坛 + 二手市集
Flask + SQLite；ML 模块（ml/ 纯 Python 实现，无需 sklearn/jieba）提供三项能力：
  1. 失物招领「寻物 ↔ 招领」智能匹配 + 站内通知互相推送
  2. 垃圾广告 / 引战 / 不文明内容识别拦截（命中即拦截，不做人工审核队列）
  3. 二手市集语义检索（同义词 + 主题词 + PMI 共现 + 三通道融合）
"""
import json
import os
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = BASE_DIR          # 源码目录（ml 包在这里）；下面 BASE_DIR 会被改成项目根

# ml 包只依赖标准库，源码模式与 exe 模式都能直接加载
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
from ml import matcher as ml_matcher          # noqa: E402
from ml import moderation as ml_moderation    # noqa: E402
from ml import semantic as ml_semantic        # noqa: E402
from ml.textutil import normalize as ml_normalize   # noqa: E402

# 数据/上传/页面统一放在项目根目录（exe 也在根目录），
# 这样"源码模式"和"exe 模式"共用同一份数据，换电脑拷整个文件夹即可。
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    # exe 可能在 软件\ 子目录：向上找包含 campus-map\frontend 的项目根
    if os.path.isdir(os.path.join(exe_dir, "campus-map", "frontend")):
        ROOT_DIR = exe_dir
    else:
        ROOT_DIR = os.path.dirname(exe_dir)
else:
    ROOT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))   # 机器学习大作业/
BASE_DIR = ROOT_DIR
WEB_DIR = os.path.join(ROOT_DIR, "campus-map", "frontend")
MOBILE_DIR = os.path.join(ROOT_DIR, "手机版")

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 情感分析与自动标签均已下线（准确率不达标）。语料与实验脚本保留在 backend/ml/ 供课程素材。
# 这里保留一个兼容空壳，让各写入调用点无需改动：统一写入中性情感、空标签。
def analyze(text):
    return {"sentiment": 1, "sentiment_prob": 0.5, "sentiment_label": "", "tags": []}

DB_PATH = os.path.join(BASE_DIR, "data.db")
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = Flask(__name__)
CORS(app)

TOKENS = {}  # token -> username（内存态，重启后重新登录即可）

SCHEMA = """
CREATE TABLE IF NOT EXISTS pois (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    lat REAL, lng REAL,
    description TEXT DEFAULT '',
    parent_id INTEGER DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poi_id INTEGER NOT NULL,
    author TEXT DEFAULT '匿名同学',
    content TEXT NOT NULL,
    sentiment INTEGER NOT NULL,
    sentiment_prob REAL NOT NULL,
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pwd_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poi_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',   -- public / private
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lostfound (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    kind TEXT NOT NULL,              -- lost 寻物 / found 招领
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    location TEXT DEFAULT '',        -- 地点描述
    poi_id INTEGER DEFAULT NULL,     -- 关联地图 POI（可选）
    contact TEXT DEFAULT '',
    image TEXT DEFAULT '',           -- 上传图片文件名
    status TEXT NOT NULL DEFAULT 'open',   -- open / resolved
    tags TEXT DEFAULT '[]',          -- ML 自动标签
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    sentiment INTEGER DEFAULT 1,
    sentiment_prob REAL DEFAULT 0.5,
    tags TEXT DEFAULT '[]',
    likes INTEGER DEFAULT 0,
    image TEXT DEFAULT '',           -- 帖子配图（uploads 文件名）
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forum_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- 地图备注（升级版）：支持绑定 POI，也支持任意经纬度自由标注
CREATE TABLE IF NOT EXISTS map_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',   -- public / private
    lat REAL, lng REAL,              -- 自由标注坐标；绑定 POI 时可为空
    poi_id INTEGER DEFAULT NULL,     -- 绑定 POI；自由标注为 NULL
    created_at TEXT NOT NULL
);
-- 自由标注备注的评论（备注像 POI 一样可被评论）
CREATE TABLE IF NOT EXISTS note_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    sentiment INTEGER NOT NULL,
    sentiment_prob REAL NOT NULL,
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);
-- 二手市集
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    kind TEXT NOT NULL,              -- sell 出售 / want 求购
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    category TEXT DEFAULT '其他',     -- 书籍/电子产品/生活用品/票券/其他
    price REAL DEFAULT NULL,
    contact TEXT DEFAULT '',
    image TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',   -- open / done（已成交/已求到）
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);
-- ML①：失物招领匹配结果（lost_id/found_id 恒按「寻物=lost、招领=found」存）
CREATE TABLE IF NOT EXISTS lf_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lost_id INTEGER NOT NULL,
    found_id INTEGER NOT NULL,
    score REAL NOT NULL,
    level TEXT DEFAULT '',
    reasons TEXT DEFAULT '[]',
    notified INTEGER DEFAULT 0,      -- 是否已互相推送通知
    created_at TEXT NOT NULL,
    UNIQUE(lost_id, found_id)
);
-- ML②：站内通知中心（匹配提醒等）
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,          -- 接收人
    ntype TEXT NOT NULL,             -- lf_match 失物匹配 ...
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    ref_type TEXT DEFAULT '',        -- lostfound / post / listing
    ref_id INTEGER DEFAULT NULL,     -- 自己的那条信息
    related_id INTEGER DEFAULT NULL, -- 匹配到的对方信息（前端点击跳转）
    score REAL DEFAULT 0,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
-- ML③：内容安全处置日志（被拦截的记录，含风险分与命中特征）
CREATE TABLE IF NOT EXISTS moderation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,       -- post/comment/note/lostfound/listing
    target_id INTEGER DEFAULT NULL,
    author TEXT DEFAULT '',
    snippet TEXT DEFAULT '',
    label TEXT DEFAULT '',           -- ad / flame / uncivil
    label_name TEXT DEFAULT '',
    risk REAL DEFAULT 0,
    action TEXT NOT NULL,            -- block（历史数据里可能有 review）
    matched TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now():
    return datetime.now().isoformat(timespec="seconds")


def current_user():
    """从 Authorization: Bearer <token> 解析当前登录用户，未登录返回 None"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return TOKENS.get(auth[7:])
    return None


# ================================================================
# ML 引擎接入层（失物招领匹配 / 内容安全 / 语义检索）
# ================================================================

def lf_payload(r):
    """数据库行 -> 匹配引擎需要的字段（保持键名与 ml/matcher.py 一致）"""
    d = dict(r)
    return {
        "id": d.get("id"), "author": d.get("author", ""),
        "kind": d.get("kind", "lost"), "title": d.get("title", ""),
        "content": d.get("content", ""), "location": d.get("location", ""),
        "poi_id": d.get("poi_id"), "status": d.get("status", "open"),
        "created_at": d.get("created_at", ""),
    }


def find_lf_matches(db, item, top_k=5, min_score=None):
    """拿一条失物/招领和"反向类型"的其它记录做匹配，返回 [(候选, 打分明细)]"""
    if min_score is None:
        min_score = ml_matcher.STORE_THRESHOLD
    opposite = "found" if item["kind"] == "lost" else "lost"
    rows = db.execute(
        "SELECT * FROM lostfound WHERE kind=? AND id<>?"
        " ORDER BY (status='resolved'), id DESC", (opposite, item["id"])).fetchall()
    cands = [lf_payload(r) for r in rows]
    return ml_matcher.rank_matches(item, cands, top_k=top_k, min_score=min_score)


def save_lf_match(db, item, cand, detail, notified):
    """写入匹配关系；lost_id/found_id 恒按「寻物=lost、招领=found」归一化存放"""
    if item["kind"] == "lost":
        lost_id, found_id = item["id"], cand["id"]
    else:
        lost_id, found_id = cand["id"], item["id"]
    db.execute(
        "INSERT OR REPLACE INTO lf_matches"
        "(lost_id,found_id,score,level,reasons,notified,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (lost_id, found_id, detail["score"], detail["level"],
         json.dumps(detail["reasons"], ensure_ascii=False),
         1 if notified else 0, now()))


def add_notification(db, username, ntype, title, body, ref_type="",
                     ref_id=None, related_id=None, score=0.0):
    """站内通知（同一人 + 同一对信息只发一次）"""
    if not username:
        return None
    dup = db.execute(
        "SELECT id FROM notifications WHERE username=? AND ntype=? AND ref_id=?"
        " AND related_id=?", (username, ntype, ref_id, related_id)).fetchone()
    if dup:
        return None
    cur = db.execute(
        "INSERT INTO notifications"
        "(username,ntype,title,body,ref_type,ref_id,related_id,score,is_read,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,0,?)",
        (username, ntype, title, body, ref_type, ref_id, related_id, score, now()))
    return cur.lastrowid


def match_and_notify(db, item, top_k=5):
    """
    核心闭环：给一条失物/招领信息找匹配 → 写 lf_matches →
    高分匹配（≥ NOTIFY_THRESHOLD）时给"双方"各推一条站内通知。
    """
    matches = find_lf_matches(db, item, top_k=top_k)
    pushed = []
    for cand, detail in matches:
        notify = (detail["score"] >= ml_matcher.NOTIFY_THRESHOLD
                  and item.get("status") == "open" and cand.get("status") == "open")
        save_lf_match(db, item, cand, detail, notify)
        if not notify:
            continue
        pct = int(round(detail["score"] * 100))
        why = "；".join(detail["reasons"][:3])
        if item["kind"] == "lost":
            t_mine = "可能找到了你丢失的物品"
            b_mine = "你发布的《%s》与《%s》相似度 %d%%（%s）。点开确认一下是不是同一件。" % (
                item["title"], cand["title"], pct, why)
            t_other = "有人丢了东西，可能与你捡到的一致"
            b_other = "%s 发布的《%s》与你发布的《%s》相似度 %d%%（%s）。" % (
                cand["author"], cand["title"], item["title"], pct, why)
        else:
            t_mine = "你捡到的东西可能有人正在找"
            b_mine = "你发布的《%s》与《%s》相似度 %d%%（%s）。建议尽快联系失主。" % (
                item["title"], cand["title"], pct, why)
            t_other = "有人捡到的东西可能就是你在找的"
            b_other = "你发布的《%s》与 %s 发布的《%s》相似度 %d%%（%s）。" % (
                cand["title"], cand["author"], item["title"], pct, why)
        if add_notification(db, item["author"], "lf_match", "🔔 " + t_mine, b_mine,
                            "lostfound", item["id"], cand["id"], detail["score"]):
            pushed.append({"to": item["author"], "related_id": cand["id"]})
        if add_notification(db, cand["author"], "lf_match", "🔔 " + t_other, b_other,
                            "lostfound", cand["id"], item["id"], detail["score"]):
            pushed.append({"to": cand["author"], "related_id": item["id"]})
    db.commit()
    return matches, pushed


def rematch_all(db):
    """把库里已有的失物招领两两匹配一遍（升级首次启动时补历史数据的匹配关系）"""
    rows = db.execute("SELECT * FROM lostfound ORDER BY id").fetchall()
    total = 0
    for r in rows:
        matches, _ = match_and_notify(db, lf_payload(r))
        total += len(matches)
    return total


# ---------------- 内容安全 ----------------

def log_moderation(db, target_type, target_id, author, text, result):
    db.execute(
        "INSERT INTO moderation_logs"
        "(target_type,target_id,author,snippet,label,label_name,risk,action,matched,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (target_type, target_id, author or "", (text or "")[:80],
         result["label"], result["label_name"], result["risk"], result["action"],
         json.dumps(result["matched"], ensure_ascii=False), now()))


def check_content(db, text, context, target_type, target_id=None, author=""):
    """
    返回 (result, 错误响应或 None)

    当前策略：判定为不良内容**直接拦截**（返回 400，不允许发表），
    并把这次拦截写入 moderation_logs 作为处置记录——没有"待人工复核"这一档。
    """
    result = ml_moderation.moderate(text, context=context)
    if result["action"] == "block":
        log_moderation(db, target_type, target_id, author, text, result)
        db.commit()
        return result, (jsonify({"error": result["message"],
                                 "moderation": result}), 400)
    return result, None


def moderation_summary(db):
    """给 /api/stats 用的内容安全汇总"""
    blocked = db.execute(
        "SELECT COUNT(*) FROM moderation_logs WHERE action='block'").fetchone()[0]
    # 兼容字段：早期版本存在"放行但转人工复核"的记录，现在只会是历史数据
    legacy_review = db.execute(
        "SELECT COUNT(*) FROM moderation_logs WHERE action='review'").fetchone()[0]
    m = ml_moderation.metrics()
    return {
        "moderation_blocked": blocked,
        "moderation_review": legacy_review,
        "moderation_acc": m["accuracy"],
        "moderation_macro_f1": m["macro_f1"],
        "moderation_samples": m["samples"],
        "moderation_model": m["model"],
    }


# ---------------- 二手市集语义检索 ----------------

_SEM_CACHE = {"version": -1, "index": None}
_INDEX_VERSION = [0]


def bump_ml_index():
    """商品/帖子/评论有写入时调用，让语义索引失效重建"""
    _INDEX_VERSION[0] += 1


def market_semantic_index(db):
    """构建（或复用）二手市集语义索引"""
    if _SEM_CACHE["index"] is not None and _SEM_CACHE["version"] == _INDEX_VERSION[0]:
        return _SEM_CACHE["index"]
    docs = []
    for r in db.execute("SELECT * FROM listings ORDER BY id").fetchall():
        d = dict(r)
        text = "%s %s %s %s" % (
            d["title"], d["content"] or "", d["category"] or "",
            "出售" if d["kind"] == "sell" else "求购")
        docs.append({"id": d["id"], "title": d["title"],
                     "norm_title": ml_normalize(d["title"]), "text": text})
    # 共现学习用的全站语料：商品 + 帖子 + 评论 + 失物招领（词表外的文本自动忽略）
    corpus = [d["text"] for d in docs]
    for sql in ("SELECT title || ' ' || content AS t FROM posts",
                "SELECT content AS t FROM forum_comments",
                "SELECT content AS t FROM comments",
                "SELECT title || ' ' || content || ' ' || location AS t FROM lostfound"):
        try:
            corpus += [row["t"] for row in db.execute(sql).fetchall() if row["t"]]
        except sqlite3.Error:
            pass
    index = ml_semantic.SemanticIndex().fit(docs, corpus_texts=corpus)
    _SEM_CACHE["index"] = index
    _SEM_CACHE["version"] = _INDEX_VERSION[0]
    return index


SEED_POIS = [
    ("图书馆", "library", 31.938432, 118.795900, "馆藏丰富，自习座位需早起抢", None),
    ("一号楼", "teaching", 31.937338, 118.790850, "校区主楼，可查看楼层平面图", None),
    ("二号楼", "teaching", 31.937923, 118.789624, "综合教学楼", None),
    ("三号楼", "teaching", 31.936380, 118.789982, "理工科实验楼", None),
    ("四号楼", "teaching", 31.938252, 118.788838, "阶梯教室集中", None),
    ("体育中心", "sports", 31.940382, 118.789584, "室内篮球/羽毛球/健身房", None),
    ("东区操场", "sports", 31.936596, 118.795024, "标准塑胶跑道，夜跑热门", None),
    ("一食堂", "canteen", 31.941484, 118.789849, "一食堂，红烧肉口碑爆棚", None),
    ("樱花广场", "other", 31.941432, 118.788678, "春天赏樱打卡点", None),
]

SEED_COMMENTS = {
    "图书馆": [
        ("张同学", "四楼自习区很安静，插座也多，带电脑来学了一整天"),
        ("李同学", "期末周人太多了，八点半到都找不到座位"),
        ("王同学", "借还书自助机很好用，就是偶尔系统出故障"),
    ],
    "一食堂": [
        ("干饭人", "红烧肉真的绝了，便宜又好吃，阿姨打菜手不抖"),
        ("陈同学", "午饭点排队太长了，人挤人"),
    ],
    "体育中心": [
        ("健身达人", "器械比较新，晚上人多要排队"),
        ("刘同学", "淋浴间经常没热水，希望后勤修一下"),
    ],
    "东区操场": [("夜跑选手", "塑胶跑道很舒服，就是晚上灯光有点暗")],
    "一号楼": [("上课人", "阶梯教室投影仪清晰，坐后排也看得清")],
}

SEED_POSTS = [
    ("学姐路过", "期末周图书馆占座现象大家怎么看",
     "最近期末周，图书馆一堆用一本书占一整天座位的，真正来学习的人找不到位置。建议平台出个「占座举报」标签，大家觉得呢？", "forum_lib.svg"),
    ("夜猫子", "推荐一个通宵自习的好去处",
     "图书馆通宵自习区真的安静，插座充足，凌晨两点人还不少，考研党可以冲。", "forum_study.svg"),
    ("干饭王", "一食堂新窗口测评",
     "新开的麻辣香锅窗口味道正宗，分量足，就是饭点排队太久，建议错峰。", "forum_food.svg"),
    ("运动仔", "东区操场夜跑组队",
     "每天晚上八点东区操场跑步，塑胶跑道很舒服，就是灯光有点暗，想找人一起跑，有意评论留言。", "forum_run.svg"),
]

SEED_LOSTFOUND = [
    ("小迷糊", "lost", "丢失一串钥匙（带飞机挂件）", "昨天下午在一号楼1-205教室上课后不见了一串钥匙，挂着一个小飞机模型挂件，很重要，捡到请联系我，重谢！", "一号楼", "QQ 123456789", "lf_key.svg"),
    ("热心人", "found", "捡到一张校园卡", "今天中午在一食堂二楼捡到一张校园卡，已交到服务台，失主可以凭学号认领。", "一食堂", "", "lf_card.svg"),
    # 与上面第一条构成一组"高置信匹配"，用于演示智能匹配 + 通知推送
    ("热心人", "found", "一号楼捡到一串钥匙", "在一号楼1-205教室捡到一串钥匙，上面挂着一个飞机模型挂件，已放到一号楼值班室，失主请联系我。", "一号楼", "QQ 987654321", "lf_key.svg"),
]

SEED_LISTINGS = [
    ("大四学长", "sell", "考研数学全书（李永乐）九五新", "毕业出清，考研数学复习全书，只做了前 50 页笔记，正版，将军路校区自取。",
     "书籍", 25.0, "vx: math学长", "mk_book.svg"),
    ("书虫", "sell", "出二手专业课本若干", "大一大二公共课教材打包出：高数、线代、大物，无缺页，便宜甩。",
     "书籍", 30.0, "QQ 222333444", "mk_textbooks.svg"),
    ("数码控", "sell", "出九成新机械键盘", "樱桃红轴，用了半年，手感很好，换了新键盘所以出掉。",
     "电子产品", 120.0, "vx: keyboardman", "mk_keyboard.svg"),
    ("新生小白", "want", "求购二手自行车", "校内代步用，能骑就行，预算 150 以内，有出的同学联系我。",
     "生活用品", None, "QQ 555666777", "mk_bike.svg"),
    ("文艺青年", "want", "求吉他一把，入门级即可", "想学吉他，求一把入门民谣吉他，价格好商量。",
     "其他", None, "vx: guitar_newbie", "mk_guitar.svg"),
]


def init_db():
    db = sqlite3.connect(DB_PATH)
    # 与请求内连接保持一致：行对象可按下标也可按列名访问
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    # 兼容旧库：给已存在的 posts 表补 image 列（帖子配图功能）
    try:
        cols = [r[1] for r in db.execute("PRAGMA table_info(posts)").fetchall()]
        if "image" not in cols:
            db.execute("ALTER TABLE posts ADD COLUMN image TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    if db.execute("SELECT COUNT(*) FROM pois").fetchone()[0] == 0:
        poi_id_map = {}
        for name, cat, lat, lng, desc, parent in SEED_POIS:
            c = db.execute(
                "INSERT INTO pois(name,category,lat,lng,description,parent_id) VALUES(?,?,?,?,?,?)",
                (name, cat, lat, lng, desc, parent))
            poi_id_map[name] = c.lastrowid
        b1 = poi_id_map["一号楼"]
        for floor, rooms in [(1, range(101, 111)), (2, range(201, 211))]:
            for r in rooms:
                db.execute(
                    "INSERT INTO pois(name,category,lat,lng,description,parent_id) VALUES(?,?,?,?,?,?)",
                    (f"1-{r}教室", "classroom", 31.937338, 118.790850, f"一号楼{floor}层教室", b1))
        for poi_name, items in SEED_COMMENTS.items():
            pid = poi_id_map[poi_name]
            for author, content in items:
                r = analyze(content)
                db.execute(
                    "INSERT INTO comments(poi_id,author,content,sentiment,sentiment_prob,tags,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (pid, author, content, r["sentiment"], r["sentiment_prob"],
                     json.dumps(r["tags"], ensure_ascii=False), now()))
    if db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0:
        for author, title, content, image in SEED_POSTS:
            r = analyze(title + "。" + content)
            db.execute(
                "INSERT INTO posts(author,title,content,sentiment,sentiment_prob,tags,likes,image,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (author, title, content, r["sentiment"], r["sentiment_prob"],
                 json.dumps(r["tags"], ensure_ascii=False), secrets.randbelow(20), image, now()))
    if db.execute("SELECT COUNT(*) FROM lostfound").fetchone()[0] == 0:
        for author, kind, title, content, loc, contact, image in SEED_LOSTFOUND:
            tags = []  # 自动标签已下线
            pid = db.execute("SELECT id FROM pois WHERE name=?", (loc,)).fetchone()
            db.execute(
                "INSERT INTO lostfound(author,kind,title,content,location,poi_id,contact,image,tags,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (author, kind, title, content, loc, pid[0] if pid else None,
                 contact, image, json.dumps(tags, ensure_ascii=False), now()))
    if db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0:
        for author, kind, title, content, cat, price, contact, image in SEED_LISTINGS:
            tags = []  # 自动标签已下线
            db.execute(
                "INSERT INTO listings(author,kind,title,content,category,price,contact,image,tags,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (author, kind, title, content, cat, price, contact, image,
                 json.dumps(tags, ensure_ascii=False), now()))
    # 旧版 notes 表数据迁移到 map_notes（仅一次）
    old_cnt = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    new_cnt = db.execute("SELECT COUNT(*) FROM map_notes").fetchone()[0]
    if old_cnt and new_cnt == 0:
        rows = db.execute("SELECT * FROM notes").fetchall()
        for r in rows:  # 裸连接返回元组：id,poi_id,author,content,visibility,created_at
            poi = db.execute("SELECT lat,lng FROM pois WHERE id=?", (r[1],)).fetchone()
            db.execute(
                "INSERT INTO map_notes(author,content,visibility,lat,lng,poi_id,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (r[2], r[3], r[4],
                 poi[0] if poi else None, poi[1] if poi else None, r[1], r[5]))

    # ---- ML① 失物招领匹配：升级后首次启动补齐历史数据的匹配关系与通知 ----
    _ensure_demo_lf_pair(db)
    if db.execute("SELECT COUNT(*) FROM lf_matches").fetchone()[0] == 0:
        n = rematch_all(db)
        if n:
            _startup_log("失物招领历史数据匹配完成：%d 组" % n)
    # ---- ML③ 语义索引：启动即预热一次，避免首次搜索卡顿 ----
    market_semantic_index(db)

    db.commit()
    db.close()


def _ensure_demo_lf_pair(db):
    """
    演示数据补种：老库里只有"丢失钥匙"、没有对应的"捡到钥匙"，
    智能匹配就没有可展示的命中。这里补一条招领信息（仅当确实缺失时），
    让升级后的平台立刻能看到"匹配 + 通知推送"的完整效果。
    """
    lost = db.execute(
        "SELECT id FROM lostfound WHERE kind='lost' AND (title LIKE '%钥匙%'"
        " OR content LIKE '%钥匙%') LIMIT 1").fetchone()
    if not lost:
        return
    found = db.execute(
        "SELECT id FROM lostfound WHERE kind='found' AND (title LIKE '%钥匙%'"
        " OR content LIKE '%钥匙%') LIMIT 1").fetchone()
    if found:
        return
    pid = db.execute("SELECT id FROM pois WHERE name='一号楼'").fetchone()
    db.execute(
        "INSERT INTO lostfound(author,kind,title,content,location,poi_id,contact,image,tags,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("热心人", "found", "一号楼捡到一串钥匙",
         "在一号楼1-205教室捡到一串钥匙，上面挂着一个飞机模型挂件，已放到一号楼值班室，失主请联系我。",
         "一号楼", pid[0] if pid else None, "QQ 987654321", "lf_key.svg", "[]", now()))
    db.commit()


# ---------------- 认证 ----------------

@app.post("/api/auth/register")
def register():
    d = request.get_json(force=True)
    username = (d.get("username") or "").strip()[:20]
    password = d.get("password") or ""
    if not username or len(password) < 4:
        return jsonify({"error": "用户名不能为空，密码至少 4 位"}), 400
    db = get_db()
    try:
        db.execute("INSERT INTO users(username,pwd_hash,created_at) VALUES(?,?,?)",
                   (username, generate_password_hash(password), now()))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "用户名已被注册"}), 409
    token = secrets.token_hex(16)
    TOKENS[token] = username
    return jsonify({"token": token, "username": username}), 201


@app.post("/api/auth/login")
def login():
    d = request.get_json(force=True)
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not check_password_hash(row["pwd_hash"], password):
        return jsonify({"error": "用户名或密码错误"}), 401
    token = secrets.token_hex(16)
    TOKENS[token] = username
    return jsonify({"token": token, "username": username})


# ---------------- 地图：POI / 评论 ----------------

def poi_stats(db, poi_id):
    total = db.execute("SELECT COUNT(*) FROM comments WHERE poi_id=?", (poi_id,)).fetchone()[0]
    return {"total": total}   # 情感/标签统计均已下线


@app.get("/api/pois")
def list_pois():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "").strip()
    sql = "SELECT * FROM pois WHERE parent_id IS NULL"
    args = []
    if cat:
        sql += " AND category=?"
        args.append(cat)
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    db = get_db()
    rows = db.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["stats"] = poi_stats(db, r["id"])
        out.append(d)
    return jsonify(out)


@app.get("/api/pois/<int:pid>")
def poi_detail(pid):
    db = get_db()
    r = db.execute("SELECT * FROM pois WHERE id=?", (pid,)).fetchone()
    if not r:
        return jsonify({"error": "not found"}), 404
    d = dict(r)
    d["stats"] = poi_stats(db, pid)
    children = db.execute(
        "SELECT id,name,category FROM pois WHERE parent_id=? ORDER BY name", (pid,)).fetchall()
    d["children"] = [dict(c) for c in children]
    return jsonify(d)


@app.get("/api/pois/<int:pid>/comments")
def poi_comments(pid):
    db = get_db()
    rows = db.execute("SELECT * FROM comments WHERE poi_id=? ORDER BY id DESC", (pid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"])
        out.append(d)
    return jsonify(out)


@app.post("/api/pois/<int:pid>/comments")
def add_comment(pid):
    data = request.get_json(force=True)
    content = (data.get("content") or "").strip()
    author = current_user() or (data.get("author") or "匿名同学").strip()[:20]
    if not content:
        return jsonify({"error": "评论内容不能为空"}), 400
    db = get_db()
    if not db.execute("SELECT 1 FROM pois WHERE id=?", (pid,)).fetchone():
        return jsonify({"error": "not found"}), 404
    # ML② 内容安全：地点评论——命中即拦截，不发表
    mod, blocked = check_content(db, content, "comment", "comment",
                                 author=author or "")
    if blocked:
        return blocked
    r = analyze(content)
    cur = db.execute(
        "INSERT INTO comments(poi_id,author,content,sentiment,sentiment_prob,tags,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (pid, author, content, r["sentiment"], r["sentiment_prob"],
         json.dumps(r["tags"], ensure_ascii=False), now()))
    db.commit()
    bump_ml_index()
    return jsonify({"id": cur.lastrowid, "analysis": r, "stats": poi_stats(db, pid),
                    "moderation": mod}), 201


@app.delete("/api/pois/<int:pid>/comments/<int:cid>")
def delete_poi_comment(pid, cid):
    """
    删评（地点评论）：只能删自己发的。

    注意：地点评论允许未登录匿名发布（作者写成"匿名同学"），
    这种评论没有可验证的所有者，因此无法删除——想删请登录后再发。
    """
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再操作"}), 401
    db = get_db()
    row = db.execute("SELECT * FROM comments WHERE id=? AND poi_id=?",
                     (cid, pid)).fetchone()
    if not row:
        return jsonify({"error": "评论不存在"}), 404
    if row["author"] != user:
        if row["author"] in ("", "匿名同学"):
            return jsonify({"error": "这是一条匿名评论，没有可验证的作者，无法删除"}), 403
        return jsonify({"error": "只能删除自己的评论"}), 403
    db.execute("DELETE FROM comments WHERE id=?", (cid,))
    db.commit()
    bump_ml_index()
    return jsonify({"ok": True, "deleted": cid, "stats": poi_stats(db, pid)})


# ---------------- 地图备注（支持 POI 绑定 + 任意点标注） ----------------

@app.get("/api/pois/<int:pid>/notes")
def list_notes(pid):
    """某 POI 下的备注：公开所有人可见，私密仅作者本人可见"""
    user = current_user()
    db = get_db()
    if user:
        rows = db.execute(
            "SELECT * FROM map_notes WHERE poi_id=? AND (visibility='public' OR author=?)"
            " ORDER BY id DESC", (pid, user)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM map_notes WHERE poi_id=? AND visibility='public' ORDER BY id DESC",
            (pid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/pois/<int:pid>/notes")
def add_note(pid):
    """在 POI 上添加备注"""
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再添加备注"}), 401
    d = request.get_json(force=True)
    content = (d.get("content") or "").strip()
    visibility = d.get("visibility") if d.get("visibility") in ("public", "private") else "public"
    if not content:
        return jsonify({"error": "备注内容不能为空"}), 400
    db = get_db()
    poi = db.execute("SELECT lat,lng FROM pois WHERE id=?", (pid,)).fetchone()
    if not poi:
        return jsonify({"error": "not found"}), 404
    mod, blocked = check_content(db, content, "note", "note", author=user)
    if blocked:
        return blocked
    cur = db.execute(
        "INSERT INTO map_notes(author,content,visibility,lat,lng,poi_id,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (user, content, visibility, poi["lat"], poi["lng"], pid, now()))
    db.commit()
    bump_ml_index()
    return jsonify({"id": cur.lastrowid, "moderation": mod}), 201


@app.get("/api/map_notes")
def list_map_notes():
    """全部自由标注备注（公开 + 自己的私密），用于在地图上渲染"""
    user = current_user()
    db = get_db()
    if user:
        rows = db.execute(
            "SELECT * FROM map_notes WHERE poi_id IS NULL AND (visibility='public' OR author=?)"
            " ORDER BY id DESC", (user,)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM map_notes WHERE poi_id IS NULL AND visibility='public' ORDER BY id DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/map_notes")
def add_map_note():
    """在地图任意位置添加备注标注"""
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再标注"}), 401
    d = request.get_json(force=True)
    content = (d.get("content") or "").strip()
    visibility = d.get("visibility") if d.get("visibility") in ("public", "private") else "public"
    try:
        lat, lng = float(d.get("lat")), float(d.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "坐标无效"}), 400
    if not content:
        return jsonify({"error": "备注内容不能为空"}), 400
    db = get_db()
    mod, blocked = check_content(db, content, "note", "note", author=user)
    if blocked:
        return blocked
    cur = db.execute(
        "INSERT INTO map_notes(author,content,visibility,lat,lng,poi_id,created_at)"
        " VALUES(?,?,?,?,?,NULL,?)",
        (user, content, visibility, lat, lng, now()))
    db.commit()
    bump_ml_index()
    return jsonify({"id": cur.lastrowid, "moderation": mod}), 201


@app.delete("/api/notes/<int:nid>")
def delete_note(nid):
    user = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM map_notes WHERE id=?", (nid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["author"] != user:
        return jsonify({"error": "只能删除自己的备注"}), 403
    db.execute("DELETE FROM note_comments WHERE note_id=?", (nid,))
    db.execute("DELETE FROM map_notes WHERE id=?", (nid,))
    db.commit()
    return jsonify({"ok": True})


def get_note_or_error(nid):
    """取备注；私密备注仅作者可见（对外返回 404）"""
    db = get_db()
    row = db.execute("SELECT * FROM map_notes WHERE id=?", (nid,)).fetchone()
    if not row:
        return None, None, (jsonify({"error": "not found"}), 404)
    if row["visibility"] == "private" and row["author"] != current_user():
        return None, None, (jsonify({"error": "not found"}), 404)
    return row, dict(row), None


@app.get("/api/map_notes/<int:nid>/comments")
def note_comments_get(nid):
    """备注的评论列表（像 POI 一样可互动）"""
    row, d, err = get_note_or_error(nid)
    if err:
        return err
    db = get_db()
    out = []
    for r in db.execute("SELECT * FROM note_comments WHERE note_id=? ORDER BY id DESC",
                        (nid,)).fetchall():
        x = dict(r)
        x["tags"] = json.loads(x["tags"])
        out.append(x)
    return jsonify(out)


@app.post("/api/map_notes/<int:nid>/comments")
def note_comments_post(nid):
    """给备注发表评论（ML② 内容安全判定：命中即拦截）"""
    row, d, err = get_note_or_error(nid)
    if err:
        return err
    data = request.get_json(force=True)
    content = (data.get("content") or "").strip()
    author = current_user() or (data.get("author") or "匿名同学").strip()[:20]
    if not content:
        return jsonify({"error": "评论内容不能为空"}), 400
    db = get_db()
    mod, blocked = check_content(db, content, "comment", "note_comment",
                                 author=author or "")
    if blocked:
        return blocked
    r = analyze(content)
    cur = db.execute(
        "INSERT INTO note_comments(note_id,author,content,sentiment,sentiment_prob,tags,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (nid, author, content, r["sentiment"], r["sentiment_prob"],
         json.dumps(r["tags"], ensure_ascii=False), now()))
    db.commit()
    return jsonify({"id": cur.lastrowid, "analysis": r, "moderation": mod}), 201


@app.delete("/api/map_notes/<int:nid>/comments/<int:cid>")
def note_comments_delete(nid, cid):
    """删评（备注评论）：评论作者本人，或备注作者（楼主）都可以删"""
    row, d, err = get_note_or_error(nid)
    if err:
        return err
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再操作"}), 401
    db = get_db()
    cmt = db.execute("SELECT * FROM note_comments WHERE id=? AND note_id=?",
                     (cid, nid)).fetchone()
    if not cmt:
        return jsonify({"error": "评论不存在"}), 404
    is_own = cmt["author"] == user
    is_owner = row["author"] == user
    if not (is_own or is_owner):
        if cmt["author"] in ("", "匿名同学"):
            return jsonify({"error": "这是一条匿名评论，没有可验证的作者，无法删除"}), 403
        return jsonify({"error": "只能删除自己的评论，或删除自己备注下的评论"}), 403
    db.execute("DELETE FROM note_comments WHERE id=?", (cid,))
    db.commit()
    return jsonify({"ok": True, "deleted": cid, "by": "author" if is_own else "owner"})


@app.get("/api/search_map")
def search_map():
    """组合搜索：POI 名称/描述 + 公开备注内容（含私密备注中自己的）"""
    q = request.args.get("q", "").strip()
    user = current_user()
    db = get_db()
    pois, notes = [], []
    if q:
        like = f"%{q}%"
        for r in db.execute(
                "SELECT * FROM pois WHERE parent_id IS NULL AND (name LIKE ? OR description LIKE ?)",
                (like, like)).fetchall():
            d = dict(r)
            d["stats"] = poi_stats(db, r["id"])
            pois.append(d)
        if user:
            rows = db.execute(
                "SELECT n.*, p.name AS poi_name FROM map_notes n LEFT JOIN pois p ON n.poi_id=p.id"
                " WHERE n.content LIKE ? AND (n.visibility='public' OR n.author=?)"
                " ORDER BY n.id DESC", (like, user)).fetchall()
        else:
            rows = db.execute(
                "SELECT n.*, p.name AS poi_name FROM map_notes n LEFT JOIN pois p ON n.poi_id=p.id"
                " WHERE n.content LIKE ? AND n.visibility='public' ORDER BY n.id DESC",
                (like,)).fetchall()
        notes = [dict(r) for r in rows]
    return jsonify({"pois": pois, "notes": notes})


# ---------------- 失物招领 ----------------

def lf_dict(r):
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    return d


@app.get("/api/lostfound")
def list_lostfound():
    kind = request.args.get("kind", "").strip()
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM lostfound WHERE 1=1"
    args = []
    if kind in ("lost", "found"):
        sql += " AND kind=?"
        args.append(kind)
    if q:
        sql += " AND (title LIKE ? OR content LIKE ? OR location LIKE ?)"
        args += [f"%{q}%"] * 3
    sql += " ORDER BY (status='resolved'), id DESC"
    db = get_db()
    rows = db.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = lf_dict(r)
        # 顺带带上"这条信息已经匹配到几条"，前端卡片可以直接展示
        d["match_count"] = db.execute(
            "SELECT COUNT(*) FROM lf_matches WHERE (lost_id=? OR found_id=?)"
            " AND score>=?", (r["id"], r["id"], ml_matcher.NOTIFY_THRESHOLD)).fetchone()[0]
        out.append(d)
    return jsonify(out)


@app.get("/api/lostfound/<int:lid>/matches")
def lostfound_matches(lid):
    """某条失物/招领信息的智能匹配结果（含相似度、置信等级、匹配理由）"""
    db = get_db()
    row = db.execute("SELECT * FROM lostfound WHERE id=?", (lid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    item = lf_payload(row)
    matches = find_lf_matches(db, item, top_k=8)
    out = []
    for cand, detail in matches:
        out.append({
            "item": cand,
            "score": detail["score"],
            "score_pct": int(round(detail["score"] * 100)),
            "level": detail["level"],
            "reasons": detail["reasons"],
            "detail": detail["detail"],
            "item_type": detail["item_type"],
            "notified": detail["score"] >= ml_matcher.NOTIFY_THRESHOLD,
        })
    return jsonify({"item": item, "matches": out,
                    "notify_threshold": ml_matcher.NOTIFY_THRESHOLD})


@app.post("/api/lostfound")
def add_lostfound():
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再发帖"}), 401
    f = request.form
    kind = f.get("kind") if f.get("kind") in ("lost", "found") else "lost"
    title = (f.get("title") or "").strip()[:50]
    content = (f.get("content") or "").strip()
    location = (f.get("location") or "").strip()[:50]
    contact = (f.get("contact") or "").strip()[:50]
    poi_id = f.get("poi_id") or None
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    db = get_db()
    # ML② 内容安全（失物招领板块：留联系方式是正常行为）
    mod, blocked = check_content(db, title + " " + content, "lostfound",
                                 "lostfound", author=user)
    if blocked:
        return blocked
    # 图片上传
    img_name = ""
    img = request.files.get("image")
    if img and img.filename:
        ext = os.path.splitext(img.filename)[1].lower()
        if ext in ALLOWED_IMG:
            img_name = uuid.uuid4().hex + ext
            img.save(os.path.join(UPLOAD_DIR, img_name))
    tags = []  # 自动标签已下线
    cur = db.execute(
        "INSERT INTO lostfound(author,kind,title,content,location,poi_id,contact,image,tags,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user, kind, title, content, location, poi_id, contact, img_name,
         json.dumps(tags, ensure_ascii=False), now()))
    db.commit()
    lid = cur.lastrowid
    # ML① 发布即匹配：写入匹配关系，高置信的立刻给双方推送站内通知
    row = db.execute("SELECT * FROM lostfound WHERE id=?", (lid,)).fetchone()
    matches, pushed = match_and_notify(db, lf_payload(row))
    bump_ml_index()
    return jsonify({
        "id": lid,
        "moderation": mod,
        "match_count": len(matches),
        "matches": [{
            "id": cand["id"], "title": cand["title"], "author": cand["author"],
            "kind": cand["kind"], "score": detail["score"],
            "score_pct": int(round(detail["score"] * 100)),
            "level": detail["level"], "reasons": detail["reasons"],
            "notified": detail["score"] >= ml_matcher.NOTIFY_THRESHOLD,
        } for cand, detail in matches],
        "notified": len(pushed),
    }), 201


@app.post("/api/lostfound/<int:lid>/resolve")
def resolve_lostfound(lid):
    user = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM lostfound WHERE id=?", (lid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["author"] != user:
        return jsonify({"error": "只有发帖人可以标记已解决"}), 403
    db.execute("UPDATE lostfound SET status='resolved' WHERE id=?", (lid,))
    db.commit()
    bump_ml_index()
    return jsonify({"ok": True})


# ---------------- 站内通知中心（ML① 的推送落地） ----------------

@app.get("/api/notifications")
def list_notifications():
    """当前登录用户的通知列表 + 未读数（未登录返回空，不报错）"""
    user = current_user()
    if not user:
        return jsonify({"items": [], "unread": 0, "login_required": True})
    db = get_db()
    only_unread = request.args.get("unread") == "1"
    sql = "SELECT * FROM notifications WHERE username=?"
    if only_unread:
        sql += " AND is_read=0"
    sql += " ORDER BY id DESC LIMIT 50"
    rows = db.execute(sql, (user,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        # 把关联的另一条信息标题带出来，通知列表可以直接点开
        if d.get("related_id") and d.get("ref_type") == "lostfound":
            other = db.execute("SELECT id,title,kind,location,author FROM lostfound WHERE id=?",
                               (d["related_id"],)).fetchone()
            d["related"] = dict(other) if other else None
        items.append(d)
    unread = db.execute(
        "SELECT COUNT(*) FROM notifications WHERE username=? AND is_read=0",
        (user,)).fetchone()[0]
    return jsonify({"items": items, "unread": unread})


@app.post("/api/notifications/read")
def read_notifications():
    """标记通知已读：传 ids 只标记这些，不传则全部标记"""
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录"}), 401
    data = request.get_json(silent=True) or {}
    db = get_db()
    ids = data.get("ids")
    if isinstance(ids, list) and ids:
        marks = ",".join("?" * len(ids))
        db.execute("UPDATE notifications SET is_read=1 WHERE username=? AND id IN (%s)"
                   % marks, [user] + [int(i) for i in ids])
    else:
        db.execute("UPDATE notifications SET is_read=1 WHERE username=?", (user,))
    db.commit()
    unread = db.execute(
        "SELECT COUNT(*) FROM notifications WHERE username=? AND is_read=0",
        (user,)).fetchone()[0]
    return jsonify({"ok": True, "unread": unread})


@app.get("/api/moderation/logs")
def moderation_logs():
    """内容安全处置记录（本机演示用：仅本机访问 + 需登录）"""
    if request.remote_addr not in ("127.0.0.1", "localhost", "::1"):
        return jsonify({"error": "仅本机可查看"}), 403
    if not current_user():
        return jsonify({"error": "请先登录"}), 401
    db = get_db()
    action = request.args.get("action", "").strip()
    sql = "SELECT * FROM moderation_logs"
    args = []
    if action in ("block", "review"):
        sql += " WHERE action=?"
        args.append(action)
    sql += " ORDER BY id DESC LIMIT 50"
    rows = db.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["matched"] = json.loads(d.get("matched") or "[]")
        out.append(d)
    summary = moderation_summary(db)
    summary["label_names"] = ml_moderation.LABEL_NAMES
    return jsonify({"items": out, "summary": summary})


@app.get("/uploads/<path:fn>")
def serve_upload(fn):
    if "/" in fn or ".." in fn:
        return jsonify({"error": "bad path"}), 400
    return send_from_directory(UPLOAD_DIR, fn)


# ---------------- 校园论坛 ----------------

def post_dict(db, r, with_comments=False):
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["comment_count"] = db.execute(
        "SELECT COUNT(*) FROM forum_comments WHERE post_id=?", (r["id"],)).fetchone()[0]
    if with_comments:
        rows = db.execute(
            "SELECT * FROM forum_comments WHERE post_id=? ORDER BY id", (r["id"],)).fetchall()
        d["comments"] = [dict(c) for c in rows]
    return d


@app.get("/api/posts")
def list_posts():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        rows = db.execute(
            "SELECT * FROM posts WHERE title LIKE ? OR content LIKE ? ORDER BY id DESC",
            (f"%{q}%", f"%{q}%")).fetchall()
    else:
        rows = db.execute("SELECT * FROM posts ORDER BY id DESC").fetchall()
    return jsonify([post_dict(db, r) for r in rows])


@app.get("/api/posts/<int:pid>")
def post_detail(pid):
    db = get_db()
    r = db.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify(post_dict(db, r, with_comments=True))


@app.post("/api/posts")
def add_post():
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再发帖"}), 401
    # 兼容两种提交方式：multipart（带配图）/ JSON（无图）
    image = ""
    if request.mimetype and request.mimetype.startswith("multipart/form-data"):
        title = (request.form.get("title") or "").strip()[:50]
        content = (request.form.get("content") or "").strip()
        f = request.files.get("image")
        if f and f.filename:
            ext = os.path.splitext(f.filename)[1].lower()
            if ext in ALLOWED_IMG:
                fn = uuid.uuid4().hex + ext
                f.save(os.path.join(UPLOAD_DIR, fn))
                image = fn
            else:
                return jsonify({"error": "图片格式仅支持 png/jpg/jpeg/gif/webp"}), 400
    else:
        d = request.get_json(force=True)
        title = (d.get("title") or "").strip()[:50]
        content = (d.get("content") or "").strip()
    if not title or not content:
        return jsonify({"error": "标题和内容不能为空"}), 400
    db = get_db()
    # ML② 内容安全：论坛是广告/引战的高发区，标题+正文一起送检
    mod, blocked = check_content(db, title + " " + content, "forum", "post",
                                 author=user)
    if blocked:
        return blocked
    # 自动标签
    r = analyze(title + "。" + content)
    cur = db.execute(
        "INSERT INTO posts(author,title,content,sentiment,sentiment_prob,tags,likes,image,created_at)"
        " VALUES(?,?,?,?,?,?,0,?,?)",
        (user, title, content, r["sentiment"], r["sentiment_prob"],
         json.dumps(r["tags"], ensure_ascii=False), image, now()))
    db.commit()
    bump_ml_index()
    return jsonify({"id": cur.lastrowid, "analysis": r, "image": image,
                    "moderation": mod}), 201


@app.post("/api/posts/<int:pid>/comments")
def add_forum_comment(pid):
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再评论"}), 401
    d = request.get_json(force=True)
    content = (d.get("content") or "").strip()
    if not content:
        return jsonify({"error": "评论不能为空"}), 400
    db = get_db()
    if not db.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone():
        return jsonify({"error": "not found"}), 404
    # ML② 内容安全：论坛评论（引战/辱骂最常见的地方）——命中即拦截，不发表
    mod, blocked = check_content(db, content, "forum", "comment", author=user)
    if blocked:
        return blocked
    cur = db.execute(
        "INSERT INTO forum_comments(post_id,author,content,created_at) VALUES(?,?,?,?)",
        (pid, user, content, now()))
    db.commit()
    bump_ml_index()
    return jsonify({"id": cur.lastrowid, "moderation": mod}), 201


@app.delete("/api/posts/<int:pid>/comments/<int:cid>")
def delete_forum_comment(pid, cid):
    """删评：评论作者本人，或帖子楼主（自己帖子下的评论）都可以删"""
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再操作"}), 401
    db = get_db()
    row = db.execute("SELECT * FROM forum_comments WHERE id=? AND post_id=?",
                     (cid, pid)).fetchone()
    if not row:
        return jsonify({"error": "评论不存在"}), 404
    post = db.execute("SELECT author FROM posts WHERE id=?", (pid,)).fetchone()
    is_own = row["author"] == user
    is_owner = bool(post) and post["author"] == user
    if not (is_own or is_owner):
        return jsonify({"error": "只能删除自己的评论，或删除自己帖子下的评论"}), 403
    db.execute("DELETE FROM forum_comments WHERE id=?", (cid,))
    db.commit()
    bump_ml_index()
    return jsonify({"ok": True, "deleted": cid, "by": "author" if is_own else "owner"})


@app.post("/api/posts/<int:pid>/like")
def like_post(pid):
    db = get_db()
    db.execute("UPDATE posts SET likes=likes+1 WHERE id=?", (pid,))
    db.commit()
    n = db.execute("SELECT likes FROM posts WHERE id=?", (pid,)).fetchone()
    return jsonify({"likes": n["likes"] if n else 0})


@app.delete("/api/posts/<int:pid>")
def delete_post(pid):
    """
    删帖：只有楼主本人可以删自己的帖子。
    连带删除该帖下的所有评论（避免留下没有父帖的孤儿评论）。
    """
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再操作"}), 401
    db = get_db()
    row = db.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    if not row:
        return jsonify({"error": "帖子不存在"}), 404
    if row["author"] != user:
        return jsonify({"error": "只能删除自己发布的帖子"}), 403
    n_comments = db.execute(
        "SELECT COUNT(*) FROM forum_comments WHERE post_id=?", (pid,)).fetchone()[0]
    db.execute("DELETE FROM forum_comments WHERE post_id=?", (pid,))
    db.execute("DELETE FROM posts WHERE id=?", (pid,))
    db.commit()
    bump_ml_index()          # 帖子正文也是语义索引的语料，删了要重建
    return jsonify({"ok": True, "deleted": pid, "comments_deleted": n_comments})


# ---------------- 二手市集 ----------------

def listing_dict(r):
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    return d


@app.get("/api/market")
def list_market():
    kind = request.args.get("kind", "").strip()
    cat = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM listings WHERE 1=1"
    args = []
    if kind in ("sell", "want"):
        sql += " AND kind=?"
        args.append(kind)
    if cat:
        sql += " AND category=?"
        args.append(cat)
    if q:
        sql += " AND (title LIKE ? OR content LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY (status='done'), id DESC"
    rows = get_db().execute(sql, args).fetchall()
    return jsonify([listing_dict(r) for r in rows])


@app.get("/api/market/search")
def market_search():
    """
    ML③ 二手市集语义检索

    ?q=关键词 &kind=sell|want &category=书籍… &mode=semantic|keyword
    semantic（默认）：同义词 + 主题词 + PMI 共现扩展后做三通道融合排序
    keyword：退回原来的 LIKE 精确匹配，方便对比演示
    """
    q = request.args.get("q", "").strip()
    kind = request.args.get("kind", "").strip()
    cat = request.args.get("category", "").strip()
    mode = request.args.get("mode", "semantic").strip() or "semantic"
    limit = request.args.get("limit", type=int) or 60
    db = get_db()

    def keep(d):
        if kind in ("sell", "want") and d["kind"] != kind:
            return False
        if cat and d["category"] != cat:
            return False
        return True

    if not q:
        rows = db.execute("SELECT * FROM listings ORDER BY (status='done'), id DESC").fetchall()
        return jsonify({"items": [listing_dict(r) for r in rows if keep(dict(r))],
                        "mode": "list", "query": q, "explain": [], "expanded": []})

    if mode == "keyword":
        rows = db.execute(
            "SELECT * FROM listings WHERE title LIKE ? OR content LIKE ?"
            " ORDER BY (status='done'), id DESC",
            (f"%{q}%", f"%{q}%")).fetchall()
        items = []
        for r in rows:
            d = listing_dict(r)
            if keep(d):
                d["score"] = 1.0
                d["matched_terms"] = []
                items.append(d)
        return jsonify({"items": items, "mode": "keyword", "query": q,
                        "explain": [], "expanded": []})

    index = market_semantic_index(db)
    result = index.search(q, top_k=limit)
    by_id = {}
    for r in db.execute("SELECT * FROM listings").fetchall():
        by_id[r["id"]] = listing_dict(r)
    items = []
    for hit in result["items"]:
        d = by_id.get(hit["id"])
        if not d or not keep(d):
            continue
        d = dict(d)
        d["score"] = hit["score"]
        d["score_pct"] = int(round(hit["score"] * 100))
        d["matched_terms"] = hit["matched_terms"]
        d["channels"] = {"word": hit["word"], "word_coverage": hit["word_coverage"],
                         "char": hit["char"], "char_coverage": hit["char_coverage"]}
        items.append(d)
    return jsonify({
        "items": items, "mode": "semantic", "query": q,
        "explain": result["explain"], "expanded": result["expanded"],
        "query_terms": result["query_terms"],
        "index_docs": len(index.docs), "corpus_docs": index.corpus_docs,
    })


@app.post("/api/market")
def add_listing():
    user = current_user()
    if not user:
        return jsonify({"error": "请先登录后再发布"}), 401
    f = request.form
    kind = f.get("kind") if f.get("kind") in ("sell", "want") else "sell"
    title = (f.get("title") or "").strip()[:50]
    content = (f.get("content") or "").strip()
    category = f.get("category") if f.get("category") in (
        "书籍", "电子产品", "生活用品", "票券", "其他") else "其他"
    contact = (f.get("contact") or "").strip()[:50]
    try:
        price = float(f.get("price")) if f.get("price") not in (None, "") else None
    except ValueError:
        price = None
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    db = get_db()
    # ML② 内容安全（market 语境：留联系方式是合法的，只有刷单/代写/贷款等强信号才拦）
    mod, blocked = check_content(db, title + " " + content, "market", "listing",
                                 author=user)
    if blocked:
        return blocked
    img_name = ""
    img = request.files.get("image")
    if img and img.filename:
        ext = os.path.splitext(img.filename)[1].lower()
        if ext in ALLOWED_IMG:
            img_name = uuid.uuid4().hex + ext
            img.save(os.path.join(UPLOAD_DIR, img_name))
    tags = []  # 自动标签已下线
    cur = db.execute(
        "INSERT INTO listings(author,kind,title,content,category,price,contact,image,tags,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user, kind, title, content, category, price, contact, img_name,
         json.dumps(tags, ensure_ascii=False), now()))
    db.commit()
    bump_ml_index()
    return jsonify({"id": cur.lastrowid, "moderation": mod}), 201


@app.post("/api/market/<int:lid>/done")
def done_listing(lid):
    user = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["author"] != user:
        return jsonify({"error": "只有发布人可以标记"}), 403
    db.execute("UPDATE listings SET status='done' WHERE id=?", (lid,))
    db.commit()
    bump_ml_index()
    return jsonify({"ok": True})


# ---------------- 全站统计 ----------------

@app.get("/api/stats")
def global_stats():
    db = get_db()
    n_poi = db.execute("SELECT COUNT(*) FROM pois WHERE parent_id IS NULL").fetchone()[0]
    n_room = db.execute("SELECT COUNT(*) FROM pois WHERE category='classroom'").fetchone()[0]
    n_cmt = db.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    n_post = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    n_lf = db.execute("SELECT COUNT(*) FROM lostfound WHERE status='open'").fetchone()[0]
    n_note = db.execute("SELECT COUNT(*) FROM map_notes").fetchone()[0]
    n_user = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    n_market = db.execute("SELECT COUNT(*) FROM listings WHERE status='open'").fetchone()[0]
    # ML 相关统计（首页/答辩演示用）
    n_match = db.execute(
        "SELECT COUNT(*) FROM lf_matches WHERE score>=?",
        (ml_matcher.NOTIFY_THRESHOLD,)).fetchone()[0]
    n_notify = db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    out = {
        "poi_count": n_poi, "classroom_count": n_room,
        "comment_count": n_cmt,
        "post_count": n_post, "lostfound_open": n_lf,
        "note_count": n_note, "user_count": n_user,
        "market_open": n_market,
        "lf_match_count": n_match,
        "notification_count": n_notify,
    }
    out.update(moderation_summary(db))
    return jsonify(out)


# ---------------- 前端页面（与 API 同端口：一个进程、一个端口，换电脑最省事） ----------------

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/<path:fname>")
def web_static(fname):
    return send_from_directory(WEB_DIR, fname)


@app.get("/m/")
def mobile_index():
    return send_from_directory(MOBILE_DIR, "index.html")


@app.get("/m/<path:fname>")
def mobile_static(fname):
    return send_from_directory(MOBILE_DIR, fname)


def _startup_log(msg):
    """无控制台模式下的启动日志（排查用）"""
    try:
        with open(os.path.join(ROOT_DIR, "_startup.log"), "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def _launch_browser(url, size):
    """打开页面：用 cmd start 启动 Edge 应用模式（独立窗口），兜底默认浏览器"""
    import subprocess

    _startup_log("准备打开页面: " + url)
    edge = None
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cand = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.isfile(cand):
                edge = cand
                break
    # 独立窗口：独立配置目录，避免被已开着的 Edge 会话接管
    profile = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "NUAA-Campus-App")
    if edge:
        cmd = (
            f'start "" "{edge}" --app="{url}" '
            f'--user-data-dir="{profile}" --no-first-run --no-default-browser-check '
            f'--window-size={size}'
        )
        try:
            subprocess.Popen(cmd, shell=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _startup_log("已用 start 启动 Edge 独立窗口: " + url)
            return True
        except Exception as e:
            _startup_log("start 启动 Edge 失败: %s" % e)
    # 兜底：系统默认浏览器
    try:
        os.startfile(url)
        _startup_log("已用默认浏览器打开: " + url)
        return True
    except Exception as e:
        _startup_log("默认浏览器打开失败: %s" % e)
    return False


def _open_client(delay=2.5):
    """exe 模式：服务起来后自动打开页面（--mobile 打开手机版）"""
    import threading
    import time

    mobile = "--mobile" in sys.argv
    url = "http://127.0.0.1:5000/m/" if mobile else "http://127.0.0.1:5000/"
    size = "430,870" if mobile else "1100,760"

    def run():
        time.sleep(delay)
        _launch_browser(url, size)

    threading.Thread(target=run, daemon=True).start()


def _port_free(port=5000):
    """检测端口是否空闲：能连上说明已有服务在监听（Windows 下 bind+REUSEADDR 不可靠）"""
    import socket
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        return False       # 已被占用
    except OSError:
        return True        # 空闲
    finally:
        s.close()


def _hide_console():
    """exe 启动后隐藏自己的控制台窗口（日志仍在，但不挡视线）"""
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


if __name__ == "__main__":
    init_db()
    if FROZEN:
        _hide_console()
    _startup_log("启动 exe，参数: " + " ".join(sys.argv[1:]))
    client_url = None
    if FROZEN and "--no-browser" not in sys.argv:
        mobile = "--mobile" in sys.argv
        client_url = "http://127.0.0.1:5000/m/" if mobile else "http://127.0.0.1:5000/"
        size = "430,870" if mobile else "1100,760"
        if not _port_free():
            # 服务已在运行：同步打开页面后退出（不开第二个服务）
            _startup_log("端口 5000 已被占用（服务已在运行）")
            _launch_browser(client_url, size)
            time.sleep(0.8)
            sys.exit(0)
        _startup_log("端口空闲，开始启动服务")
        _open_client()
    try:
        app.run(host="127.0.0.1", port=5000, debug=False)
    except Exception as e:
        _startup_log("服务异常退出: %s" % e)
        if client_url:
            _launch_browser(client_url, size)
