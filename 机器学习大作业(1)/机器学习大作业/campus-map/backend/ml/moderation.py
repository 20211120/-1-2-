# -*- coding: utf-8 -*-
"""
内容安全识别：垃圾广告 / 引战挑事 / 不文明用语
================================================================
两层判别，最后取并集（谁的分高听谁的），并输出命中证据：

  第一层 规则层（RuleEngine）
      面向校园场景整理的高精度关键词/正则，权重可累加。
      —— 优点：可解释、可维护；缺点：写法一变就漏。

  第二层 统计层（TfidfCentroidClassifier）
      字符 1-2gram TF-IDF 特征的"类质心 + 余弦相似度"分类器（Rocchio），
      训练语料是下面这份人工标注的 160+ 条校园短文本（正常/广告/引战/不文明）。

      为什么不是朴素贝叶斯：本语料只有 160 条、字级词表却有数千维，NB 的
      "未登录特征"平滑项会主导打分，5 折交叉验证准确率只有 0.41（见
      CharNBBayes，作为对比基线保留）。换成类质心后升到 0.85，且 cosine
      分数天然可解释（"这条文本离广告质心 0.42，离正常质心 0.08"）。

  上下文感知
      二手市集 / 失物招领里"留联系方式"是合法字段，因此这两类板块会
      大幅削弱"留微信/留 QQ"这类规则的权重，并把模型给出的广告概率整体
      压低（正常买卖贴本来就长得像广告），只保留刷单、代写、贷款、
      办证等强风险信号，避免把正常交易信息误判成广告。

  动作分级（没有人工审核队列）
      risk ≥ 0.50 → block  直接拦截，返回 400 说明原因，不允许发表
      否则        → allow  正常发布

  阈值 0.50 的标定依据（复算见 python -m ml.eval_all）：
      164 条标注语料上，正常内容 risk 的最大值只有 0.282；取 0.50 时
      广告召回 0.95、不文明召回 0.97、引战召回 0.78，正常误伤率 0.000。
"""

import io
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict

try:                                    # 作为包导入（app.py / exe 打包）
    from .textutil import _cosine, char_ngrams, softmax
except ImportError:                     # 直接 python moderation.py 跑自检
    from textutil import _cosine, char_ngrams, softmax

LABELS = ["normal", "ad", "flame", "uncivil"]
LABEL_NAMES = {
    "normal": "正常内容",
    "ad": "垃圾广告",
    "flame": "引战挑事",
    "uncivil": "不文明用语",
}
LABEL_HINTS = {
    "ad": "校园平台禁止刷单兼职、代写代考、贷款办证、引流导购等广告信息",
    "flame": "请就事论事，避免院系/地域/群体的对立与人身挑衅",
    "uncivil": "请使用文明用语，辱骂与人身攻击会被拦截",
    "normal": "",
}

# ================================================================ 训练语料
# 每类都刻意包含"容易混淆"的样本：正常类里有大量吐槽/负面情绪、含联系方式的
# 正常交易；广告类里有伪装成正常求助的引流；引战与不文明互相交叉。

