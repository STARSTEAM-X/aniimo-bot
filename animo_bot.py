import base64
import ctypes
import json
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from ctypes import wintypes
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox

if __name__ == "__main__":
    multiprocessing.freeze_support()

from windows_backend import BackgroundInput, CaptureError, WindowCapture, with_foreground
from capture_cycle import CaptureCycle

try:
    import customtkinter as ctk
    import cv2
    import numpy as np
    import pytesseract
    from PIL import ImageGrab
except ImportError:
    ctk = None
    cv2 = None
    np = None
    pytesseract = None
    ImageGrab = None


TARGET_EXE = "aniimo.exe"
APP_VERSION = "2.5 Background Assist"
CONTROL_MODES = ("Background", "Hybrid", "Foreground")
LOCK_INPUTS = {"Middle Mouse": "middle", "Tab": "tab", "Mouse 4": "mouse4", "Mouse 5": "mouse5"}
TAB_DELAY_MS = 200
CLICK_DELAY_MS = 250
DEFAULT_KEYWORD = "คลังเต็ม"

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
VK_TAB = 0x09
TAB_SCAN_CODE = 0x0F
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
CRYPTPROTECT_UI_FORBIDDEN = 0x01
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
SETTINGS_FILE = APP_DIR / "animo_bot_settings.json"
UI_ANCHOR_FILE = RESOURCE_DIR / "anipod_ui_anchor.png"
LOCK_ANCHOR_FILE = RESOURCE_DIR / "target_lock_anchor.png"
APP_ICON_FILE = RESOURCE_DIR / "Aniimo_Bot.ico"
LOG_FILE = APP_DIR / "log.txt"
UI_DEBUG_FILE = APP_DIR / "ui_debug_last.png"
UI_CAPTURE_DIR = APP_DIR / "ui_captures"
UI_MATCH_THRESHOLD = 0.80
UI_REFERENCE_WIDTH = 1920
UI_CHECK_INTERVAL = 0.10
RIGHT_CLICK_COOLDOWN = 1.5
UI_MISSING_SCANS = 2
UI_LOST_SCANS = 12
UI_CAPTURE_INTERVAL = 1.0
BACKGROUND_PREVIEW_FILE = APP_DIR / "background_capture_preview.png"
MAX_UI_RESTORE_ATTEMPTS = 3
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

LOGGER = logging.getLogger("animo_bot")
LOGGER.setLevel(logging.DEBUG)
LOGGER.propagate = False
if not LOGGER.handlers and multiprocessing.current_process().name == "MainProcess":
    log_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=1, encoding="utf-8"
    )
    log_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(threadName)s | %(message)s")
    )
    LOGGER.addHandler(log_handler)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# ต้องตั้งก่อนสร้างหน้าต่าง Tk เพื่อให้พิกัดและภาพจับหน้าจอเป็น pixel จริง
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
try:
    dpi_aware = bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    if dpi_aware:
        LOGGER.info("เปิด Per-Monitor DPI Awareness V2 สำเร็จ")
    else:
        LOGGER.warning("ตั้ง DPI Awareness ไม่สำเร็จ error=%s", ctypes.get_last_error())
except AttributeError:
    dpi_aware = bool(user32.SetProcessDPIAware())
    LOGGER.info("ใช้ DPI Awareness แบบเดิม result=%s", dpi_aware)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUT_UNION)]

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
kernel32.LocalFree.restype = wintypes.HLOCAL
crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(DATA_BLOB),
    wintypes.LPCWSTR,
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptProtectData.restype = wintypes.BOOL
crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DATA_BLOB),
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptUnprotectData.restype = wintypes.BOOL


