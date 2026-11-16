"""Focused detection UI tests with mocked Tk widgets and controlled worker timing."""
import importlib.util
from pathlib import Path
import queue
import sys
import types
import unittest
from unittest.mock import Mock, patch


# The UI controller is exercised directly; widget creation is mocked so these
# tests also run on headless machines without Tk installed or a display server.
tk = types.ModuleType("tkinter")
tk.Tk = type("Tk", (), {})
tk.END = "end"
for name in ("filedialog", "messagebox", "ttk"):
    setattr(tk, name, types.ModuleType(f"tkinter.{name}"))
with patch.dict(sys.modules, {"tkinter": tk, "tkinter.filedialog": tk.filedialog,
                              "tkinter.messagebox": tk.messagebox, "tkinter.ttk": tk.ttk}):
    spec = importlib.util.spec_from_file_location("detection_ui_under_test", Path(__file__).with_name("app.py"))
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)


class Variable:
    def __init__(self, value=""):
        self.value = value
        self.callbacks = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.callbacks:
            callback("name", "", "write")

    def trace_add(self, mode, callback):
        self.callbacks.append(callback)


class DeferredThread:
    """Allow the test to decide exactly when a background scan finishes."""
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        pass

    def finish(self):
        self.target()


class Controller:
    detect_game = app.App.detect_game
    show_detected = app.App.show_detected
    browse_game = app.App.browse_game
    _poll = app.App._poll

    def __init__(self):
        self.game = Variable()
        self.status = Variable()
        self.detecting = False
        self.busy = False
        self.events = queue.Queue()
        self.detect_button = Mock()
        self.install_button = Mock()
        self.restore_button = Mock()
        self.log = Mock()
        self.after = Mock()
        self.path_revision = 0
        self.game.trace_add("write", self.path_changed)

    def path_changed(self, *_):
        self.path_revision += 1