CORPUS = [
    # ---------------- 正常内容（60） ----------------
    ("图书馆四楼自习区很安静，插座也多，适合复习", "normal"),
    ("一食堂的红烧肉真的绝了，便宜又好吃", "normal"),
    ("期末周图书馆座位太紧张了，八点半到就没位置", "normal"),
    ("体育中心淋浴间又没热水了，希望后勤修一下", "normal"),
    ("三号楼302的投影仪有点模糊，坐后排看不清", "normal"),
    ("东区操场夜跑很舒服，就是灯光有点暗", "normal"),
    ("请问大物实验报告什么时候交", "normal"),
    ("有没有同学一起组队打羽毛球", "normal"),
    ("出二手高数教材，同济第七版，九成新，需要的同学留言", "normal"),
    ("求购一辆二手自行车，预算150以内，校内自取", "normal"),
    ("出九成新机械键盘，樱桃红轴，用了半年", "normal"),
    ("捡到一张校园卡，已交到一食堂服务台，失主凭学号认领", "normal"),
    ("在一号楼1-205丢失一串钥匙，带飞机挂件，捡到请联系我", "normal"),
    ("考研数学复习全书转让，只做了前50页笔记", "normal"),
    ("有人知道选课系统什么时候开放吗", "normal"),
    ("食堂阿姨打菜手不抖，分量足，好评", "normal"),
    ("宿舍楼下的快递柜爆满了，快递堆了一地", "normal"),
    ("二号楼自习教室空调不太制冷，夏天有点热", "normal"),
    ("求助：图书馆研讨间怎么预约", "normal"),
    ("樱花广场的樱花开了，拍照很好看", "normal"),
    ("一食堂新开的麻辣香锅味道正宗，建议错峰去", "normal"),
    ("体育馆器材比较新，晚上人多要排队", "normal"),
    ("教务处电话一直打不通，有同学知道怎么联系吗", "normal"),
    ("大四出清，专业课本打包出，便宜甩", "normal"),
    ("求一个二手台灯，宿舍用，价格好商量", "normal"),
    ("图书馆闭馆时间是不是提前了", "normal"),
    ("四号楼阶梯教室的音响有杂音", "normal"),
    ("找一起上早八的同学，可以互相提醒", "normal"),
    ("学校的打印店涨到两毛一张了", "normal"),
    ("篮球场地胶不错，打球不怎么伤膝盖", "normal"),
    ("想问问寒假图书馆开不开放", "normal"),
    ("出个充电宝，10000毫安，几乎没用过", "normal"),
    ("求购考研英语真题，有笔记也行", "normal"),
    ("一食堂排队太长了，人挤人", "normal"),
    ("学校附近有没有修自行车的店", "normal"),
    ("宿舍热水几点停，昨天十一点就没热水了", "normal"),
    ("教学楼的自助售货机吞钱不出货，无语", "normal"),
    ("有没有人拼车去禄口机场", "normal"),
    ("二号楼201教室的桌椅有点晃", "normal"),
    ("图书馆占座现象太严重了，一本书占一整天", "normal"),
    ("大家好，我是今年新来的研究生，请多指教", "normal"),
    ("建议食堂增加清淡窗口", "normal"),
    ("求推荐学校附近便宜又好吃的店", "normal"),
    ("出羽毛球拍一支，尤尼克斯，八成新，可小刀", "normal"),
    ("谁丢了眼镜，我在操场捡到一副黑框眼镜", "normal"),
    ("请问选修课什么时候出成绩", "normal"),
    ("实验楼晚上十点关门，做实验的同学注意时间", "normal"),
    ("学校VPN今晚维护，校园网可能断一下", "normal"),
    ("有一起备考四六级的同学吗，可以互相监督", "normal"),
    ("洗衣房的三号机器坏了，投币不退", "normal"),
    ("图书馆新到的考研资料在二楼新书区", "normal"),
    ("想收一本机器学习的西瓜书，二手的就行", "normal"),
    ("体育课选游泳的人好多，根本抢不到", "normal"),
    ("主楼前面的草坪很适合晒太阳看书", "normal"),
    ("食堂的菜又贵又难吃，避雷", "normal"),
    ("自习室总有人占座不来，资源浪费严重", "normal"),
    ("健身房器械老旧，有的还坏了没人修", "normal"),
    ("教室太闷了，通风不好容易犯困", "normal"),
    ("校园卡丢了怎么办，需要去哪里补办", "normal"),
    ("有人捡到一个黑色保温杯吗，落在图书馆了", "normal"),

    # ---------------- 垃圾广告（40） ----------------
    ("招兼职，日结200，加微信详聊", "ad"),
    ("刷单返利，一单一结，学生党可做，加我微信", "ad"),
    ("论文代写包过，专业团队，价格实惠，加QQ详聊", "ad"),
    ("四六级答案，考前必过，需要的加微信", "ad"),
    ("无抵押贷款，学生也能贷，秒下款，扫码咨询", "ad"),
    ("办证刻章，各种证件当天出，加微信", "ad"),
    ("低价代购，一手货源，厂家直销，加微信下单", "ad"),
    ("点击链接领取优惠券，全场五折 www.abc.com", "ad"),
    ("加QQ群123456789，每天更新兼职信息", "ad"),
    ("招聘校园代理，月入过万，非诚勿扰", "ad"),
    ("免费领取考研资料，加微信私发", "ad"),
    ("花呗套现，信用卡代还，手续简单", "ad"),
    ("长期有效，量大优惠，需要私聊", "ad"),
    ("出售各种账号，含校园网账号，加微信", "ad"),
    ("招商加盟，校园奶茶店合作，扫码进群", "ad"),
    ("朋友圈点赞兼职，一单5元，日结", "ad"),
    ("出租床位，一个月即可，加微信联系", "ad"),
    ("卖答案，期末考试各科都有，加群", "ad"),
    ("微商货源，招代理，扫码了解详情", "ad"),
    ("领券购物返现，加微信群", "ad"),
    ("有需要发票的联系我，正规渠道", "ad"),
    ("专业代写作业，包修改，加QQ", "ad"),
    ("兼职打字员，日结工资，会用电脑即可", "ad"),
    ("校园快递代取，扫码下单，首单免费", "ad"),
    ("低价出售二手手机，量大从优，加微信13800000000", "ad"),
    ("借款平台推荐，当天放款，不看征信", "ad"),
    ("免费送流量卡，扫码登记", "ad"),
    ("招募校园推广，时间自由，日结300", "ad"),
    ("出售考试答案和内部资料，加微信详聊", "ad"),
    ("需要实习证明的联系我，包盖章", "ad"),
    ("加微信送考研网课，全程免费", "ad"),
    ("秒批信用卡，额度过万，扫码申请", "ad"),
    ("招收代理，一件代发，无需囤货", "ad"),
    ("微信引流服务，涨粉加好友，效果保证", "ad"),
    ("长期收校园卡出租，一天20，加微信", "ad"),
    ("关注公众号领红包，每天可领，扫码进群", "ad"),
    ("代刷网课，包过，价格便宜，加微信", "ad"),
    ("全网最低价，加VX看货，支持货到付款", "ad"),
    ("急招话务员，日结150，微信联系", "ad"),
    ("帮你改成绩，有内部渠道，私聊", "ad"),

    # ---------------- 引战挑事（32） ----------------
    ("计算机学院的就这水平，连个代码都写不明白", "flame"),
    ("你们文科生懂什么，别在这儿瞎说", "flame"),
    ("这种帖子也能发出来，管理员是不是不管事", "flame"),
    ("呵呵，还有人为食堂洗地，是不是收钱了", "flame"),
    ("不服来辩，我就说一食堂最难吃", "flame"),
    ("笑死，某些人连这点常识都没有还敢发言", "flame"),
    ("南航的学生就这素质，真丢人", "flame"),
    ("楼上就是个杠精，专门找人吵架", "flame"),
    ("有本事你去跟学校反映，别在这打字", "flame"),
    ("你们这些天天占座的人早晚遭报应", "flame"),
    ("我笑了，还有人说游泳池干净", "flame"),
    ("某些院系就是学校亲儿子，资源全占了", "flame"),
    ("别洗了，一看就是水军", "flame"),
    ("你以为你是谁啊，说话阴阳怪气", "flame"),
    ("就这也叫优秀学生，我笑了", "flame"),
    ("建议把这种帖子删了，发帖人明显带节奏", "flame"),
    ("你们南方人就是矫情，一点小事就抱怨", "flame"),
    ("研究生就这水平，还不如我们本科生", "flame"),
    ("又来带节奏了，是不是闲得慌", "flame"),
    ("有些人真是站着说话不腰疼", "flame"),
    ("你们这群键盘侠也就网上厉害", "flame"),
    ("谁在图书馆外放音乐的，出来聊聊", "flame"),
    ("不服气的可以私聊约球，别在网上嘴硬", "flame"),
    ("这种老师也能留在学校，误人子弟", "flame"),
    ("说食堂好吃的怕是没吃过饭吧", "flame"),
    ("一群人在那装学霸，看着就烦", "flame"),
    ("别总拿南航说事，有本事考清华去", "flame"),
    ("楼上别对线了，你根本不懂", "flame"),
    ("我劝有些人积点口德，别老挑事", "flame"),
    ("敢不敢报上你的学院，我们当面说", "flame"),
    ("是不是傻，这种事也信", "flame"),
    ("你们这些外地人别来抢我们的资源", "flame"),

    # ---------------- 不文明用语（32） ----------------
    ("你他妈算什么东西，也配评论", "uncivil"),
    ("傻逼东西，滚出去", "uncivil"),
    ("你妈的，谁再占座我砸了他桌子", "uncivil"),
    ("去死吧，恶心玩意", "uncivil"),
    ("操你妈，谁在图书馆说话", "uncivil"),
    ("nmsl，闭嘴", "uncivil"),
    ("这老师就是个废物，赶紧滚蛋", "uncivil"),
    ("你就是个垃圾，别在这丢人", "uncivil"),
    ("贱人就是矫情，别来我这找存在感", "uncivil"),
    ("你脑子有病吧，滚", "uncivil"),
    ("一群畜生，占着座不来人", "uncivil"),
    ("傻逼管理员，收了多少钱", "uncivil"),
    ("你妈死了吗，这么晚还发消息", "uncivil"),
    ("我操，谁把我车胎放了气，找死", "uncivil"),
    ("sb一个，别回我", "uncivil"),
    ("狗东西，别理这种人", "uncivil"),
    ("白痴才去上这门课", "uncivil"),
    ("你他妈的有没有素质", "uncivil"),
    ("滚你妈的，别在我帖子里逼逼", "uncivil"),
    ("弱智吧你，这都看不懂", "uncivil"),
    ("一群神经病在食堂插队", "uncivil"),
    ("你就是个废物点心，什么都不行", "uncivil"),
    ("死开，别挡道", "uncivil"),
    ("你算个屁，少管闲事", "uncivil"),
    ("智障发言，建议拉黑", "uncivil"),
    ("恶心死了，你这种人怎么还有脸发帖", "uncivil"),
    ("傻狗，想找事是吗", "uncivil"),
    ("你妈没教过你排队吗", "uncivil"),
    ("操，什么人啊这是，真想抽他", "uncivil"),
    ("滚吧，这里不欢迎你这种贱货", "uncivil"),
    ("废物一个，赶紧退学", "uncivil"),
    ("tmd 谁把我的书拿走了，别装死", "uncivil"),
]


