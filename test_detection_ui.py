"""Focused detection UI tests with mocked Tk widgets and controlled worker timing."""
import hashlib
import importlib.util
import json
from pathlib import Path
import queue
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import Mock, patch

from installer import Job


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
    check = app.App.check
    run_work = app.App.run_work
    restore = app.App.restore

    def __init__(self):
        self.game = Variable()
        self.status = Variable()
        self.jobs = []
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


class CatalogTests(unittest.TestCase):
    def test_camera_clipping_fix_uses_manual_initial_pak_preset(self):
        entries = [entry for entry in app.CATALOG if entry[0] == "Camera Clipping Fix"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], "https://mod.io/g/snowrunner/m/manual-camera-clipping-fix")
        self.assertEqual(entries[0][2], "pak:initial.pak")


class UninstallUITests(unittest.TestCase):
    def test_uninstall_confirms_the_exact_latest_batch(self):
        ui = Controller()
        ui.game.set("fixture root")
        ui.run_work = Mock()
        backup = Path("fixture-backup")
        with patch.object(app, "latest_batch", return_value=(backup, ["Mod A", "Mod B"])), \
             patch.object(app.messagebox, "askyesno", return_value=True, create=True) as confirm, \
             patch.object(app, "restore_latest") as restore:
            ui.restore()
            self.assertIn("Mod A", confirm.call_args.args[1])
            self.assertIn("entire latest batch", confirm.call_args.args[1])
            operation = ui.run_work.call_args.args[0]
            operation(lambda _: None)
            restore.assert_called_once_with("fixture root", unittest.mock.ANY, expected_backup=backup)

    def test_uninstall_rejects_unresolved_backup_without_prompting(self):
        ui = Controller()
        with patch.object(app, "latest_batch", side_effect=app.InstallError("Unresolved backup")), \
             patch.object(app.messagebox, "showerror", create=True) as error, \
             patch.object(app.messagebox, "askyesno", create=True) as confirm:
            ui.restore()
            error.assert_called_once_with("Cannot uninstall", "Unresolved backup")
            confirm.assert_not_called()


class CheckQueueUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "SnowRunner"
        self.bin = self.root / "Sources" / "Bin"
        self.paks = self.root / "preload" / "paks" / "client"
        self.bin.mkdir(parents=True)
        self.paks.mkdir(parents=True)
        (self.bin / "SnowRunner.exe").write_bytes(b"fake game")
        self.write_zip(self.paks / "initial.pak", {"[media]/truck.xml": b"original"})
        self.ui = Controller()
        self.ui.game.set(str(self.root))
        self.threads = []
        def create_thread(**kwargs):
            thread = DeferredThread(**kwargs)
            self.threads.append(thread)
            return thread
        thread_patch = patch.object(app.threading, "Thread", side_effect=create_thread)
        thread_patch.start()
        self.addCleanup(thread_patch.stop)
        self.info = Mock()
        self.error = Mock()
        for name, mock in (("showinfo", self.info), ("showerror", self.error)):
            dialog_patch = patch.object(app.messagebox, name, mock, create=True)
            dialog_patch.start()
            self.addCleanup(dialog_patch.stop)

    @staticmethod
    def write_zip(path, entries):
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)

    def package(self, name, entries, mode):
        path = Path(self.tmp.name) / name
        self.write_zip(path, entries)
        return Job(str(path), mode, name)

    def snapshot_game(self):
        return {str(path.relative_to(self.root)): (path.read_bytes() if path.is_file() else None)
                for path in self.root.rglob("*")}

    def check_queue(self):
        before = self.snapshot_game()
        self.ui.check()
        self.assertTrue(self.ui.busy)
        self.ui.install_button.configure.assert_called_with(state="disabled")
        self.ui.restore_button.configure.assert_called_with(state="disabled")
        self.threads[-1].finish()
        self.ui._poll()
        self.assertFalse(self.ui.busy)
        self.ui.install_button.configure.assert_called_with(state="normal")
        self.ui.restore_button.configure.assert_called_with(state="normal")
        self.assertEqual(before, self.snapshot_game(), "Check queue modified the game fixture")

    def test_success_reports_count_without_writes(self):
        self.ui.jobs = [self.package("truck.zip", {"[media]/truck.xml": b"modified"}, "pak:initial.pak")]
        self.check_queue()
        self.info.assert_called_once_with("SnowRunner Manual Mod Installer",
            "Queue checked.\n\n1 unique target files; no known conflicts found. Install rechecks before writing.")
        self.error.assert_not_called()
        self.assertEqual(self.ui.status.get(), "Queue checked.")

    def test_multiple_conflicts_and_spotter_warnings_are_all_displayed(self):
        (self.bin / "version.dll").write_bytes(b"unknown loader")
        (self.bin / "SnowMap.asi").write_bytes(b"snowmap")
        (self.bin / "dinput8.dll").write_bytes(b"existing loader")
        self.ui.jobs = [
            self.package("a.zip", {"[media]/truck.xml": b"a", "[media]/other.xml": b"a"}, "pak:initial.pak"),
            self.package("b.zip", {"[media]/truck.xml": b"b", "[media]/other.xml": b"b"}, "pak:initial.pak"),
            self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"new loader"}, "spotter"),
        ]
        self.check_queue()
        self.info.assert_not_called()
        self.error.assert_called_once()
        title, message = self.error.call_args.args
        self.assertEqual(title, "Operation stopped")
        for text in ("Pre-flight found these issues:", "truck.xml", "other.xml",
                     "unknown existing version.dll", "SnowMap.asi", "Existing file differs", "dinput8.dll"):
            self.assertIn(text, message)
        self.ui.log.assert_called_once_with("ERROR: " + message)
        self.assertEqual(self.ui.status.get(), "Operation stopped. See Activity.")

    def test_queued_nextgen_exempts_spotter_loader_but_not_other_conflicts(self):
        (self.bin / "dinput8.dll").write_bytes(b"existing loader")
        (self.bin / "Spotter.asi").write_bytes(b"existing spotter")
        self.ui.jobs = [
            self.package("gameplay.zip", {"Gauges.asi": b"gameplay", "version.dll": b"nextgen"}, "gameplay"),
            self.package("spotter.zip", {"Spotter.asi": b"new spotter", "dinput8.dll": b"spotter loader"}, "spotter"),
        ]
        self.check_queue()
        message = self.error.call_args.args[1]
        self.assertIn("Spotter.asi", message)
        self.assertNotIn("dinput8.dll", message)
        self.info.assert_not_called()

    def test_previously_installed_nextgen_exempts_spotter_loader(self):
        loader = b"known nextgen loader"
        (self.bin / "version.dll").write_bytes(loader)
        (self.bin / "dinput8.dll").write_bytes(b"existing loader")
        backup = self.root / "SnowRunnerManualBackups" / "nextgen"
        backup.mkdir(parents=True)
        (backup / "manifest.json").write_text(json.dumps({"status": "installed", "game": str(self.root), "modes": ["gameplay"],
            "claims": [{"group": "bin", "path": "version.dll", "hash": hashlib.sha256(loader).hexdigest()}]}), encoding="utf-8")
        self.ui.jobs = [self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"spotter loader"}, "spotter")]
        self.check_queue()
        self.info.assert_called_once()
        self.assertIn("no known conflicts found", self.info.call_args.args[1])
        self.error.assert_not_called()

    def test_non_spotter_loader_is_not_exempt_when_nextgen_is_queued(self):
        (self.bin / "dinput8.dll").write_bytes(b"existing loader")
        self.ui.jobs = [
            self.package("gameplay.zip", {"Gauges.asi": b"gameplay", "version.dll": b"nextgen"}, "gameplay"),
            self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"spotter loader"}, "spotter"),
            self.package("other.zip", {"dinput8.dll": b"other loader"}, "bin"),
        ]
        self.check_queue()
        message = self.error.call_args.args[1]
        self.assertIn("Conflict: bin/dinput8.dll", message)
        self.assertIn("Existing file differs", message)
        self.info.assert_not_called()

    def test_empty_queue_reports_error_without_writes(self):
        self.check_queue()
        self.error.assert_called_once_with("Operation stopped", "The queue is empty.")
        self.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
