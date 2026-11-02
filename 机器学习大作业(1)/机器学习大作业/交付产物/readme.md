# 南航评论区 · 校园一站式社区平台

> 机器学习课程设计（以南航将军路校区为原型）  
> 交付内容：完整源代码 + 依赖清单 + 部署说明

---

## 一、项目简介

面向南航将军路校区的校园社区平台，包含四大模块 + 三项机器学习能力：

| 模块   | 功能                                 |
| ---- | ---------------------------------- |
| 校园地图 | 腾讯地图真实底图、校园 POI、地点评论/备注、楼层平面图、步行导航 |
| 失物招领 | 寻物/招领发布、图片上传、地点关联、已解决标记、**ML① 智能匹配 + 站内通知互相推送** |
| 二手市集 | 出售/求购、分类筛选、价格展示、图片上传、已成交标记、**ML③ 语义检索** |
| 校园评论 | 图文发帖、点赞、评论互动、关键词搜索、**删帖 + 删评（楼主 / 作者本人）** |
| 内容安全 | **ML② 垃圾广告 / 引战 / 不文明内容识别，命中即拦截、不给发表**（无人工审核队列），覆盖论坛·评论·备注·失物·市集 |

三项机器学习能力全部用**纯 Python**（仅标准库）实现，不依赖 sklearn / jieba / numpy，
exe 模式开箱即用；离线评估指标见 `campus-map\backend\ml_eval_report.txt`：

| 能力 | 模型 | 评估结果 |
| ---- | ---- | ---- |
| 失物招领匹配 | 多特征加权打分（文本 TF-IDF + 物品类别 + 地点 + 时间 + 特征词） | 20 对人工标注样本：准确率 1.00 / 精确率 1.00 / 召回率 1.00 |
| 内容安全 | 规则层（30+ 正则，noisy-OR）+ 字符 1-2gram TF-IDF 类质心分类器（164 条标注语料） | 分层 5 折：准确率 0.8537 / 宏平均 F1 0.8374（朴素贝叶斯基线仅 0.4024）；判定线 0.50 时正常内容误伤 0/60 |
| 语义检索 | 同义词表 + 主题上位词 + 语料 PMI 共现扩展 → 四通道融合排序 | 13 条标注查询：Recall@1 = 1.00 / MRR = 1.00（关键词匹配仅 0.15） |

同时提供 **电脑版**（宽屏 5 页）与 **手机版**（5 Tab 移动端）两套前端，共用同一套后端 API 与 SQLite 数据库。

---

## 二、运行环境

| 项          | 要求                           |
| ---------- | ---------------------------- |
| 操作系统       | Windows 10 / 11（64 位）        |
| 浏览器        | Edge（Windows 自带）或其他现代浏览器     |
| Python（可选） | 3.10 及以上（仅源码模式需要；exe 模式无需安装） |

---

## 三、快速启动（推荐，无需安装 Python）

1. 把整个"机器学习大作业"文件夹拷贝到任意位置（盘符、路径不限）
2. 进入 `软件` 文件夹，双击：
   - **打开-电脑版.lnk** → 启动服务并打开电脑版独立窗口
   - **打开-手机版.lnk** → 打开手机版窗口（430×870）
   - 若快捷方式失效：双击 `打开独立窗口.bat` / `打开手机版窗口.bat`，或运行 `修复快捷方式.bat` 重建快捷方式
3. 停止服务：双击 `软件\停止服务.bat`
4. 页面地址（服务固定监听本机 5000 端口）：
   - 电脑版：`http://127.0.0.1:5000/`
   - 手机版：`http://127.0.0.1:5000/m/`

**原理**：`软件\南航评论区.exe` 内置 Python 与 Flask 运行环境，启动后自动打开页面；前端页面与数据文件在 exe 外部，可随时修改。

---

## 四、源码运行（开发模式）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动后端（同时提供 API 与页面）
python campus-map/backend/app.py

