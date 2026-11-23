"""Build the app icon files from assets/icon-source.png (a tile on a white background).

Cuts the white background away, puts the tile on a 1024 px transparent canvas (896 px tile),
and writes assets/icon.png, icon.ico and icon.icns.  Usage: python tools/make_icon.py"""
from pathlib import Path
import numpy as np, cv2
from PIL import Image
A = Path(__file__).resolve().parent.parent / 'assets'
a = np.asarray(Image.open(A / 'icon-source.png').convert('RGB')); mn = a.min(axis=2)
white = (mn > 240).astype(np.uint8)
_, lab = cv2.connectedComponents(white)
bg = np.isin(lab, np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) & (white > 0)   # white touching the border
edge = (cv2.dilate(bg.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) & ~bg
alpha = np.ones(mn.shape, np.float32); alpha[bg] = 0
alpha[edge] = np.clip((245 - mn[edge]) / 95.0, 0, 1)                    # soft edge
rgb = a.copy(); rgb[edge] = cv2.inpaint(a, (alpha < 1).astype(np.uint8), 3, cv2.INPAINT_TELEA)[edge]   # no white halo
im = Image.fromarray(np.dstack([rgb, (alpha * 255).astype(np.uint8)]), 'RGBA')
ys, xs = np.where(alpha > 0.5)
tile = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)).resize((896, 896), Image.LANCZOS)
out = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0)); out.alpha_composite(tile, (64, 64))
out.save(A / 'icon.png')
out.save(A / 'icon.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
out.save(A / 'icon.icns')
