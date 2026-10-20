import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from PIL import Image

import animo_bot as app
import windows_backend as backend
from capture_cycle import CaptureCycle, CyclePhase
from target_lock import LockDetector
from test_background import NativeWindow

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class LockAndCycleTests(unittest.TestCase):
    def setUp(self):
        self.detector = LockDetector(app.LOCK_ANCHOR_FILE)

    def test_real_locked_marker_from_user_reference(self):
        image = Image.open(FIXTURES / "locked_target.png")
        self.assertTrue(self.detector.check(image, window_width=1920)[0])
        for factor in (0.75, 1.5):
            scaled = image.resize((round(image.width * factor), round(image.height * factor)))
            self.assertTrue(self.detector.check(scaled, window_width=round(1920 * factor))[0])

    def test_aim_reticle_and_white_face_are_not_target_lock(self):
        for name in ("tab_focus_1_unlocked.png", "tab_system_1_unlocked.png"):
            self.assertFalse(self.detector.check(Image.open(FIXTURES / name), window_width=1920)[0])

    def test_controller_always_starts_next_cycle_with_lock_then_one_shot(self):
        bot = app.AnimoBot.__new__(app.AnimoBot)
        bot.root = Mock()
        bot.root.after.return_value = "timer"
        bot.action_allowed = Mock(return_value=True)
        bot.status_var = Mock()
        bot.tab_delay_ms = bot.click_delay_ms = 200
        bot.session = Mock()
        bot.session.mode = "Foreground"
        bot.session.lock_input = "Tab"
        bot.session.cycle = CaptureCycle()
        bot.lock_step()
        bot.session.press_lock.assert_called_once_with()
        bot.shoot_step()
        bot.session.click.assert_called_once_with("left")
        self.assertEqual(bot.session.cycle.phase, CyclePhase.COOLDOWN)

        # The old white marker may linger, but the next callback must press Tab
        # before another shot can ever be sent.
        bot.lock_step()
        self.assertEqual(bot.session.press_lock.call_count, 2)
        self.assertEqual(bot.session.click.call_count, 1)
        self.assertEqual(bot.session.cycle.phase, CyclePhase.WAIT_DELAY)

    def test_delay_allows_exactly_one_shot_without_image_detection(self):
        cycle = CaptureCycle()
        cycle.lock_sent()
        cycle.delay_elapsed()
        cycle.shot_sent()
        with self.assertRaises(RuntimeError):
            cycle.shot_sent()
        self.assertEqual(cycle.completed_cycles, 1)


class MouseLockTests(unittest.TestCase):
    def setUp(self):
        self.window = NativeWindow()

    def tearDown(self):
        self.window.close()

    def test_background_lock_uses_focus_pulse_without_moving_cursor(self):
        session = app.BotSession(self.window.hwnd, ("คลังเต็ม", "", 1, 30, "", 200, 250, "Background", "Middle Mouse"))
        with patch.object(app, "press_lock_input") as global_input, \
             patch.object(app, "with_foreground", side_effect=lambda hwnd, stop, action, **options: action()) as foreground:
            session.press_lock()
            global_input.assert_called_once_with(self.window.hwnd, "Middle Mouse", session.stop)
            foreground.assert_called_once()
            self.assertFalse(foreground.call_args.kwargs["move_cursor"])
            self.assertTrue(foreground.call_args.kwargs["shield_window"])
        self.assertTrue(self.window.messages.empty())

    def test_mouse4_and_mouse5_encode_button_on_both_down_and_up(self):
        interaction = backend.BackgroundInput(self.window.hwnd, threading.Event())
        for button, number, flag in (("mouse4", 1, 0x20), ("mouse5", 2, 0x40)):
            interaction.click(button)
            down, up = [self.window.messages.get(timeout=2) for _ in range(2)]
            self.assertEqual(down[2], (number << 16) | flag)
            self.assertEqual(up[2], number << 16)

    def test_native_middle_requires_foreground_and_releases_on_stop(self):
        stop = threading.Event()
        with patch.object(app.user32, "GetForegroundWindow", return_value=0), patch.object(app.user32, "mouse_event") as mouse:
            with self.assertRaises(RuntimeError):
                app.press_lock_input(self.window.hwnd, "Middle Mouse", stop)
            mouse.assert_not_called()
        with patch.object(app.user32, "GetForegroundWindow", return_value=self.window.hwnd), \
             patch.object(app.user32, "mouse_event") as mouse, \
             patch.object(stop, "wait", side_effect=lambda seconds: stop.set()):
            app.press_lock_input(self.window.hwnd, "Middle Mouse", stop)
        self.assertEqual([item.args[0] for item in mouse.call_args_list], [0x20, 0x40])


