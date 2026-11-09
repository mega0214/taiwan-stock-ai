# -*- coding: utf-8 -*-
"""版面分析：以「最大水平連續墨跡」判別圖像塊（文字行短、線稿/照片長）。

用法：
    python ebook_layout.py <來源圖片目錄> <工作目錄>
產出：
    <工作目錄>/figs/*.png    逐塊裁切的插圖
    <工作目錄>/layout.json   {layout: {檔名: [區塊…]}, kinds: {檔名: text|graphic}}
"""
import os, sys, json, glob, re
import numpy as np
from PIL import Image

if len(sys.argv) < 3:
    sys.exit("用法: python ebook_layout.py <來源圖片目錄> <工作目錄>")

SRC = os.path.abspath(sys.argv[1])
WDIR = os.path.abspath(sys.argv[2])
OUT_FIG = os.path.join(WDIR, "figs")

ocr = {}
for f in glob.glob(os.path.join(WDIR, "ocr_part_*.json")):
    ocr.update(json.load(open(f, encoding="utf-8")))

def geom(b):
    xs = [p[0] for p in b]; ys = [p[1] for p in b]
    return min(xs), min(ys), max(xs), max(ys)

def max_run(seg):
    best = 0
    for r in seg:
        if not r.any(): continue
        d = np.diff(np.r_[0, r.astype(np.int8), 0])
        st = np.flatnonzero(d == 1); en = np.flatnonzero(d == -1)
        if len(st): best = max(best, int((en - st).max()))
    return best

def trim(loose, y0, y1, x0, x1):
    seg = loose[y0:y1 + 1, x0:x1 + 1]
    rs = np.where(seg.sum(axis=1) > 0)[0]
    if len(rs) == 0: return None
    return y0 + int(rs[0]), y0 + int(rs[-1])

def analyze(path):
    key = os.path.basename(path)
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    H, W = a.shape[:2]
    dist = (255 - a).max(axis=2)
    ink = dist > 20
    loose = dist > 8
    cols = np.where(ink.sum(axis=0) > 2)[0]; rows = np.where(ink.sum(axis=1) > 2)[0]
    if len(cols) == 0: return {"kind": "blank", "items": []}, im
    cx0, cx1 = int(cols[0]), int(cols[-1]); cy0, cy1 = int(rows[0]), int(rows[-1])
    cw = cx1 - cx0
    lum = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    chroma = a.max(axis=2) - a.min(axis=2)
    tint = (lum >= 195) & (chroma >= 10)

    col_lines = []
    for L in ocr.get(key, []):
        if "box" not in L: continue
        x0, y0, x1, y1 = geom(L["box"]); h = y1 - y0; w = x1 - x0
        if 12 <= h <= 32 and w >= 0.62 * cw and x0 <= cx0 + 0.30 * cw and len(L["text"]) >= 8:
            col_lines.append((int(y0), int(y1)))
    col_lines.sort()

    def is_fig(ya, yb):
        if yb - ya < 40: return False
        seg = ink[ya:yb + 1, cx0:cx1 + 1]
        if seg.size == 0 or seg.mean() <= 0.004: return False
        mr = max_run(seg)
        rowwith = (seg.sum(axis=1) > 1).mean()
        if mr >= 90: return True
        if (yb - ya) >= 150 and rowwith >= 0.72: return True
        return False

    if len(col_lines) < 2:
        t = trim(loose, cy0, cy1, cx0, cx1)
        if not t: return {"kind": "blank", "items": []}, im
        return {"kind": "graphic", "items": [{"type": "fig", "y0": t[0], "y1": t[1]}]}, im

    blocks = []
    for y0, y1 in col_lines:
        if blocks and y0 - blocks[-1]["y1"] <= 30:
            b = blocks[-1]; b["y1"] = max(b["y1"], y1); b["n"] += 1
        else:
            blocks.append({"y0": y0, "y1": y1, "n": 1})

    raw = []; prev = cy0
    for b in blocks:
        raw.append(("gap", prev, b["y0"] - 2))
        raw.append(("text", b["y0"] - 2, b["y1"] + 2, b["n"]))
        prev = b["y1"] + 2
    raw.append(("gap", prev, cy1))

    items = []
    for it in raw:
        kind, y0, y1 = it[0], max(cy0, it[1]), min(cy1, it[2])
        if y1 - y0 < 8: continue
        if kind == "text":
            items.append({"type": "text", "y0": y0, "y1": y1, "nlines": it[3]})
            continue
        t = trim(loose, y0, y1, cx0, cx1)
        if not t: continue
        ya, yb = t
        if not is_fig(ya, yb):
            items.append({"type": "text", "y0": y0, "y1": y1, "nlines": max(1, round((y1 - y0) / 28))})
            continue
        if yb - ya <= 240 and tint[ya:yb + 1, cx0:cx1 + 1].mean() > 0.30:
            items.append({"type": "text", "y0": y0, "y1": y1, "nlines": 1})
            continue
        items.append({"type": "fig", "y0": ya, "y1": yb})

    # 於整行空白（>=16px 無墨）處細分 gap 帶，再重新分類
    def split_blank(y0, y1):
        seg = ink[y0:y1 + 1, cx0:cx1 + 1]
        prof = seg.sum(axis=1) > 0
        cuts = []
        run = 0
        for i, v in enumerate(prof):
            if not v:
                run += 1
            else:
                if run >= 16: cuts.append((i - run, i))
                run = 0
        if run >= 16: cuts.append((len(prof) - run, len(prof)))
        bounds = []; cur = 0
        for a, b in cuts:
            bounds.append((y0 + cur, y0 + a - 1)); cur = b
        bounds.append((y0 + cur, y1))
        return [(a, b) for a, b in bounds if b - a >= 12]

    refined = []
    for it in items:
        if it["type"] != "fig":
            refined.append(it); continue
        parts = split_blank(it["y0"], it["y1"])
        if len(parts) <= 1:
            refined.append(it); continue
        for (a, b) in parts:
            if is_fig(a, b):
                refined.append({"type": "fig", "y0": a, "y1": b})
            else:
                refined.append({"type": "text", "y0": a, "y1": b,
                                "nlines": max(1, round((b - a) / 28))})
    items = refined

    merged = []
    for it in items:
        if merged and merged[-1]["type"] == "fig" and it["type"] == "fig" and it["y0"] - merged[-1]["y1"] <= 10:
            merged[-1]["y1"] = max(merged[-1]["y1"], it["y1"]); continue
        if merged and merged[-1]["type"] == "text" and it["type"] == "text":
            merged[-1]["y1"] = it["y1"]; merged[-1]["nlines"] += it["nlines"]; continue
        merged.append(it)
    return {"kind": "text", "items": merged}, im

