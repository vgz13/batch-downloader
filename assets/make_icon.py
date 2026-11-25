import sys
from PIL import Image, ImageDraw, ImageFilter, ImageChops
out = sys.argv[1]
SS = 2048
k = SS / 1024
def s(v): return int(round(v * k))
def blank(): return Image.new("L", (SS, SS), 0)
def dilate(m, r): return m.filter(ImageFilter.GaussianBlur(r)).point(lambda a: 255 if a > 8 else 0)
def solid(rgb, alpha):
    im = Image.new("RGBA", (SS, SS), rgb + (0,)); im.putalpha(alpha); return im

canvas = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))

# bentuk dasar ikon macOS
body = blank()
ImageDraw.Draw(body).rounded_rectangle((s(100), s(100), s(924), s(924)), radius=s(185), fill=255)
shadow = blank()
ImageDraw.Draw(shadow).rounded_rectangle((s(100), s(118), s(924), s(942)), radius=s(185), fill=120)
canvas = Image.alpha_composite(canvas, solid((90, 50, 0), shadow.filter(ImageFilter.GaussianBlur(s(20)))))

# gradien diagonal: kuning (kiri atas) → amber (kanan bawah)
g = Image.linear_gradient("L").rotate(45, resample=Image.BICUBIC, expand=True)
w, h = g.size
g = g.crop((w // 4, h // 4, w - w // 4, h - h // 4)).resize((SS, SS), Image.BICUBIC)
bg = Image.composite(Image.new("RGBA", (SS, SS), (245, 140, 10, 255)),
                     Image.new("RGBA", (SS, SS), (255, 214, 40, 255)), g)
# sorotan lembut kiri atas
hl = blank(); ImageDraw.Draw(hl).ellipse((s(40), s(20), s(700), s(560)), fill=55)
bg = Image.alpha_composite(bg, solid((255, 255, 255), hl.filter(ImageFilter.GaussianBlur(s(90)))))
layer = Image.new("RGBA", (SS, SS), (0, 0, 0, 0)); layer.paste(bg, (0, 0), body)
canvas = Image.alpha_composite(canvas, layer)

# awan
cloud = blank(); cd = ImageDraw.Draw(cloud)
cd.ellipse((s(232), s(330), s(492), s(590)), fill=255)
cd.ellipse((s(372), s(208), s(682), s(518)), fill=255)
cd.ellipse((s(572), s(318), s(792), s(538)), fill=255)
cd.rectangle((s(362), s(430), s(682), s(590)), fill=255)

# panah
arrow = blank(); ad = ImageDraw.Draw(arrow); cx = s(512)
ad.rounded_rectangle((cx - s(48), s(352), cx + s(48), s(640)), radius=s(24), fill=255)
ad.polygon([(cx - s(140), s(612)), (cx + s(140), s(612)), (cx, s(778))], fill=255)
ad.ellipse((cx - s(48), s(352) - 1, cx + s(48), s(448)), fill=255)

# tray
tray = blank(); td = ImageDraw.Draw(tray); t = s(46)
td.rounded_rectangle((s(262), s(690), s(262) + t, s(812)), radius=t // 2, fill=255)
td.rounded_rectangle((s(762) - t, s(690), s(762), s(812)), radius=t // 2, fill=255)
td.rounded_rectangle((s(262), s(812) - t, s(762), s(812)), radius=t // 2, fill=255)

gap = dilate(arrow, s(14))
white = ImageChops.lighter(ImageChops.subtract(cloud, gap), ImageChops.subtract(tray, gap))
white = ImageChops.lighter(white, arrow)

# perkecil sedikit (tetap di tengah) supaya pojok kanan atas lega untuk petir
K = 0.78
def place(m, ocx, ocy, ncx, ncy, k=K):
    return m.transform(m.size, Image.AFFINE, (1 / k, 0, s(ocx) - s(ncx) / k, 0, 1 / k, s(ocy) - s(ncy) / k),
                       resample=Image.BICUBIC)
white = place(white, 512, 510, 512, 530)

# petir berdiri sendiri di pojok kanan atas, terpisah dari awan
bx, by, bk = s(775), s(245), 0.7
pts = [(44, -124), (-62, 18), (0, 18), (-40, 124), (66, -20), (6, -20)]
bolt = blank(); ImageDraw.Draw(bolt).polygon([(bx + s(x * bk), by + s(y * bk)) for x, y in pts], fill=255)
white = ImageChops.lighter(white, bolt)

# bayangan lembut di bawah elemen putih
sh = solid((140, 70, 0), white.point(lambda a: int(a * 0.45)))
sh = sh.transform(sh.size, Image.AFFINE, (1, 0, 0, 0, 1, -s(12))).filter(ImageFilter.GaussianBlur(s(14)))
masked = Image.new("RGBA", (SS, SS), (0, 0, 0, 0)); masked.paste(sh, (0, 0), body)
canvas = Image.alpha_composite(canvas, masked)
canvas = Image.alpha_composite(canvas, solid((255, 255, 255), white))

canvas.resize((1024, 1024), Image.LANCZOS).save(out)
print("saved", out)