# ================================================================ 规则层

def _r(pattern, weight, name, tag=""):
    return (re.compile(pattern, re.I), weight, name, tag)


# tag="contact" 的规则在二手市集/失物招领语境下会被削弱（留联系方式是合法的）
RULES = {
    "ad": [
        _r(r"(加|扫)(微信|vx|v信|qq|群|码)", 0.55, "加微信/加群/扫码", "contact"),
        _r(r"(微信|vx|v信|威信)(同号|同 号|[:：]?\s*[a-z0-9_\-]{4,})", 0.35, "微信号引流", "contact"),
        _r(r"(兼职|日结|刷单|返利|躺赚|月入过万|轻松赚钱|学生党可做)", 0.62, "兼职刷单"),
        _r(r"(代写|代考|代刷|包过|保过|改成绩|卖答案|考试答案|内部资料)", 0.68, "代写代考"),
        _r(r"(贷款|借款|放款|免息|低息|套现|代还|不看征信|秒批)", 0.62, "贷款套现"),
        _r(r"(办证|假证|刻章|代开发票|实习证明|包盖章)", 0.60, "办证/证明"),
        _r(r"(优惠券|返现|领券|免费领|免费送|扫码领|扫码申请|扫码登记)", 0.52, "扫码领券"),
        _r(r"(https?://|www\.|\.com|\.cn|\.xyz|\.top)", 0.55, "外链"),
        _r(r"(代理|招商|加盟|一手货源|厂家直销|一件代发|无需囤货|微商)", 0.50, "招商代理"),
        _r(r"(私聊|私我|加我|详聊|咨询|dd我|联系v|联系微信)", 0.38, "引导私聊", "contact"),
        _r(r"(\d{6,})", 0.22, "疑似联系方式", "contact"),
        _r(r"(微信号|电话|手机号|qq)\s*[:：]?\s*\d{5,}", 0.35, "联系方式字段", "contact"),
        _r(r"(招人|招聘|招募|小时工|话务员|打字员|校园推广|校园代理)", 0.45, "招聘招募"),
        _r(r"(出借|出租)(校园卡|床位|账号|学号)", 0.50, "出租账号/床位"),
        _r(r"[【\[].{2,10}[】\]].{0,12}[【\[]", 0.25, "营销排版"),
        _r(r"[!！?？~～]{4,}", 0.20, "刷屏标点"),
    ],
    "flame": [
        _r(r"(不服|有种|敢不敢|来辩|对线|约架|当面说|出来聊)", 0.55, "挑衅对线"),
        _r(r"(你们|楼上|某些人|这群人|这些人).{0,8}(就|只会|都是|也配|算什么)", 0.50, "群体指责"),
        _r(r"(计算机|自动化|航空|材料|文科|理科|艺术|体育|研究生|本科生|专科)"
           r"(院|系|学院)?(的|生)?(都|就|只会|全是|也配)", 0.55, "院系对立"),
        _r(r"(南方人|北方人|本地人|外地人|农村|小地方)(都|就|全|别|滚)", 0.55, "地域对立"),
        _r(r"(引战|对线|开喷|喷子|杠精|键盘侠|水军|洗地|带节奏|站队|挑事)", 0.48, "引战词"),
        _r(r"(笑死|呵呵|我笑了|就这|懂哥|小丑|巨婴)", 0.40, "嘲讽"),
        _r(r"(是不是傻|是不是有病|怕是没|没吃过饭|闭嘴吧)", 0.45, "贬损反问"),
        _r(r"(丢人|误人子弟|素质|什么水平|就这水平)", 0.42, "素质指责"),
        _r(r"(抢我们|占我们|滚出|别来)(资源|学校|南航|宿舍)", 0.50, "排挤"),
    ],
    "uncivil": [
        _r(r"(傻逼|傻B|傻比|沙比|煞笔|sb|SB)", 0.85, "辱骂-sb"),
        _r(r"(他妈|你妈|妈的|tmd|tnd|nmsl|nm|草你|操你|卧槽|我操|妈的死)", 0.80, "脏话"),
        _r(r"(滚蛋|滚出去|滚你|滚吧|死开|去死|找死|打死|抽他|砸)", 0.75, "暴力威胁"),
        _r(r"(智障|脑残|弱智|白痴|蠢货|傻子|神经病)", 0.72, "智力侮辱"),
        _r(r"(贱人|贱货|婊|畜生|狗东西|傻狗|废物|垃圾东西)", 0.70, "人格侮辱"),
        _r(r"(你|这人|这群人|他|她|楼上)(就|也|真)?(是|个)?(垃圾|废物|狗|屁)", 0.60, "指向性侮辱"),
        _r(r"(逼逼|逼逼赖赖|装死|找存在感)", 0.55, "粗俗表达"),
    ],
}

