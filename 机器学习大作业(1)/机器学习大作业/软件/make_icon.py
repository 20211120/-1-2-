# -*- coding: utf-8 -*-
"""把「封图.jpg」转成多尺寸 .ico（供 Windows 快捷方式当软件图标）"""
import os
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "封图.jpg")
OUT_DIR = os.path.join(ROOT, "软件")
os.makedirs(OUT_DIR, exist_ok=True)
ICO = os.path.join(OUT_DIR, "封图.ico")

img = Image.open(SRC).convert("RGBA")
w, h = img.size
s = min(w, h)                      # 取中心正方形
left, top = (w - s) // 2, (h - s) // 2
img = img.crop((left, top, left + s, top + s))
img = img.resize((256, 256), Image.LANCZOS)
img.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("已生成:", ICO, os.path.getsize(ICO), "字节")