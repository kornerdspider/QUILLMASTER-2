"""Draws the Quill Master 2 app icon (assets/icon.png, .ico, .icns). Run once: python tools/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=(17, 19, 22, 255))
d.rounded_rectangle([88, 88, S - 88, S - 88], radius=160, outline=(201, 174, 116, 255), width=10)
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 600)
box = d.textbbox((0, 0), "Q", font=font)
w, h = box[2] - box[0], box[3] - box[1]
d.text(((S - w) / 2 - box[0], (S - h) / 2 - box[1] - 20), "Q", font=font, fill=(237, 231, 220, 255))
d.ellipse([700, 190, 790, 280], fill=(127, 181, 158, 255))  # the reading lamp
out = ROOT / "assets"
out.mkdir(exist_ok=True)
img.save(out / "icon.png")
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out / "icon.icns")
img.resize((192, 192), Image.LANCZOS).save(ROOT / "quillmaster2" / "static" / "icon.png")
print("icons written")
