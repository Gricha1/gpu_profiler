"""Capture a Cursor window and stream JPEG frames for the dashboard."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import io
import time
from typing import Any

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

PW_RENDERFULLCONTENT = 2
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def _window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _exe_path(hwnd: int) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid.value
    )
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        if psapi.GetModuleFileNameExW(handle, None, buf, 520):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _is_cursor_window(title: str, exe: str) -> bool:
    low_t = title.lower()
    low_e = exe.lower().replace("/", "\\")
    if "cursor" in low_t:
        return True
    return low_e.endswith("\\cursor.exe")


def list_cursor_windows() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def _cb(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        exe = _exe_path(hwnd)
        if not _is_cursor_window(title, exe):
            return True
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        iconic = bool(user32.IsIconic(hwnd))
        score = 1
        low = title.lower()
        if "chat" in low or "agent" in low or "conversation" in low:
            score += 5
        if "ssh" in low or "remote" in low:
            score += 2
        if not iconic and w >= 400 and h >= 300:
            score += 10
        elif iconic:
            score -= 3
        found.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "score": score,
                "w": w,
                "h": h,
                "iconic": iconic,
            }
        )
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    found.sort(key=lambda x: (x["score"], x["w"] * x["h"]), reverse=True)
    return found


def pick_cursor_hwnd(preferred_substr: str | None = None) -> int | None:
    wins = list_cursor_windows()
    if not wins:
        return None
    if preferred_substr:
        pref = preferred_substr.lower()
        for w in wins:
            if pref in w["title"].lower():
                return int(w["hwnd"])
    return int(wins[0]["hwnd"])


def ensure_restored(hwnd: int) -> None:
    if not user32.IsWindow(hwnd):
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.15)


def capture_hwnd_jpeg(hwnd: int, max_width: int = 1100, quality: int = 55) -> bytes | None:
    if not user32.IsWindow(hwnd):
        return None
    ensure_restored(hwnd)

    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 80 or height < 80:
        return None

    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bmp)

    # PrintWindow is more reliable for GPU-composited apps than BitBlt alone.
    ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
    if not ok:
        ok = gdi32.BitBlt(mem_dc, 0, 0, width, height, hwnd_dc, 0, 0, 0x00CC0020)

    gdi32.SelectObject(mem_dc, old)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", wt.LONG),
            ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG),
            ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height  # top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf_len = width * height * 4
    buf = (ctypes.c_char * buf_len)()
    gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bi), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)

    if not ok:
        return None

    try:
        from PIL import Image
    except ImportError:
        return None

    img = Image.frombuffer("RGBA", (width, height), bytes(buf), "raw", "BGRA", 0, 1)
    if width > max_width:
        ratio = max_width / float(width)
        img = img.resize((max_width, max(1, int(height * ratio))), Image.Resampling.BILINEAR)
    rgb = img.convert("RGB")
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def focus_hwnd(hwnd: int) -> None:
    try:
        ensure_restored(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


_tracked_hwnd: int | None = None
_tracked_at: float = 0.0


def track_newest_cursor_window(wait_sec: float = 4.0) -> int | None:
    """After launching Cursor, wait briefly and latch the best matching window."""
    global _tracked_hwnd, _tracked_at
    before = {w["hwnd"] for w in list_cursor_windows()}
    deadline = time.time() + wait_sec
    best = None
    while time.time() < deadline:
        wins = list_cursor_windows()
        if not wins:
            time.sleep(0.35)
            continue
        # Prefer a newly appeared window
        fresh = [w for w in wins if w["hwnd"] not in before]
        pool = fresh or wins
        pool.sort(key=lambda x: (x["score"], x["w"] * x["h"]), reverse=True)
        best = pool[0]["hwnd"]
        if pool[0]["score"] >= 5 and not pool[0].get("iconic"):
            break
        time.sleep(0.35)
    if best is not None:
        _tracked_hwnd = int(best)
        _tracked_at = time.time()
        ensure_restored(_tracked_hwnd)
    return _tracked_hwnd


def get_tracked_hwnd() -> int | None:
    global _tracked_hwnd
    if _tracked_hwnd and user32.IsWindow(_tracked_hwnd):
        return _tracked_hwnd
    _tracked_hwnd = pick_cursor_hwnd()
    return _tracked_hwnd