def protect_text(value):
    if not value:
        return ""
    raw = value.encode("utf-8")
    source_buffer = ctypes.create_string_buffer(raw)
    source = DATA_BLOB(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    protected = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Animo Bot Discord Webhook",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        encrypted = ctypes.string_at(protected.pbData, protected.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(protected.pbData)


def unprotect_text(value):
    if not value:
        return ""
    raw = base64.b64decode(value)
    source_buffer = ctypes.create_string_buffer(raw)
    source = DATA_BLOB(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    decrypted = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(decrypted),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(decrypted.pbData, decrypted.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(decrypted.pbData)


def process_name_from_window(hwnd):
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(process)


def find_target_window():
    result = []

    @WNDENUMPROC
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and process_name_from_window(hwnd) == TARGET_EXE:
            result.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    return result[0] if result else None


def get_window_box(hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("อ่านตำแหน่งหน้าต่าง aniimo.exe ไม่สำเร็จ")
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise RuntimeError("หน้าต่าง aniimo.exe ไม่มีพื้นที่ให้ตรวจ")
    return rect.left, rect.top, rect.right, rect.bottom


def normalize_text(value):
    return "".join(value.lower().split())


def move_cursor_inside_window(hwnd):
    from windows_backend import client_screen_box

    left, top, right, bottom = client_screen_box(hwnd)
    x = left + (right - left) // 2
    y = top + (bottom - top) // 2
    if not user32.SetCursorPos(x, y):
        raise ctypes.WinError(ctypes.get_last_error())
    return x, y


def press_tab():
    key_down = INPUT()
    key_down.type = INPUT_KEYBOARD
    key_down.ki = KEYBDINPUT(0, TAB_SCAN_CODE, KEYEVENTF_SCANCODE, 0, 0)
    if user32.SendInput(1, ctypes.byref(key_down), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())

    # Aniimo's Unity input backend needs a full scan-code press. Shorter
    # pulses can disappear while focus is changing.
    time.sleep(0.080)

    key_up = INPUT()
    key_up.type = INPUT_KEYBOARD
    key_up.ki = KEYBDINPUT(
        0, TAB_SCAN_CODE, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, 0
    )
    if user32.SendInput(1, ctypes.byref(key_up), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def left_click():
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)


def right_click():
    user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, None)
    user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, None)


def press_lock_input(hwnd, lock_input, stop_event):
    if stop_event.is_set():
        return
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError("เกมต้องอยู่ด้านหน้าก่อนส่งปุ่มล็อกแบบจริง")
    if lock_input == "Tab":
        press_tab()
        return
    down, up, data = {
        "Middle Mouse": (0x0020, 0x0040, 0),
        "Mouse 4": (0x0080, 0x0100, 1),
        "Mouse 5": (0x0080, 0x0100, 2),
    }[lock_input]
    user32.mouse_event(down, 0, 0, data, None)
    try:
        stop_event.wait(0.080)
    finally:
        user32.mouse_event(up, 0, 0, data, None)


def anipod_ui_is_visible(image, anchor, window_width=None):
    screen = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    height, width = screen.shape
    if window_width is None:
        # รองรับการทดสอบด้วยภาพเต็มหน้าจอ
        search_area = screen[int(height * 0.55) : height, 0 : int(width * 0.70)]
        scale_width = width
    else:
        # ตอนทำงานจริง image เป็นเฉพาะมุมล่างซ้ายอยู่แล้ว
        search_area = screen
        scale_width = window_width
    expected_scale = scale_width / UI_REFERENCE_WIDTH
    scales = [expected_scale * factor for factor in (0.90, 0.95, 1.00, 1.05, 1.10)]
    best_score = 0.0
    best_scale = expected_scale

    for scale in scales:
        anchor_width = max(8, round(anchor.shape[1] * scale))
        anchor_height = max(8, round(anchor.shape[0] * scale))
        if search_area.shape[0] < anchor_height or search_area.shape[1] < anchor_width:
            continue
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        scaled_anchor = cv2.resize(
            anchor, (anchor_width, anchor_height), interpolation=interpolation
        )
        result = cv2.matchTemplate(search_area, scaled_anchor, cv2.TM_CCOEFF_NORMED)
        score = float(cv2.minMaxLoc(result)[1])
        if score > best_score:
            best_score = score
            best_scale = scale

    return best_score >= UI_MATCH_THRESHOLD, best_score, best_scale


def save_ui_capture(image, visible, score, scale):
    UI_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    state = "open" if visible else "closed"
    capture_path = UI_CAPTURE_DIR / (
        f"{timestamp}_{state}_score-{score:.3f}_scale-{scale:.3f}.jpg"
    )
    image.save(capture_path, format="JPEG", quality=72, optimize=True)

    captures = sorted(
        UI_CAPTURE_DIR.glob("*.jpg"), key=lambda path: path.stat().st_mtime
    )
    for old_capture in captures[:-40]:
        try:
            old_capture.unlink()
        except OSError:
            LOGGER.warning("ลบภาพ debug เก่าไม่สำเร็จ: %s", old_capture)
    return capture_path


def post_discord(webhook_url, keyword):
    payload = {
        "content": f"🔔 ตรวจพบข้อความใน aniimo.exe\n**{keyword}**",
        "allowed_mentions": {"parse": []},
    }
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "AnimoBot/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord ตอบกลับด้วยสถานะ {response.status}")


class BotSession:
    def __init__(self, hwnd, settings):
        self.hwnd = hwnd
        self.settings = settings
        self.mode = settings[7]
        self.lock_input = settings[8] if len(settings) > 8 else "Tab"
        self.cycle = CaptureCycle()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.capture = None
        self.interaction = BackgroundInput(hwnd, self.stop)

    def available(self):
        if not self.interaction.available():
            return False
        return self.mode in ("Background", "Hybrid") or user32.GetForegroundWindow() == self.hwnd

    def press_lock(self):
        if not self.available():
            raise RuntimeError("หน้าต่างเกมไม่พร้อมรับคำสั่ง")
        if self.mode == "Background":
            # Aniimo reads target-lock through Unity's real input path. Pulse
            # focus only for that one input; capture and clicks stay local.
            def send_after_focus_settles():
                if not self.stop.wait(0.05):
                    press_lock_input(self.hwnd, self.lock_input, self.stop)

            with_foreground(
                self.hwnd, self.stop, send_after_focus_settles,
                move_cursor=False, shield_window=True,
            )
        elif self.mode == "Hybrid":
            with_foreground(self.hwnd, self.stop, lambda: press_lock_input(self.hwnd, self.lock_input, self.stop))
        else:
            if self.lock_input != "Tab":
                move_cursor_inside_window(self.hwnd)
            press_lock_input(self.hwnd, self.lock_input, self.stop)

    def click(self, button="left"):
        if not self.available():
            raise RuntimeError("หน้าต่างเกมไม่พร้อมรับคำสั่ง")
        if self.mode in ("Background", "Hybrid"):
            return self.interaction.click(button)
        position = move_cursor_inside_window(self.hwnd)
        # Recheck focus after moving, before any global click.
        if not self.available():
            raise RuntimeError("เกมไม่ได้เป็นหน้าต่างหลักแล้ว")
        (left_click if button == "left" else right_click)()
        return position

    def close(self):
        try:
            if self.mode in ("Background", "Hybrid"):
                self.interaction.deactivate()
        except OSError:
            LOGGER.exception("คืนสถานะ active ของเกมไม่สำเร็จ")
        finally:
            if self.capture is not None:
                self.capture.close()


class AnimoBot:
    def __init__(self):
        LOGGER.info("เปิดโปรแกรม Animo Bot %s", APP_VERSION)
        LOGGER.info("UI threshold=%.2f anchor=%s", UI_MATCH_THRESHOLD, UI_ANCHOR_FILE)
        LOGGER.info(
            "DPI-aware=%s desktop=%sx%s",
            dpi_aware,
            user32.GetSystemMetrics(0),
            user32.GetSystemMetrics(1),
        )
        self.running = False
        self.preparing_ocr = False
        self.target_hwnd = None
        self.action_timer = None
        self.session = None
        self.preview_busy = False
        self.closing = False
        self.events = queue.Queue()
        self.tab_delay_ms = TAB_DELAY_MS
        self.click_delay_ms = CLICK_DELAY_MS

        saved = self.load_settings()
        if getattr(sys, "frozen", False):
            tesseract_value = self.find_tesseract()
        else:
            tesseract_value = saved.get("tesseract_path") or self.find_tesseract()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        ctk.set_window_scaling(0.60)
        ctk.set_widget_scaling(0.60)
        self.root = ctk.CTk()
        self.root.title("Aniimo_Bot")
        if APP_ICON_FILE.is_file():
            try:
                self.root.iconbitmap(str(APP_ICON_FILE))
            except tk.TclError:
                LOGGER.warning("ตั้งไอคอนหน้าต่างไม่สำเร็จ: %s", APP_ICON_FILE)
        self.root.geometry("820x960+25+20")
        self.root.minsize(760, 900)
        self.root.configure(fg_color="#07111F")
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.keyword_var = tk.StringVar(value=saved.get("keyword", DEFAULT_KEYWORD))
        self.webhook_var = tk.StringVar(value=saved.get("webhook", ""))
        self.interval_var = tk.StringVar(value=saved.get("interval", "1.0"))
        self.cooldown_var = tk.StringVar(value=saved.get("cooldown", "30"))
        self.tab_delay_var = tk.StringVar(
            value=saved.get("tab_delay_ms", str(TAB_DELAY_MS))
        )
        self.click_delay_var = tk.StringVar(
            value=saved.get("click_delay_ms", str(CLICK_DELAY_MS))
        )
        self.tesseract_var = tk.StringVar(
            value=tesseract_value
        )
        self.show_webhook_var = tk.BooleanVar(value=False)
        saved_mode = saved.get("mode", "Background")
        self.mode_var = tk.StringVar(
            value=saved_mode if saved_mode in CONTROL_MODES else "Background"
        )
        saved_lock = saved.get("lock_input", "Tab")
        self.lock_input_var = tk.StringVar(value=saved_lock if saved_lock in LOCK_INPUTS else "Tab")
        self.status_var = tk.StringVar(value="พร้อมทำงาน")
        self.timing_var = tk.StringVar(
            value=(
                f"Lock {self.tab_delay_var.get()} ms  |  "
                f"Click {self.click_delay_var.get()} ms"
            )
        )

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 7))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="ANIIMO_BOT",
            font=("Segoe UI", 26, "bold"),
            text_color="#F8FAFC",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="v2.5  •  Background Assist & Discord Alert  •  STARSTEAM_X",
            font=("Segoe UI", 13),
            text_color="#94A3B8",
        ).grid(row=1, column=0, sticky="w")
        self.ui_badge = ctk.CTkLabel(
            header,
            text="●  UI WAITING",
            width=128,
            height=32,
            corner_radius=18,
            fg_color="#3B2F16",
            text_color="#FBBF24",
            font=("Segoe UI", 12, "bold"),
        )
        self.ui_badge.grid(row=0, column=1, rowspan=2, sticky="e")

        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=18)
        content.grid_columnconfigure((0, 1), weight=1, uniform="cards")
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left.grid_columnconfigure(0, weight=1)
        right = ctk.CTkFrame(content, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right.grid_columnconfigure(0, weight=1)

        automation = self.make_card(left, "AUTOMATION", "Keyboard and mouse timing")
        automation.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        automation.grid_columnconfigure((0, 1), weight=1)
        self.add_field(automation, "Delay หลังล็อก (ms)", self.tab_delay_var, 2, 0)
        self.add_field(automation, "Delay หลัง Click (ms)", self.click_delay_var, 2, 1)
        ctk.CTkLabel(
            automation, text="โหมดควบคุม", text_color="#CBD5E1", font=("Segoe UI", 12)
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(8, 4))
        self.mode_menu = ctk.CTkOptionMenu(
            automation, variable=self.mode_var, values=list(CONTROL_MODES),
            height=36, fg_color="#1E3A5F", button_color="#294E7A",
        )
        self.mode_menu.grid(row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            automation, text="Background: สลับโฟกัสสั้น ๆ เฉพาะปุ่มล็อก แล้วคืนทันที\nHybrid: ดึงเกม/คืนโปรแกรมและเมาส์ตอนล็อก\nForeground: เกมต้องอยู่ด้านหน้า",
            text_color="#94A3B8", font=("Segoe UI", 11), justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))
        ctk.CTkLabel(
            automation, text="ปุ่มล็อกเป้า (ให้ตรงกับที่ตั้งในเกม)",
            text_color="#CBD5E1", font=("Segoe UI", 12),
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 4))
        self.lock_input_menu = ctk.CTkOptionMenu(
            automation, variable=self.lock_input_var, values=list(LOCK_INPUTS),
            height=36, fg_color="#1E3A5F", button_color="#294E7A",
        )
        self.lock_input_menu.grid(row=8, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12))

        detection = self.make_card(left, "UI DETECTION", "Game window and template matching")
        detection.grid(row=1, column=0, sticky="ew")
        detection.grid_columnconfigure(1, weight=1)
        self.add_stat(detection, "Reference size", "1920 × 1080", 2, "#38BDF8")
        self.add_stat(detection, "Match threshold", "80%", 3, "#38BDF8")
        self.detection_status = ctk.CTkLabel(
            detection,
            text="READY",
            font=("Segoe UI", 13, "bold"),
            text_color="#22C55E",
        )
        ctk.CTkLabel(
            detection, text="Status", text_color="#94A3B8", font=("Segoe UI", 12)
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(5, 15))
        self.detection_status.grid(row=4, column=1, sticky="e", padx=16, pady=(5, 15))
        self.capture_button = ctk.CTkButton(
            detection, text="ตรวจภาพเกม (ไม่ส่งอินพุต)", command=self.test_capture,
            height=34, fg_color="#1E3A5F", hover_color="#294E7A",
        )
        self.capture_button.grid(row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 15))

        ocr = self.make_card(right, "OCR MONITOR", "Scan text with Tesseract OCR")
        ocr.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ocr.grid_columnconfigure((0, 1), weight=1)
        self.add_field(ocr, "คำที่ต้องตรวจ", self.keyword_var, 2, 0, columnspan=2)
        self.add_field(ocr, "ตรวจทุก (วินาที)", self.interval_var, 4, 0)
        self.add_field(ocr, "แจ้งซ้ำหลัง (วินาที)", self.cooldown_var, 4, 1)
        ctk.CTkLabel(
            ocr, text="ตำแหน่ง Tesseract OCR", text_color="#CBD5E1", font=("Segoe UI", 12)
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=16, pady=(9, 4))
        path_row = ctk.CTkFrame(ocr, fg_color="transparent")
        path_row.grid(row=7, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 15))
        path_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            path_row,
            textvariable=self.tesseract_var,
            height=36,
            fg_color="#0B1628",
            border_color="#29415F",
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            path_row,
            text="Browse",
            width=72,
            height=36,
            command=self.choose_tesseract,
            fg_color="#1E3A5F",
            hover_color="#294E7A",
        ).grid(row=0, column=1, padx=(8, 0))

        discord = self.make_card(right, "DISCORD", "Optional — leave blank to disable alerts")
        discord.grid(row=1, column=0, sticky="ew")
        discord.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            discord, text="Webhook URL (ไม่บังคับ)", text_color="#CBD5E1", font=("Segoe UI", 12)
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(4, 4))
        self.webhook_entry = ctk.CTkEntry(
            discord,
            textvariable=self.webhook_var,
            show="•",
            height=38,
            fg_color="#0B1628",
            border_color="#29415F",
        )
        self.webhook_entry.grid(row=3, column=0, sticky="ew", padx=16)
        controls = ctk.CTkFrame(discord, fg_color="transparent")
        controls.grid(row=4, column=0, sticky="ew", padx=16, pady=(10, 15))
        controls.grid_columnconfigure(1, weight=1)
        ctk.CTkSwitch(
            controls,
            text="Show URL",
            variable=self.show_webhook_var,
            command=self.toggle_webhook,
            progress_color="#38BDF8",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            controls,
            text="Test Webhook",
            command=self.test_webhook,
            height=34,
            fg_color="#1E3A5F",
            hover_color="#294E7A",
        ).grid(row=0, column=1, sticky="e")

        activity = ctk.CTkFrame(
            self.root,
            fg_color="#0F1B2D",
            border_width=1,
            border_color="#21344F",
            corner_radius=14,
        )
        activity.grid(row=2, column=0, sticky="ew", padx=18, pady=(9, 7))
        activity.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            activity,
            text="ACTIVITY LOG",
            font=("Segoe UI", 13, "bold"),
            text_color="#E2E8F0",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))
        self.activity_log = ctk.CTkTextbox(
            activity,
            height=58,
            corner_radius=8,
            fg_color="#081321",
            text_color="#A5D8FF",
            font=("Consolas", 11),
            activate_scrollbars=True,
        )
        self.activity_log.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.activity_log.configure(state="disabled")

        footer = ctk.CTkFrame(self.root, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1)
        status_box = ctk.CTkFrame(footer, fg_color="transparent")
        status_box.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            status_box,
            textvariable=self.status_var,
            text_color="#CBD5E1",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            status_box,
            textvariable=self.timing_var,
            text_color="#64748B",
            font=("Segoe UI", 11),
        ).grid(row=1, column=0, sticky="w")
        self.start_button = ctk.CTkButton(
            footer,
            text="▶  START BOT",
            command=self.start,
            width=150,
            height=38,
            corner_radius=10,
            fg_color="#16A34A",
            hover_color="#15803D",
            font=("Segoe UI", 13, "bold"),
        )
        self.start_button.grid(row=0, column=1, padx=(12, 8))
        self.stop_button = ctk.CTkButton(
            footer,
            text="■  STOP",
            state="disabled",
            command=self.stop,
            width=108,
            height=38,
            corner_radius=10,
            fg_color="#DC2626",
            hover_color="#B91C1C",
            font=("Segoe UI", 13, "bold"),
        )
        self.stop_button.grid(row=0, column=2)

        self.ui_log("Ready to start")

        self.root.after(100, self.process_events)

    @staticmethod
    def make_card(parent, title, subtitle):
        card = ctk.CTkFrame(
            parent,
            fg_color="#0F1B2D",
            border_width=1,
            border_color="#21344F",
            corner_radius=14,
        )
        ctk.CTkLabel(
            card,
            text=title,
            font=("Segoe UI", 16, "bold"),
            text_color="#F1F5F9",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 0))
        ctk.CTkLabel(
            card,
            text=subtitle,
            font=("Segoe UI", 11),
            text_color="#7890AE",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 8))
        return card

    @staticmethod
    def add_field(parent, label, variable, row, column, columnspan=1):
        ctk.CTkLabel(
            parent, text=label, text_color="#CBD5E1", font=("Segoe UI", 12)
        ).grid(row=row, column=column, columnspan=columnspan, sticky="w", padx=16, pady=(4, 4))
        ctk.CTkEntry(
            parent,
            textvariable=variable,
            height=38,
            fg_color="#0B1628",
            border_color="#29415F",
        ).grid(
            row=row + 1,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=16,
            pady=(0, 12),
        )

    @staticmethod
    def add_stat(parent, label, value, row, color):
        ctk.CTkLabel(
            parent, text=label, text_color="#94A3B8", font=("Segoe UI", 12)
        ).grid(row=row, column=0, sticky="w", padx=16, pady=5)
        ctk.CTkLabel(
            parent, text=value, text_color=color, font=("Segoe UI", 13, "bold")
        ).grid(row=row, column=1, sticky="e", padx=16, pady=5)

    def ui_log(self, message):
        if not hasattr(self, "activity_log"):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.configure(state="normal")
        self.activity_log.insert("end", f"[{timestamp}]  {message}\n")
        self.activity_log.see("end")
        self.activity_log.configure(state="disabled")

    def set_ui_badge(self, text, state="waiting"):
        palette = {
            "online": ("#123D2A", "#4ADE80"),
            "error": ("#451A1A", "#F87171"),
            "waiting": ("#3B2F16", "#FBBF24"),
            "idle": ("#1E293B", "#94A3B8"),
        }
        background, foreground = palette[state]
        self.ui_badge.configure(text=text, fg_color=background, text_color=foreground)

    @staticmethod
    def find_tesseract():
        candidates = [
            str(RESOURCE_DIR / "tesseract" / "tesseract.exe"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        return next((path for path in candidates if os.path.isfile(path)), "")

    @staticmethod
    def load_settings():
        if not SETTINGS_FILE.is_file():
            return {}
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            encrypted = data.pop("webhook_encrypted", "")
            if encrypted:
                data["webhook"] = unprotect_text(encrypted)
            else:
                data["webhook"] = data.pop("webhook_local_fallback", "")
            return data
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def save_settings(self):
        webhook = self.webhook_var.get().strip()
        data = {
            "keyword": self.keyword_var.get().strip(),
            "interval": self.interval_var.get().strip(),
            "cooldown": self.cooldown_var.get().strip(),
            "tab_delay_ms": self.tab_delay_var.get().strip(),
            "click_delay_ms": self.click_delay_var.get().strip(),
            "tesseract_path": self.tesseract_var.get().strip(),
            "mode": self.mode_var.get(),
            "lock_input": self.lock_input_var.get(),
        }
        try:
            data["webhook_encrypted"] = protect_text(webhook)
        except OSError:
            # บาง Python runtime ทำงานโดยไม่มี Windows user profile จึงใช้ DPAPI ไม่ได้
            data["webhook_local_fallback"] = webhook
        temporary = SETTINGS_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(SETTINGS_FILE)

    def toggle_webhook(self):
        self.webhook_entry.configure(show="" if self.show_webhook_var.get() else "•")

    def choose_tesseract(self):
        path = filedialog.askopenfilename(
            title="เลือก tesseract.exe",
            filetypes=[("Tesseract", "tesseract.exe"), ("Executable", "*.exe")],
        )
        if path:
            self.tesseract_var.set(path)

    def read_settings(self):
        keyword = self.keyword_var.get().strip()
        webhook = self.webhook_var.get().strip()
        tesseract_path = self.tesseract_var.get().strip()
        mode = self.mode_var.get()
        if mode not in CONTROL_MODES:
            raise ValueError("โหมดควบคุมไม่ถูกต้อง")
        lock_input = self.lock_input_var.get()
        if lock_input not in LOCK_INPUTS:
            raise ValueError("ปุ่มล็อกเป้าไม่ถูกต้อง")
        if not keyword:
            raise ValueError("กรุณากรอกคำที่ต้องตรวจ")
        if webhook and not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith(
            "https://discordapp.com/api/webhooks/"
        ):
            raise ValueError("Discord Webhook URL ไม่ถูกต้อง")
        try:
            interval = float(self.interval_var.get())
            cooldown = float(self.cooldown_var.get())
            tab_delay_ms = int(self.tab_delay_var.get())
            click_delay_ms = int(self.click_delay_var.get())
        except ValueError as error:
            raise ValueError("รอบตรวจ, ช่วงแจ้งซ้ำ และ Delay ต้องเป็นตัวเลข") from error
        if interval < 0.3 or cooldown < 0:
            raise ValueError("รอบตรวจขั้นต่ำคือ 0.3 วินาที และช่วงแจ้งซ้ำต้องไม่ติดลบ")
        if not 1 <= tab_delay_ms <= 10000 or not 1 <= click_delay_ms <= 10000:
            raise ValueError("Delay ต้องอยู่ระหว่าง 1–10000 ms")
        if not tesseract_path or not os.path.isfile(tesseract_path):
            raise ValueError("กรุณาเลือกไฟล์ tesseract.exe")
        return (
            keyword,
            webhook,
            interval,
            cooldown,
            tesseract_path,
            tab_delay_ms,
            click_delay_ms,
            mode,
            lock_input,
        )

    def start(self):
        LOGGER.info("กด Start")
        self.ui_log("Start requested")
        if self.running or self.preparing_ocr or self.preview_busy:
            LOGGER.debug("ข้าม Start เพราะบอตทำงานหรือกำลังเตรียม OCR อยู่")
            return
        if cv2 is None or np is None or pytesseract is None or ImageGrab is None:
            messagebox.showerror(
                "ขาดไลบรารี",
                "กรุณาติดตั้งไลบรารีก่อน:\n\npip install -r requirements.txt",
            )
            return
        if not UI_ANCHOR_FILE.is_file():
            LOGGER.error("ไม่พบไฟล์อ้างอิง UI: %s", UI_ANCHOR_FILE)
            messagebox.showerror("ขาดไฟล์ตรวจ UI", f"ไม่พบไฟล์:\n{UI_ANCHOR_FILE}")
            return
        try:
            settings = self.read_settings()
        except ValueError as error:
            LOGGER.warning("ค่าตั้งค่าไม่ถูกต้อง: %s", error)
            messagebox.showwarning("Animo Bot", str(error))
            return
        try:
            self.save_settings()
        except OSError as error:
            messagebox.showwarning("บันทึกค่าไม่สำเร็จ", str(error))

        self.target_hwnd = find_target_window()
        if not self.target_hwnd:
            LOGGER.error("ไม่พบหน้าต่าง %s", TARGET_EXE)
            messagebox.showwarning("Animo Bot", "ไม่พบ aniimo.exe กรุณาเปิดเกมก่อน")
            return
        LOGGER.info("พบหน้าต่าง %s hwnd=%s bbox=%s", TARGET_EXE, self.target_hwnd, get_window_box(self.target_hwnd))

        pytesseract.pytesseract.tesseract_cmd = settings[4]
        bundled_tessdata = Path(settings[4]).resolve().parent / "tessdata"
        if bundled_tessdata.is_dir():
            os.environ["TESSDATA_PREFIX"] = str(bundled_tessdata)

        self.preparing_ocr = True
        self.session = BotSession(self.target_hwnd, settings)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.mode_menu.configure(state="disabled")
        self.lock_input_menu.configure(state="disabled")
        self.capture_button.configure(state="disabled")
        self.status_var.set("กำลังเตรียม OCR...")
        self.set_ui_badge("●  OCR LOADING", "waiting")
        self.detection_status.configure(text="OCR LOADING", text_color="#FBBF24")
        self.ui_log("Preparing Tesseract OCR in background...")
        threading.Thread(
            target=self.prepare_ocr,
            args=(self.session,),
            daemon=True,
        ).start()

    def emit_event(self, kind, message, session=None):
        self.events.put((session, kind, message))

    def fail_session(self, session, message):
        session.ready.clear()
        if session.capture is not None:
            session.capture.invalidate()
        session.stop.set()
        self.emit_event("fatal", message, session)

    def prepare_ocr(self, session):
        try:
            languages = pytesseract.get_languages(config="")
            if "tha" not in languages or "eng" not in languages:
                raise RuntimeError("ต้องมีไฟล์ภาษา tha.traineddata และ eng.traineddata ใน Tesseract")
            if session.stop.is_set():
                return
            if session.mode in ("Background", "Hybrid"):
                session.capture = WindowCapture(session.hwnd, session.mode)
                if session.stop.is_set():
                    return
                session.capture.grab(timeout=8.0)
                LOGGER.info("Background capture preflight สำเร็จ hwnd=%s", session.hwnd)
            if not session.stop.is_set():
                self.emit_event("ocr_ready", session, session)
        except Exception as error:
            LOGGER.exception("เตรียม OCR/Capture ไม่สำเร็จ")
            if not session.stop.is_set():
                self.emit_event("ocr_prepare_error", str(error), session)
        finally:
            if session.stop.is_set() and session.capture is not None:
                session.capture.close()

    def finish_start(self, session):
        if session is not self.session or session.stop.is_set():
            return
        self.preparing_ocr = False
        if not session.interaction.available():
            self.stop("หน้าต่าง aniimo.exe ถูกปิดไปแล้ว")
            return
        if session.mode in ("Background", "Hybrid"):
            self.begin_if_active(session)
        else:
            user32.ShowWindow(session.hwnd, SW_RESTORE)
            user32.SetForegroundWindow(session.hwnd)
            self.root.after(150, lambda: self.begin_if_active(session))

    def begin_if_active(self, session):
        if session is not self.session or session.stop.is_set():
            return
        if not session.available():
            self.stop("ไม่สามารถสลับไปยังหน้าต่าง aniimo.exe ได้")
            return
        if session.capture is None:
            session.capture = WindowCapture(session.hwnd, session.mode)
        self.running = True
        session.cycle.reset()
        self.tab_delay_ms = session.settings[5]
        self.click_delay_ms = session.settings[6]
        self.timing_var.set(
            f"{session.mode} | {session.lock_input} {self.tab_delay_ms} ms | Click {self.click_delay_ms} ms"
        )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("กำลังตรวจภาพและรอ UI เกม...")
        self.set_ui_badge("●  UI SCANNING", "waiting")
        self.detection_status.configure(text="SCANNING", text_color="#FBBF24")
        self.ui_log(f"Started in {session.mode} mode")
        session.ready.clear()
        threading.Thread(
            target=self.ui_monitor_loop, args=(session,), daemon=True,
            name="UI-Monitor",
        ).start()
        threading.Thread(
            target=self.ocr_loop, args=(session,), daemon=True,
            name="OCR-Monitor",
        ).start()

    def action_allowed(self, session):
        if not self.running or session is None or session.stop.is_set():
            return False
        if not session.available():
            self.stop("หยุดแล้ว: เกมถูกปิดหรือไม่ได้เป็นหน้าต่างหลักในโหมด Foreground")
            return False
        return session.ready.is_set() and session.capture.fresh()

    def shoot_step(self):
        self.action_timer = None
        session = self.session
        if not self.action_allowed(session):
            if self.running and session is not None and not session.stop.is_set():
                self.action_timer = self.root.after(100, self.shoot_step)
            return
        try:
            session.cycle.delay_elapsed()
            position = session.click("left")
            session.cycle.shot_sent()
            LOGGER.debug(
                "คลิกซ้ายหนึ่งครั้ง mode=%s position=%s cycle=%s",
                session.mode, position, session.cycle.completed_cycles,
            )
            self.status_var.set(f"จับรอบที่ {session.cycle.completed_cycles} แล้ว — เตรียมเปลี่ยนเป้า")
        except Exception as error:
            LOGGER.exception("คลิกซ้ายไม่สำเร็จ")
            self.stop(f"คลิกซ้ายไม่สำเร็จ: {error}")
            return
        self.action_timer = self.root.after(self.click_delay_ms, self.lock_step)

    def lock_step(self):
        self.action_timer = None
        session = self.session
        if not self.action_allowed(session):
            if self.running and session is not None and not session.stop.is_set():
                self.action_timer = self.root.after(100, self.lock_step)
            return
        try:
            # Every cycle starts with one lock input, even if the previous lock
            # marker is still visible after throwing the ball.
            session.press_lock()
            session.cycle.lock_sent()
            LOGGER.debug(
                "ส่งปุ่มล็อก mode=%s input=%s next_cycle=%s",
                session.mode, session.lock_input, session.cycle.completed_cycles + 1,
            )
            self.status_var.set(f"กด {session.lock_input} แล้ว — รอ {self.tab_delay_ms} ms ก่อนโยน")
        except Exception as error:
            LOGGER.exception("ส่งปุ่มล็อกไม่สำเร็จ")
            self.stop(f"ส่งปุ่มล็อกไม่สำเร็จ: {error}")
            return
        self.action_timer = self.root.after(self.tab_delay_ms, self.shoot_step)

    def ocr_loop(self, session):
        keyword, webhook, interval, cooldown = session.settings[:4]
        normalized_keyword = normalize_text(keyword)
        while not session.stop.is_set():
            try:
                image = session.capture.latest()
                if image is None:
                    session.stop.wait(0.1)
                    continue
                text = pytesseract.image_to_string(
                    image, lang="tha+eng", config="--psm 11", timeout=5,
                )
                if session.stop.is_set():
                    return
                if normalized_keyword in normalize_text(text):
                    # Stop input immediately, before a slow Discord request.
                    session.stop.set()
                    session.ready.clear()
                    self.emit_event("found", keyword, session)
                    if webhook:
                        try:
                            post_discord(webhook, keyword)
                            LOGGER.info("ส่ง Discord หลังพบคำแล้ว: %s", keyword)
                        except Exception as error:
                            LOGGER.warning("ส่ง Discord ไม่สำเร็จ: %s", error)
                            self.emit_event("notification_error", str(error), session)
                    return
            except Exception as error:
                LOGGER.exception("OCR ผิดพลาด")
                if not session.stop.is_set():
                    self.fail_session(session, f"OCR ผิดพลาด: {error}")
                return
            session.stop.wait(interval)

    def ui_monitor_loop(self, session):
        anchor = cv2.imread(str(UI_ANCHOR_FILE), cv2.IMREAD_GRAYSCALE)
        if anchor is None:
            self.fail_session(session, "เปิดไฟล์อ้างอิง UI ไม่สำเร็จ")
            return
        missing_count = restore_attempts = 0
        last_right_click = last_capture_time = 0.0
        previous_visible = None
        confirmed_once = False
        while not session.stop.is_set():
            try:
                if not session.available():
                    raise CaptureError("เกมถูกปิดหรือไม่ได้อยู่ด้านหน้าในโหมด Foreground")
                full_image = session.capture.grab()
                if session.stop.is_set():
                    return
                window_width, window_height = full_image.size
                capture_box = (
                    0, int(window_height * 0.68),
                    int(window_width * 0.28), window_height,
                )
                image = full_image.crop(capture_box)
                visible, score, scale = anipod_ui_is_visible(
                    image, anchor, window_width=window_width,
                )
                scan_time = time.monotonic()
                if visible != previous_visible or scan_time - last_capture_time >= UI_CAPTURE_INTERVAL:
                    capture_path = save_ui_capture(image, visible, score, scale)
                    last_capture_time = scan_time
                    LOGGER.debug(
                        "UI mode=%s visible=%s score=%.4f scale=%.3f size=%s capture=%s",
                        session.mode, visible, score, scale, full_image.size, capture_path.name,
                    )
                if visible:
                    missing_count = restore_attempts = 0
                    confirmed_once = True
                    if not session.ready.is_set():
                        session.ready.set()
                        self.emit_event("ui_ready", f"ตรวจพบ UI แล้ว ({score:.2f})", session)
                else:
                    session.ready.clear()
                    missing_count += 1
                    if previous_visible:
                        self.emit_event("ui_lost", "พักการกดและคลิก — กำลังรอ UI เกม", session)
                    required_misses = UI_LOST_SCANS if confirmed_once else UI_MISSING_SCANS
                    if missing_count >= required_misses and scan_time - last_right_click >= RIGHT_CLICK_COOLDOWN:
                        if restore_attempts >= MAX_UI_RESTORE_ATTEMPTS:
                            raise CaptureError(
                                "เปิด UI ไม่สำเร็จหลังลอง 3 ครั้ง ตรวจภาพอ้างอิง หรือเปลี่ยนเป็น Foreground"
                            )
                        if not session.capture.fresh() or session.stop.is_set():
                            return
                        image.save(UI_DEBUG_FILE)
                        position = session.click("right")
                        restore_attempts += 1
                        missing_count = 0
                        last_right_click = scan_time
                        LOGGER.warning("เปิด UI mode=%s attempt=%s position=%s", session.mode, restore_attempts, position)
                        self.emit_event("ui_restore", f"กำลังเปิด UI ({restore_attempts}/3)", session)
                previous_visible = visible
            except Exception as error:
                LOGGER.exception("ตรวจภาพ/UI ผิดพลาด")
                if not session.stop.is_set():
                    self.fail_session(session, f"หยุดเพื่อความปลอดภัย: {error}")
                return
            session.stop.wait(UI_CHECK_INTERVAL)

    def test_capture(self):
        if self.running or self.preparing_ocr or self.preview_busy:
            return
        hwnd = find_target_window()
        if not hwnd:
            messagebox.showwarning("ตรวจภาพเกม", "กรุณาเปิด aniimo.exe ก่อน")
            return
        if cv2 is None or ImageGrab is None:
            messagebox.showerror("ขาดไลบรารี", "ติดตั้งไลบรารีจาก requirements.txt ก่อน")
            return
        mode = self.mode_var.get()
        self.preview_busy = True
        self.preview_stop = threading.Event()
        self.start_button.configure(state="disabled")
        self.capture_button.configure(state="disabled")
        self.mode_menu.configure(state="disabled")
        self.lock_input_menu.configure(state="disabled")
        self.status_var.set("กำลังทดสอบภาพเกม...")
        preview_stop = self.preview_stop

        def worker():
            capture = None
            try:
                capture = WindowCapture(hwnd, mode)
                self.preview_capture = capture
                if preview_stop.is_set():
                    return
                image = capture.grab(timeout=8.0)
                for _ in range(2):
                    if preview_stop.wait(0.15):
                        return
                    image = capture.grab()
                image.save(BACKGROUND_PREVIEW_FILE)
                width, height = image.size
                anchor = cv2.imread(str(UI_ANCHOR_FILE), cv2.IMREAD_GRAYSCALE)
                if anchor is None:
                    raise CaptureError("เปิดภาพอ้างอิง UI ไม่สำเร็จ")
                region = image.crop((0, int(height * 0.68), int(width * 0.28), height))
                visible, score, _ = anipod_ui_is_visible(region, anchor, window_width=width)
                if not preview_stop.is_set():
                    self.emit_event("capture_ok", (image, mode, visible, score))
            except Exception as error:
                LOGGER.exception("ทดสอบภาพไม่สำเร็จ")
                if not preview_stop.is_set():
                    self.emit_event("capture_error", str(error))
            finally:
                if capture is not None:
                    capture.close()

        threading.Thread(target=worker, daemon=True, name="Capture-Preview").start()

    def show_capture_preview(self, result):
        image, mode, visible, score = result
        preview = ctk.CTkToplevel(self.root)
        preview.title(f"ภาพเกม — {mode}")
        preview.attributes("-topmost", True)
        width, height = image.size
        display_width = min(720, width)
        display_height = round(height * display_width / width)
        preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=(display_width, display_height))
        label = ctk.CTkLabel(preview, text="", image=preview_image)
        label.pack(padx=12, pady=12)
        label.preview_image = preview_image
        ctk.CTkLabel(
            preview,
            text=f"{mode} | {width} × {height} | UI {'พบ' if visible else 'ไม่พบ'} ({score:.2f})\n"
                 "ตรวจภาพสำเร็จ การรับ Tab/คลิกต้องทดสอบในเกม",
        ).pack(padx=12, pady=(0, 12))


    def test_webhook(self):
        LOGGER.info("กดทดสอบ Webhook")
        webhook = self.webhook_var.get().strip()
        keyword = self.keyword_var.get().strip() or DEFAULT_KEYWORD
        if not webhook:
            messagebox.showwarning("Animo Bot", "กรุณากรอก Discord Webhook URL")
            return
        try:
            self.save_settings()
        except OSError as error:
            messagebox.showwarning("บันทึกค่าไม่สำเร็จ", str(error))
        self.status_var.set("กำลังทดสอบ Webhook...")

        def worker():
            try:
                post_discord(webhook, f"ทดสอบ: {keyword}")
                LOGGER.info("ทดสอบ Webhook สำเร็จ")
                self.emit_event("webhook_ok", "ส่งข้อความทดสอบสำเร็จ")
            except Exception as error:
                LOGGER.warning("ทดสอบ Webhook ไม่สำเร็จ: %s", error)
                self.emit_event("error", f"ทดสอบ Webhook ไม่สำเร็จ: {error}")

        threading.Thread(target=worker, daemon=True).start()

    def process_events(self):
        try:
            while True:
                session, kind, message = self.events.get_nowait()
                if session is not None:
                    if session is not self.session:
                        continue
                    if session.stop.is_set() and kind not in (
                        "found", "fatal", "notification_error", "ocr_prepare_error",
                    ):
                        continue
                if kind == "ocr_ready":
                    self.ui_log("OCR and capture ready")
                    self.finish_start(session)
                elif kind == "ocr_prepare_error":
                    self.stop(f"เตรียม OCR/Capture ไม่สำเร็จ: {message}")
                    self.set_ui_badge("●  OCR ERROR", "error")
                    self.detection_status.configure(text="OCR ERROR", text_color="#F87171")
                    messagebox.showerror("OCR / Capture", message)
                elif kind == "fatal":
                    self.stop(message)
                    self.set_ui_badge("●  CAPTURE ERROR", "error")
                    self.detection_status.configure(text="STOPPED", text_color="#F87171")
                elif kind == "found":
                    LOGGER.info("หยุดบอตเพราะ OCR พบคำ: %s", message)
                    self.ui_log(f"Keyword found: {message}")
                    self.stop(f"พบคำ '{message}' — หยุดบอตแล้ว")
                elif kind == "notification_error":
                    self.ui_log(f"Discord ส่งไม่สำเร็จ: {message}")
                elif kind in ("capture_ok", "capture_error"):
                    self.preview_busy = False
                    self.preview_capture = None
                    self.start_button.configure(state="normal")
                    self.capture_button.configure(state="normal")
                    self.mode_menu.configure(state="normal")
                    self.lock_input_menu.configure(state="normal")
                    if kind == "capture_ok":
                        self.status_var.set("ตรวจภาพสำเร็จ")
                        self.ui_log(f"Capture preview saved: {BACKGROUND_PREVIEW_FILE.name}")
                        self.show_capture_preview(message)
                    else:
                        self.status_var.set("ตรวจภาพไม่สำเร็จ")
                        self.ui_log(message)
                        messagebox.showerror("ตรวจภาพเกม", message)
                elif kind == "webhook_ok":
                    self.status_var.set(message)
                    self.ui_log(message)
                    messagebox.showinfo("Discord Webhook", message)
                elif kind == "error":
                    self.status_var.set(message)
                    self.ui_log(message)
                elif kind == "ui_restore":
                    self.status_var.set(message)
                    self.set_ui_badge("●  UI OPENING", "waiting")
                    self.detection_status.configure(text="OPENING", text_color="#FBBF24")
                    self.ui_log(message)
                elif kind == "ui_ready":
                    self.status_var.set(message)
                    self.set_ui_badge("●  UI ONLINE", "online")
                    self.detection_status.configure(text="ONLINE", text_color="#4ADE80")
                    self.ui_log(message)
                    if (
                        self.running
                        and session.ready.is_set()
                        and session.available()
                        and self.action_timer is None
                    ):
                        self.lock_step()
                elif kind == "ui_lost":
                    self.status_var.set(message)
                    self.set_ui_badge("●  UI LOST", "error")
                    self.detection_status.configure(text="LOST", text_color="#F87171")
                    self.ui_log(message)
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(100, self.process_events)

    def stop(self, message="หยุดแล้ว"):
        LOGGER.info("หยุดบอต: %s", message)
        self.running = False
        self.preparing_ocr = False
        if self.action_timer is not None:
            self.root.after_cancel(self.action_timer)
            self.action_timer = None
        session = self.session
        if session is not None:
            session.stop.set()
            session.ready.clear()
            if session.capture is not None:
                session.capture.invalidate()
                threading.Thread(target=session.close, daemon=True).start()
        self.status_var.set(message)
        self.set_ui_badge("●  BOT STOPPED", "idle")
        self.detection_status.configure(text="IDLE", text_color="#94A3B8")
        self.ui_log(message)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.mode_menu.configure(state="normal")
        self.lock_input_menu.configure(state="normal")
        self.capture_button.configure(state="normal")

    def close(self):
        self.closing = True
        if self.preview_busy:
            self.preview_stop.set()
            preview_capture = getattr(self, "preview_capture", None)
            if preview_capture is not None:
                threading.Thread(target=preview_capture.close, daemon=True).start()
        try:
            self.save_settings()
        except OSError:
            pass
        self.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--capture-probe":
        probe = None
        try:
            probe = WindowCapture(int(sys.argv[2]), "Background")
            probe.grab(timeout=15).save(sys.argv[3])
        except Exception:
            LOGGER.exception("Capture probe failed")
            sys.exit(1)
        finally:
            if probe is not None:
                probe.close()
        sys.exit(0)
    if ctk is None:
        dependency_root = tk.Tk()
        dependency_root.withdraw()
        messagebox.showerror(
            "ขาดไลบรารี", "ติดตั้งไลบรารีก่อนด้วย python -m pip install -r requirements.txt",
        )
        dependency_root.destroy()
        sys.exit(1)
    AnimoBot().run()