# 3. 访问
# 电脑版 http://127.0.0.1:5000/
# 手机版 http://127.0.0.1:5000/m/
```

说明：本机装有 Python 且依赖齐全时，启动脚本会优先使用源码模式（修改后端后重启即生效）；否则自动使用 exe。

---

## 五、目录结构

```
机器学习大作业\
├─ 软件\                         一键启动与可执行程序
│   打开-电脑版.lnk / 打开-手机版.lnk
│   打开独立窗口.bat / 打开手机版窗口.bat
│   南航评论区.exe + _internal\  免 Python 运行环境
│   停止服务.bat / 修复快捷方式.bat
│   封图.ico / 使用说明.txt
├─ campus-map\
│   ├─ frontend\                电脑版前端（5 个页面 + common.css/js + 主楼图）
│   ├─ backend\                 后端（app.py、测试脚本、ml_eval_report.txt）
│   │   └─ ml\                  机器学习模块（textutil/matcher/moderation/semantic/eval_all
│   │                            + 早期情感分析课程素材 corpus/sentiment/experiment）
│   └─ README.md                功能、算法设计、API、验证方式
├─ 手机版\                       手机版前端（index.html / style.css / app.js）
├─ uploads\                     用户上传的图片
├─ data.db                      SQLite 数据库（自动建表 + 种子数据）
├─ 主楼-最终.png                界面使用的横幅图
└─ 交付产物\
    ├─ readme.md                部署说明（本文件）
    ├─ requirements.txt         依赖清单（与 campus-map\backend\requirements.txt 内容完全一致）
    ├─ AI对话过程记录.docx        开发过程记录
    ├─ 截图\                     浏览器实测截图 11 张（首页 ML 面板 / 通知中心 / 拦截提示 / 语义检索 / 手机版）
    ├─ ui_check.py              浏览器端到端验证脚本（可选，需 pip install websocket-client）
    └─ ui_check.log             该脚本的 20 项验证结果日志
```

> 依赖清单有两份、内容完全一致：`campus-map\backend\requirements.txt`（在 backend 目录执行
> `pip install -r requirements.txt` 时用的就是这份）与 `交付产物\requirements.txt`（交付归档副本）。
> 部署说明也有两份、各管一件事：`交付产物\readme.md`（怎么装、怎么跑、怎么重新打包、FAQ）、
> `campus-map\README.md`（功能清单、算法设计、API 速览、验证命令）。

新增的数据表（首次启动自动创建，旧库自动升级）：

| 表 | 用途 |
| --- | --- |
| `lf_matches` | 失物招领匹配关系（lost_id / found_id / 相似度 / 匹配理由 / 是否已推送） |
| `notifications` | 站内通知（匹配提醒，含未读状态与跳转目标） |
| `moderation_logs` | 内容安全处置记录（拦截 / 待复核、命中特征、风险分） |

---

## 六、数据与配置

| 项        | 位置 / 说明                                         |
| -------- | ----------------------------------------------- |
| 数据库      | `data.db`（首次启动自动建表、升级新表并写入种子数据）                  |
| 上传图片     | `uploads\`（自动创建）                                |
| 端口       | 5000，仅监听 127.0.0.1（不触发防火墙）                      |
| 腾讯地图 Key | `campus-map/frontend/map.html` 顶部 `TMAP_KEY` 常量 |
| 用户账号     | 注册后保存在 `users` 表，密码使用 werkzeug 哈希               |
| ML 评估报告  | `campus-map\backend\ml_eval_report.txt`（跑 `python -m ml.eval_all` 重新生成） |

---

## 七、修改与重新打包

| 修改内容                          | 做法                                             |
| ----------------------------- | ---------------------------------------------- |
| 页面（文字 / 样式 / 布局）              | 改 `campus-map\frontend\` 或 `手机版\` 下的文件，刷新页面即生效 |
| 后端（接口 / 数据逻辑）                 | 改 `campus-map\backend\app.py`；源码模式重启即生效        |
| 内容安全的规则 / 语料                   | 改 `campus-map\backend\ml\moderation.py` 里的 `RULES` / `CORPUS` |
| 语义检索的词表                         | 改 `campus-map\backend\ml\semantic.py` 里的 `LEXICON_WORDS` / `SYNONYM_GROUPS` / `TOPIC_MAP` |
| 打包新 exe（改了后端且要给无 Python 的电脑用） | 见下                                             |

```bash
# 在 campus-map\backend 目录执行（三项 ML 能力是纯 Python，无需额外依赖）
pyinstaller --onedir --noconfirm --name 南航评论区 --icon "..\..\软件\封图.ico" ^
  --exclude-module sklearn --exclude-module jieba --exclude-module numpy ^
  --exclude-module scipy --exclude-module pandas app.py

