"""Window capture, local mouse messages, and bounded focus-assisted input."""

import ctypes
import multiprocessing as mp
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _bind(dll, name, arguments, result):
    function = getattr(dll, name)
    function.argtypes = arguments
    function.restype = result
    return function


_bind(user32, "IsWindow", [wintypes.HWND], wintypes.BOOL)
_bind(user32, "IsIconic", [wintypes.HWND], wintypes.BOOL)
_bind(user32, "GetForegroundWindow", [], wintypes.HWND)
_bind(user32, "SetForegroundWindow", [wintypes.HWND], wintypes.BOOL)
_bind(user32, "GetCursorPos", [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL)
_bind(user32, "SetCursorPos", [ctypes.c_int, ctypes.c_int], wintypes.BOOL)
_bind(user32, "ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL)
_bind(user32, "BringWindowToTop", [wintypes.HWND], wintypes.BOOL)
_bind(user32, "SetActiveWindow", [wintypes.HWND], wintypes.HWND)
_bind(user32, "SetFocus", [wintypes.HWND], wintypes.HWND)
_bind(user32, "AttachThreadInput", [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL], wintypes.BOOL)
_bind(user32, "GetWindowLongPtrW", [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t)
_bind(user32, "SetWindowPos", [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
], wintypes.BOOL)
_bind(user32, "GetClientRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL)
_bind(user32, "ClientToScreen", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL)
_bind(user32, "ScreenToClient", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL)
_bind(user32, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
_bind(user32, "ChildWindowFromPointEx", [wintypes.HWND, wintypes.POINT, wintypes.UINT], wintypes.HWND)
_bind(user32, "PostMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL)
_bind(user32, "GetDC", [wintypes.HWND], wintypes.HDC)
_bind(user32, "ReleaseDC", [wintypes.HWND, wintypes.HDC], ctypes.c_int)
_bind(user32, "PrintWindow", [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL)
_bind(gdi32, "CreateCompatibleDC", [wintypes.HDC], wintypes.HDC)
_bind(gdi32, "SelectObject", [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE)
_bind(gdi32, "DeleteObject", [wintypes.HANDLE], wintypes.BOOL)
_bind(gdi32, "DeleteDC", [wintypes.HDC], wintypes.BOOL)
_bind(gdi32, "GdiFlush", [], wintypes.BOOL)
_bind(kernel32, "GetCurrentThreadId", [], wintypes.DWORD)

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_ACTIVATE, WA_INACTIVE, WA_ACTIVE = 0x0006, 0, 1
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
MOUSE_BUTTON_MESSAGES = {
    "left": (WM_LBUTTONDOWN, WM_LBUTTONUP, 1, 0),
    "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, 2, 0),
    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, 0x10, 0),
    "mouse4": (WM_XBUTTONDOWN, WM_XBUTTONUP, (1 << 16) | 0x20, 1 << 16),
    "mouse5": (WM_XBUTTONDOWN, WM_XBUTTONUP, (2 << 16) | 0x40, 2 << 16),
}
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2
FRAME_MAX_AGE = 1.5
MINIMIZED_STALE_TIMEOUT = 5.0


class CaptureError(RuntimeError):
    pass


def window_pid(hwnd):
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return process_id.value


def client_size(hwnd):
    rect = wintypes.RECT()
    if not user32.IsWindow(hwnd) or not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise CaptureError("หน้าต่างเกมถูกปิดหรืออ่านขนาดไม่ได้")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 8 or height < 8 or width * height > 25_000_000:
        raise CaptureError("หน้าต่างเกมไม่มีพื้นที่ภาพที่รองรับ")
    return width, height


def client_screen_box(hwnd):
    width, height = client_size(hwnd)
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise CaptureError("อ่านตำแหน่งพื้นที่เกมไม่สำเร็จ")
    return origin.x, origin.y, origin.x + width, origin.y + height


def pack_position(x, y):
    if not -32768 <= x <= 32767 or not -32768 <= y <= 32767:
        raise ValueError("พิกัดคลิกเกินขอบเขต Windows message")
    return (x & 0xFFFF) | ((y & 0xFFFF) << 16)


def key_lparam(scan_code, key_up=False):
    return 1 | (scan_code << 16) | ((3 << 30) if key_up else 0)


class BackgroundInput:
    """Post messages to the game/its child, without focus or cursor changes."""

    def __init__(self, hwnd, stop_event):
        self.hwnd = hwnd
        self.pid = window_pid(hwnd)
        self.stop_event = stop_event
        self._lock = threading.Lock()
        self._activated_targets = set()

    def available(self):
        return (
            not self.stop_event.is_set()
            and user32.IsWindow(self.hwnd)
            and window_pid(self.hwnd) == self.pid
        )

    def _target_at(self, x, y):
        target = self.hwnd
        point = wintypes.POINT(x, y)
        for _ in range(16):
            child = user32.ChildWindowFromPointEx(target, point, 7)
            if not child or child == target or window_pid(child) != self.pid:
                break
            screen = wintypes.POINT(point.x, point.y)
            if not user32.ClientToScreen(target, ctypes.byref(screen)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not user32.ScreenToClient(child, ctypes.byref(screen)):
                raise ctypes.WinError(ctypes.get_last_error())
            target, point = child, screen
        return target, point.x, point.y

    @staticmethod
    def _post(hwnd, message, wparam, lparam):
        ctypes.set_last_error(0)
        if not user32.PostMessageW(hwnd, message, wparam, lparam):
            raise ctypes.WinError(ctypes.get_last_error())

    def _activate(self, target):
        # Unity ignores queued input while its own window state is inactive.
        # Match ok-script: notify the game, without SetForegroundWindow/SetFocus.
        activation = WA_ACTIVE | (int(bool(user32.IsIconic(self.hwnd))) << 16)
        for hwnd in dict.fromkeys((self.hwnd, target)):
            self._post(hwnd, WM_ACTIVATE, activation, 0)
            self._activated_targets.add(hwnd)

    def deactivate(self):
        with self._lock:
            # Preserve normal state when the user has brought the game forward.
            if user32.GetForegroundWindow() != self.hwnd:
                for hwnd in self._activated_targets:
                    if user32.IsWindow(hwnd) and window_pid(hwnd) == self.pid:
                        self._post(hwnd, WM_ACTIVATE, WA_INACTIVE, 0)
            self._activated_targets.clear()

    def press_tab(self):
        with self._lock:
            if not self.available():
                return
            width, height = client_size(self.hwnd)
            target, _, _ = self._target_at(width // 2, height // 2)
            self._activate(target)
            self._post(target, WM_KEYDOWN, 0x09, key_lparam(0x0F))
            # Release the key even if Stop arrives while it is held.
            try:
                self.stop_event.wait(0.060)
            finally:
                self._post(target, WM_KEYUP, 0x09, key_lparam(0x0F, True))

    def click(self, button="left", x=None, y=None):
        if button not in MOUSE_BUTTON_MESSAGES:
            raise ValueError("ปุ่มเมาส์ไม่ถูกต้อง")
        with self._lock:
            if not self.available():
                return
            width, height = client_size(self.hwnd)
            if x is None and y is None:
                x, y = width // 2, height // 2
            if x is None or y is None or not 0 <= x < width or not 0 <= y < height:
                raise ValueError("พิกัดคลิกต้องอยู่ภายในพื้นที่เกม")
            target, x, y = self._target_at(x, y)
            self._activate(target)
            position = pack_position(x, y)
            down, up, flag, released_flag = MOUSE_BUTTON_MESSAGES[button]
            self._post(target, WM_MOUSEMOVE, 0, position)
            self._post(target, down, flag, position)
            try:
                self.stop_event.wait(0.060)
            finally:
                self._post(target, up, released_flag, position)
            return x, y


def _force_foreground(hwnd):
    """Attach input queues only long enough to activate a validated HWND."""
    if not user32.IsWindow(hwnd):
        return False
    current_thread = kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    thread_ids = []
    for window in (foreground, hwnd):
        if window:
            thread_id = user32.GetWindowThreadProcessId(window, None)
            if thread_id and thread_id != current_thread and thread_id not in thread_ids:
                thread_ids.append(thread_id)
    attached = []
    try:
        for thread_id in thread_ids:
            if user32.AttachThreadInput(current_thread, thread_id, True):
                attached.append(thread_id)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
        return user32.GetForegroundWindow() == hwnd
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(current_thread, thread_id, False)


def with_foreground(
    hwnd, stop_event, send_input, move_cursor=True, shield_window=False,
):
    """Focus one window for one real input, then restore the user's context."""
    if stop_event.is_set() or not user32.IsWindow(hwnd):
        return
    previous = user32.GetForegroundWindow()
    cursor = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(cursor)):
        raise ctypes.WinError(ctypes.get_last_error())
    minimized = bool(user32.IsIconic(hwnd))
    game_position = None
    restore_failed = False
    shield_applied = False
    try:
        # Keep the user's current app visually above the game while Unity gets
        # the real keyboard focus it requires. The topmost flag is temporary.
        if (
            shield_window and previous and previous != hwnd
            and user32.IsWindow(previous)
            and not (user32.GetWindowLongPtrW(previous, -20) & 0x8)
        ):
            shield_applied = bool(user32.SetWindowPos(
                previous, wintypes.HWND(-1), 0, 0, 0, 0, 0x0013,
            ))
        if minimized:
            user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() != hwnd:
            _force_foreground(hwnd)
        if stop_event.wait(0.05):
            return
        if user32.GetForegroundWindow() != hwnd:
            raise RuntimeError("เปิดโฟกัสเกมชั่วคราวไม่สำเร็จ กรุณาเลือก Foreground")
        width, height = client_size(hwnd)
        game_position = wintypes.POINT(width // 2, height // 2)
        if not user32.ClientToScreen(hwnd, ctypes.byref(game_position)):
            raise ctypes.WinError(ctypes.get_last_error())
        if move_cursor:
            if not user32.SetCursorPos(game_position.x, game_position.y):
                raise ctypes.WinError(ctypes.get_last_error())
        if stop_event.is_set() or user32.GetForegroundWindow() != hwnd:
            return
        send_input()
    finally:
        if user32.GetForegroundWindow() == hwnd:
            if previous and previous != hwnd and user32.IsWindow(previous):
                user32.SetForegroundWindow(previous)
                if user32.GetForegroundWindow() != previous:
                    _force_foreground(previous)
                restore_failed = user32.GetForegroundWindow() != previous
            if minimized:
                user32.ShowWindow(hwnd, 7)
        if game_position is not None:
            current = wintypes.POINT()
            # Unity may recenter a captured cursor one pixel from our position.
            # Restore that game-driven move, but preserve other user movements.
            if user32.GetCursorPos(ctypes.byref(current)) and max(
                abs(current.x - game_position.x), abs(current.y - game_position.y)
            ) <= 2:
                user32.SetCursorPos(cursor.x, cursor.y)
        if shield_applied and user32.IsWindow(previous):
            user32.SetWindowPos(
                previous, wintypes.HWND(-2), 0, 0, 0, 0, 0x0013,
            )
        if restore_failed:
            raise RuntimeError("Hybrid คืนโปรแกรมเดิมไม่สำเร็จ หยุดส่งอินพุตแล้ว")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("header", BITMAPINFOHEADER), ("colors", wintypes.DWORD * 1)]


_bind(gdi32, "CreateDIBSection", [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
], wintypes.HANDLE)


class _PrintWindowSurface:
    """Reusable GDI bitmap; only used inside the disposable capture process."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.size = None
        self.dc = self.bitmap = self.old_bitmap = None
        self.bits = ctypes.c_void_p()

    def capture(self):
        width, height = client_size(self.hwnd)
        if self.size != (width, height):
            self.close()
            window_dc = user32.GetDC(self.hwnd)
            if not window_dc:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                self.dc = gdi32.CreateCompatibleDC(window_dc)
                info = BITMAPINFO()
                info.header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                info.header.biWidth, info.header.biHeight = width, -height
                info.header.biPlanes, info.header.biBitCount = 1, 32
                self.bitmap = gdi32.CreateDIBSection(
                    window_dc, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0
                )
                if not self.dc or not self.bitmap or not self.bits.value:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.old_bitmap = gdi32.SelectObject(self.dc, self.bitmap)
                if not self.old_bitmap or self.old_bitmap == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.size = (width, height)
            finally:
                user32.ReleaseDC(self.hwnd, window_dc)
        # Clear the bitmap so failed/partial rendering cannot reuse old pixels.
        ctypes.memset(self.bits, 0, width * height * 4)
        if not user32.PrintWindow(self.hwnd, self.dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            raise CaptureError("PrintWindow อ่านภาพเกมไม่สำเร็จ")
        gdi32.GdiFlush()
        return self.size, ctypes.string_at(self.bits, width * height * 4)

    def close(self):
        if self.dc and self.old_bitmap:
            gdi32.SelectObject(self.dc, self.old_bitmap)
        if self.bitmap:
            gdi32.DeleteObject(self.bitmap)
        if self.dc:
            gdi32.DeleteDC(self.dc)
        self.size = None
        self.dc = self.bitmap = self.old_bitmap = None


def _capture_worker(connection, hwnd, pid):
    # Spawned process has its own DPI context; keep client pixels consistent.
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()
    surface = _PrintWindowSurface(hwnd)
    try:
        while connection.recv() == "capture":
            try:
                if window_pid(hwnd) != pid:
                    raise CaptureError("หน้าต่างเกมเปลี่ยนไปแล้ว")
                size, pixels = surface.capture()
                connection.send(("frame", size, pixels))
            except Exception as error:
                connection.send(("error", str(error)))
    except (EOFError, OSError):
        pass
    finally:
        surface.close()
        connection.close()


def validate_frame(image):
    from PIL import ImageStat

    sample = image.resize((96, 54)).convert("L")
    if ImageStat.Stat(sample).stddev[0] < 1.0:
        raise CaptureError("ภาพเกมว่างหรือสีเดียว ลองเปิดเกมแบบ Windowed หรือใช้ Foreground")


class WindowCapture:
    """One reader, cached frames for OCR, bounded PrintWindow wait."""

    def __init__(self, hwnd, mode):
        if mode == "Hybrid":
            mode = "Background"
        if mode not in ("Background", "Foreground"):
            raise ValueError("โหมดจับภาพไม่ถูกต้อง")
        self.hwnd, self.mode = hwnd, mode
        self.pid = window_pid(hwnd)
        self._lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._closed = threading.Event()
        self._frame = None
        self._frame_time = 0.0
        self._signature = None
        self._last_change = time.monotonic()
        self._process = self._connection = None
        if mode == "Background":
            context = mp.get_context("spawn")
            self._connection, child_connection = context.Pipe()
            self._process = context.Process(
                target=_capture_worker, args=(child_connection, hwnd, self.pid), daemon=True
            )
            try:
                self._process.start()
            except Exception:
                self._connection.close()
                self._closed.set()
                raise
            finally:
                child_connection.close()

    def grab(self, timeout=2.0):
        from PIL import Image, ImageGrab

        with self._lock:
            try:
                if self._closed.is_set() or not user32.IsWindow(self.hwnd) or window_pid(self.hwnd) != self.pid:
                    raise CaptureError("หน้าต่างเกมถูกปิดหรือหยุดจับภาพแล้ว")
                if self.mode == "Background":
                    if not self._process.is_alive():
                        raise CaptureError("โปรเซสจับภาพหยุดทำงาน")
                    self._connection.send("capture")
                    if not self._connection.poll(timeout):
                        self._process.terminate()
                        raise CaptureError("จับภาพเกมเกินเวลาที่กำหนด อาจไม่รองรับ PrintWindow")
                    response = self._connection.recv()
                    if response[0] != "frame":
                        raise CaptureError(response[1])
                    image = Image.frombytes("RGB", response[1], response[2], "raw", "BGRX")
                else:
                    if user32.IsIconic(self.hwnd) or user32.GetForegroundWindow() != self.hwnd:
                        raise CaptureError("Foreground ต้องเปิดเกมเป็นหน้าต่างหลัก")
                    image = ImageGrab.grab(bbox=client_screen_box(self.hwnd), all_screens=True)
                validate_frame(image)
                now = time.monotonic()
                signature = image.resize((64, 36)).tobytes()
                if signature != self._signature or not user32.IsIconic(self.hwnd):
                    self._signature, self._last_change = signature, now
                elif now - self._last_change >= MINIMIZED_STALE_TIMEOUT:
                    raise CaptureError("ภาพเกมค้างหลังย่อหน้าต่าง กรุณาเปิดหน้าต่างเกมไว้")
                if self._closed.is_set():
                    raise CaptureError("หยุดจับภาพแล้ว")
                with self._frame_lock:
                    self._frame, self._frame_time = image, now
                return image
            except (EOFError, OSError) as error:
                self.invalidate()
                raise CaptureError(f"อ่านภาพเกมไม่สำเร็จ: {error}") from error
            except Exception:
                self.invalidate()
                raise

    def invalidate(self):
        with self._frame_lock:
            self._frame, self._frame_time = None, 0.0

    def latest(self):
        with self._frame_lock:
            if self._closed.is_set() or self._frame is None or time.monotonic() - self._frame_time > FRAME_MAX_AGE:
                return None
            return self._frame.copy()

    def fresh(self):
        with self._frame_lock:
            return not self._closed.is_set() and self._frame is not None and time.monotonic() - self._frame_time <= FRAME_MAX_AGE

    def close(self):
        self._closed.set()
        self.invalidate()
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=0.5)
        with self._lock:
            if self._connection is not None:
                self._connection.close()
