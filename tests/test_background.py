import ctypes
import queue
import threading
import time
import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch

from PIL import Image

import animo_bot as app
import windows_backend as backend


def hung_capture_worker(connection, hwnd, pid):
    connection.recv()
    pixels = (b"\x00\x00\xff\x00" * 320 * 100) + (b"\xff\x00\x00\x00" * 320 * 100)
    connection.send(("frame", (320, 200), pixels))
    connection.recv()
    threading.Event().wait(10)  # Simulate PrintWindow stuck inside the worker.


class NativeWindow:
    """Own GDI window off screen, with a real Windows message loop."""

    def __init__(self):
        self.messages = queue.Queue()
        self.active_windows = set()
        self.ready = threading.Event()
        self.unblock_print = threading.Event()
        self.block_print = False
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("Fixture window did not start")
        if self.error:
            raise self.error

    def _run(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT), ("lpfnWndProc", callback_type),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
            ]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32),
            ]

        backend._bind(kernel32, "GetModuleHandleW", [wintypes.LPCWSTR], wintypes.HMODULE)
        backend._bind(user32, "RegisterClassW", [ctypes.POINTER(WNDCLASS)], wintypes.WORD)
        backend._bind(user32, "UnregisterClassW", [wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL)
        backend._bind(user32, "CreateWindowExW", [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, ctypes.c_void_p,
        ], wintypes.HWND)
        backend._bind(user32, "DefWindowProcW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], ctypes.c_ssize_t)
        backend._bind(user32, "GetMessageW", [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL)
        backend._bind(user32, "TranslateMessage", [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL)
        backend._bind(user32, "DispatchMessageW", [ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t)
        backend._bind(user32, "FillRect", [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE], ctypes.c_int)
        backend._bind(user32, "DestroyWindow", [wintypes.HWND], wintypes.BOOL)
        backend._bind(user32, "UpdateWindow", [wintypes.HWND], wintypes.BOOL)
        backend._bind(user32, "BeginPaint", [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)], wintypes.HDC)
        backend._bind(user32, "EndPaint", [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)], wintypes.BOOL)
        backend._bind(user32, "PostQuitMessage", [ctypes.c_int], None)
        backend._bind(gdi32, "CreateSolidBrush", [wintypes.DWORD], wintypes.HANDLE)
        backend._bind(gdi32, "DeleteObject", [wintypes.HANDLE], wintypes.BOOL)
        instance = kernel32.GetModuleHandleW(None)
        name = f"AniimoBackendTest_{id(self)}"

        def draw(hwnd, dc):
            rect = wintypes.RECT()
            backend.user32.GetClientRect(hwnd, ctypes.byref(rect))
            for color, area in (
                (0x0000FF, wintypes.RECT(0, 0, rect.right, rect.bottom // 2)),
                (0xFF0000, wintypes.RECT(0, rect.bottom // 2, rect.right, rect.bottom)),
            ):
                brush = gdi32.CreateSolidBrush(color)
                user32.FillRect(dc, ctypes.byref(area), brush)
                gdi32.DeleteObject(brush)
            backend.gdi32.GdiFlush()

        @callback_type
        def window_proc(hwnd, message, wparam, lparam):
            if message == backend.WM_ACTIVATE:
                if wparam & 0xFFFF:
                    self.active_windows.add(hwnd)
                else:
                    self.active_windows.discard(hwnd)
                return 0
            if message == 0x000F:
                paint = PAINTSTRUCT()
                dc = user32.BeginPaint(hwnd, ctypes.byref(paint))
                draw(hwnd, dc)
                user32.EndPaint(hwnd, ctypes.byref(paint))
                return 0
            if message in (0x0317, 0x0318):  # WM_PRINT / WM_PRINTCLIENT
                if self.block_print:
                    self.unblock_print.wait(10)
                draw(hwnd, wparam)
                return 0
            if message in (backend.WM_KEYDOWN, backend.WM_KEYUP, backend.WM_LBUTTONDOWN,
                           backend.WM_LBUTTONUP, backend.WM_RBUTTONDOWN, backend.WM_RBUTTONUP,
                           backend.WM_MBUTTONDOWN, backend.WM_MBUTTONUP,
                           backend.WM_XBUTTONDOWN, backend.WM_XBUTTONUP):
                # Model Unity: queued input is ignored without active notification.
                if hwnd in self.active_windows:
                    self.messages.put((hwnd, message, wparam, lparam))
                return 0
            if message == 0x0010:
                user32.DestroyWindow(hwnd)
                return 0
            if message == 0x0002 and hwnd == getattr(self, "hwnd", None):
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        try:
            window_class = WNDCLASS()
            window_class.lpfnWndProc = window_proc
            window_class.hInstance = instance
            window_class.lpszClassName = name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Visible to child HWND lookup, positioned outside the desktop, no activation.
            self.hwnd = user32.CreateWindowExW(
                0x08000000, name, "Backend fixture", 0x90000000,
                -10000, -10000, 320, 200, None, None, instance, None,
            )
            self.child = user32.CreateWindowExW(
                0, name, "Input child", 0x50000000,
                100, 50, 120, 100, self.hwnd, None, instance, None,
            )
            if not self.hwnd or not self.child:
                raise ctypes.WinError(ctypes.get_last_error())
            user32.UpdateWindow(self.hwnd)
            user32.UpdateWindow(self.child)
            # Ask for a first software-rendered frame before starting capture.
            first_surface = backend._PrintWindowSurface(self.hwnd)
            try:
                first_surface.capture()
            finally:
                first_surface.close()
            self.ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self.error = error
            self.ready.set()
        finally:
            user32.UnregisterClassW(name, instance)

    def close(self):
        self.unblock_print.set()
        backend.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)
        self.thread.join(timeout=3)


class BackgroundIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.window = NativeWindow()

    def tearDown(self):
        self.window.close()

    def test_real_capture_client_pixels_and_cache_expiration(self):
        capture = backend.WindowCapture(self.window.hwnd, "Background")
        try:
            image = capture.grab(timeout=8)
            self.assertEqual(image.size, (320, 200))
            self.assertEqual(image.getpixel((20, 20)), (255, 0, 0))
            self.assertEqual(image.getpixel((20, 180)), (0, 0, 255))
            self.assertTrue(capture.fresh())
            capture._frame_time -= 2
            self.assertFalse(capture.fresh())
            self.assertIsNone(capture.latest())
        finally:
            capture.close()
        self.assertFalse(capture._process.is_alive())

    def test_real_input_targets_child_and_releases_on_stop(self):
        stop = threading.Event()
        interaction = backend.BackgroundInput(self.window.hwnd, stop)
        interaction.press_tab()
        interaction.click("left")
        interaction.click("right")
        received = [self.window.messages.get(timeout=2) for _ in range(6)]
        self.assertEqual([item[0] for item in received], [self.window.child] * 6)
        self.assertEqual([item[1] for item in received], [
            backend.WM_KEYDOWN, backend.WM_KEYUP, backend.WM_LBUTTONDOWN,
            backend.WM_LBUTTONUP, backend.WM_RBUTTONDOWN, backend.WM_RBUTTONUP,
        ])
        self.assertEqual(received[1][3], 0xC00F0001)
        self.assertEqual(received[2][3], backend.pack_position(60, 50))
        with patch.object(stop, "wait", side_effect=lambda seconds: stop.set()):
            interaction.press_tab()
        released = [self.window.messages.get(timeout=2) for _ in range(2)]
        self.assertEqual(released[-1][1], backend.WM_KEYUP)
        interaction.click()
        self.assertTrue(self.window.messages.empty())

    def test_capture_timeout_kills_worker_and_invalidates_cache(self):
        with patch.object(backend, "_capture_worker", hung_capture_worker):
            capture = backend.WindowCapture(self.window.hwnd, "Background")
        try:
            capture.grab(timeout=8)
            started = time.monotonic()
            with self.assertRaises(backend.CaptureError):
                capture.grab(timeout=0.2)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertIsNone(capture.latest())
        finally:
            self.window.unblock_print.set()
            capture.close()

    def test_minimized_unchanged_frames_stop(self):
        capture = backend.WindowCapture(self.window.hwnd, "Background")
        try:
            capture.grab(timeout=8)
            capture._last_change -= 6
            with patch.object(backend.user32, "IsIconic", return_value=True):
                with self.assertRaisesRegex(backend.CaptureError, "ภาพเกมค้าง"):
                    capture.grab()
        finally:
            capture.close()

    def test_background_uses_real_input_only_inside_bounded_focus_pulse(self):
        session = app.BotSession(self.window.hwnd, ("คลังเต็ม", "", 1, 30, "", 200, 250, "Background"))
        with patch.object(app, "press_lock_input") as lock, patch.object(app, "left_click") as left, \
             patch.object(app, "right_click") as right, patch.object(app, "move_cursor_inside_window") as move, \
             patch.object(app, "with_foreground", side_effect=lambda hwnd, stop, action, **options: action()) as pulse:
            session.press_lock()
            session.click("left")
            session.click("right")
            lock.assert_called_once_with(self.window.hwnd, "Tab", session.stop)
            pulse.assert_called_once()
            self.assertFalse(pulse.call_args.kwargs["move_cursor"])
            self.assertTrue(pulse.call_args.kwargs["shield_window"])
            for method in (left, right, move):
                method.assert_not_called()

    def test_input_activates_target_without_changing_foreground(self):
        interaction = backend.BackgroundInput(self.window.hwnd, threading.Event())
        foreground = backend.user32.GetForegroundWindow()
        interaction.press_tab()
        self.window.messages.get(timeout=2)
        self.assertIn(self.window.hwnd, self.window.active_windows)
        self.assertIn(self.window.child, self.window.active_windows)
        self.assertEqual(backend.user32.GetForegroundWindow(), foreground)
        interaction.deactivate()
        deadline = time.monotonic() + 1
        while self.window.active_windows and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        self.assertFalse(self.window.active_windows)

    def test_click_client_coordinates_and_rejects_outside_window(self):
        interaction = backend.BackgroundInput(self.window.hwnd, threading.Event())
        interaction.click("left", 20, 30)
        received = self.window.messages.get(timeout=2)
        self.assertEqual(received[0], self.window.hwnd)
        self.assertEqual(received[3], backend.pack_position(20, 30))
        with self.assertRaises(ValueError):
            interaction.click("left", 320, 30)

    def test_session_close_deactivates_background_game_and_closes_capture(self):
        session = app.BotSession(self.window.hwnd, ("คลังเต็ม", "", 1, 30, "", 200, 250, "Background"))
        session.capture = Mock()
        session.click("left")
        self.window.messages.get(timeout=2)
        self.assertTrue(self.window.active_windows)
        session.stop.set()
        session.close()
        session.capture.close.assert_called_once()
        deadline = time.monotonic() + 1
        while self.window.active_windows and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        self.assertFalse(self.window.active_windows)


class SessionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.bot = app.AnimoBot.__new__(app.AnimoBot)
        self.bot.events = queue.Queue()
        self.session = Mock()
        self.session.stop = threading.Event()
        self.session.ready = threading.Event()
        self.session.settings = ("คลังเต็ม", "https://example.invalid", 1, 30)
        self.bot.session = self.session

    def test_keyword_stops_input_before_webhook(self):
        def send(*args):
            self.assertTrue(self.session.stop.is_set())
            self.assertEqual(self.bot.events.queue[0][1], "found")
        with patch.object(app.pytesseract, "image_to_string", return_value="คลัง เต็ม"), \
             patch.object(app, "post_discord", side_effect=send):
            self.bot.ocr_loop(self.session)

    def test_ocr_result_after_manual_stop_does_not_notify(self):
        def scan(*args, **kwargs):
            self.session.stop.set()
            return "คลังเต็ม"
        with patch.object(app.pytesseract, "image_to_string", side_effect=scan), \
             patch.object(app, "post_discord") as notify:
            self.bot.ocr_loop(self.session)
        notify.assert_not_called()
        self.assertTrue(self.bot.events.empty())

    def test_ocr_failure_disables_input_and_invalidates_frame(self):
        self.session.ready.set()
        with patch.object(app.pytesseract, "image_to_string", side_effect=RuntimeError("timeout")):
            self.bot.ocr_loop(self.session)
        self.assertTrue(self.session.stop.is_set())
        self.assertFalse(self.session.ready.is_set())
        self.session.capture.invalidate.assert_called_once()
        self.assertEqual(self.bot.events.get()[1], "fatal")

    def test_old_session_events_do_not_restart_or_update_current_run(self):
        self.bot.closing = False
        self.bot.root = Mock()
        self.bot.finish_start = Mock()
        self.bot.status_var = Mock()
        old = Mock()
        self.bot.events.put((old, "ocr_ready", old))
        self.bot.events.put((old, "found", "คลังเต็ม"))
        self.bot.process_events()
        self.bot.finish_start.assert_not_called()
        self.bot.status_var.set.assert_not_called()

    def test_flat_capture_rejected(self):
        with self.assertRaises(backend.CaptureError):
            backend.validate_frame(Image.new("RGB", (320, 200), "black"))

    def test_stale_frame_blocks_actions(self):
        self.bot.running = True
        self.session.available.return_value = True
        self.session.ready.set()
        self.session.capture.fresh.return_value = False
        self.assertFalse(self.bot.action_allowed(self.session))

    def test_gui_initializes_with_background_assist_tab_default(self):
        with patch.object(app.AnimoBot, "load_settings", return_value={}), \
             patch.object(app.AnimoBot, "save_settings"):
            bot = app.AnimoBot()
            try:
                bot.root.withdraw()
                bot.root.update_idletasks()
                self.assertEqual(bot.mode_var.get(), "Background")
                self.assertEqual(bot.lock_input_var.get(), "Tab")
                self.assertEqual(bot.capture_button.cget("state"), "normal")
            finally:
                bot.close()


if __name__ == "__main__":
    unittest.main()
