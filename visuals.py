"""Branded sign-in mail and authenticated affiliate GIF rendering."""
from functools import lru_cache
import io
import math
from pathlib import Path
import random
import re
import threading

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent / "assets"
_render_lock = threading.Lock()

def sign_in_html(code):
    if not re.fullmatch(r"[0-9]{6}", str(code)):
        raise ValueError("A six-digit sign-in code is required")
    return (ASSETS / "signin-email.html").read_text().replace("{{CODE}}", str(code))

def affiliate_gif(code, discount):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", str(code)) or not 0 < discount <= 100:
        raise ValueError("Invalid affiliate banner details")
    # Serialize encoding to bound peak memory, and reuse completed banners.
    with _render_lock:
        return _affiliate_gif(str(code), int(discount))

@lru_cache(maxsize=12)
def _affiliate_gif(code, discount):
    background = Image.open(ASSETS / "banner-background.png").convert("RGBA")
    w, h = background.size
    bold = lambda size: ImageFont.truetype(str(ASSETS / "qt-bold.ttf"), size)
    regular = lambda size: ImageFont.truetype(str(ASSETS / "qt-regular.ttf"), size)
    # Static text stays crisp while only the ambient layers move.
    content = Image.new("RGBA", (w,h))
    d = ImageDraw.Draw(content)
    d.rounded_rectangle((24,32,74,82), radius=13, fill=(10,43,32,245), outline=(55,179,133,200))
    d.text((32,43), "QT", font=bold(21), fill="#66e8b5")
    d.text((88,34), "QUILTED TWEAKS", font=bold(19), fill="#e8fff4")
    d.text((89,63), "AFFILIATE PARTNER", font=regular(10), fill="#83b7a0")
    d.line((310,33,310,84), fill="#29573f", width=1)
    d.text((339,25), str(discount)+"% OFF", font=bold(30), fill="#61e7b1")
    d.text((341,68), "YOUR NEXT OPTIMIZATION", font=regular(9), fill="#a0c6b4")
    d.rounded_rectangle((548,24,907,80), radius=12, fill=(4,20,15,235), outline="#2b6e50", width=1)
    d.text((566,31), "USE REFERRAL CODE", font=regular(8), fill="#8ac3a7")
    font = bold(22)
    while d.textlength(code, font=font)>320:
        font=bold(font.size-1)
    d.text((566,45), code, font=font, fill="#f0fff6")
    rng = random.Random(47)
    particles=[(rng.random()*w,rng.random()*h,rng.uniform(1,2),rng.uniform(.3,1)) for _ in range(32)]
    frames=[]
    for n in range(60):
        phase=n/60
        ambient=Image.new("RGBA", (w,h))
        a=ImageDraw.Draw(ambient)
        for i,(x,y,r,brightness) in enumerate(particles):
            px=(x+phase*w)%w
            py=y+4*math.sin(phase*2*math.pi+i)
            a.ellipse((px-r,py-r,px+r,py+r),fill=(65,225,164,int(65*brightness)))
        sweep=-160+(w+320)*phase
        a.polygon([(sweep,0),(sweep+45,0),(sweep-10,h),(sweep-55,h)],fill=(80,245,187,10))
        pulse=int(16+12*math.sin(phase*2*math.pi))
        glow=Image.new("RGBA",(w,h));g=ImageDraw.Draw(glow)
        g.ellipse((335,-10,530,130),fill=(29,190,125,pulse))
        frame=Image.alpha_composite(background,glow.filter(ImageFilter.GaussianBlur(26)))
        frame=Image.alpha_composite(Image.alpha_composite(frame,ambient),content).convert("RGB")
        frames.append(frame.quantize(colors=128,method=Image.Quantize.MEDIANCUT))
    output=io.BytesIO()
    frames[0].save(output,format="GIF",save_all=True,append_images=frames[1:],duration=50,loop=0,disposal=2,optimize=False)
    return output.getvalue()