# 打包完成后，把 dist\南航评论区\南航评论区.exe 与 _internal 文件夹复制到 软件\ 覆盖
```

---

## 八、常见问题

| 问题          | 解决办法                                           |
| ----------- | ---------------------------------------------- |
| 双击后页面没有自动弹出 | 手动访问 `http://127.0.0.1:5000/`；或双击 `打开独立窗口.bat` |
| 提示端口被占用     | 双击 `停止服务.bat` 后再启动；或检查任务管理器里的 `南航评论区.exe`      |
| 换电脑后快捷方式失效  | 双击 `软件\修复快捷方式.bat` 重建快捷方式                      |
| 页面样式没更新     | 按 `Ctrl + F5` 强制刷新                             |
| 想看启动过程日志    | 根目录 `_startup.log`（排查用，可随时删除）                  |
| 铃铛没有红点      | 铃铛只显示**自己账号**的通知：用发布信息时的同一账号登录即可看到匹配提醒 |
| 想清空通知 / 匹配记录 | 删除 `data.db` 后重启（会重建种子数据），或用 SQLite 工具清 `notifications`、`lf_matches` 表 |
| 看不到「删除」按钮   | 按权限显示：**帖子**只有楼主能看到「删除帖子」；**评论**只有评论作者本人（论坛帖还包含楼主）能看到；未登录都看不到 |
| 正常内容被判为不良被拦 | 判定线在 `campus-map\backend\ml\moderation.py` 的 `BLOCK_LINE`（当前 0.50），可上调；被拦时会提示命中特征 |
| 删不掉某条地点评论   | 匿名发布（作者记为"匿名同学"）的评论没有可验证作者，设计上不允许删除 |
| 删帖会连带删掉评论吗  | 会：删帖同时删除该帖下所有评论（避免孤儿数据），删除前会二次确认并提示 |

---

## 九、技术栈

- 后端：Flask 3 + flask-cors + SQLite
- 前端：原生 HTML / CSS / JavaScript（电脑版 5 页 + 手机版 5 Tab）
- 地图：腾讯地图 GL JS（POI、楼层平面图 SVG、步行导航）
- 打包：PyInstaller（onedir，内置 Python 运行时）
- 机器学习（线上生效，纯 Python 无第三方依赖）：
  - 失物招领智能匹配：字级 TF-IDF + 物品类别词典 + 地点/时间/特征词加权（`ml/matcher.py`）
  - 内容安全识别：30+ 条规则 noisy-OR + 字符 1-2gram TF-IDF 类质心分类器，164 条标注语料，
    **命中即拦截（判定线 0.50）、不给发表、无人工审核队列**（`ml/moderation.py`）
  - 二手市集语义检索：同义词表 + 主题上位词 + PMI 共现学习 + 四通道融合排序（`ml/semantic.py`）
  - 离线评估：`python -m ml.eval_all` → `ml_eval_report.txt`（含判定线标定表）
- 内容删除：
  - `DELETE /api/posts/{id}`（楼主删帖，连带删除帖下评论）
  - `DELETE /api/posts/{id}/comments/{cid}`、`/api/pois/{id}/comments/{cid}`、
    `/api/map_notes/{id}/comments/{cid}`（删评）
  - 权限在服务端二次校验（帖子=楼主；论坛/备注评论=作者本人或楼主；地点评论=作者本人）
- 机器学习课程素材（存档，线上不加载）：`ml/corpus.py`、`ml/sentiment.py`、`ml/experiment.py`
  （早期情感分析语料与特征方案对比实验，另含朴素贝叶斯对比基线）