# 校园正常语境的"减震器"：出现这些词且没有任何强广告信号时，压低广告分
CAMPUS_HINTS = ["食堂", "图书馆", "自习", "选修", "考试", "作业", "老师", "课程",
                "宿舍", "操场", "快递", "校园卡", "失物", "捡到", "丢失", "求购",
                "出售", "二手", "教材", "转让", "成色", "自取", "维修", "预约",
                "体育馆", "浴室", "热水", "选课", "成绩", "实验", "论文", "考研"]

# 上下文：允许留联系方式的板块
CONTACT_OK_CONTEXTS = ("market", "lostfound")


def _norm_for_rule(text):
    s = unicodedata.normalize("NFKC", str(text or "")).lower()
    return s


# 吐槽 / 举报别人的广告行为（而不是自己发广告）的表达
_COMPLAINT = re.compile(r"(举报|吐槽|骚扰|别信|别加|影响学习|拉群|骗子|广告太多|刷屏|天天发)")


class RuleEngine:
    """
    规则层：命中权重用"噪声或"(noisy-OR) 合并 —— 1-Π(1-wᵢ)

    比直接相加更合理：单条弱规则不会把分数顶到满分（相加容易饱和成 1.0，
    失去分段意义），多条独立证据同时出现才会迅速逼近 1.0。

    额外两条"别误伤"的处理：
      · 二手/失物板块里，留联系方式的规则权重降到 1/4；
      · 只是"吐槽/举报别人的广告"（且没有任何强风险信号）时，广告分减半。
    """

    def score(self, text, context="forum"):
        s = _norm_for_rule(text)
        scores = {}
        strong = {}          # 每个类别里"非联系方式类"规则的累计权重
        matched = []
        for label, rules in RULES.items():
            remain = 1.0
            strong_sum = 0.0
            for pattern, weight, name, tag in rules:
                if not pattern.search(s):
                    continue
                w = weight
                if tag == "contact" and context in CONTACT_OK_CONTEXTS:
                    w *= 0.25          # 二手/失物板块里留联系方式是正常行为
                w = max(0.0, min(0.95, w))
                remain *= (1.0 - w)
                if tag != "contact":
                    strong_sum += w
                matched.append((label, name, round(w, 3)))
            scores[label] = round(min(1.0, 1.0 - remain), 4)
            strong[label] = strong_sum
        # "有人在图书馆加微信拉群，影响学习" 这类是在吐槽广告，不是发广告
        if strong.get("ad", 0.0) <= 0.0 and _COMPLAINT.search(s):
            scores["ad"] = round(scores["ad"] * 0.5, 4)
        return scores, matched


