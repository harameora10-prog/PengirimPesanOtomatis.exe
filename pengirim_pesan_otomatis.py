"""
Pengirim Pesan Otomatis (Tahap 16: Smart Claim v2)
Mendukung: Mazoku, Karuta, Index, Yume, Shisui
"""

import json
import os
import sys
import time
import uuid
import random
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta
import winreg
import calendar
import re
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

try:
    import pyautogui
    import pyperclip
    import pygetwindow
    import pystray
    from PIL import Image, ImageDraw, ImageOps, ImageChops, ImageStat, ImageFont, ImageFilter
    import winsound
    import pytesseract
except ImportError:
    sys.exit("Pustaka belum terpasang. Jalankan: pip install pyautogui pyperclip pygetwindow pystray Pillow pytesseract")

# ==========================================
# Lokasi Tesseract dicari otomatis (Program Files, x86, LocalAppData, PATH)
# ==========================================
def find_tesseract():
    cands = [r'C:\Program Files\Tesseract-OCR\tesseract.exe',
             r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
             shutil.which("tesseract") or ""]
    for c in cands:
        if c and os.path.exists(c): return c
    return None

TESSERACT_PATH = find_tesseract()
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
# ==========================================

if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

APP_NAME = "PengirimPesanOtomatis"
DT_FORMAT = "%Y-%m-%d %H:%M:%S"
GRACE = timedelta(seconds=60)
MIN_INTERVAL = 5
MAX_ENTERS = 5
PASTE_KEY = "command" if sys.platform == "darwin" else "ctrl"

METHOD_LABELS = {"paste": "Tempel (Ctrl+V)", "type": "Ketik langsung"}
LABEL_TO_METHOD = {v: k for k, v in METHOD_LABELS.items()}

REPEAT_LABELS = {"once": "Sekali", "daily": "Setiap hari", "weekly": "Setiap minggu",
                 "monthly": "Setiap bulan", "loop": "Terus-menerus (tiap N menit)"}
LABEL_TO_REPEAT = {v: k for k, v in REPEAT_LABELS.items()}
STEPS = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
DAY_NAMES = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

# =====================================================================
#  Windows API Helper
# =====================================================================
def is_f8_pressed():
    if sys.platform != "win32": return False
    return ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000

def prevent_sleep():
    if sys.platform != "win32": return
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)

def allow_sleep():
    if sys.platform != "win32": return
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

# =====================================================================
#  Logika Waktu & Jadwal
# =====================================================================
def parse_dt(s): return datetime.strptime(s, DT_FORMAT)
def fmt_dt(d): return d.strftime(DT_FORMAT)

def parse_time(s):
    try: return datetime.strptime(s, "%H:%M").time()
    except: return None

def add_month(d):
    m = d.month
    y = d.year + (m // 12)
    m = (m % 12) + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=day)

def step_for(repeat, every_sec=600):
    if repeat == "loop": return timedelta(seconds=every_sec)
    return STEPS[repeat]

def next_occurrence(when, repeat, every_sec, now):
    if when > now: return when
    if repeat == "monthly":
        while when <= now: when = add_month(when)
        return when
    step = step_for(repeat, every_sec)
    n = (now - when) // step + 1
    return when + step * n

def is_within_active_hours(now_time, start_str, end_str):
    if not start_str or not end_str: return True
    start, end = parse_time(start_str), parse_time(end_str)
    if not start or not end: return True
    if start <= end: return start <= now_time <= end
    else: return now_time >= start or now_time <= end

def process_variables(text):
    now = datetime.now()
    return text.replace("{jam}", now.strftime("%H:%M")) \
               .replace("{tanggal}", now.strftime("%d-%m-%Y")) \
               .replace("{hari}", DAY_NAMES[now.weekday()])

def new_message(text, when, repeat, count, interval, every_sec=600, enters=1, enter_pause=1.0,
                target="", rel_pos=None, active_start="", active_end="", max_sends=0,
                days=None, seq_delay=5, channel="", simulation=False,
                smart_claim=False, claim_timeout=15, claim_btn1="", claim_btn2="", claim_btn3="",
                tier_ss="", tier_s="", tier_a="", tier_b="", tier_c="",
                claim_bot="auto", claim_mode="numbers", claim_fallback="strict"):
    return {
        "id": uuid.uuid4().hex[:8], "text": text, "when": fmt_dt(when),
        "repeat": repeat, "count": count, "interval": interval, "every_sec": every_sec,
        "enters": enters, "enter_pause": enter_pause, "enabled": True, "status": "Dijadwalkan",
        "target": target, "rel_pos": list(rel_pos) if rel_pos else None,
        "active_start": active_start, "active_end": active_end, "max_sends": max_sends,
        "days": days or [], "seq_delay": seq_delay, "sent_total": 0,
        "channel": channel, "simulation": simulation,
        "smart_claim": smart_claim, "claim_timeout": claim_timeout,
        "claim_btn1": claim_btn1, "claim_btn2": claim_btn2, "claim_btn3": claim_btn3,
        "tier_ss": tier_ss, "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b, "tier_c": tier_c,
        "claim_bot": claim_bot, "claim_mode": claim_mode, "claim_fallback": claim_fallback
    }

def normalize(m):
    try:
        repeat = m.get("repeat", "once")
        legacy_single = bool(m.get("claim_btn1") and not m.get("claim_btn2") and not m.get("claim_btn3"))
        claim_mode = m.get("claim_mode") or ("single" if legacy_single else "numbers")
        claim_fb = {"skip": "strict"}.get(m.get("claim_fallback"), m.get("claim_fallback"))      # nilai lama 'skip' -> 'strict' (lebih aman)
        out = {
            "id": str(m.get("id") or uuid.uuid4().hex[:8]),
            "text": str(m["text"]), "when": fmt_dt(parse_dt(m["when"])),
            "repeat": repeat if repeat in REPEAT_LABELS else "once",
            "count": max(1, int(m.get("count", 1))),
            "interval": max(MIN_INTERVAL, int(m.get("interval", 60))),
            "every_sec": max(MIN_INTERVAL, int(m["every_sec"]) if "every_sec" in m else int(m.get("every", 10)) * 60),
            "enters": min(MAX_ENTERS, max(1, int(m.get("enters", 1)))),
            "enter_pause": min(10.0, max(0.3, float(m.get("enter_pause", 1.0)))),
            "enabled": bool(m.get("enabled", True)),
            "status": str(m.get("status", "Dijadwalkan")),
            "target": str(m.get("target", "")),
            "rel_pos": list(m["rel_pos"]) if m.get("rel_pos") else None,
            "active_start": str(m.get("active_start", "")),
            "active_end": str(m.get("active_end", "")),
            "max_sends": int(m.get("max_sends", 0)),
            "days": list(m.get("days", [])),
            "seq_delay": int(m.get("seq_delay", 5)),
            "sent_total": int(m.get("sent_total", 0)),
            "channel": str(m.get("channel", "")),
            "simulation": bool(m.get("simulation", False)),
            "smart_claim": bool(m.get("smart_claim", False)),
            "claim_timeout": int(m.get("claim_timeout", 15)),
            "claim_btn1": str(m.get("claim_btn1", "")),
            "claim_btn2": str(m.get("claim_btn2", "")),
            "claim_btn3": str(m.get("claim_btn3", "")),
            "tier_ss": str(m.get("tier_ss", "")),
            "tier_s": str(m.get("tier_s", "")),
            "tier_a": str(m.get("tier_a", "")),
            "tier_b": str(m.get("tier_b", "")),
            "tier_c": str(m.get("tier_c", "")),
            "claim_bot": m.get("claim_bot") if m.get("claim_bot") in BOT_LABELS else "auto",
            "claim_mode": claim_mode if claim_mode in CLAIM_MODE_LABELS else "numbers",
            "claim_fallback": claim_fb if claim_fb in FALLBACK_LABELS else "strict",
        }
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if out["enabled"]: out["status"] = "Dijadwalkan"
    return out

def prepare_message(m, now):
    when = parse_dt(m["when"])
    m["_remaining"] = m["count"]
    if when < now - GRACE:
        if m["repeat"] == "once":
            m["enabled"] = False
            m["_next"] = None
            m["status"] = "Terlewat"
            return
        when = next_occurrence(when, m["repeat"], m["every_sec"], now)
        m["when"] = fmt_dt(when)
    m["_next"] = when
    m["status"] = "Menunggu"

def is_due(m, now):
    return m["enabled"] and m.get("_next") is not None and m["_next"] <= now

def is_overdue(m, now):
    return now - m["_next"] > GRACE

def finish_cycle(m, status, now, use_jitter=False):
    if m["repeat"] == "once":
        m["enabled"] = False
        m["_next"] = None
    else:
        step = step_for(m["repeat"], m["every_sec"])
        if use_jitter and m["repeat"] == "loop":
            jitter_sec = random.uniform(-0.1 * m["every_sec"], 0.1 * m["every_sec"])
            step = timedelta(seconds=max(MIN_INTERVAL, m["every_sec"] + jitter_sec))
            when = next_occurrence(parse_dt(m["when"]), m["repeat"], int(step.total_seconds()), now)
        elif m["repeat"] == "monthly":
            when = add_month(parse_dt(m["when"]))
            while when <= now: when = add_month(when)
        else:
            when = next_occurrence(parse_dt(m["when"]), m["repeat"], m["every_sec"], now)
        m["when"] = fmt_dt(when)
        m["_next"] = when
        m["_remaining"] = m["count"]
    m["status"] = status

def mark_sent(m, now, use_jitter=False):
    m["_remaining"] -= 1
    m["sent_total"] += 1
    if m["max_sends"] > 0 and m["sent_total"] >= m["max_sends"]:
        finish_cycle(m, f"Selesai (Limit {m['max_sends']})", now, use_jitter)
        m["enabled"] = False
        return
    if m["_remaining"] > 0:
        delay = m["interval"]
        if use_jitter: delay = max(MIN_INTERVAL, delay + random.uniform(-2.0, 2.0))
        m["_next"] = now + timedelta(seconds=delay)
        m["status"] = f"Mengirim {m['count'] - m['_remaining']}/{m['count']}"
    else:
        finish_cycle(m, f"Terkirim {now:%H:%M}", now, use_jitter)

def data_path():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    base = base or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "data.json")

def history_path():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    base = base or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    return os.path.join(folder, "history.json")

def log_path():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    base = base or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    return os.path.join(folder, "log.txt")

# =====================================================================
#  Smart Claim v2: deteksi layout drop, OCR kartu, dan penilaian
# =====================================================================
BOT_LABELS = {"auto": "Otomatis (coba semua format)", "karuta": "Karuta (print · edisi di bawah kartu)",
              "yume": "Yume (daftar '#print' di atas gambar)", "shisui": "Shisui (edisi · print di kiri-bawah)",
              "index": "Index (angka + v1 di kanan-bawah)", "mazoku": "Mazoku / Summon (rarity di daftar)"}
LABEL_TO_BOT = {v: k for k, v in BOT_LABELS.items()}
CLAIM_MODE_LABELS = {"numbers": "Beberapa tombol berjajar (1/2/3 atau Claim x3)", "single": "Satu tombol 'Claim'"}
LABEL_TO_CLAIM_MODE = {v: k for k, v in CLAIM_MODE_LABELS.items()}
FALLBACK_LABELS = {"strict": "Lewati bila ada kartu tak terbaca (paling aman)",
                   "partial": "Pilih dari kartu yang terbaca saja",
                   "first": "Seperti di atas; klik kartu 1 bila semuanya gagal"}
LABEL_TO_FALLBACK = {v: k for k, v in FALLBACK_LABELS.items()}


def app_folder():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    base = base or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return folder


def save_debug_image(im, name, keep=30):
    """Simpan gambar debug di folder AppData (BUKAN folder kerja) dan buang yang lama.
    Kegagalan menyimpan tidak boleh mengganggu proses klaim."""
    try:
        folder = os.path.join(app_folder(), "debug")
        os.makedirs(folder, exist_ok=True)
        im.save(os.path.join(folder, name))
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg"))]
        files.sort(key=os.path.getmtime)
        for old in files[:-keep]:
            try: os.remove(old)
            except OSError: pass
        return True
    except Exception:
        return False


def dpi_scale():
    if sys.platform != "win32": return 1.0
    try: return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except Exception: return 1.0


# ---------- utilitas gambar (hanya Pillow, tanpa numpy/OpenCV) ----------
def _bg_candidates(im, k=3, min_share=0.06):
    """Warna latar yang mungkin: warna paling dominan. Pesan yang di-highlight/mention punya latar
    sedikit berbeda dari chat biasa, jadi beberapa kandidat dicoba."""
    rgb = im.convert("RGB")
    small = rgb.resize((max(1, rgb.width // 8), max(1, rgb.height // 8)), Image.NEAREST)
    colors = small.getcolors(maxcolors=small.width * small.height + 1) or [(1, (0, 0, 0))]
    total = float(sum(c for c, _ in colors)) or 1.0
    colors.sort(reverse=True)
    out = [col for cnt, col in colors if cnt / total >= min_share][:k]
    return out or [colors[0][1]]


def _nonbg_mask(im, thr=14, bg=None):
    """Topeng piksel yang BEDA dari warna latar."""
    rgb = im.convert("RGB")
    if bg is None: bg = _bg_candidates(rgb, k=1)[0]
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg))
    r, g, b = diff.split()
    d = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return d.point(lambda v: 255 if v > thr else 0), bg


def _col_profile(mask):
    return [v / 255.0 for v in mask.resize((mask.width, 1), Image.BOX).tobytes()]


def _row_profile(mask):
    return [v / 255.0 for v in mask.resize((1, mask.height), Image.BOX).tobytes()]


def _runs(flags, min_len=1, max_gap=0):
    """Rentang berurutan [start, end) dari nilai True; celah <= max_gap digabung."""
    runs, start, last, gap = [], None, None, 0
    for i, f in enumerate(flags):
        if f:
            if start is None: start = i
            last, gap = i, 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                if last - start + 1 >= min_len: runs.append((start, last + 1))
                start = None
    if start is not None and last - start + 1 >= min_len:
        runs.append((start, last + 1))
    return runs


def same_sig(a, b, tol=6.0):
    """Dua 'sidik jari' gambar (thumbnail) dianggap sama jika selisih rata-rata kecil."""
    if a is None or b is None or a.size != b.size: return False
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0] < tol


def _sig_of(shot, box):
    l, t, r, b = [int(v) for v in box]
    l, t = max(0, l), max(0, t)
    r, b = min(shot.width, r), min(shot.height, b)
    if r - l < 8 or b - t < 8: return None
    return shot.crop((l, t, r, b)).convert("L").resize((32, 16), Image.BOX)