class DetectionUITests(unittest.TestCase):
    def setUp(self):
        self.ui = Controller()
        self.threads = []
        def create_thread(**kwargs):
            thread = DeferredThread(**kwargs)
            self.threads.append(thread)
            return thread
        self.thread_patch = patch.object(app.threading, "Thread", side_effect=create_thread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)

    def start_scan(self, result=None, error=None):
        detector = patch.object(app, "detect_game_installations", side_effect=error, return_value=result)
        detector.start()
        self.addCleanup(detector.stop)
        self.ui.detect_game()
        self.assertTrue(self.ui.detecting)
        self.ui.detect_button.configure.assert_called_with(state="disabled")

    def finish_scan(self):
        self.threads[-1].finish()
        self.ui._poll()
        self.assertFalse(self.ui.detecting)
        self.ui.detect_button.configure.assert_called_with(state="normal")

    def browse(self, path):
        with patch.object(app.filedialog, "askdirectory", return_value=path, create=True), \
             patch.object(app, "game_paths", return_value=(path, None, None)):
            self.ui.browse_game()

    def dialog(self):
        window = Mock()
        choices = Mock()
        choices.curselection.return_value = (1,)
        buttons = []
        def button(*args, **kwargs):
            buttons.append(kwargs)
            return Mock()
        patches = [patch.object(app.tk, "Toplevel", return_value=window, create=True),
                   patch.object(app.tk, "Listbox", return_value=choices, create=True),
                   patch.object(app.ttk, "Label", return_value=Mock(), create=True),
                   patch.object(app.ttk, "Button", side_effect=button, create=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return window, choices, buttons

    def test_single_install_is_selected_after_scan(self):
        self.start_scan([("Steam", "C:/Steam/SnowRunner")])
        self.finish_scan()
        self.assertEqual(self.ui.game.get(), "C:/Steam/SnowRunner")
        self.assertIn("Detected Steam", self.ui.status.get())

    def test_no_install_keeps_manual_fallback(self):
        self.start_scan([])
        self.finish_scan()
        self.assertEqual(self.ui.game.get(), "")
        self.assertIn("Browse", self.ui.status.get())
        self.browse("D:/SnowRunner")
        self.assertEqual(self.ui.game.get(), "D:/SnowRunner")

    def test_multiple_installs_allow_choice(self):
        window, choices, buttons = self.dialog()
        self.start_scan([("Steam", "C:/Steam/SnowRunner"), ("Epic Games", "D:/Epic/SnowRunner")])
        self.finish_scan()
        self.assertEqual(self.ui.game.get(), "")
        self.assertEqual(choices.insert.call_count, 2)
        choices.selection_set.assert_called_once_with(0)
        buttons[0]["command"]()
        self.assertEqual(self.ui.game.get(), "D:/Epic/SnowRunner")
        self.assertIn("Selected Epic Games", self.ui.status.get())
        window.destroy.assert_called_once()

    def test_browse_while_scan_runs_keeps_manual_selection(self):
        self.start_scan([("Steam", "C:/Steam/SnowRunner")])
        self.browse("D:/Manual/SnowRunner")
        self.finish_scan()
        self.assertEqual(self.ui.game.get(), "D:/Manual/SnowRunner")
        self.assertIn("manually", self.ui.status.get())

    def test_browse_same_existing_path_while_scan_runs_keeps_it(self):
        self.ui.game.set("D:/Manual/SnowRunner")
        self.start_scan([("Steam", "C:/Steam/SnowRunner")])
        self.browse("D:/Manual/SnowRunner")
        self.finish_scan()
        self.assertEqual(self.ui.game.get(), "D:/Manual/SnowRunner")

    def test_manual_selection_during_scan_prevents_multiple_dialog(self):
        with patch.object(app.tk, "Toplevel", create=True) as window:
            self.start_scan([("Steam", "C:/Steam/SnowRunner"), ("Epic Games", "D:/Epic/SnowRunner")])
            self.browse("E:/Manual/SnowRunner")
            self.finish_scan()
            self.assertEqual(self.ui.game.get(), "E:/Manual/SnowRunner")
            window.assert_not_called()

    def test_same_path_reselected_after_dialog_wins(self):
        self.ui.game.set("E:/Manual/SnowRunner")
        window, _, buttons = self.dialog()
        self.start_scan([("Steam", "C:/Steam/SnowRunner"), ("Epic Games", "D:/Epic/SnowRunner")])
        self.finish_scan()
        self.browse("E:/Manual/SnowRunner")
        buttons[0]["command"]()
        self.assertEqual(self.ui.game.get(), "E:/Manual/SnowRunner")
        window.destroy.assert_called_once()

    def test_manual_selection_after_multiple_dialog_wins(self):
        window, _, buttons = self.dialog()
        self.start_scan([("Steam", "C:/Steam/SnowRunner"), ("Epic Games", "D:/Epic/SnowRunner")])
        self.finish_scan()
        self.browse("E:/Manual/SnowRunner")
        buttons[0]["command"]()
        self.assertEqual(self.ui.game.get(), "E:/Manual/SnowRunner")
        window.destroy.assert_called_once()

    def test_failed_scan_does_not_overwrite_manual_success_status(self):
        self.start_scan(error=RuntimeError("search failed"))
        self.browse("D:/Manual/SnowRunner")
        self.finish_scan()
        self.assertEqual(self.ui.game.get(), "D:/Manual/SnowRunner")
        self.assertIn("Game found", self.ui.status.get())
        self.ui.log.assert_called_once()

    def test_scan_does_not_change_path_during_install(self):
        self.start_scan([("Steam", "C:/Steam/SnowRunner")])
        self.ui.busy = True
        self.ui.status.set("Installing")
        self.finish_scan()
        self.assertEqual(self.ui.game.get(), "")
        self.assertEqual(self.ui.status.get(), "Installing")
        self.ui.log.assert_called_once()

    def test_dialog_choice_during_install_does_not_change_path_or_status(self):
        window, _, buttons = self.dialog()
        self.start_scan([("Steam", "C:/Steam/SnowRunner"), ("Epic Games", "D:/Epic/SnowRunner")])
        self.finish_scan()
        self.ui.busy = True
        self.ui.status.set("Installing")
        buttons[0]["command"]()
        self.assertEqual(self.ui.game.get(), "")
        self.assertEqual(self.ui.status.get(), "Installing")
        window.destroy.assert_called_once()

    def test_failed_scan_without_manual_selection_shows_fallback(self):
        self.start_scan(error=RuntimeError("search failed"))
        self.finish_scan()
        self.assertIn("Browse", self.ui.status.get())
        self.ui.log.assert_called_once()

    def test_failed_scan_during_install_preserves_status(self):
        self.start_scan(error=RuntimeError("search failed"))
        self.ui.busy = True
        self.ui.status.set("Installing")
        self.finish_scan()
        self.assertEqual(self.ui.status.get(), "Installing")
        self.ui.log.assert_called_once()

    def test_no_duplicate_scan_while_running(self):
        self.start_scan([])
        self.ui.detect_game()
        self.assertEqual(len(self.threads), 1)
        self.finish_scan()


if __name__ == "__main__":
    unittest.main()