class HybridContextTests(unittest.TestCase):
    def run_hybrid(
        self, send, activate=True, stop=None, restore=True,
        move_cursor=True, shield_window=False,
    ):
        state = {"foreground": 1000, "cursor": (40, 60)}
        def set_foreground(hwnd):
            if activate and (restore or hwnd != 1000):
                state["foreground"] = hwnd
            return activate
        def get_cursor(point):
            point._obj.x, point._obj.y = state["cursor"]
            return True
        def set_cursor(x, y):
            state["cursor"] = (x, y)
            return True
        with patch.object(backend.user32, "IsWindow", return_value=True), \
             patch.object(backend.user32, "IsIconic", return_value=False), \
             patch.object(backend.user32, "GetForegroundWindow", side_effect=lambda: state["foreground"]), \
             patch.object(backend.user32, "SetForegroundWindow", side_effect=set_foreground) as focus, \
             patch.object(backend.user32, "GetCursorPos", side_effect=get_cursor), \
             patch.object(backend.user32, "SetCursorPos", side_effect=set_cursor) as cursor, \
             patch.object(backend.user32, "GetWindowLongPtrW", return_value=0), \
             patch.object(backend.user32, "SetWindowPos", return_value=True) as position, \
             patch.object(backend.user32, "ClientToScreen", return_value=True), \
             patch.object(backend, "_force_foreground", side_effect=lambda hwnd: set_foreground(hwnd)), \
             patch.object(backend, "client_size", return_value=(320, 200)):
            backend.with_foreground(
                2000, stop or threading.Event(), lambda: send(state), move_cursor=move_cursor,
                shield_window=shield_window,
            )
        return state, focus, cursor, position

    def test_restores_previous_focus_and_cursor_after_one_input(self):
        def send(state):
            self.assertEqual(state, {"foreground": 2000, "cursor": (160, 100)})
        state, focus, cursor, position = self.run_hybrid(send)
        self.assertEqual(state, {"foreground": 1000, "cursor": (40, 60)})
        self.assertEqual(focus.call_args_list, [call(2000), call(1000)])

    def test_user_window_and_cursor_changes_take_precedence(self):
        def send(state):
            state.update(foreground=3000, cursor=(80, 90))
        state, focus, cursor, position = self.run_hybrid(send)
        self.assertEqual(state, {"foreground": 3000, "cursor": (80, 90)})
        focus.assert_called_once_with(2000)

    def test_game_cursor_recentering_still_restores_original_position(self):
        def send(state):
            state["cursor"] = (159, 99)
        state, focus, cursor, position = self.run_hybrid(send)
        self.assertEqual(state, {"foreground": 1000, "cursor": (40, 60)})
        self.assertEqual(cursor.call_args_list, [call(160, 100), call(40, 60)])

    def test_keyboard_focus_pulse_never_moves_cursor(self):
        def send(state):
            self.assertEqual(state, {"foreground": 2000, "cursor": (40, 60)})
        state, focus, cursor, position = self.run_hybrid(send, move_cursor=False)
        self.assertEqual(state, {"foreground": 1000, "cursor": (40, 60)})
        cursor.assert_not_called()

    def test_keyboard_focus_pulse_restores_game_recentering(self):
        def send(state):
            state["cursor"] = (159, 99)
        state, focus, cursor, position = self.run_hybrid(send, move_cursor=False)
        self.assertEqual(state, {"foreground": 1000, "cursor": (40, 60)})
        cursor.assert_called_once_with(40, 60)

    def test_failed_activation_sends_no_input(self):
        send = Mock()
        with self.assertRaises(RuntimeError):
            self.run_hybrid(send, activate=False)
        send.assert_not_called()

    def test_failed_restore_reports_error_after_sending_once(self):
        send = Mock()
        with self.assertRaisesRegex(RuntimeError, "คืนโปรแกรมเดิม"):
            self.run_hybrid(send, restore=False)
        send.assert_called_once()

    def test_restore_to_no_foreground_window_reports_error(self):
        send = Mock()
        state = {"foreground": 1000, "cursor": (40, 60)}
        def set_foreground(hwnd):
            state["foreground"] = hwnd if hwnd == 2000 else 0
            return True
        def get_cursor(point):
            point._obj.x, point._obj.y = state["cursor"]
            return True
        with patch.object(backend.user32, "IsWindow", return_value=True), \
             patch.object(backend.user32, "IsIconic", return_value=False), \
             patch.object(backend.user32, "GetForegroundWindow", side_effect=lambda: state["foreground"]), \
             patch.object(backend.user32, "SetForegroundWindow", side_effect=set_foreground), \
             patch.object(backend.user32, "GetCursorPos", side_effect=get_cursor), \
             patch.object(backend.user32, "SetCursorPos", return_value=True), \
             patch.object(backend.user32, "ClientToScreen", return_value=True), \
             patch.object(backend, "_force_foreground", side_effect=set_foreground), \
             patch.object(backend, "client_size", return_value=(320, 200)):
            with self.assertRaisesRegex(RuntimeError, "คืนโปรแกรมเดิม"):
                backend.with_foreground(2000, threading.Event(), send, move_cursor=False)
        send.assert_called_once()

    def test_stop_while_activating_restores_focus_without_input(self):
        send = Mock()
        stop = threading.Event()
        with patch.object(stop, "wait", side_effect=lambda seconds: stop.set() or True):
            state, focus, cursor, position = self.run_hybrid(send, stop=stop)
        send.assert_not_called()
        self.assertEqual(state["foreground"], 1000)
        cursor.assert_not_called()

    def test_background_shield_is_added_and_removed_around_focus_pulse(self):
        send = Mock()
        state, focus, cursor, position = self.run_hybrid(
            send, move_cursor=False, shield_window=True,
        )
        send.assert_called_once()
        self.assertEqual(state["foreground"], 1000)
        self.assertEqual(position.call_count, 2)
        self.assertEqual([item.args[0] for item in position.call_args_list], [1000, 1000])


if __name__ == "__main__":
    unittest.main()