def _components(grid, min_cells):
    """Komponen terhubung pada grid biner kecil. Return [(x0,y0,x1,y1,jumlah_sel)] dengan ukuran >= min_cells."""
    w, h = grid.size
    data = grid.tobytes()
    seen = bytearray(w * h)
    comps = []
    for i in range(w * h):
        if not data[i] or seen[i]: continue
        stack, n = [i], 0
        seen[i] = 1
        x0 = x1 = i % w
        y0 = y1 = i // w
        while stack:
            j = stack.pop(); n += 1
            x, y = j % w, j // w
            if x < x0: x0 = x
            if x > x1: x1 = x
            if y < y0: y0 = y
            if y > y1: y1 = y
            for nb, ok in ((j - 1, x > 0), (j + 1, x < w - 1), (j - w, y > 0), (j + w, y < h - 1)):
                if ok and data[nb] and not seen[nb]:
                    seen[nb] = 1; stack.append(nb)
        if x1 - x0 + 1 >= min_cells and y1 - y0 + 1 >= min_cells:
            comps.append((x0, y0, x1 + 1, y1 + 1, n))
    return comps


def _texture_mask(im, rng_thr=10):
    """Piksel yang berada di area bertekstur (banyak tepi/warna). Latar datar berwarna apa pun diabaikan,
    jadi pesan yang di-highlight atau sidebar tidak ikut tertangkap."""
    g = ImageOps.grayscale(im)
    rng = ImageChops.subtract(g.filter(ImageFilter.MaxFilter(5)), g.filter(ImageFilter.MinFilter(5)))
    return rng.point(lambda v: 255 if v > rng_thr else 0)


def _local_bg(shot, bb, pad=14):
    """Warna latar di sekitar sebuah kotak (dari titik-titik tepat di luar kotak)."""
    l, t, r, b = [int(v) for v in bb]
    W, H = shot.size
    xm = (l + r) // 2
    pts = [(max(0, l - pad), max(0, t - pad)), (min(W - 1, r + pad), max(0, t - pad)),
           (max(0, l - pad), min(H - 1, b + pad)), (min(W - 1, r + pad), min(H - 1, b + pad)),
           (xm, max(0, t - pad)), (xm, min(H - 1, b + pad))]
    return Counter(shot.getpixel(p) for p in pts).most_common(1)[0][0]


def _refine_box(shot, bb, pad=12):
    """Rapatkan kotak hasil grid kasar ke tepi kartu yang sebenarnya (beda dari latar lokal)."""
    l, t, r, b = bb
    W, H = shot.size
    L, T, R, B = max(0, l - pad), max(0, t - pad), min(W, r + pad), min(H, b + pad)
    sub = shot.crop((L, T, R, B))
    mask, _ = _nonbg_mask(sub, 14, _local_bg(shot, bb, pad))
    rows = [i for i, p in enumerate(_row_profile(mask)) if p >= 0.3]
    cols = [i for i, p in enumerate(_col_profile(mask)) if p >= 0.3]
    if not rows or not cols: return bb
    return (L + cols[0], T + rows[0], L + cols[-1] + 1, T + rows[-1] + 1)


def _is_art(shot, bb):
    """Gambar kartu punya banyak warna berbeda; teks / blok datar tidak."""
    l, t, r, b = bb
    small = shot.crop((l, t, r, b)).convert("RGB").resize((48, 48), Image.NEAREST)
    cols = small.getcolors(maxcolors=4096)
    return cols is None or len(cols) >= 60


def _trim_vertical(tm, bb):
    """Jika baris tombol/teks ikut menempel ke blok kartu, pangkas di celah kosong (>6 px) antara keduanya
    dan ambil bagian terpanjang (= gambar kartu)."""
    l, t, r, b = bb
    rp = _row_profile(tm.crop((l, t, r, b)))
    runs = _runs([p >= 0.2 for p in rp], min_len=60, max_gap=6)
    if not runs: return bb
    a, z = max(runs, key=lambda x: x[1] - x[0])
    return (l, t + a, r, min(b, t + z + 10))