# ================================================================ 统计层

class TfidfCentroidClassifier:
    """
    TF-IDF 类质心分类器（Rocchio）

    训练：对每一类的所有文本求 TF-IDF 向量的平均方向，得到"类质心"
    预测：算待判文本与 4 个质心的余弦相似度，softmax 归一成概率

    在 164 条小语料上，它比朴素贝叶斯稳得多——因为 cosine 只看"命中的
    特征贡献多少"，不会像 NB 那样被大量未登录特征的平滑项牵着走。
    """

    def __init__(self, ngram_range=(1, 2), min_df=1, temperature=6.0):
        self.ngram_range = ngram_range
        self.min_df = min_df
        self.temperature = temperature
        self.labels = list(LABELS)
        self.idf = {}
        self.centroids = {}
        self.trained = 0

    def train(self, samples):
        docs = [(char_ngrams(t, self.ngram_range), lb) for t, lb in samples
                if lb in self.labels]
        self.trained = len(docs)
        df = Counter()
        for grams, _ in docs:
            df.update(grams.keys())
        self.idf = {t: math.log((1.0 + len(docs)) / (1.0 + d)) + 1.0
                    for t, d in df.items() if d >= self.min_df}

        acc = {lb: defaultdict(float) for lb in self.labels}
        num = Counter()
        for grams, lb in docs:
            vec = self._vectorize(grams)
            nrm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            for t, v in vec.items():
                acc[lb][t] += v / nrm
            num[lb] += 1
        for lb in self.labels:
            v = {t: x / max(1, num[lb]) for t, x in acc[lb].items()}
            self.centroids[lb] = (v, math.sqrt(sum(y * y for y in v.values())) or 1.0)
        return self

    def _vectorize(self, grams):
        return {t: (1.0 + math.log(c)) * self.idf[t]
                for t, c in grams.items() if t in self.idf}

    def similarities(self, text):
        vec = self._vectorize(char_ngrams(text, self.ngram_range))
        nrm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {lb: _cosine(vec, nrm, self.centroids[lb][0], self.centroids[lb][1])
                for lb in self.labels}

    def predict_proba(self, text):
        sims = self.similarities(text)
        return softmax({k: v * self.temperature for k, v in sims.items()})