def main():
    files = sorted(glob.glob(os.path.join(SRC, "*.png")))
    files = [f for f in files if re.search(r"[-_]\d{1,4}\.png$", os.path.basename(f))]
    os.makedirs(OUT_FIG, exist_ok=True)
    for old in glob.glob(os.path.join(OUT_FIG, "*.png")): os.remove(old)
    report, kinds = {}, {}
    for i, f in enumerate(files, 1):
        key = os.path.basename(f)
        res, im = analyze(f)
        a = np.asarray(im.convert("RGB")).astype(np.int16)
        ink = (255 - a).max(axis=2) > 20
        recs = []
        for bi, b in enumerate(res["items"]):
            if b["type"] != "fig":
                recs.append(b); continue
            seg = ink[b["y0"]:b["y1"] + 1, :]
            cs = np.where(seg.sum(axis=0) > 0)[0]
            x0 = max(0, int(cs[0]) - 8) if len(cs) else 0
            x1 = min(im.width - 1, int(cs[-1]) + 8) if len(cs) else im.width - 1
            y0 = max(0, b["y0"] - 8); y1 = min(im.height - 1, b["y1"] + 8)
            name = f"{os.path.splitext(key)[0]}_fig{bi:02d}.png"
            im.crop((x0, y0, x1 + 1, y1 + 1)).save(os.path.join(OUT_FIG, name))
            recs.append({"type": "fig", "y0": b["y0"], "y1": b["y1"], "file": name,
                         "w": x1 - x0 + 1, "h": y1 - y0 + 1})
        report[key] = recs; kinds[key] = res["kind"]
        if i % 40 == 0: print(f"{i}/{len(files)}", flush=True)
    json.dump({"layout": report, "kinds": kinds},
              open(os.path.join(WDIR, "layout.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter(kinds.values()))
    print("figs", sum(1 for v in report.values() for r in v if r["type"] == "fig"))

if __name__ == "__main__":
    main()