def _art_boxes(shot, tm, cell_thr, opening, min_fill):
    """Kotak blok gambar padat dari topeng tekstur. `opening` memotong sambungan tipis (garis highlight, teks)."""
    W, H = shot.size
    cs = 8
    grid = tm.resize((max(1, W // cs), max(1, H // cs)), Image.BOX).point(lambda v: 255 if v >= cell_thr else 0)
    if opening: grid = grid.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    gw = grid.size[0]
    boxes = []
    for x0, y0, x1, y1, n in _components(grid, 9):                     # baris teks terlalu pendek (<72 px) sehingga tersaring
        if x0 == 0 or x1 >= gw: continue                                # menempel ke tepi tangkapan = garis highlight/panel, bukan kartu
        if n < min_fill * (x1 - x0) * (y1 - y0): continue               # gambar kartu padat; gabungan garis/teks/ikon tidak
        bb = (x0 * cs, y0 * cs, x1 * cs, y1 * cs)
        if bb[2] - bb[0] < 72 or bb[3] - bb[1] < 72: continue
        if not _is_art(shot, bb): continue
        bb = _trim_vertical(tm, bb)
        bb = _refine_box(shot, bb)
        if bb[2] - bb[0] >= 72 and bb[3] - bb[1] >= 72:
            boxes.append(bb)
    return boxes


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / float((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter or 1)


def _art_groups(shot):
    """Blok gambar kartu berukuran besar, dikelompokkan per baris. Urut dari yang PALING BAWAH."""
    tm = _texture_mask(shot)
    boxes = _art_boxes(shot, tm, 90, False, 0.45)
    for bb in _art_boxes(shot, tm, 140, True, 0.6):                    # lintasan ketat: pisahkan kartu dari gumpalan teks/garis
        if all(_iou(bb, o) < 0.5 for o in boxes): boxes.append(bb)
    groups = []
    for bb in sorted(boxes, key=lambda b: b[3], reverse=True):
        for g in groups:
            ov = min(g["y1"], bb[3]) - max(g["y0"], bb[1])
            if ov >= 0.6 * min(g["y1"] - g["y0"], bb[3] - bb[1]):
                g["boxes"].append(bb)
                g["x0"], g["y0"] = min(g["x0"], bb[0]), min(g["y0"], bb[1])
                g["x1"], g["y1"] = max(g["x1"], bb[2]), max(g["y1"], bb[3])
                break
        else:
            groups.append({"boxes": [bb], "x0": bb[0], "y0": bb[1], "x1": bb[2], "y1": bb[3]})
    groups.sort(key=lambda g: g["y1"], reverse=True)
    return groups


def _button_band(bmask):
    """Dalam pita sempit di bawah gambar: cari baris yang isinya HANYA 2-5 blok berukuran mirip (= tombol)."""
    bw, bh = bmask.size
    sm = bmask.resize((max(1, bw // 2), max(1, bh // 2)), Image.BOX).point(lambda v: 255 if v >= 128 else 0)
    sw, sh = sm.size
    data = sm.tobytes()
    good = []
    for y in range(sh):
        allr = [r for r in _runs([b > 0 for b in data[y * sw:(y + 1) * sw]], min_len=3)
                if r[0] > 0 and r[1] < sw]                     # blok yang menempel ke tepi (garis highlight pesan) bukan tombol
        if any(r[1] - r[0] > 150 for r in allr):
            good.append(False); continue
        btn = [r for r in allr if r[1] - r[0] >= 10]
        ok = 2 <= len(btn) <= 5 and (len(allr) - len(btn)) <= 1
        if ok:
            ws = [r[1] - r[0] for r in btn]
            ok = max(ws) <= 1.8 * min(ws)
        good.append(ok)
    stretches, start, end, miss = [], None, 0, 0
    for y, ok in enumerate(good):
        if ok:
            if start is None: start = y
            end, miss = y, 0
        elif start is not None:
            miss += 1
            if miss > 16:                                      # jembatani baris yang terpotong glif/teks di dalam tombol
                stretches.append((start, end + 1)); start = None
    if start is not None: stretches.append((start, end + 1))
    for y0, y1 in stretches:                                   # yang paling dekat di bawah gambar dulu
        if not 10 <= y1 - y0 <= 60: continue
        if sum(good[y0:y1]) < 4: continue
        band = bmask.crop((0, y0 * 2, bw, y1 * 2))
        segs = [s for s in _runs([p >= 0.45 for p in _col_profile(band)], min_len=20, max_gap=3)
                if s[1] - s[0] <= 300 and s[0] > 0 and s[1] < bw]
        if not 2 <= len(segs) <= 5: continue
        ws = [s[1] - s[0] for s in segs]
        if max(ws) > 1.8 * min(ws): continue
        # rapatkan sumbu vertikal: baris di mana kolom-kolom tombol benar-benar terisi
        profs = [_row_profile(band.crop((a, 0, b, band.height))) for a, b in segs]
        avg = [sum(p[i] for p in profs) / len(profs) for i in range(band.height)]
        vr = _runs([v >= 0.5 for v in avg], min_len=16, max_gap=14)
        if vr:
            r0, r1 = max(vr, key=lambda r: r[1] - r[0])
            return segs, y0 * 2 + r0, y0 * 2 + r1
        return segs, y0 * 2, y1 * 2
    return None


def _drop_from_group(shot, g, buttons=None):
    """Bentuk dict drop dari satu kelompok gambar kartu + tombol di bawahnya."""
    W, H = shot.size
    gh = g["y1"] - g["y0"]
    if gh < 100 or gh > 1000 or g["x1"] - g["x0"] < 150: return None
    if buttons is None:
        by0 = g["y1"] + 2
        by1 = min(H, by0 + max(120, int(gh * 0.45)))
        if by1 - by0 < 40: return None
        bshot = shot.crop((0, by0, W, by1))
        bmask, _ = _nonbg_mask(bshot, thr=3)                    # kontras tombol bisa sangat rendah (selisih ~10)
        found = _button_band(bmask)
        if not found: return None
        segs, ty0, ty1 = found
        cy = by0 + (ty0 + ty1) / 2.0
        buttons = [((a + b) / 2.0, cy) for a, b in segs]
    n = len(buttons)
    boxes = sorted(g["boxes"], key=lambda b: b[0])
    ws = [b[2] - b[0] for b in boxes]
    consistent = (len(boxes) == n and max(ws) <= 1.12 * min(ws)
                  and all(boxes[i][2] <= boxes[i + 1][0] for i in range(n - 1)))      # bayangan kartu bisa membuat kotak tumpang tindih
    if consistent:                                               # tiap kartu = satu blok gambar
        cards = [tuple(int(v) for v in b) for b in boxes]
    else:                                                        # satu gambar lebar / kotak tak konsisten: bagi sama lebar
        cw = (g["x1"] - g["x0"]) / float(n)
        pad = cw * 0.02
        cards = [(int(g["x0"] + i * cw + pad), int(g["y0"]), int(g["x0"] + (i + 1) * cw - pad), int(g["y1"])) for i in range(n)]
    strip = (g["x0"], g["y0"], g["x1"], g["y1"])
    head = (max(0, g["x0"] - 80), max(0, g["y0"] - int(0.9 * gh)), W, max(0, g["y0"] - 2))
    return {"method": "struktur", "buttons": buttons, "cards": cards, "strip": strip, "head": head,
            "clipped": g["y0"] <= 3, "sig": _sig_of(shot, strip)}


# ---------- mencari drop TERBARU secara struktural ----------
def locate_drop(shot):
    """Cari strip gambar kartu + baris tombol yang PALING BAWAH (= drop terbaru).
    Tidak memakai OCR sehingga cepat dan tidak tertipu angka di dalam gambar kartu.
    Return dict (koordinat relatif terhadap `shot`) atau None."""
    W, H = shot.size
    if W < 300 or H < 200: return None
    for g in _art_groups(shot):
        d = _drop_from_group(shot, g)
        if d: return d
    return None


def drop_from_buttons(shot, btns):
    """Tombol sudah ketemu (lewat OCR): cari blok gambar tepat di atasnya; bila gagal pakai perkiraan."""
    by = min(b[1] for b in btns)
    for g in _art_groups(shot):
        if 0 <= by - g["y1"] <= max(160, int(0.6 * (g["y1"] - g["y0"]))):
            d = _drop_from_group(shot, g, buttons=btns)
            if d:
                d["method"] = "OCR+struktur"
                return d
    return None


# ---------- cadangan: cari tombol lewat OCR ----------
def _digits_in_band(band, y_off):
    im = ImageOps.autocontrast(ImageOps.grayscale(band), cutoff=5)
    im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    d = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT, config="--psm 6")
    cands = []
    for i in range(len(d["text"])):
        t = str(d["text"][i]).strip().upper()
        try: conf = float(d["conf"][i])
        except (TypeError, ValueError): continue
        if conf < 25 or t not in ("1", "2", "3", "4", "I", "L", "|"): continue
        w, h = d["width"][i] / 2.0, d["height"][i] / 2.0
        if w < 6 or h < 10 or h > 50: continue
        cands.append((t, d["left"][i] / 2.0 + w / 2.0, d["top"][i] / 2.0 + h / 2.0 + y_off))
    keys = {}
    for t, x, y in sorted(cands, key=lambda c: c[1]):
        if t in ("2", "3", "4"): keys.setdefault(t, (x, y))
    for t, x, y in sorted(cands, key=lambda c: c[1]):
        if t in ("1", "I", "L", "|") and ("2" in keys and x < keys["2"][0] or t == "1"): keys.setdefault("1", (x, y))
    if len(keys) < 2: return []
    if "2" in keys and "3" in keys and "1" not in keys:
        (x2, y2), (x3, _y3) = keys["2"], keys["3"]
        keys["1"] = (x2 - (x3 - x2), y2)
    if "1" in keys and "2" in keys and "3" not in keys:
        (x1, y1), (x2, _y2) = keys["1"], keys["2"]
        keys["3"] = (x2 + (x2 - x1), y1)
    if "1" in keys and "3" in keys and "2" not in keys:
        keys["2"] = ((keys["1"][0] + keys["3"][0]) / 2.0, keys["1"][1])
    pts = [keys[k] for k in sorted(keys)]
    xs = [p[0] for p in pts]
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    if min(gaps) <= 15 or max(gaps) > 1.8 * min(gaps): return []
    return pts


def ocr_find_digit_buttons(shot):
    """Cadangan bila deteksi struktural gagal: pindai PITA sempit dari bawah ke atas dan ambil
    deretan tombol '1','2','3' pertama (= paling bawah = terbaru). Digit di dalam gambar kartu atau
    teks chat tidak membentuk deretan sejajar bernomor berurutan sehingga diabaikan."""
    u = dpi_scale()
    bh, step = int(80 * u), int(50 * u)
    y1 = shot.height
    while y1 > 40:
        y0 = max(0, y1 - bh)
        pts = _digits_in_band(shot.crop((0, y0, shot.width, y1)), y0)
        if pts: return pts
        y1 -= step
    return []


def fallback_cards(buttons, size):
    """Perkiraan area kartu bila deteksi struktural gagal (pakai ukuran yang dikalikan skala DPI)."""
    W, H = size
    u = dpi_scale()
    xs = [b[0] for b in buttons]
    n = len(xs)
    spacing = (xs[-1] - xs[0]) / (n - 1)
    cy = buttons[0][1]
    if spacing >= 180 * u:                      # tombol tersebar tepat di bawah tiap kartu
        cw = spacing * 0.95
        left = xs[0] - cw / 2.0
    else:                                       # tombol rapat di kiri: kartu berjajar dari tepi gambar
        cw = 250 * u
        left = xs[0] - 40 * u
    ch = cw * 1.4
    bottom = cy - 22 * u
    boxes = []
    for i in range(n):
        l, r = left + i * cw, left + (i + 1) * cw
        boxes.append((int(max(0, l)), int(max(0, bottom - ch)), int(min(W, r)), int(min(H, bottom))))
    return boxes


def ocr_find_claim_button(shot):
    """Tombol 'Claim' (mode satu tombol): kata persis CLAIM, ambil yang PALING BAWAH (terbaru)."""
    im = ImageOps.autocontrast(ImageOps.grayscale(shot), cutoff=5)
    im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    d = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT, config="--psm 11")
    best = None
    for i in range(len(d["text"])):
        t = str(d["text"][i]).strip().upper().strip("!.:")
        try: conf = float(d["conf"][i])
        except (TypeError, ValueError): continue
        w, h = d["width"][i] / 2.0, d["height"][i] / 2.0
        if t != "CLAIM" or conf < 20 or w < 10 or h < 8: continue
        pt = (d["left"][i] / 2.0 + w / 2.0, d["top"][i] / 2.0 + h / 2.0)
        if best is None or pt[1] > best[1]: best = pt
    return best


def read_head_text(shot, drop):
    """OCR teks pesan tepat di ATAS gambar kartu. Banyak bot menaruh daftar '1. KODE - #56 - Nama - Seri'
    di situ: teksnya besar dan jelas sehingga jauh lebih andal daripada membaca badge kecil di gambar kartu."""
    box = drop.get("head")
    if not box: return ""
    l, t, r, b = [int(v) for v in box]
    if r - l < 80 or b - t < 20: return ""
    im = ImageOps.autocontrast(ImageOps.grayscale(shot.crop((l, t, r, b))))
    st = drop.get("strip")
    if not st or st[3] - st[1] < 380:                       # zoom kecil: teks perlu diperbesar; zoom besar sudah cukup jelas
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    return pytesseract.image_to_string(im, config="--psm 6")


def head_tail(text):
    """Bagian teks yang milik drop TERBARU: mulai dari baris '1.' terakhir, atau 3 baris terakhir bila tak ada daftar.
    Dipakai agar tulisan 'expired' milik drop lama di atasnya tidak membatalkan klaim."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    idx = [i for i, ln in enumerate(lines) if re.match(r"^\s*[1IlL|]\s*[.)]\s", ln)]
    return "\n".join(lines[idx[-1]:] if idx else lines[-3:])


def parse_drop_list(text, n):
    """Daftar teks drop -> {indeks kartu (mulai 0): nomor print}. Memakai kelompok TERAKHIR yang diawali '1.'
    (supaya daftar drop lama yang masih terlihat tidak ikut terbaca)."""
    cur = {}
    for line in (text or "").splitlines():
        mt = re.match(r"^\s*([1-9IlL|])\s*[.)]\s*(.+)$", line)
        if not mt: continue
        idx = 1 if mt.group(1) in "IlL|" else int(mt.group(1))
        if idx == 1: cur = {}
        pm = re.search(r"[#＃]\s*([0-9OoIlSB]{1,6})(?![0-9A-Za-z])", mt.group(2))
        if not pm or idx > n: continue
        tok = pm.group(1).translate(_DIGIT_FIX_LOWER)
        if tok.isdigit(): cur[idx - 1] = int(tok)
    return cur


# ---------- pembacaan per bot: area kecil (ROI) tempat angka print berada ----------
# Koordinat ROI relatif terhadap kotak kartu (kiri, atas, kanan, bawah), dikalibrasi dari screenshot tiap bot.
ROI_SPECS = {
    "index":  {"rois": [(0.58, 0.847, 0.90, 0.897), (0.55, 0.835, 0.92, 0.905)], "wl": "0123456789"},   # pil rata kanan: angka 2-3 digit bergeser
    "shisui": {"rois": [(0.02, 0.93, 0.55, 1.0), (0.0, 0.91, 0.6, 1.0)], "wl": "0123456789.-:,"},
    "karuta": {"rois": [(0.46, 0.935, 0.74, 0.98), (0.42, 0.925, 0.78, 0.99)], "wl": "0123456789.-:,"},
}
INDEX_TIER_ROI = (0.02, 0.835, 0.16, 0.905)


def _otsu(g):
    h = g.histogram()
    total, sm = sum(h), sum(i * v for i, v in enumerate(h))
    sb = wb = 0
    best = (0, 128)
    for t in range(256):
        wb += h[t]
        if wb == 0: continue
        wf = total - wb
        if wf == 0: break
        sb += t * h[t]
        v = wb * wf * (sb / wb - (sm - sb) / wf) ** 2
        if v > best[0]: best = (v, t)
    return best[1]


def _roi_images(crop):
    """Beberapa pra-proses yang berbeda untuk potongan kecil; dibuat bertahap (lazy)."""
    g = ImageOps.grayscale(crop)
    w, h = g.size
    k = max(3, min(8, int(round(110.0 / max(h, 1)))))
    up = lambda im: im.resize((w * k, h * k), Image.LANCZOS)
    yield up(ImageOps.autocontrast(g))
    yield up(g.point(lambda v: 0 if v >= 170 else 255))
    yield up(ImageOps.autocontrast(g)).filter(ImageFilter.SHARPEN)
    t = _otsu(g)
    yield up(g.point(lambda v: 255 if v > t else 0))


def _parse_print(kind, t):
    t = (t or "").strip()
    if kind == "index":
        mt = re.search(r"(?<!\d)(\d{2,5})(?!\d)", t)
        return mt.group(1) if mt else None
    if kind == "shisui":                                                 # 'edisi - print'; print = angka kedua
        mt = re.search(r"(?<!\d)(\d{1,2})\s*[.\-:,]\s*(\d{1,6})(?!\d)", t)
        return mt.group(2) if mt else None
    if kind == "karuta":                                                 # 'print . edisi'
        mt = re.search(r"(?<!\d)(\d{1,6})\s*[.\-:,]\s*\d{1,2}(?!\d)", t)
        if mt: return mt.group(1)
        mt = re.search(r"(?<!\d)(\d{4,7})(?!\d)", t)
        if mt: return mt.group(1)[:-1] if len(mt.group(1)) >= 6 else mt.group(1)
    return None


def _consensus(reads):
    """Nilai print dari beberapa bacaan. Yakin bila >=2 bacaan sama (atau beda 1 digit pada angka panjang)."""
    if not reads: return None, False
    val, n = Counter(reads).most_common(1)[0]
    if n >= 2: return int(val), True
    for i in range(len(reads)):
        for j in range(i + 1, len(reads)):
            a, b = reads[i], reads[j]
            if len(a) == len(b) >= 5 and sum(x != y for x, y in zip(a, b)) <= 1:
                return int(a), True
    return int(reads[0]), False


ROI_SHIFTS = (0.0, -0.03, 0.03, -0.055, 0.055)
ROI_SHIFTS_AUTO = (0.0, -0.03, 0.03)       # geser vertikal (pecahan tinggi kartu): kotak strip bisa meleset beberapa piksel


def read_roi_print(card_im, kind, shifts=None):
    """Baca nomor print dari ROI kartu. Return (nilai, yakin, dy). Hanya memakai ROI kecil sehingga tidak tertipu
    noise dari gambar kartu. Tiap posisi ROI dinilai sendiri-sendiri (konsensus antar-varian pra-proses) dan
    ROI digeser vertikal bila perlu karena tinggi kotak kartu tidak selalu pas."""
    spec = ROI_SPECS[kind]
    w, h = card_im.size
    cfg = "--psm 7 -c tessedit_char_whitelist=" + spec["wl"]
    weak = None
    for dy in (shifts or ROI_SHIFTS):
        for roi in (spec["rois"] if dy == 0 else spec["rois"][:1]):
            top, bot = roi[1] + dy, roi[3] + dy
            if top < 0.5 or bot > 1.0: continue
            crop = card_im.crop((int(w * roi[0]), int(h * top), int(w * roi[2]), int(h * bot)))
            if crop.width < 8 or crop.height < 6: continue
            reads, empty = [], 0
            for img in _roi_images(crop):
                val = _parse_print(kind, pytesseract.image_to_string(img, config=cfg))
                if val is not None: reads.append(val)
                else: empty += 1
                value, ok = _consensus(reads)
                if ok: return value, True, dy
                if not reads and empty >= 2: break         # dua cara pra-proses tanpa angka sama sekali: coba posisi lain
            if reads and weak is None:
                weak = (_consensus(reads)[0], False, dy)   # bacaan tunggal/ragu dari posisi ini; simpan, lanjut cari posisi lebih baik
    return weak if weak else (None, False, 0.0)


def read_index_tier(card_im, letters, dy=0.0):
    """Huruf tier di kotak kiri-bawah kartu Index (A/B/C/S...). None bila tidak terbaca."""
    w, h = card_im.size
    r = INDEX_TIER_ROI
    crop = card_im.crop((int(w * r[0]), int(h * (r[1] + dy)), int(w * r[2]), int(h * (r[3] + dy))))
    if crop.width < 8 or crop.height < 6: return None
    g = ImageOps.grayscale(crop)
    k = 6
    wl = "".join(sorted(set("SABCDEF") | set(c for c in letters if c.isalpha() and len(c) == 1)))
    reads = []
    for img in (ImageOps.autocontrast(g), ImageOps.autocontrast(ImageOps.invert(g))):
        t = pytesseract.image_to_string(img.resize((g.width * k, g.height * k), Image.LANCZOS),
                                        config="--psm 10 -c tessedit_char_whitelist=" + wl).strip().upper()
        if len(t) == 1: reads.append(t)
    if not reads: return None
    val, n = Counter(reads).most_common(1)[0]
    return val if n >= 2 or len(reads) == 1 else None


def score_roi_card(kind, card_im, m, shifts=None):
    """Nilai satu kartu dengan pembaca ROI bot `kind`. Return (skor, label, jenis, yakin) atau None."""
    val, ok, dy = read_roi_print(card_im, kind, shifts)
    if val is None: return None
    if kind == "karuta":
        return 10000000 - val, "Karuta Print (%d)" % val, kind, ok
    if kind == "shisui":
        score = 15000 + (100 - val) if val < 100 else (10000 + (1000 - val) if val < 1000 else 5000 + max(0, 10000 - val))
        return score, "Shisui Print (%d)" % val, kind, ok
    letters = [m.get(k_, "") for k_ in ("tier_ss", "tier_s", "tier_a", "tier_b", "tier_c")]
    letter = read_index_tier(card_im, letters, dy)
    hi = bool(letter) and letter in [str(x).strip().upper() for x in letters[:2] if x]
    mid = bool(letter) and letter in [str(x).strip().upper() for x in letters[2:] if x]
    score, label = _score_index(val, hi, mid)
    return score, label + (" tier %s" % letter if letter else ""), kind, ok


_RARITY_SCORE = {"SSR": 50000, "SR": 40000, "R": 30000, "UC": 20000, "C": 10000}
_RARITY_ALIAS = {"C": "C", "CC": "C", "(": "C", "(C": "C", "(€": "C", "€": "C", "[": "C", "[C": "C",
                 "UC": "UC", "R": "R", "RR": "R", "SR": "SR", "SSR": "SSR"}


def parse_rarity_list(text, n):
    """Daftar '<ikon rarity> Nama • Seri' (bot Summon). Ikon dibaca OCR sebagai huruf (C -> 'C', 'Cc', '(').
    Return [rarity] untuk n baris terakhir yang diawali token ikon yang dikenal, atau [] bila tidak ada."""
    out = []
    for line in (text or "").splitlines():
        mt = re.match(r"^\s*(\S{1,3})\s+[A-Za-z0-9]", line)
        if mt and mt.group(1).upper() in _RARITY_ALIAS:
            out.append(_RARITY_ALIAS[mt.group(1).upper()])
    return out[-n:] if len(out) >= n else []


def annotate_drop(shot, drop, results=None, best=None):
    im = shot.convert("RGB")
    dr = ImageDraw.Draw(im)
    try: fnt = ImageFont.load_default(size=16)
    except Exception: fnt = None
    if drop.get("head"): dr.rectangle(drop["head"], outline=(255, 0, 255), width=1)
    if drop.get("strip"): dr.rectangle(drop["strip"], outline=(255, 215, 0), width=2)
    for i, (l, t, r, b) in enumerate(drop["cards"]):
        col = (0, 200, 0) if i == best else (0, 170, 255)
        dr.rectangle((l, t, r, b), outline=col, width=3)
        label = str(i + 1)
        if results and results[i]: label += " %s (%s)" % (results[i][1], results[i][0])
        dr.rectangle((l + 3, t + 3, l + 3 + 9 * len(label), t + 24), fill=(0, 0, 0))
        dr.text((l + 6, t + 5), label, fill=col, font=fnt)
    for cx, cy in drop["buttons"]:
        dr.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), outline=(255, 0, 0), width=3)
    return im


# ---------- membaca & menilai teks kartu ----------
_SEP_DOT = r"[·•∙⋅]"
_SEP_ANY = r"[·•∙⋅.,:;'’*°+\-–—]"
_DIGIT_FIX = str.maketrans({"O": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8", "Z": "2"})
_DIGIT_FIX_LOWER = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"})
KIND_PRIORITY = ["karuta", "yume", "shisui", "mazoku", "index"]
N_VARIANTS = 6


def fix_digits(text):
    """Perbaiki salah baca OCR di dalam token angka (O->0, I->1, S->5, ...). Teks biasa tidak disentuh."""
    def fix(mt):
        tok = mt.group(0)
        return tok.translate(_DIGIT_FIX) if sum(c.isdigit() for c in tok) >= 2 else tok
    return re.sub(r"[0-9OILSBZ|]{3,8}", fix, text)


def _tier_hit(letters, text):
    """Huruf tier harus berdiri sendiri; 'S' dari "ALICE'S" atau huruf di dalam kata tidak dihitung."""
    letters = (letters or "").strip().upper()
    if not letters: return False
    return bool(re.search(r"(?<![A-Z0-9'’])" + re.escape(letters) + r"(?![A-Z0-9'’])", text))


def _det_mazoku(text, m, loose):
    for tag, score in (("SSR", 50000), ("SR", 40000), ("R", 30000), ("UC", 20000), ("C", 10000)):
        if not loose and tag in ("R", "C"): continue      # 'R'/'C' sendirian terlalu sering muncul sebagai noise OCR
        if re.search(r"(?<![A-Z0-9])" + tag + r"(?![A-Z0-9])", text):
            return score, "Mazoku " + tag, "mazoku", True
    return None


def _det_karuta(text, m, loose):
    mt = re.search(r"(?<!\d)(\d{1,6})\s*" + _SEP_DOT + r"\s*(\d{1,2})(?!\d)", text)
    if not mt:
        digits = r"\d{1,6}" if loose else r"\d{4,6}"
        mt = re.search(r"(?<!\d)(" + digits + r")\s*" + _SEP_ANY + r"\s*(\d{1,2})(?!\d)", text)
    if not mt: return None
    p = int(mt.group(1))
    return 10000000 - p, "Karuta Print (%d)" % p, "karuta", True     # print kecil = skor besar, semua digit


def _det_yume(text, m, loose):
    mt = re.search(r"[#*＃]\s*(\d{1,5})(?!\d)", text)
    if not mt: return None
    p = int(mt.group(1))
    bonus = 10000 if _tier_hit(m.get("tier_s"), text) else 0
    if p < 100: score = 15000 + (100 - p)
    elif p < 1000: score = 10000 + (1000 - p)
    else: score = 5000 + max(0, 10000 - p)
    return score + bonus, "Yume Print (%d)" % p, "yume", True


def _det_shisui(text, m, loose):
    mt = re.search(r"(?<!\d)\d+\s*[-:]\s*(\d+)(?!\d)", text)
    if not mt: return None
    p = int(mt.group(1))
    if p < 100: score = 15000 + (100 - p)
    elif p < 1000: score = 10000 + (1000 - p)
    else: score = 5000 + max(0, 10000 - p)
    return score, "Shisui Print (%d)" % p, "shisui", True


def _score_index(p, hi, mid):
    if hi:
        return (10000 + (50 - p), "S/SS Print Rendah (%d)" % p) if p <= 50 else (5000 + max(0, 1000 - p), "S/SS Print Tinggi (%d)" % p)
    if mid:
        return (8000 + (5 - p), "A/B/C Print Super Rendah (%d)" % p) if p <= 5 else (1000 + max(0, 1000 - p), "A/B/C Print Biasa (%d)" % p)
    return 100 + max(0, 1000 - p), "Tier Lain (%d)" % p


def _det_index(text, m, loose):
    mt = re.search(r"(?<!\d)(\d{1,4})\s*[VX]\s*(\d+)", text)
    explicit = bool(mt)
    if mt:
        p = int(mt.group(1))
    else:
        clean = re.sub(r"[VX]\s*\d+", "", text)
        nums = re.findall(r"(?<!\d)\d{1,4}(?!\d)", clean)
        p = min(int(n) for n in nums) if nums else 9999
    hi = _tier_hit(m.get("tier_ss"), text) or _tier_hit(m.get("tier_s"), text)
    mid = _tier_hit(m.get("tier_a"), text) or _tier_hit(m.get("tier_b"), text) or _tier_hit(m.get("tier_c"), text)
    score, label = _score_index(p, hi, mid)
    confident = explicit or (loose and p != 9999 and (hi or mid))
    return score, label, "index", confident


DETECTORS = {"mazoku": _det_mazoku, "karuta": _det_karuta, "yume": _det_yume,
             "shisui": _det_shisui, "index": _det_index}


def parse_card_text(raw, m, bot="auto"):
    """Return (skor, label, jenis, yakin) atau None. Skor lebih besar = kartu lebih bagus.
    bot='auto' memakai pola yang ketat; bot tertentu memakai pola khusus bot itu saja (lebih longgar)."""
    text = fix_digits(re.sub(r"\s+", " ", (raw or "").upper())).strip()
    kinds = ["mazoku", "karuta", "yume", "shisui", "index"] if bot == "auto" else [bot if bot in DETECTORS else "index"]
    for kind in kinds:
        r = DETECTORS[kind](text, m, bot != "auto")
        if r: return r
    return None


def best_parse(texts, m, bot):
    """Dari beberapa hasil OCR satu kartu, ambil hasil pertama yang yakin (kalau ada)."""
    weak = None
    for t in texts:
        r = parse_card_text(t, m, bot)
        if r and r[3]: return r
        if r and weak is None: weak = r
    return weak


def resolve_cards(texts_per_card, m, bot):
    """Nilai semua kartu dengan ATURAN YANG SAMA. Di mode otomatis, jenis bot ditentukan dari
    kartu-kartu yang terbaca yakin (suara terbanyak), lalu semua kartu dinilai ulang memakai jenis itu,
    sehingga skor antar kartu selalu sebanding."""
    res = [best_parse(t, m, bot) for t in texts_per_card]
    dom = bot if bot != "auto" else None
    if bot == "auto":
        kinds = [r[2] for r in res if r and r[3]]
        if kinds:
            cnt = Counter(kinds)
            top = max(cnt.values())
            dom = next(k for k in KIND_PRIORITY if cnt.get(k) == top)
            res = [best_parse(t, m, dom) for t in texts_per_card]
    return res, dom


def _variant(gray, idx):
    """Cara pra-proses OCR, dicoba berurutan. Isolasi teks terang (ambang piksel) lebih dulu karena
    teks print biasanya terang di atas latar ramai; varian lain jadi cadangan."""
    w, h = gray.size
    def up(im, k): return im.resize((w * k, h * k), Image.LANCZOS)
    if idx == 0: return up(gray.point(lambda v: 0 if v >= 225 else 255), 3), "--psm 6"
    if idx == 1: return up(ImageOps.autocontrast(ImageOps.invert(gray)), 4), "--psm 6"      # varian lama
    if idx == 2: return up(gray.point(lambda v: 0 if v >= 240 else 255), 3), "--psm 6"
    if idx == 3: return up(gray.point(lambda v: 0 if v >= 200 else 255), 3), "--psm 6"
    if idx == 4: return up(ImageOps.autocontrast(gray), 3), "--psm 11"
    return up(gray.point(lambda v: 0 if v <= 70 else 255), 3), "--psm 6"                       # teks gelap


def ocr_card(card_im, m, bot, texts):
    """OCR bertahap (maks. N_VARIANTS cara pra-proses) sampai ada hasil yang yakin.
    `texts` diisi di tempat supaya bisa dilanjutkan dengan aturan bot yang berbeda."""
    r = best_parse(texts, m, bot)
    if r and r[3]: return texts
    gray = ImageOps.grayscale(card_im)
    while len(texts) < N_VARIANTS:
        img, cfg = _variant(gray, len(texts))
        texts.append(pytesseract.image_to_string(img, config=cfg))
        r = parse_card_text(texts[-1], m, bot)
        if r and r[3]: break
    return texts


# =====================================================================
#  Dialog Ubah Pesan
# =====================================================================
class MessageDialog(tk.Toplevel):
    def __init__(self, parent, m=None):
        super().__init__(parent)
        self.title("Pesan baru" if m is None else "Ubah pesan")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        if m:
            when, text = parse_dt(m["when"]), m["text"]
            repeat, count, interval, every = m["repeat"], m["count"], m["interval"], m["every_sec"]
            target, rel_pos = m.get("target", ""), m.get("rel_pos")
            active_start, active_end = m.get("active_start", ""), m.get("active_end", "")
            max_sends, days, seq_delay = m.get("max_sends", 0), m.get("days", []), m.get("seq_delay", 5)
            enters, pause = m.get("enters", 1), m.get("enter_pause", 1.0)
            channel, simulation = m.get("channel", ""), m.get("simulation", False)
            smart_claim, claim_timeout = m.get("smart_claim", False), m.get("claim_timeout", 15)
            claim_btn1, claim_btn2, claim_btn3 = m.get("claim_btn1", ""), m.get("claim_btn2", ""), m.get("claim_btn3", "")
            tier_ss, tier_s, tier_a, tier_b, tier_c = m.get("tier_ss", ""), m.get("tier_s", ""), m.get("tier_a", ""), m.get("tier_b", ""), m.get("tier_c", "")
            claim_bot, claim_mode, claim_fallback = m.get("claim_bot", "auto"), m.get("claim_mode", "numbers"), m.get("claim_fallback", "strict")
        else:
            when, text = datetime.now() + timedelta(minutes=2), ""
            repeat, count, interval, every = "once", 1, 60, 600
            target, rel_pos = "", None
            active_start, active_end = "", ""
            max_sends, days, seq_delay = 0, [], 5
            enters, pause = 1, 1.0
            channel, simulation = "", False
            smart_claim, claim_timeout = False, 15
            claim_btn1, claim_btn2, claim_btn3 = "", "", ""
            tier_ss, tier_s, tier_a, tier_b, tier_c = "SS", "S", "A", "B", "C"
            claim_bot, claim_mode, claim_fallback = "auto", "numbers", "strict"

        self.date_var = tk.StringVar(value=when.strftime("%Y-%m-%d"))
        self.time_var = tk.StringVar(value=when.strftime("%H:%M:%S"))
        self.repeat_var = tk.StringVar(value=REPEAT_LABELS[repeat])
        self.every_min_var = tk.StringVar(value=str(every // 60))
        self.every_sec_var = tk.StringVar(value=str(every % 60))
        self.count_var = tk.StringVar(value=str(count))
        self.interval_var = tk.StringVar(value=str(interval))
        self.enters_var = tk.StringVar(value=str(enters))
        self.pause_var = tk.StringVar(value=f"{pause:g}")
        
        self.target_var = tk.StringVar(value=target)
        self.channel_var = tk.StringVar(value=channel)
        self.rel_pos = rel_pos
        self.active_start_var = tk.StringVar(value=active_start)
        self.active_end_var = tk.StringVar(value=active_end)
        self.max_sends_var = tk.StringVar(value=str(max_sends))
        self.seq_delay_var = tk.StringVar(value=str(seq_delay))
        self.sim_var = tk.BooleanVar(value=simulation)
        
        self.smart_claim_var = tk.BooleanVar(value=smart_claim)
        self.claim_timeout_var = tk.StringVar(value=str(claim_timeout))
        self.claim_b1_var = tk.StringVar(value=claim_btn1)
        self.claim_b2_var = tk.StringVar(value=claim_btn2)
        self.claim_b3_var = tk.StringVar(value=claim_btn3)
        
        self.tier_ss_var = tk.StringVar(value=tier_ss)
        self.tier_s_var = tk.StringVar(value=tier_s)
        self.tier_a_var = tk.StringVar(value=tier_a)
        self.tier_b_var = tk.StringVar(value=tier_b)
        self.tier_c_var = tk.StringVar(value=tier_c)
        self.claim_bot_var = tk.StringVar(value=BOT_LABELS.get(claim_bot, BOT_LABELS["auto"]))
        self.claim_mode_var = tk.StringVar(value=CLAIM_MODE_LABELS.get(claim_mode, CLAIM_MODE_LABELS["numbers"]))
        self.claim_fb_var = tk.StringVar(value=FALLBACK_LABELS.get(claim_fallback, FALLBACK_LABELS["strict"]))
        
        self.day_vars = [tk.BooleanVar(value=(i in days)) for i in range(7)]

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        tab1 = ttk.Frame(nb, padding=10)
        tab2 = ttk.Frame(nb, padding=10)
        tab3 = ttk.Frame(nb, padding=10)
        tab4 = ttk.Frame(nb, padding=10)
        nb.add(tab1, text="Pesan & Rangkaian")
        nb.add(tab2, text="Jadwal & Batasan")
        nb.add(tab3, text="Target & Channel")
        nb.add(tab4, text="Smart Auto-Claim")

        # TAB 1
        ttk.Label(tab1, text="Pesan (Baris baru = jeda otomatis):").pack(anchor="w")
        self.txt = tk.Text(tab1, width=50, height=8, wrap="word")
        self.txt.pack(fill="both", expand=True, pady=5)
        self.txt.insert("1.0", text)
        ttk.Label(tab1, text="Variabel: {jam}, {tanggal}, {hari}", foreground="#006699").pack(anchor="w")
        
        row_seq = ttk.Frame(tab1)
        row_seq.pack(fill="x", pady=5)
        ttk.Label(row_seq, text="Jeda antar baris (detik):").pack(side="left")
        ttk.Spinbox(row_seq, from_=1, to=60, textvariable=self.seq_delay_var, width=5).pack(side="left", padx=5)
        
        row_enter = ttk.Frame(tab1)
        row_enter.pack(fill="x", pady=5)
        ttk.Label(row_enter, text="Tekan Enter di akhir:").pack(side="left")
        ttk.Spinbox(row_enter, from_=1, to=MAX_ENTERS, textvariable=self.enters_var, width=4).pack(side="left", padx=5)
        ttk.Label(row_enter, text="kali, jeda").pack(side="left")
        ttk.Spinbox(row_enter, from_=0.3, to=10, increment=0.5, textvariable=self.pause_var, width=5).pack(side="left", padx=5)
        ttk.Label(row_enter, text="detik").pack(side="left")

        ttk.Checkbutton(tab1, text="Mode Simulasi (Ketik saja, tidak di-Enter)", variable=self.sim_var).pack(anchor="w", pady=10)

        # TAB 2
        grid2 = ttk.Frame(tab2)
        grid2.pack(fill="both", expand=True)
        
        ttk.Label(grid2, text="Tanggal:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(grid2, textvariable=self.date_var, width=15).grid(row=0, column=1, sticky="w")
        ttk.Label(grid2, text="Jam:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(grid2, textvariable=self.time_var, width=15).grid(row=1, column=1, sticky="w")

        ttk.Label(grid2, text="Pengulangan:").grid(row=2, column=0, sticky="w", pady=3)
        cb = ttk.Combobox(grid2, textvariable=self.repeat_var, values=list(REPEAT_LABELS.values()), state="readonly", width=25)
        cb.grid(row=2, column=1, columnspan=2, sticky="w")
        cb.bind("<<ComboboxSelected>>", self.update_fields)

        ttk.Label(grid2, text="Interval Loop:").grid(row=3, column=0, sticky="w", pady=3)
        frm_loop = ttk.Frame(grid2)
        frm_loop.grid(row=3, column=1, columnspan=2, sticky="w")
        self.sp_every_min = ttk.Spinbox(frm_loop, from_=0, to=10080, textvariable=self.every_min_var, width=5)
        self.sp_every_min.pack(side="left")
        ttk.Label(frm_loop, text="m").pack(side="left", padx=2)
        self.sp_every_sec = ttk.Spinbox(frm_loop, from_=0, to=59, textvariable=self.every_sec_var, width=4)
        self.sp_every_sec.pack(side="left")
        ttk.Label(frm_loop, text="s").pack(side="left")

        ttk.Label(grid2, text="Jumlah kirim & Jeda:").grid(row=4, column=0, sticky="w", pady=3)
        frm_c = ttk.Frame(grid2)
        frm_c.grid(row=4, column=1, columnspan=2, sticky="w")
        self.sp_count = ttk.Spinbox(frm_c, from_=1, to=1000, textvariable=self.count_var, width=5)
        self.sp_count.pack(side="left")
        ttk.Label(frm_c, text="x, jeda").pack(side="left", padx=2)
        self.sp_interval = ttk.Spinbox(frm_c, from_=MIN_INTERVAL, to=86400, textvariable=self.interval_var, width=5)
        self.sp_interval.pack(side="left")
        ttk.Label(frm_c, text="dtk").pack(side="left")

        ttk.Separator(grid2, orient="horizontal").grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)

        ttk.Label(grid2, text="Jam Aktif:").grid(row=6, column=0, sticky="w", pady=3)
        frm_act = ttk.Frame(grid2)
        frm_act.grid(row=6, column=1, columnspan=2, sticky="w")
        ttk.Entry(frm_act, textvariable=self.active_start_var, width=6).pack(side="left")
        ttk.Label(frm_act, text="s/d").pack(side="left", padx=4)
        ttk.Entry(frm_act, textvariable=self.active_end_var, width=6).pack(side="left")

        ttk.Label(grid2, text="Maksimal kirim:").grid(row=7, column=0, sticky="w", pady=3)
        frm_max = ttk.Frame(grid2)
        frm_max.grid(row=7, column=1, columnspan=2, sticky="w")
        ttk.Spinbox(frm_max, from_=0, to=100000, textvariable=self.max_sends_var, width=8).pack(side="left")

        ttk.Label(grid2, text="Hari Aktif:").grid(row=8, column=0, sticky="nw", pady=3)
        frm_days = ttk.Frame(grid2)
        frm_days.grid(row=8, column=1, columnspan=2, sticky="w")
        for i, name in enumerate(DAY_NAMES):
            ttk.Checkbutton(frm_days, text=name, variable=self.day_vars[i]).grid(row=i//4, column=i%4, sticky="w", padx=2)

        # TAB 3
        ttk.Label(tab3, text="Target Window (misal: Discord - Brave):").pack(anchor="w", pady=3)
        ttk.Entry(tab3, textvariable=self.target_var, width=30).pack(anchor="w", pady=3)
        
        ttk.Label(tab3, text="Auto-Pilih Channel (cth: #general):").pack(anchor="w", pady=(10, 0))
        ttk.Entry(tab3, textvariable=self.channel_var, width=30).pack(anchor="w", pady=3)

        ttk.Label(tab3, text="Posisi Klik:").pack(anchor="w", pady=(10, 0))
        self.lbl_pos = ttk.Label(tab3, text="Belum diatur", foreground="#b00020")
        self.lbl_pos.pack(anchor="w")
        self.update_pos_label()
        
        ttk.Button(tab3, text="Ambil Posisi (5 detik)", command=self.capture_pos).pack(anchor="w", pady=5)

        # TAB 4 (SMART CLAIM)
        ttk.Checkbutton(tab4, text="Aktifkan Smart Claim (Baca Teks & Pilih Terbaik)", variable=self.smart_claim_var).pack(anchor="w", pady=5)

        frm_opt = ttk.Frame(tab4)
        frm_opt.pack(fill="x", pady=2)
        ttk.Label(frm_opt, text="Bot / format kartu:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(frm_opt, textvariable=self.claim_bot_var, values=list(BOT_LABELS.values()), state="readonly", width=44).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(frm_opt, text="Mode tombol:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(frm_opt, textvariable=self.claim_mode_var, values=list(CLAIM_MODE_LABELS.values()), state="readonly", width=44).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(frm_opt, text="Jika kartu tak terbaca:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(frm_opt, textvariable=self.claim_fb_var, values=list(FALLBACK_LABELS.values()), state="readonly", width=44).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(frm_opt, text="Batas waktu (detik):").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(frm_opt, from_=3, to=60, textvariable=self.claim_timeout_var, width=5).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(tab4, text="Pilih bot yang dipakai (jangan 'Otomatis') agar penilaian paling akurat.", foreground="#666").pack(anchor="w", pady=(4, 0))

        ttk.Separator(tab4, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(tab4, text="Prioritas Rarity (Kosongkan jika bot tidak pakai huruf):").pack(anchor="w")

        frm_tier = ttk.Frame(tab4)
        frm_tier.pack(fill="x", pady=2)
        for lbl, var in (("SS", self.tier_ss_var), ("S", self.tier_s_var), ("A", self.tier_a_var), ("B", self.tier_b_var), ("C", self.tier_c_var)):
            ttk.Label(frm_tier, text="Tier %s:" % lbl).pack(side="left")
            ttk.Entry(frm_tier, textvariable=var, width=4).pack(side="left", padx=(2, 10))

        ttk.Label(tab4, text="Gambar debug tersimpan di %APPDATA%\\PengirimPesanOtomatis\\debug", foreground="#666").pack(anchor="w", pady=(10, 0))

        # BUTTONS
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="Simpan", command=self.save).pack(side="right", padx=4)
        ttk.Button(btns, text="Batal", command=self.destroy).pack(side="right")

        self.update_fields()
        self.bind("<Escape>", lambda e: self.destroy())
        self.wait_visibility()
        self.grab_set()

    def pick_claim_img(self, var):
        f = filedialog.askopenfilenames(filetypes=[("PNG Image", "*.png")])
        if f: var.set(", ".join(f))

    def update_fields(self, _event=None):
        loop = LABEL_TO_REPEAT[self.repeat_var.get()] == "loop"
        for sp in (self.sp_every_min, self.sp_every_sec): sp.config(state="normal" if loop else "disabled")
        for sp in (self.sp_count, self.sp_interval): sp.config(state="disabled" if loop else "normal")

    def update_pos_label(self):
        if self.rel_pos: self.lbl_pos.config(text=f"Relatif: ({self.rel_pos[0]}, {self.rel_pos[1]})", foreground="#1b7f3b")
        else: self.lbl_pos.config(text="Belum diatur", foreground="#b00020")

    def capture_pos(self):
        self.lbl_pos.config(text="Siapkan kursor...", foreground="black")
        self.update()
        self.countdown(5)

    def countdown(self, count):
        if count > 0:
            self.lbl_pos.config(text=f"Arahkan kursor... {count}")
            self.after(1000, self.countdown, count - 1)
        else:
            p = pyautogui.position()
            target_title = self.target_var.get().strip()
            if target_title:
                try:
                    wins = pygetwindow.getWindowsWithTitle(target_title)
                    if wins:
                        win = wins[0]
                        self.rel_pos = (int(p.x - win.left), int(p.y - win.top))
                    else: self.rel_pos = (int(p.x), int(p.y))
                except Exception: self.rel_pos = (int(p.x), int(p.y))
            else: self.rel_pos = (int(p.x), int(p.y))
            self.update_pos_label()

    @staticmethod
    def to_int(var):
        try: return int(var.get())
        except ValueError: return None

    def save(self):
        text = self.txt.get("1.0", "end").strip()
        if not text: return messagebox.showerror("Pesan kosong", "Tulis pesannya dulu.", parent=self)

        when = None
        date_s, time_s = self.date_var.get().strip(), self.time_var.get().strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                when = datetime.strptime(f"{date_s} {time_s}", f"%Y-%m-%d {fmt}")
                break
            except ValueError: continue
        if when is None: return messagebox.showerror("Format salah", "Gunakan YYYY-MM-DD dan HH:MM.", parent=self)

        repeat = LABEL_TO_REPEAT[self.repeat_var.get()]
        count, interval = self.to_int(self.count_var), self.to_int(self.interval_var)
        e_min, e_sec = self.to_int(self.every_min_var), self.to_int(self.every_sec_var)
        every = e_min * 60 + e_sec if (e_min is not None and e_sec is not None) else 600
        
        if repeat == "loop":
            if every < MIN_INTERVAL: return messagebox.showerror("Error", "Minimal 5 detik.", parent=self)
            count = 1
            if interval is None or interval < MIN_INTERVAL: interval = 60
        else:
            if count is None or count < 1: return messagebox.showerror("Error", "Jumlah kirim minimal 1.", parent=self)
            interval = max(interval or MIN_INTERVAL, MIN_INTERVAL)

        enters = self.to_int(self.enters_var) or 1
        try: pause = float(self.pause_var.get().replace(",", "."))
        except ValueError: pause = 1.0

        ast, aen = self.active_start_var.get().strip(), self.active_end_var.get().strip()
        max_sends = self.to_int(self.max_sends_var) or 0
        seq_delay = self.to_int(self.seq_delay_var) or 5
        days = [i for i, var in enumerate(self.day_vars) if var.get()]

        now = datetime.now()
        if repeat == "once" and when <= now:
            return messagebox.showerror("Waktu lewat", "Pilih waktu di masa depan.", parent=self)
        if repeat != "once":
            when = next_occurrence(when, repeat, every, now)

        self.result = {
            "text": text, "when": when, "repeat": repeat, "count": count,
            "interval": interval, "every_sec": every, "enters": enters, "enter_pause": pause,
            "target": self.target_var.get().strip(), "rel_pos": self.rel_pos,
            "active_start": ast, "active_end": aen, "max_sends": max_sends,
            "days": days, "seq_delay": seq_delay,
            "channel": self.channel_var.get().strip(), "simulation": self.sim_var.get(),
            "smart_claim": self.smart_claim_var.get(),
            "claim_timeout": self.to_int(self.claim_timeout_var) or 15,
            "claim_btn1": self.claim_b1_var.get(),
            "claim_btn2": self.claim_b2_var.get(),
            "claim_btn3": self.claim_b3_var.get(),
            "tier_ss": self.tier_ss_var.get().strip(),
            "tier_s": self.tier_s_var.get().strip(),
            "tier_a": self.tier_a_var.get().strip(),
            "tier_b": self.tier_b_var.get().strip(),
            "tier_c": self.tier_c_var.get().strip(),
            "claim_bot": LABEL_TO_BOT.get(self.claim_bot_var.get(), "auto"),
            "claim_mode": LABEL_TO_CLAIM_MODE.get(self.claim_mode_var.get(), "numbers"),
            "claim_fallback": LABEL_TO_FALLBACK.get(self.claim_fb_var.get(), "strict"),
        }
        self.destroy()

# =====================================================================
#  Jendela Riwayat & Pengaturan
# =====================================================================
class HistoryWindow(tk.Toplevel):
    def __init__(self, parent, history):
        super().__init__(parent)
        self.title("Riwayat Pengiriman")
        self.geometry("600x400")
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        cols = ("waktu", "pesan", "hasil")
        self.tree = ttk.Treeview(frm, columns=cols, show="headings")
        for col, txt, w in [("waktu", "Waktu", 130), ("pesan", "Pesan", 300), ("hasil", "Hasil", 100)]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor="w" if col=="pesan" else "center")
        sb = ttk.Scrollbar(frm, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for h in reversed(history):
            tag = "failed" if h.get("result") == "Gagal" else ("missed" if h.get("result") == "Terlewat" else "")
            self.tree.insert("", "end", values=(h.get("time", ""), h.get("text", ""), h.get("result", "")), tags=(tag,))
        self.tree.tag_configure("failed", foreground="#b00020")
        self.tree.tag_configure("missed", foreground="#ff8c00")

class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Pengaturan Lanjutan")
        self.geometry("420x560")
        self.resizable(False, False)
        self.transient(parent)
        frm = ttk.Frame(self, padding=15)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Cara Input Pesan:").pack(anchor="w", pady=(0, 5))
        self.method_var = tk.StringVar(value=METHOD_LABELS[self.app.method])
        cb = ttk.Combobox(frm, textvariable=self.method_var, state="readonly", values=list(METHOD_LABELS.values()), width=25)
        cb.pack(anchor="w", pady=(0, 15))
        cb.bind("<<ComboboxSelected>>", self.save_method)

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=10)

        self.jitter_var = tk.BooleanVar(value=self.app.settings.get("jitter", True))
        ttk.Checkbutton(frm, text="Aktifkan Jitter (Waktu acak anti-spam)", variable=self.jitter_var, command=self.save_settings).pack(anchor="w", pady=5)
        
        self.sound_var = tk.BooleanVar(value=self.app.settings.get("sound", False))
        ttk.Checkbutton(frm, text="Bunyikan 'Beep' saat sukses mengirim", variable=self.sound_var, command=self.save_settings).pack(anchor="w", pady=5)

        self.claim_debug_var = tk.BooleanVar(value=self.app.settings.get("claim_debug", True))
        ttk.Checkbutton(frm, text="Simpan gambar debug Smart Claim", variable=self.claim_debug_var, command=self.save_settings).pack(anchor="w", pady=5)

        self.auto_cd_var = tk.BooleanVar(value=self.app.settings.get("auto_cd", True))
        ttk.Checkbutton(frm, text="Deteksi Cooldown Otomatis (Baca Chat)", variable=self.auto_cd_var, command=self.save_settings).pack(anchor="w", pady=5)
        
        ttk.Label(frm, text="Jeda Fokus Jendela (detik):").pack(anchor="w", pady=(10, 0))
        self.focus_var = tk.StringVar(value=str(self.app.settings.get("focus_delay", 1.0)))
        ttk.Spinbox(frm, from_=0.5, to=5.0, increment=0.5, textvariable=self.focus_var, width=5, command=self.save_settings).pack(anchor="w", pady=2)

        ttk.Label(frm, text="Jeda Klik Kolom Chat (detik):").pack(anchor="w", pady=(10, 0))
        self.click_var = tk.StringVar(value=str(self.app.settings.get("click_delay", 0.6)))
        ttk.Spinbox(frm, from_=0.2, to=3.0, increment=0.1, textvariable=self.click_var, width=5, command=self.save_settings).pack(anchor="w", pady=2)

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=15)
        
        ttk.Label(frm, text="Jeda Global Antar Pesan (detik):", font=("", 10, "bold")).pack(anchor="w", pady=(0, 5))
        self.global_cd_var = tk.StringVar(value=str(self.app.settings.get("global_cooldown", 0)))
        ttk.Spinbox(frm, from_=0, to=3600, increment=10, textvariable=self.global_cd_var, width=8, command=self.save_settings).pack(anchor="w", pady=2)
        ttk.Label(frm, text="Mencegah bot error 'Cooldown'. (0 = Nonaktif)", foreground="#666").pack(anchor="w")

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=15)

        self.startup_var = tk.BooleanVar(value=self.is_startup_enabled())
        ttk.Checkbutton(frm, text="Jalankan otomatis saat Windows menyala", variable=self.startup_var, command=self.toggle_startup).pack(anchor="w", pady=5)

    def save_method(self, _event=None):
        self.app.method = LABEL_TO_METHOD[self.method_var.get()]
        self.app.save()

    def save_settings(self):
        try:
            self.app.settings["jitter"] = self.jitter_var.get()
            self.app.settings["sound"] = self.sound_var.get()
            self.app.settings["auto_cd"] = self.auto_cd_var.get()
            self.app.settings["claim_debug"] = self.claim_debug_var.get()
            self.app.settings["focus_delay"] = float(self.focus_var.get())
            self.app.settings["click_delay"] = float(self.click_var.get())
            self.app.settings["global_cooldown"] = int(self.global_cd_var.get())
            self.app.save()
        except ValueError: pass

    def is_startup_enabled(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError: return False
        except Exception: return False

    def toggle_startup(self):
        exe_path = sys.executable if getattr(sys, 'frozen', False) else sys.argv[0]
        exe_path = f'"{exe_path}"'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if self.startup_var.get(): winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
            else:
                try: winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError: pass
            winreg.CloseKey(key)
        except Exception as e: messagebox.showerror("Error", f"Gagal mengubah startup: {e}", parent=self)

# =====================================================================
#  Aplikasi Utama
# =====================================================================
class App:
    def __init__(self, root):
        self.root = root
        root.title("Pengirim Pesan Otomatis (Tahap 16 - Smart Claim v2)")
        root.geometry("1100x700")
        root.minsize(900, 550)

        self.path = data_path()
        self.hist_path = history_path()
        self.log_path = log_path()
        self.running = False
        self.paused = False
        self.is_sending = False
        self.next_global_send = None 
        self.method = "paste"
        self.settings = {"jitter": True, "focus_delay": 1.0, "click_delay": 0.6, "global_cooldown": 0, "sound": False, "auto_cd": True, "claim_debug": True}
        
        self.pos, self.messages = self.load()
        self.history = self.load_history()
        self.tray_icon = None
        
        self.sort_col = None
        self.sort_desc = False
        self.log_visible = True

        self.setup_menu()
        self.setup_ui()
        self.setup_tray()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()
        if not TESSERACT_PATH:
            root.after(800, lambda: messagebox.showwarning(
                "Tesseract-OCR tidak ditemukan",
                "Smart Claim butuh Tesseract-OCR.\nUnduh di: https://github.com/UB-Mannheim/tesseract/wiki\n"
                "lalu pasang di lokasi bawaan (C:\\Program Files\\Tesseract-OCR)."))

    def setup_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Impor Pesan...", command=self.import_data)
        file_menu.add_command(label="Ekspor Pesan...", command=self.export_data)
        file_menu.add_command(label="Buka File Log", command=lambda: os.startfile(self.log_path))
        file_menu.add_separator()
        file_menu.add_command(label="Keluar Total", command=self.exit_app)
        menubar.add_cascade(label="File", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Buka Pengaturan...", command=lambda: SettingsWindow(self.root, self))
        menubar.add_cascade(label="Pengaturan", menu=settings_menu)
        self.root.config(menu=menubar)

    def setup_ui(self):
        bar = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        bar.pack(fill="x")
        self.edit_buttons = []
        
        b_up = ttk.Button(bar, text="▲", width=3, command=self.move_up)
        b_up.pack(side="left", padx=(0, 2))
        b_down = ttk.Button(bar, text="▼", width=3, command=self.move_down)
        b_down.pack(side="left", padx=(0, 10))
        
        for label, cmd in (("+ Tambah", self.add), ("Ubah", self.edit), ("Hapus", self.delete),
                           ("Duplikat", self.duplicate), ("Aktif/Nonaktif", self.toggle_enabled), 
                           ("Tes kirim", self.test_send), ("Kirim Sekarang", self.force_send),
                           ("Riwayat", self.show_history), ("Bersihkan Selesai", self.clear_completed)):
            b = ttk.Button(bar, text=label, command=cmd)
            b.pack(side="left", padx=(0, 4))
            if label not in ("Riwayat", "Bersihkan Selesai", "Kirim Sekarang"):
                self.edit_buttons.append(b)
                
        search_frm = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        search_frm.pack(fill="x")
        ttk.Label(search_frm, text="Cari:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh())
        ttk.Entry(search_frm, textvariable=self.search_var, width=30).pack(side="left", padx=5)
        ttk.Label(search_frm, text="(Klik judul kolom untuk mengurutkan)", foreground="#666").pack(side="left")

        mid = ttk.Frame(self.root, padding=(10, 0))
        mid.pack(fill="both", expand=True)
        cols = (("aktif", "Aktif", 50, "center"), ("pesan", "Pesan", 250, "w"),
                ("target", "Target / Ch", 150, "center"), ("jadwal", "Jadwal", 150, "center"), 
                ("ulang", "Ulangi", 150, "center"), ("status", "Status", 150, "w"))
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in cols], show="headings", selectmode="browse", height=10)
        for key, title, width, anchor in cols:
            self.tree.heading(key, text=title, command=lambda c=key: self.sort_tree(c))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "pesan"))
        self.tree.tag_configure("off", foreground="#888888")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit())

        self.log_frm = ttk.Frame(self.root, padding=(10, 5))
        self.log_frm.pack(fill="x")
        self.btn_log = ttk.Button(self.log_frm, text="Sembunyikan Log \u25b2", command=self.toggle_log)
        self.btn_log.pack(anchor="w")
        self.log_text = tk.Text(self.log_frm, height=6, bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9), state="disabled")
        self.log_text.pack(fill="x")

        run_row = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        run_row.pack(fill="x")
        self.btn_run = ttk.Button(run_row, text="\u25b6 Jalankan Penjadwal", command=self.toggle_run)
        self.btn_run.pack(side="left")
        
        self.btn_pause = ttk.Button(run_row, text="\u23f8 Jeda", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=6)
        
        self.status = tk.StringVar(value="Siap. F8 = Darurat.")
        ttk.Label(run_row, textvariable=self.status).pack(side="left", padx=12)

    def toggle_log(self):
        if self.log_visible:
            self.log_text.pack_forget()
            self.btn_log.config(text="Tampilkan Log \u25bc")
            self.log_visible = False
        else:
            self.log_text.pack(fill="x")
            self.btn_log.config(text="Sembunyikan Log \u25b2")
            self.log_visible = True

    def log(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        def _update():
            self.log_text.config(state="normal")
            self.log_text.insert("end", f"[{now}] {msg}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{now}] {msg}\n")
            except: pass
        self.root.after(0, _update)

    def setup_tray(self):
        img = Image.new('RGB', (64, 64), color=(30, 30, 30))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill=(0, 120, 215))
        d.text((24, 20), "P", fill=(255, 255, 255))

        def show_window(icon, item): self.root.after(0, self.root.deiconify)
        def quit_app(icon, item):
            icon.stop()
            self.root.after(0, self.exit_app)

        menu = pystray.Menu(pystray.MenuItem('Buka', show_window, default=True), pystray.MenuItem('Keluar', quit_app))
        self.tray_icon = pystray.Icon("PengirimPesan", img, "Pengirim Pesan Otomatis", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    # ---------- penyimpanan ----------
    def load(self):
        pos, msgs = None, []
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("method") in METHOD_LABELS: self.method = data["method"]
                self.settings.update(data.get("settings", {}))
                p = data.get("pos")
                if isinstance(p, list) and len(p) == 2: pos = (int(p[0]), int(p[1]))
                for raw in data.get("messages", []):
                    m = normalize(raw)
                    if m: msgs.append(m)
            except Exception:
                try: os.replace(self.path, self.path + ".bak")
                except OSError: pass
        return pos, msgs

    def save(self):
        data = {
            "pos": list(self.pos) if self.pos else None,
            "method": self.method,
            "settings": self.settings,
            "messages": [{k: v for k, v in m.items() if not k.startswith("_")} for m in self.messages],
        }
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            try:
                with open(self.path + ".bak", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except: pass
        except OSError as e:
            messagebox.showerror("Gagal menyimpan", str(e))

    def load_history(self):
        if os.path.exists(self.hist_path):
            try:
                with open(self.hist_path, "r", encoding="utf-8") as f: return json.load(f)
            except Exception: pass
        return []

    def add_history(self, text, result):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.history.append({"time": now, "text": text[:50] + ("..." if len(text) > 50 else ""), "result": result})
        if len(self.history) > 200: self.history = self.history[-200:]
        try:
            with open(self.hist_path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except OSError: pass

    def show_history(self): HistoryWindow(self.root, self.history)

    # ---------- tampilan & sorting ----------
    def sort_tree(self, col):
        if self.sort_col == col: self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col
            self.sort_desc = False
        self.refresh()

    def refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().lower()
        
        display_msgs = []
        for m in self.messages:
            if query:
                if query not in m["text"].lower() and query not in m.get("target", "").lower() and query not in m.get("channel", "").lower():
                    continue
            display_msgs.append(m)
            
        if self.sort_col:
            def get_key(m):
                if self.sort_col == "aktif": return m["enabled"]
                elif self.sort_col == "pesan": return m["text"].lower()
                elif self.sort_col == "target": return m.get("target", "").lower()
                elif self.sort_col == "jadwal": return m["when"]
                elif self.sort_col == "ulang": return m["repeat"]
                elif self.sort_col == "status": return m["status"].lower()
                return ""
            display_msgs.sort(key=get_key, reverse=self.sort_desc)
            
        for m in display_msgs:
            text = m["text"].replace("\n", " | ")
            if len(text) > 40: text = text[:40] + "..."
            if m["repeat"] == "loop": ulang = f"Tiap {m['every_sec']}s"
            else: ulang = REPEAT_LABELS[m["repeat"]] + (f" x{m['count']}" if m["count"] > 1 else "")
            
            tgt = m.get("target", "")
            ch = m.get("channel", "")
            if ch: tgt += f" {ch}"
            if not tgt and m.get("rel_pos"): tgt = "Global"
            
            self.tree.insert("", "end", iid=m["id"], tags=(() if m["enabled"] else ("off",)),
                             values=("\u2714" if m["enabled"] else "-", text, tgt, m["when"], ulang, m["status"]))
        if sel and self.tree.exists(sel[0]): self.tree.selection_set(sel[0])
        
        active = sum(1 for m in self.messages if m["enabled"])
        if self.running:
            if self.paused: self.root.title(f"[PAUSED] Pengirim Pesan Otomatis - {active} Aktif")
            else: self.root.title(f"[RUNNING] Pengirim Pesan Otomatis - {active} Aktif")
        else: self.root.title("Pengirim Pesan Otomatis (Tahap 16 - Smart Claim v2)")

    def set_running_ui(self, running):
        state = "disabled" if running else "normal"
        for b in self.edit_buttons: b.config(state=state)
        self.btn_run.config(text="\u25a0 Hentikan" if running else "\u25b6 Jalankan Penjadwal")
        self.btn_pause.config(state="normal" if running else "disabled")
        if not running:
            self.paused = False
            self.btn_pause.config(text="\u23f8 Jeda")

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Pilih pesan", "Klik salah satu pesan di daftar dulu.")
            return None
        return next((m for m in self.messages if m["id"] == sel[0]), None)

    # ---------- kelola pesan ----------
    def move_up(self):
        m = self.selected()
        if not m: return
        idx = self.messages.index(m)
        if idx > 0:
            self.messages[idx], self.messages[idx-1] = self.messages[idx-1], self.messages[idx]
            self.refresh()
            self.save()
            self.tree.selection_set(m["id"])

    def move_down(self):
        m = self.selected()
        if not m: return
        idx = self.messages.index(m)
        if idx < len(self.messages) - 1:
            self.messages[idx], self.messages[idx+1] = self.messages[idx+1], self.messages[idx]
            self.refresh()
            self.save()
            self.tree.selection_set(m["id"])

    def clear_completed(self):
        if messagebox.askyesno("Bersihkan", "Hapus semua pesan berstatus Terkirim/Terlewat/Selesai?"):
            self.messages = [m for m in self.messages if not (m["status"].startswith(("Terkirim", "Terlewat", "Selesai")) or not m["enabled"])]
            self.refresh()
            self.save()
            self.log("Pesan selesai telah dibersihkan.")

    def add(self):
        dlg = MessageDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.result:
            self.messages.append(new_message(**dlg.result))
            self.refresh()
            self.save()

    def edit(self):
        if self.running: return
        m = self.selected()
        if not m: return
        dlg = MessageDialog(self.root, m)
        self.root.wait_window(dlg)
        if dlg.result:
            m.update(text=dlg.result["text"], when=fmt_dt(dlg.result["when"]), repeat=dlg.result["repeat"], 
                     count=dlg.result["count"], interval=dlg.result["interval"], every_sec=dlg.result["every_sec"], 
                     enters=dlg.result["enters"], enter_pause=dlg.result["enter_pause"], target=dlg.result["target"], 
                     rel_pos=dlg.result["rel_pos"], active_start=dlg.result["active_start"], active_end=dlg.result["active_end"], 
                     max_sends=dlg.result["max_sends"], days=dlg.result["days"], seq_delay=dlg.result["seq_delay"],
                     channel=dlg.result["channel"], simulation=dlg.result["simulation"],
                     smart_claim=dlg.result["smart_claim"], claim_timeout=dlg.result["claim_timeout"],
                     claim_btn1=dlg.result["claim_btn1"], claim_btn2=dlg.result["claim_btn2"], claim_btn3=dlg.result["claim_btn3"],
                     tier_ss=dlg.result["tier_ss"], tier_s=dlg.result["tier_s"], tier_a=dlg.result["tier_a"], 
                     tier_b=dlg.result["tier_b"], tier_c=dlg.result["tier_c"],
                     claim_bot=dlg.result["claim_bot"], claim_mode=dlg.result["claim_mode"],
                     claim_fallback=dlg.result["claim_fallback"])
            if m["status"].startswith(("Terlewat", "Terkirim", "Selesai")): m["enabled"] = True
            if m["enabled"]: m["status"] = "Dijadwalkan"
            self.refresh()
            self.save()

    def duplicate(self):
        m = self.selected()
        if not m: return
        new_m = json.loads(json.dumps({k: v for k, v in m.items() if not k.startswith("_")}))
        new_m["id"] = uuid.uuid4().hex[:8]
        new_m["text"] = m["text"] + " (Copy)"
        new_m["status"] = "Dijadwalkan"
        self.messages.append(new_m)
        self.refresh()
        self.save()

    def delete(self):
        m = self.selected()
        if m and messagebox.askyesno("Hapus pesan", "Hapus pesan yang dipilih?"):
            self.messages.remove(m)
            self.refresh()
            self.save()

    def toggle_enabled(self):
        m = self.selected()
        if not m: return
        if not m["enabled"]:
            if m["repeat"] == "once" and parse_dt(m["when"]) < datetime.now() - GRACE:
                return messagebox.showinfo("Waktu lewat", "Jadwal sudah lewat.")
            m["enabled"] = True
            m["status"] = "Dijadwalkan"
        else:
            m["enabled"] = False
            m["status"] = "Nonaktif"
        self.refresh()
        self.save()

    def export_data(self):
        f = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if f:
            try:
                with open(f, "w", encoding="utf-8") as fp: json.dump(self.messages, fp, indent=2)
                messagebox.showinfo("Sukses", "Data berhasil diekspor.")
            except Exception as e: messagebox.showerror("Gagal", str(e))

    def import_data(self):
        f = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if f:
            try:
                with open(f, "r", encoding="utf-8") as fp: data = json.load(fp)
                count = 0
                for raw in data:
                    m = normalize(raw)
                    if m:
                        m["id"] = uuid.uuid4().hex[:8]
                        self.messages.append(m)
                        count += 1
                self.refresh()
                self.save()
                messagebox.showinfo("Sukses", f"{count} pesan berhasil diimpor.")
            except Exception as e: messagebox.showerror("Gagal", str(e))

    # ---------- mengirim (THREADING) ----------
    @staticmethod
    def copy_to_clipboard(text):
        try:
            pyperclip.copy(text)
            time.sleep(0.15)
            norm = lambda s: s.replace("\r\n", "\n").strip()
            return norm(pyperclip.paste()) == norm(text)
        except Exception: return False

    @staticmethod
    def type_text(text):
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line: 
                for char in line:
                    if is_f8_pressed(): return False
                    try:
                        pyautogui.write(char)
                    except Exception:
                        pass 
                    time.sleep(random.uniform(0.01, 0.05))
            if i < len(lines) - 1: 
                if is_f8_pressed(): return False
                pyautogui.hotkey("shift", "enter")
        return True

    # ---------- Smart Claim v2 ----------
    def claim_region(self, win, input_y=None):
        """Area chat yang dipindai: bagian bawah jendela, tanpa kotak input chat."""
        try: sw, sh = pyautogui.size()
        except Exception: sw, sh = 1920, 1080
        if win: left, top, width, height = int(win.left), int(win.top), int(win.width), int(win.height)
        else: left, top, width, height = 0, 0, int(sw), int(sh)
        if -50 < left < 0:                      # jendela maximize: tepi kiri -8px
            width, left = width + left, 0
        y0 = top + int(height * 0.05)
        y1 = top + height
        if input_y: y1 = min(y1, int(input_y) - 30)
        if y1 - y0 < 250: y0 = y1 - 250
        return (left, y0, width, y1 - y0)

    def find_drop(self, shot, allow_ocr=True):
        """Drop TERBARU (paling bawah). Utama: deteksi struktural (cepat, tanpa OCR). Cadangan: OCR tombol angka."""
        d = locate_drop(shot)
        if d: return d
        if not allow_ocr: return None
        btns = ocr_find_digit_buttons(shot)
        if len(btns) < 2: return None
        d = drop_from_buttons(shot, btns)           # tombol dari OCR + gambar kartu dari struktur
        if d: return d
        cards = fallback_cards(btns, shot.size)     # terakhir: perkiraan geometri
        if not cards or any(c[2] - c[0] < 60 or c[3] - c[1] < 80 for c in cards): return None
        union = (min(c[0] for c in cards), min(c[1] for c in cards), max(c[2] for c in cards), max(c[3] for c in cards))
        head = (0, max(0, union[1] - int(0.9 * (union[3] - union[1]))), shot.width, max(0, union[1] - 2))
        return {"method": "OCR", "buttons": btns, "cards": cards, "strip": union, "head": head,
                "clipped": union[1] <= 3, "sig": _sig_of(shot, union)}

    def claim_snapshot(self, m, win, input_y=None):
        """Rekam drop yang SUDAH ada di layar sebelum pesan dikirim, supaya drop lama tidak salah diambil."""
        if m.get("claim_mode", "numbers") != "numbers": return None
        try:
            shot = pyautogui.screenshot(region=self.claim_region(win, input_y))
            d = self.find_drop(shot, allow_ocr=True)
            if d and d.get("sig") is not None:
                self.log("Drop lama terdeteksi di layar (akan diabaikan).")
                return d["sig"]
        except Exception as e:
            self.log(f"Snapshot awal Smart Claim gagal: {e}")
        return None

    def evaluate_cards(self, imgs, m, bot, texts):
        """OCR semua kartu paralel, lalu nilai dengan aturan yang sama. Return (hasil per kartu, jenis bot)."""
        n = len(imgs)
        with ThreadPoolExecutor(max_workers=n) as ex:
            list(ex.map(lambda i: ocr_card(imgs[i], m, bot, texts[i]), range(n)))
        results, dom = resolve_cards(texts, m, bot)
        weak = [i for i, r in enumerate(results) if not (r and r[3])]
        if dom and weak and len(weak) < n:      # kartu yang gagal dicoba lagi dengan aturan bot yang sudah terdeteksi
            with ThreadPoolExecutor(max_workers=len(weak)) as ex:
                list(ex.map(lambda i: ocr_card(imgs[i], m, dom, texts[i]), weak))
            results, dom = resolve_cards(texts, m, bot)
        return results, dom

    def evaluate_drop(self, imgs, head_text, m, bot):
        """Nilai semua kartu. Urutan sumber: daftar teks (Yume) -> daftar rarity (Summon) -> pembaca area kecil
        per bot (Index/Shisui/Karuta) -> OCR seluruh kartu (cadangan, hanya Otomatis/Yume).
        Return (hasil per kartu, teks OCR per kartu, catatan untuk log)."""
        n = len(imgs)
        results, texts, notes = [None] * n, [[] for _ in range(n)], []

        if bot in ("auto", "yume"):                                  # 1) daftar '1. KODE - #56 - Nama'
            listed = parse_drop_list(head_text, n)
            if listed and len(listed) >= (1 if bot == "yume" else max(2, n - 1)):
                notes.append("Daftar teks drop terbaca: " + ", ".join(f"{i + 1}=#{p}" for i, p in sorted(listed.items())))
                for i, p in listed.items():
                    r = _det_yume("#%d" % p, m, True)
                    results[i] = (r[0], r[1] + " [daftar]", r[2], True)

        if bot in ("auto", "mazoku") and all(r is None for r in results):      # 2) daftar rarity bot Summon
            rar = parse_rarity_list(head_text, n)
            if rar and (bot == "mazoku" or all(rar)):
                notes.append("Rarity terbaca: " + ", ".join(f"{i + 1}={t or '?'}" for i, t in enumerate(rar)))
                for i, tag in enumerate(rar):
                    if tag: results[i] = (_RARITY_SCORE[tag], "Mazoku %s [daftar]" % tag, "mazoku", True)

        missing = [i for i in range(n) if results[i] is None]
        kinds = [bot] if bot in ROI_SPECS else (["index", "shisui", "karuta"] if bot == "auto" else [])
        if missing and kinds:                                        # 3) pembaca area kecil per bot
            shifts = ROI_SHIFTS_AUTO if bot == "auto" else None
            tasks = [(kind, i) for kind in kinds for i in missing]
            with ThreadPoolExecutor(max_workers=min(6, len(tasks))) as ex:
                outs = list(ex.map(lambda t: score_roi_card(t[0], imgs[t[1]], m, shifts), tasks))
            per_kind = {kind: [outs[tasks.index((kind, i))] for i in missing] for kind in kinds}
            best_kind = max(kinds, key=lambda k: sum(1 for r in per_kind[k] if r and r[3]))
            best_res = per_kind[best_kind]
            best_cnt = sum(1 for r in best_res if r and r[3])
            # Otomatis: terima hanya bila SEMUA kartu terbaca yakin (bacaan di tata letak yang salah bisa tampak yakin pada sebagian kartu)
            if bot != "auto" or best_cnt == len(missing):
                notes.append(f"Pembaca '{best_kind}': {best_cnt} dari {len(missing)} kartu terbaca yakin.")
                for i, r in zip(missing, best_res): results[i] = r

        missing = [i for i in range(n) if results[i] is None]
        if missing and bot in ("auto", "yume"):                      # 4) cadangan: OCR seluruh kartu
            if any(r and "[daftar]" in r[1] for r in results) or bot == "yume":
                with ThreadPoolExecutor(max_workers=len(missing)) as ex:
                    list(ex.map(lambda i: ocr_card(imgs[i], m, "yume", texts[i]), missing))
                for i in missing:
                    r = best_parse(texts[i], m, "yume")
                    if r:                                           # nilai ulang tanpa bonus tier agar sebanding dengan kartu dari daftar
                        p = int(re.search(r"\((\d+)\)", r[1]).group(1))
                        r = _det_yume("#%d" % p, m, True)
                    results[i] = r
            else:
                sub, _dom = self.evaluate_cards([imgs[i] for i in missing], m, bot, [texts[i] for i in missing])
                for i, r in zip(missing, sub): results[i] = r
        kinds_ok = Counter(r[2] for r in results if r and r[3])
        if len(kinds_ok) > 1:                                        # skala skor antar-jenis tidak sebanding
            main = max(kinds_ok, key=lambda k: (kinds_ok[k], -KIND_PRIORITY.index(k)))
            notes.append("Peringatan: jenis bacaan campur (%s); hanya '%s' yang dipakai." % (", ".join(sorted(kinds_ok)), main))
            results = [(r[0], r[1] + " (jenis lain)", r[2], False) if (r and r[3] and r[2] != main) else r for r in results]
        return results, texts, notes

    def _click_at(self, ox, oy, pt):
        pyautogui.moveTo(int(ox + pt[0]), int(oy + pt[1]), duration=0.15)
        pyautogui.click()

    def check_and_click_card(self, m, win=None, baseline_sig=None, input_y=None):
        if not m.get("smart_claim"): return False
        if not TESSERACT_PATH:
            self.log("Smart Claim: Tesseract-OCR tidak ditemukan. Pasang dulu lalu coba lagi.")
            return False

        timeout = max(3, int(m.get("claim_timeout", 15)))
        mode = m.get("claim_mode", "numbers")
        bot = m.get("claim_bot", "auto")
        fallback = m.get("claim_fallback", "strict")
        debug = self.settings.get("claim_debug", True)
        t0 = time.time()
        deadline = t0 + timeout
        prev_sig = None
        attempts = 0
        said_wait = said_old = said_clip = False
        last_err = ""

        while time.time() < deadline:
            if is_f8_pressed(): return False
            try:
                region = self.claim_region(win, input_y)
                ox, oy = region[0], region[1]
                shot = pyautogui.screenshot(region=region)
                elapsed = time.time() - t0

                # ---- mode satu tombol 'Claim'
                if mode == "single":
                    if elapsed < 1.2:                       # beri waktu bot membalas supaya tidak menekan tombol drop lama
                        time.sleep(0.3); continue
                    pt = ocr_find_claim_button(shot)
                    if pt:
                        self.log("Tombol 'Claim' terbaru terdeteksi. Mengklaim...")
                        self._click_at(ox, oy, pt)
                        return True
                    time.sleep(0.5); continue

                # ---- mode tombol angka: cari drop terbaru
                drop = self.find_drop(shot, allow_ocr=elapsed > 1.5)
                if drop is None:
                    if not said_wait: self.log("Menunggu drop muncul..."); said_wait = True
                    time.sleep(0.4); continue
                if baseline_sig is not None and same_sig(drop["sig"], baseline_sig):
                    if not said_old: self.log("Yang terlihat masih drop lama, menunggu drop baru..."); said_old = True
                    time.sleep(0.4); continue
                if drop.get("clipped") and elapsed < 4:     # gambar terpotong di tepi atas = hampir pasti drop lama yang terdorong naik
                    if not said_clip: self.log("Gambar drop terpotong, menunggu drop baru..."); said_clip = True
                    time.sleep(0.4); continue
                if prev_sig is None or not same_sig(prev_sig, drop["sig"]):
                    prev_sig = drop["sig"]                  # tunggu sampai gambar selesai dimuat/stabil
                    time.sleep(0.35); continue

                n = len(drop["cards"])
                self.log(f"Drop baru terdeteksi ({drop['method']}): {n} kartu. Membaca kartu...")
                imgs = [shot.crop(tuple(int(v) for v in c)) for c in drop["cards"]]

                head_text = ""
                if bot in ("auto", "yume", "mazoku"):       # bot lain tidak punya daftar teks: hemat waktu OCR
                    head_text = read_head_text(shot, drop)
                    low = head_tail(head_text).lower()
                    if "expired" in low or "cannot be grabbed" in low:
                        self.log("DROP EXPIRED terdeteksi! Membatalkan Smart Claim.")
                        return False

                results, texts, notes = self.evaluate_drop(imgs, head_text, m, bot)
                for note in notes: self.log(note)
                for i, r in enumerate(results):
                    seen = (texts[i][-1] if texts[i] else "").replace("\n", " ").strip()[:45]
                    desc = ("%s | skor %s" % (r[1], r[0]) + ("" if r[3] else " (ragu)")) if r else "tidak terbaca"
                    self.log(f"Kartu {i + 1}: {desc}" + (f" | OCR: {seen}" if seen else ""))

                ok = [i for i, r in enumerate(results) if r and r[3]]
                best = None
                if len(ok) < n:
                    attempts += 1
                    if attempts < 3 and deadline - time.time() > 3:       # gambar mungkin belum selesai dimuat / bacaan ragu
                        self.log(f"{n - len(ok)} kartu belum terbaca yakin; mencoba membaca ulang...")
                        prev_sig = None
                        time.sleep(0.6)
                        continue
                if len(ok) == n:
                    best = max(ok, key=lambda i: (results[i][0], -i))       # skor tertinggi, seri -> kartu paling kiri
                elif ok and fallback in ("partial", "first"):
                    best = max(ok, key=lambda i: (results[i][0], -i))
                    self.log(f"Peringatan: {n - len(ok)} kartu tidak terbaca, memilih dari kartu yang terbaca saja.")
                elif fallback == "first":
                    best = 0
                    self.log("Semua kartu tidak terbaca. Memakai cadangan: klik kartu 1.")
                else:
                    self.log(f"{n - len(ok)} dari {n} kartu tidak terbaca. Klaim dilewati agar tidak salah pilih (cek gambar debug).")

                if debug:
                    save_debug_image(annotate_drop(shot, drop, results, best), f"claim_{datetime.now():%H%M%S}.jpg")
                if best is None: return False

                # ---- pindai ulang tepat sebelum klik: chat bisa bergeser selama OCR berjalan
                target, region2 = None, region
                for _ in range(3):
                    region2 = self.claim_region(win, input_y)
                    shot2 = pyautogui.screenshot(region=region2)
                    d2 = self.find_drop(shot2, allow_ocr=(drop["method"] != "struktur"))
                    if d2 and len(d2["buttons"]) == len(drop["buttons"]) and same_sig(d2["sig"], drop["sig"]):
                        target = d2["buttons"][best]
                        break
                    time.sleep(0.25)
                if target is None:
                    self.log("Tata letak chat berubah sebelum klik; memindai ulang agar tidak salah kartu.")
                    prev_sig = None
                    continue

                self.log(f"MENGKLIK KARTU {best + 1}: {results[best][1]}")
                self._click_at(region2[0], region2[1], target)
                return True

            except pyautogui.FailSafeException:
                raise
            except Exception as e:
                if str(e) != last_err:
                    self.log(f"Smart Claim error: {e}")
                    last_err = str(e)
            time.sleep(0.5)

        self.log("Waktu habis. Drop tidak muncul atau gagal diklaim.")
        return False

    def detect_cooldown(self, click_x, click_y):
        if not self.settings.get("auto_cd", True): return 0
        try:
            region = (int(click_x) - 400, int(click_y) - 500, 800, 500)
            im = pyautogui.screenshot(region=region)
            text = pytesseract.image_to_string(im).lower().replace("\n", " ")
            
            keywords = ["cooldown", "expires in", "try again", "wait", "before dropping", "off roll", "must wait"]
            
            if any(k in text for k in keywords):
                match_sec = re.search(r'(\d+)\s*(second|sec|detik)', text)
                if match_sec: return int(match_sec.group(1)) + 10
                    
                match_min = re.search(r'(\d+)\s*(minute|min|menit)', text)
                if match_min: return (int(match_min.group(1)) * 60) + 10
                    
                match_time = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)', text)
                if match_time:
                    target_h = int(match_time.group(1))
                    target_m = int(match_time.group(2))
                    ampm = match_time.group(3)
                    if ampm == 'pm' and target_h != 12: target_h += 12
                    if ampm == 'am' and target_h == 12: target_h = 0
                    now = datetime.now()
                    target_time = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
                    if target_time < now: target_time += timedelta(days=1)
                    delta = (target_time - now).total_seconds()
                    if 0 < delta < 7200: return int(delta) + 10
        except Exception as e:
            self.log(f"Gagal cek cooldown: {e}")
        return 0

    def _send_thread(self, m):
        old_clipboard = ""
        try: old_clipboard = pyperclip.paste()
        except: pass
        
        try:
            for attempt in range(1, 4):
                try:
                    if is_f8_pressed(): return "aborted"
                    if attempt > 1: time.sleep(5)
                        
                    time.sleep(0.5)
                    target_title = m.get("target", "").strip()
                    rel_pos = m.get("rel_pos")
                    channel = m.get("channel", "").strip()
                    
                    click_x, click_y = None, None
                    win = None
                    need_switch = True
                    
                    if target_title:
                        try:
                            wins = pygetwindow.getWindowsWithTitle(target_title)
                            valid_wins = [w for w in wins if w.visible]
                            if not valid_wins: continue
                            win = valid_wins[0]
                            for w in valid_wins:
                                if target_title.lower() in w.title.lower(): win = w; break
                            try:
                                hwnd = ctypes.windll.user32.FindWindowW(None, win.title)
                                if hwnd:
                                    if ctypes.windll.user32.IsIconic(hwnd): ctypes.windll.user32.ShowWindow(hwnd, 9)
                                    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                                    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
                                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                            except Exception: pass
                            time.sleep(self.settings.get("focus_delay", 1.0))
                            if is_f8_pressed(): return "aborted"
                            if rel_pos: click_x, click_y = win.left + rel_pos[0], win.top + rel_pos[1]
                            if channel and win:
                                try:
                                    current_title = win.title.lower()
                                    clean_channel = channel.replace("#", "").lower()
                                    if clean_channel in current_title: need_switch = False
                                except Exception: pass
                        except Exception: pass
                    
                    if click_x is None and self.pos: click_x, click_y = self.pos
                    if click_x is None: return "error"

                    pyautogui.moveTo(click_x, click_y, duration=0.25)
                    pyautogui.click()
                    time.sleep(self.settings.get("click_delay", 0.6))

                    if channel and need_switch:
                        self.log(f"Pindah ke {channel}...")
                        pyautogui.hotkey("ctrl", "k")
                        time.sleep(0.8)
                        pyautogui.write(channel, interval=0.05)
                        time.sleep(1.0)
                        pyautogui.press("enter")
                        time.sleep(1.0)

                    raw_text = process_variables(m["text"])
                    method = self.method
                    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                    
                    if method == "paste" and not self.copy_to_clipboard(raw_text):
                        if raw_text.isascii(): method = "type"
                        else: return "error"

                    pyautogui.moveTo(click_x, click_y, duration=0.25)
                    pyautogui.click()
                    time.sleep(0.5)

                    claim_base = None
                    if m.get("smart_claim") and not m.get("simulation"):
                        claim_base = self.claim_snapshot(m, win, click_y)

                    for i, line in enumerate(lines):
                        if is_f8_pressed(): return "aborted"
                        if i > 0: time.sleep(m.get("seq_delay", 5))
                        if method == "paste":
                            if i == 0 or not self.copy_to_clipboard(line): self.copy_to_clipboard(line)
                            pyautogui.keyDown(PASTE_KEY)
                            pyautogui.press("v")
                            pyautogui.keyUp(PASTE_KEY)
                        else:
                            if not self.type_text(line): return "aborted"
                        
                        time.sleep(0.5)
                        
                        if not m.get("simulation", False):
                            for j in range(m["enters"]):
                                if is_f8_pressed(): return "aborted"
                                if j: time.sleep(m["enter_pause"])
                                pyautogui.press("enter")
                        time.sleep(0.5)

                    if m.get("smart_claim") and not m.get("simulation"):
                        try:
                            self.check_and_click_card(m, win, claim_base, click_y)
                        except Exception as e:
                            self.log(f"Smart Claim error: {e}")

                    if self.settings.get("auto_cd", True) and not m.get("simulation"):
                        self.log("Memeriksa status cooldown...")
                        time.sleep(4) 
                        cd_seconds = self.detect_cooldown(click_x, click_y)
                        if cd_seconds > 0:
                            self.log(f"COOLDOWN TERDETEKSI! Menunda jadwal selama {cd_seconds} detik.")
                            m["_next"] = datetime.now() + timedelta(seconds=cd_seconds)
                            m["status"] = f"Cooldown {cd_seconds}s"
                            return "cooldown"

                    return "ok"
                    
                except pyautogui.FailSafeException: return "aborted"
                except Exception as e: self.log(f"Error ({attempt}/3): {e}")
                    
            return "error"
        finally:
            if old_clipboard:
                try: pyperclip.copy(old_clipboard)
                except: pass

    def _on_send_complete(self, m, result):
        self.is_sending = False
        if result == "ok":
            self.log(f"Sukses mengirim: {m['text'][:20]}...")
            if self.settings.get("sound"): 
                try: winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except: pass
            mark_sent(m, datetime.now(), self.settings.get("jitter", True))
            self.add_history(m["text"], "Terkirim")
            
            cd = self.settings.get("global_cooldown", 0)
            if cd > 0:
                self.next_global_send = datetime.now() + timedelta(seconds=cd)
                self.log(f"Jeda Global aktif: Menunggu {cd} detik...")
        elif result == "cooldown":
            self.log(f"Pesan ditunda karena cooldown.")
        elif result == "aborted":
            self.halt("Dihentikan paksa (F8).")
            return
        else:
            self.log(f"Gagal mengirim: {m['text'][:20]}...")
            self.halt("Gagal mengirim pesan. Cek Live Log.")
            return

        self.refresh()
        self.save()
        if self.running and not self.paused:
            self.root.after(1000, self.tick)

    def test_send(self):
        m = self.selected()
        if not m: return
        if not messagebox.askyesno("Tes kirim", "Pesan ini akan langsung diproses. Lanjutkan?"): return
        
        self.log("Memulai tes kirim...")
        self.root.iconify()
        self.is_sending = True 
        threading.Thread(target=self._thread_wrapper, args=(m,), daemon=True).start()

    def force_send(self):
        m = self.selected()
        if not m: return
        if not messagebox.askyesno("Kirim Sekarang", "Kirim pesan ini sekarang dan perbarui jadwalnya?"): return
        
        self.log("Memaksa kirim sekarang...")
        self.root.iconify()
        self.is_sending = True 
        threading.Thread(target=self._thread_wrapper, args=(m,), daemon=True).start()

    # ---------- penjadwal ----------
    def toggle_run(self):
        if self.running: self.halt("Penjadwal dihentikan.")
        else: self.start_scheduler()

    def toggle_pause(self):
        if not self.running: return
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.config(text="\u25b6 Lanjutkan")
            self.status.set("Penjadwal DIJEDA.")
            self.log("Penjadwal dijeda.")
        else:
            self.btn_pause.config(text="\u23f8 Jeda")
            self.status.set("Penjadwal dilanjutkan.")
            self.log("Penjadwal dilanjutkan.")
            self.tick()
        self.refresh()

    def start_scheduler(self):
        now = datetime.now()
        for m in self.messages:
            if m["enabled"]: prepare_message(m, now)
        self.refresh()
        self.save()
        
        if not any(m["enabled"] for m in self.messages):
            return messagebox.showinfo("Tidak ada jadwal", "Tidak ada pesan aktif yang valid.")
            
        self.running = True
        self.paused = False
        self.next_global_send = None
        self.set_running_ui(True)
        prevent_sleep()
        self.log("Penjadwal berjalan.")
        self.tick()

    def halt(self, text):
        self.running = False
        self.paused = False
        allow_sleep()
        for m in self.messages:
            if m["enabled"] and m["status"].startswith(("Menunggu", "Mengirim")):
                m["status"] = "Dijadwalkan"
        self.set_running_ui(False)
        self.log(text)
        self.refresh()
        self.save()
        self.root.deiconify()

    def tick(self):
        if not self.running or self.is_sending or self.paused: return
        if is_f8_pressed():
            self.halt("Dihentikan: Tombol darurat F8.")
            return
            
        if self.next_global_send and datetime.now() < self.next_global_send:
            sisa_cd = int((self.next_global_send - datetime.now()).total_seconds())
            self.status.set(f"Jeda Global (Anti-Cooldown): {sisa_cd} detik tersisa...")
            self.root.after(1000, self.tick)
            return

        now = datetime.now()
        due = sorted((m for m in self.messages if is_due(m, now)), key=lambda m: m["_next"])
        
        if due:
            m = due[0]
            if is_overdue(m, now):
                finish_cycle(m, "Terlewat", now)
                self.add_history(m["text"], "Terlewat")
                self.log(f"Terlewat: {m['text'][:20]}...")
            else:
                if m["days"] and now.weekday() not in m["days"]:
                    m["_next"] = now + timedelta(days=1)
                    self.refresh()
                    self.root.after(1000, self.tick)
                    return
                if not is_within_active_hours(now.time(), m["active_start"], m["active_end"]):
                    m["_next"] = now + timedelta(minutes=15)
                    self.refresh()
                    self.root.after(1000, self.tick)
                    return

                self.is_sending = True
                self.root.iconify()
                self.log(f"Mengirim: {m['text'][:20]}...")
                self.status.set(f"Mengirim: {m['text'][:20]}...")
                threading.Thread(target=self._thread_wrapper, args=(m,), daemon=True).start()
                return 
            self.refresh()
            self.save()

        upcoming = [m["_next"] for m in self.messages if m["enabled"] and m.get("_next")]
        if not upcoming:
            self.halt("Selesai: semua jadwal sudah dijalankan.")
            return
        
        active_count = sum(1 for m in self.messages if m["enabled"])
        sisa = max(0, int((min(upcoming) - datetime.now()).total_seconds()))
        if sisa == 0: self.status.set("Menyiapkan pengiriman...")
        else: self.status.set(f"Berjalan ({active_count} aktif). Berikutnya dlm {timedelta(seconds=sisa)}.")
        self.root.after(500, self.tick)
        
    def _thread_wrapper(self, m):
        try:
            success = self._send_thread(m)
            self.root.after(0, self._on_send_complete, m, success)
        except Exception as e:
            self.log(f"Thread Crash: {e}")
            self.root.after(0, self._on_send_complete, m, "error")

    def on_close(self):
        ans = messagebox.askyesnocancel("Tutup Aplikasi", "Sembunyikan ke System Tray?\n\nPilih 'Yes' untuk Sembunyikan.\nPilih 'No' untuk Keluar Total.")
        if ans is True: self.root.withdraw()
        elif ans is False: self.exit_app()

    def exit_app(self):
        if self.running and not messagebox.askokcancel("Penjadwal berjalan", "Yakin ingin keluar total?"): return
        allow_sleep()
        self.save()
        if self.tray_icon: self.tray_icon.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()