class CharNBBayes:
    """
    字符 2-3gram 多项式朴素贝叶斯（纯 Python）—— 对比基线，线上不使用。

    保留它的价值在于课程对比实验：同样语料下 NB 只有 0.41 的 5 折准确率，
    原因是字级特征太稀疏，未登录特征的平滑概率主导了打分。
    """

    def __init__(self, ngram_range=(2, 3), alpha=0.30, by_length=True):
        self.ngram_range = ngram_range
        self.alpha = alpha
        self.by_length = by_length
        self.labels = list(LABELS)
        self.log_prior = {}
        self.log_lik = {}          # label -> {feature: log P}
        self.default_log = {}      # label -> 未登录特征的兜底 log P
        self.vocab = 0
        self.trained = 0

    def train(self, samples):
        counts = {lb: Counter() for lb in self.labels}
        totals = Counter()
        docs = Counter()
        for text, label in samples:
            if label not in counts:
                continue
            grams = char_ngrams(text, self.ngram_range)
            counts[label].update(grams)
            totals[label] += sum(grams.values())
            docs[label] += 1
        vocab = set()
        for lb in self.labels:
            vocab |= set(counts[lb].keys())
        self.vocab = len(vocab)
        self.trained = sum(docs.values())
        self.log_prior = {lb: -1.0 * 0 for lb in self.labels}   # 均匀先验
        for lb in self.labels:
            denom = totals[lb] + self.alpha * max(1, self.vocab)
            self.log_lik[lb] = {
                t: (c + self.alpha) / denom for t, c in counts[lb].items()}
            self.default_log[lb] = self.alpha / denom
        return self

    def predict_proba(self, text):
        import math

        grams = char_ngrams(text, self.ngram_range)
        n = sum(grams.values()) or 1
        raw = {}
        for lb in self.labels:
            lik = self.log_lik[lb]
            default = self.default_log[lb]
            total = 0.0
            for t, c in grams.items():
                p = lik.get(t, default)
                total += c * math.log(p) if p > 0 else 0.0
            raw[lb] = total / n if self.by_length else total
        return softmax(raw)


# ================================================================ 对外接口

_engine = RuleEngine()
_clf = None
_nb = None
_metrics = None

BLOCK_LINE = 0.50       # 唯一判定线：≥ 该分数直接拦截（没有中间"待复核"档）
# 历史遗留：早期版本的 0.50~0.72 会"放行但转人工复核"，现已取消，
# moderation_logs 里 action='review' 的记录只会来自旧数据。


def get_classifier():
    """线上使用的统计层模型：TF-IDF 类质心"""
    global _clf
    if _clf is None:
        _clf = TfidfCentroidClassifier(ngram_range=(1, 2)).train(CORPUS)
    return _clf


def get_nb_baseline():
    """对比基线：朴素贝叶斯（只在评估脚本里用）"""
    global _nb
    if _nb is None:
        _nb = CharNBBayes().train(CORPUS)
    return _nb


def moderate(text, context="forum", extra_fields=()):
    """
    判定一段文本。

    text: 标题 / 正文 / 评论内容
    context: forum / market / lostfound / note / comment
    extra_fields: 需要一起送检的附加字段（例如帖子标题+正文拼接）
    返回 dict（见模块头注释）
    """
    payload = " ".join([str(text or "")] + [str(x or "") for x in extra_fields])
    if not payload.strip():
        return {"action": "allow", "label": "normal", "label_name": "正常内容",
                "risk": 0.0, "scores": {}, "matched": [], "message": ""}

    rule_scores, matched = _engine.score(payload, context=context)
    prob = dict(get_classifier().predict_proba(payload))

    # 校园语境保护：规则层完全没命中广告、正文里又都是校园词时，
    # 把模型给出的广告概率压一压，避免把"吐槽广告/讨论二手"误伤成广告。
    if (rule_scores.get("ad", 0.0) <= 0.0
            and any(h in _norm_for_rule(payload) for h in CAMPUS_HINTS)):
        prob["ad"] = prob.get("ad", 0.0) * 0.6

    # 二手市集 / 失物招领里的"广告"只指刷单代写这类违规信息，而正常买卖贴
    # 本来长得就像广告（标题+描述+联系方式），所以在这两个板块把模型的广告
    # 概率整体压低，给"直接拦截"留出安全边际（违规内容仍由强规则拦住）。
    if context in CONTACT_OK_CONTEXTS:
        prob["ad"] = prob.get("ad", 0.0) * 0.8

    # 两层取并集：规则命中或模型判断为风险类，都以高分那条为准
    scores = {}
    for lb in LABELS:
        scores[lb] = round(max(rule_scores.get(lb, 0.0), prob.get(lb, 0.0)), 4)

    # 取非正常类里风险最高的那一个作为判定；过线即拦截，没有人工复核档
    top_label = max(("ad", "flame", "uncivil"), key=lambda k: scores[k])
    risk = scores[top_label]
    if risk >= BLOCK_LINE:
        action = "block"
    else:
        action = "allow"
        top_label = "normal"

    hits = sorted({name for lb, name, _ in matched if lb == top_label})
    if action == "allow":
        message = ""
    else:
        detail = ("（命中：" + "、".join(hits) + "）") if hits else ""
        message = "内容涉嫌%s%s，已被拦截" % (LABEL_NAMES[top_label], detail)

    return {
        "action": action,
        "label": top_label,
        "label_name": LABEL_NAMES[top_label],
        "risk": round(risk, 4),
        "scores": scores,
        "matched": hits,
        "message": message,
        "suggestion": LABEL_HINTS.get(top_label, ""),
        "model": {"centroid": {k: round(v, 4) for k, v in prob.items()},
                  "rule": {k: round(v, 4) for k, v in rule_scores.items()}},
    }


# ---------------------------------------------------------------- 评估

def _stratified_folds(samples, k, seed=42):
    """按类别分层切分，避免某一折缺类"""
    import random

    rnd = random.Random(seed)
    by_label = defaultdict(list)
    for text, label in samples:
        by_label[label].append((text, label))
    folds = [[] for _ in range(k)]
    for label, items in by_label.items():
        items = items[:]
        rnd.shuffle(items)
        for i, it in enumerate(items):
            folds[i % k].append(it)
    return folds


def cross_validate(model_factory=None, k=5, seed=42):
    """分层 k 折交叉验证（每次都重新训练一份模型，与线上同构）"""
    factory = model_factory or (lambda: TfidfCentroidClassifier(ngram_range=(1, 2)))
    samples = list(CORPUS)
    folds = _stratified_folds(samples, k, seed)
    n = len(samples)
    correct = 0
    per_class = {lb: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for lb in LABELS}
    confusion = {a: {b: 0 for b in LABELS} for a in LABELS}
    for i in range(k):
        test = folds[i]
        train = [s for j, f in enumerate(folds) if j != i for s in f]
        model = factory().train(train)
        for text, gold in test:
            pred = max(LABELS, key=lambda lb: model.predict_proba(text)[lb])
            confusion[gold][pred] += 1
            per_class[gold]["support"] += 1
            if pred == gold:
                correct += 1
                per_class[gold]["tp"] += 1
            else:
                per_class[gold]["fn"] += 1
                per_class[pred]["fp"] += 1
    f1s = []
    for lb in LABELS:
        c = per_class[lb]
        prec = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
        rec = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
        c["precision"], c["recall"] = round(prec, 4), round(rec, 4)
        c["f1"] = round(2 * prec * rec / (prec + rec), 4) if (prec + rec) else 0.0
        f1s.append(c["f1"])
    return {
        "samples": n,
        "k": k,
        "accuracy": round(correct / n, 4),
        "macro_f1": round(sum(f1s) / len(f1s), 4),
        "per_class": per_class,
        "confusion": confusion,
    }


def metrics(force=False):
    """缓存交叉验证结果（首页 /api/stats 会读，避免每次请求都重算）"""
    global _metrics
    if _metrics is None or force:
        main = cross_validate()
        baseline = cross_validate(
            lambda: CharNBBayes(ngram_range=(2, 3), alpha=0.3))
        main["baseline_nb"] = {"accuracy": baseline["accuracy"],
                              "macro_f1": baseline["macro_f1"],
                              "model": "朴素贝叶斯(2-3gram, α=0.3)"}
        main["model"] = "TF-IDF 类质心(1-2gram)"
        _metrics = main
    return _metrics


def rule_only_report():
    """
    规则层单独的表现：只统计"规则确实触发"的那部分

    返回 precision（触发里真的是违规的比例）/ recall（违规样本被规则捕获的比例）
    / coverage（触发占比），比"规则给每条都打分再取最大值"更有意义。
    """
    triggered = 0
    tp = 0
    bad_total = 0
    for text, gold in CORPUS:
        s, _ = _engine.score(text)
        is_bad = gold != "normal"
        if is_bad:
            bad_total += 1
        if max(s.values()) >= BLOCK_LINE:
            triggered += 1
            if is_bad:
                tp += 1
    return {
        "samples": len(CORPUS),
        "triggered": triggered,
        "precision": round(tp / triggered, 4) if triggered else 0.0,
        "recall": round(tp / bad_total, 4) if bad_total else 0.0,
        "coverage": round(triggered / len(CORPUS), 4),
    }


def _report():
    out = io.StringIO()
    m = metrics(force=True)
    print("== 5 折交叉验证（%s）==" % m["model"], file=out)
    print("样本数=%d 准确率=%.4f 宏平均F1=%.4f" %
          (m["samples"], m["accuracy"], m["macro_f1"]), file=out)
    for lb in LABELS:
        c = m["per_class"][lb]
        print("  %-8s support=%-3d P=%.3f R=%.3f F1=%.3f" %
              (LABEL_NAMES[lb], c["support"], c["precision"], c["recall"], c["f1"]),
              file=out)
    print("  对比基线 %s：准确率=%.4f 宏平均F1=%.4f" %
          (m["baseline_nb"]["model"], m["baseline_nb"]["accuracy"],
           m["baseline_nb"]["macro_f1"]), file=out)
    r = rule_only_report()
    print("  仅规则层：触发 %d 条，精确率=%.4f 召回率=%.4f 触发率=%.4f" %
          (r["triggered"], r["precision"], r["recall"], r["coverage"]), file=out)

    print("\n== 混淆矩阵（行=人工标注，列=模型判定） ==", file=out)
    print("           " + "".join("%10s" % LABEL_NAMES[lb] for lb in LABELS), file=out)
    for a in LABELS:
        print("%-10s " % LABEL_NAMES[a] +
              "".join("%10d" % m["confusion"][a][b] for b in LABELS), file=out)

    print("\n== 线上判定示例（规则 + 模型，≥%.2f 直接拦截） ==" % BLOCK_LINE, file=out)
    cases = [
        ("图书馆四楼很安静，适合自习", "forum"),
        ("食堂的菜又贵又难吃，避雷", "forum"),
        ("求购一辆二手自行车，预算150以内", "market"),
        ("出二手高数教材，需要的加微信 abc123", "market"),
        ("出二手高数教材，需要的加微信 abc123", "forum"),
        ("招兼职日结200，加微信详聊", "forum"),
        ("专业代写论文包过，加QQ详聊", "forum"),
        ("计算机学院的就这水平，也配发言", "forum"),
        ("不服来辩，我就说食堂最难吃", "forum"),
        ("傻逼东西，滚出去", "forum"),
        ("你他妈算什么东西", "forum"),
        ("球赛输了，好气啊，难受", "forum"),
    ]
    for text, ctx in cases:
        r = moderate(text, context=ctx)
        print("[%-6s %-6s risk=%.3f] %s\n         -> %s" %
              (ctx, r["action"], r["risk"], text, r["message"] or "放行"), file=out)
    return out.getvalue()


if __name__ == "__main__":
    # 直接运行本文件即可查看评估报告（完整三模块报告见 python -m ml.eval_all）
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(_report())