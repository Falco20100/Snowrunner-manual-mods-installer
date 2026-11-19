"""SnowRunner manual mod installer: offline desktop UI."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from installer import InstallError, Job, detect_game_installations, game_paths, install, latest_batch, preflight, restore_latest


# The catalog links to authors' pages; it never distributes or runs their files.
CATALOG = [
    ("Real Life Mod & Bug Fix (Phase 19)", "https://mod.io/g/snowrunner/m/real-life-mod", "rlm", "Base files only: [media], [strings], initial.cache_block and MUST-HAVE [meshes]. Optional variants require manual selection. Other truck overhauls may replace the same files."),
    ("Spotter — SnowRunner Minimap", "https://mod.io/g/snowrunner/m/spotter-snowrunner-minimap", "spotter", "Steam only. Uses an ASI loader; existing loaders are not overwritten. Next Gen's loader can load Spotter. Check the author page for your game build."),
    ("Next Gen Gameplay", "https://mod.io/g/snowrunner/m/next-gen-gameplay", "gameplay", "Binary payload next to SnowRunner.exe. Loader and ReShade files can conflict with other mods; check the author instructions."),
    ("Next Gen Tire Inflation", "https://mod.io/g/snowrunner/m/tire-inflation-system", "tire", "Binary payload next to SnowRunner.exe. If another ASI loader or ReShade is present, follow the mod author's instructions."),
    ("Cargo Straps (CargoStrap)", "https://mod.io/g/snowrunner/m/cargo-straps-cargostrap", "cargo", "Binary payload. CargoStrap and other mods may provide different version.dll loaders; do not overwrite one blindly."),
    ("Next Gen Graphics + sky pack", "https://mod.io/g/snowrunner/m/snowrunner-next-gen1", "external", "Download both the program and the separate sky pack, extract to the same folder, and run the author's SnowRunnerNextGen.exe. Install it last. The author tool chooses and applies graphics modules."),
    ("Headlight Shadows", "https://mod.io/g/snowrunner/m/headlight-shadows", "headlights", "Binary payload under Sources/Bin. Check version-specific compatibility with Next Gen Graphics."),
    ("SnowRunner Cruise Control (gamepad)", "https://mod.io/g/snowrunner/m/snowrunner-cruise-control-gamepad-only-no-keybo", "external", "ReShade add-on; read its prerequisites and the author's install instructions."),
    ("Immersive In-Cabin View & Head Out", "https://mod.io/g/snowrunner/m/immersive-in-cabin-view-head-out", "pak:initial.pak", "Select its intended [media] payload. May conflict with other truck XML modifications."),
    ("Camera Clipping Fix", "https://mod.io/g/snowrunner/m/manual-camera-clipping-fix", "pak:initial.pak", "Original manual release: [media] into initial.pak. Already included in McKillem's Tweak Pack; can overlap No Collision Camera and other object XML mods. Check the author page for game compatibility."),
    ("No Collision Camera", "https://mod.io/g/snowrunner/m/no-collision-camera", "external", "Manual installation with WinRAR per the author. Choose exactly one of the default or improved wheel-grip folders. Back up initial.pak outside the PAK folder; overlaps Camera Clipping Fix and other object XML mods. Check game version and author instructions."),
    ("Weather Overhaul", "https://mod.io/g/snowrunner/m/weather-overhaul", "external", "Select each intended option folder and target PAK as directed by the author. May overlap Next Gen Graphics and Effects Removal."),
    ("Models Redesign", "https://mod.io/g/snowrunner/m/models-redesign", "pak:main_menu_textures.pak", "Select the [textures] payload for main_menu_textures.pak."),
    ("Interface Refinement", "https://mod.io/g/snowrunner/m/interface-overhaul", "external", "Follow the author's target-archive and option instructions; select an extracted payload using a PAK mode."),
    ("LOD Optimizer", "https://mod.io/g/snowrunner/m/lod-optimizer", "external", "Author's Python patcher; run it separately and choose the options yourself. Check for overlap with Next Gen Graphics."),
    ("Effects Removal — Bokeh", "https://mod.io/g/snowrunner/m/effects-removal", "effects:bokeh", "Install only Bokeh to boot.pak. Already included in Weather Overhaul. Select the full ZIP or the named Bokeh Effect Removal folder."),
    ("Effects Removal — Indicators", "https://mod.io/g/snowrunner/m/effects-removal", "effects:indicators", "Install only Indicators to boot.pak. Already included in Gameplay Tweaks by a different method. Select the full ZIP or the named Indicators Removal folder."),
    ("Effects Removal — Truck SFX", "https://mod.io/g/snowrunner/m/effects-removal", "effects:truck", "Install only Truck SFX to initial.pak. Select the full ZIP or the named Truck SFX Removal folder."),
    ("Effects Removal — Horn SFX", "https://mod.io/g/snowrunner/m/effects-removal", "effects:horn", "Install only Horn SFX to initial.pak. Select the full ZIP or the named Horn SFX Removal folder."),
    ("Gameplay Tweaks", "https://mod.io/g/snowrunner/m/gameplay-tweaks", "external", "Author's patcher modifies initial.cache_block; use its instructions. Applying a replacement cache afterward can undo it."),
    ("Persistent mud and realism", "https://mod.io/g/snowrunner/m/persistent-mud-and-realism", "external", "Use the author's launcher and Install.cmd; it has its own configuration and loader compatibility constraints."),
    ("Serj4ever's QoL Pack - ONLY FOR PC", "https://mod.io/g/snowrunner/m/serj4evers-qol-modpack-only-for-pc", "pak:initial.pak", "[media] into initial.pak. May overlap Real Life Mod and other truck XML edits; conflicts are not merged."),
]
MODES = {
    "Real Life Mod base": "rlm",
    "Spotter": "spotter",
    "Next Gen Gameplay": "gameplay",
    "Next Gen Tire Inflation": "tire",
    "Cargo Straps": "cargo",
    "Headlight Shadows": "headlights",
    "Binary mod → Sources/Bin": "bin",
    "PAK payload → initial.pak": "pak:initial.pak",
    "PAK payload → shared.pak": "pak:shared.pak",
    "PAK payload → boot.pak": "pak:boot.pak",
    "PAK payload → main_menu_textures.pak": "pak:main_menu_textures.pak",
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SnowRunner Manual Mod Installer")
        icon_path = Path(__file__).resolve().parent / "icon.png"
        if icon_path.is_file():
            self.icon_image = tk.PhotoImage(file=str(icon_path))
            self.iconphoto(True, self.icon_image)
        self.geometry("1100x760")
        self.minsize(850, 620)
        self.jobs = []
        self.events = queue.Queue()
        self.busy = False
        self.detecting = False
        self.game = tk.StringVar()
        self.path_revision = 0
        self.game.trace_add("write", self.path_changed)
        self.mode = tk.StringVar(value="Binary mod → Sources/Bin")
        self.status = tk.StringVar(value="Choose the SnowRunner folder, then add downloaded ZIPs or extracted folders.")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        self.after(100, self._poll)
        self.after(100, self.detect_game)

    def close_window(self):
        if self.busy:
            messagebox.showwarning("Operation in progress", "Wait until the current operation finishes before closing the installer.")
            return
        self.destroy()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self, padding=(16, 14, 16, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="SNOWRUNNER  /  MANUAL MOD INSTALLER", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(header, text="Game folder").grid(row=1, column=0, sticky="w", pady=(14, 0))
        ttk.Entry(header, textvariable=self.game).grid(row=1, column=1, sticky="ew", padx=10, pady=(14, 0))
        self.detect_button = ttk.Button(header, text="Detect", command=self.detect_game)
        self.detect_button.grid(row=1, column=2, padx=(0, 6), pady=(14, 0))
        ttk.Button(header, text="Browse…", command=self.browse_game).grid(row=1, column=3, pady=(14, 0))
        ttk.Label(self, text="Close SnowRunner before installing. Downloads are provided by mod authors, not bundled here.", padding=(16, 3)).grid(row=1, column=0, sticky="w")
        tabs = ttk.Notebook(self)
        tabs.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
        self._catalog_tab(tabs)
        self._queue_tab(tabs)
        self._activity_tab(tabs)
        foot = ttk.Frame(self, padding=(16, 5, 16, 14))
        foot.grid(row=3, column=0, sticky="ew")
        foot.columnconfigure(0, weight=1)
        ttk.Label(foot, textvariable=self.status, wraplength=700).grid(row=0, column=0, sticky="w")
        self.restore_button = ttk.Button(foot, text="Uninstall last batch…", command=self.restore)
        self.restore_button.grid(row=0, column=1, padx=6)
        self.install_button = ttk.Button(foot, text="Install queue", command=self.install)
        self.install_button.grid(row=0, column=2)

    def _catalog_tab(self, tabs):
        tab = ttk.Frame(tabs, padding=12)
        tab.columnconfigure(0, weight=2)
        tab.columnconfigure(1, weight=3)
        tab.rowconfigure(1, weight=1)
        tabs.add(tab, text="  Mod library  ")
        ttk.Label(tab, text="POPULAR MANUAL MODS", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.library = tk.Listbox(tab, exportselection=False, font=("Segoe UI", 10), activestyle="none")
        self.library.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        for item in CATALOG:
            self.library.insert(tk.END, item[0])
        self.library.bind("<<ListboxSelect>>", self.select_catalog)
        detail = ttk.Frame(tab)
        detail.grid(row=1, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        self.title_label = ttk.Label(detail, text="Choose a mod", font=("Segoe UI", 14, "bold"), wraplength=470)
        self.title_label.grid(row=0, column=0, sticky="w", pady=(2, 10))
        self.detail_label = ttk.Label(detail, text="Select a mod to see its install method and open its official download page.", wraplength=470, justify="left")
        self.detail_label.grid(row=1, column=0, sticky="nw", pady=(0, 14))
        ttk.Button(detail, text="Open author download page", command=self.open_page).grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Button(detail, text="Add downloaded ZIP…", command=lambda: self.add_catalog(False)).grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Button(detail, text="Add extracted folder…", command=lambda: self.add_catalog(True)).grid(row=4, column=0, sticky="w")
        ttk.Label(detail, text="Some mods are standalone tools. Open the author page and follow their instructions; they cannot be safely installed as a generic file copy.", wraplength=470, justify="left").grid(row=5, column=0, sticky="w", pady=(22, 0))
        self.library.selection_set(0)
        self.select_catalog()

    def _queue_tab(self, tabs):
        tab = ttk.Frame(tabs, padding=12)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        tabs.add(tab, text="  Install queue  ")
        ttk.Label(tab, text="QUEUE ORDER · conflicting files are blocked, not automatically merged", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.tree = ttk.Treeview(tab, columns=("mode", "source"), show="headings", selectmode="extended")
        self.tree.heading("mode", text="Install method")
        self.tree.heading("source", text="Downloaded ZIP / extracted folder")
        self.tree.column("mode", width=230, stretch=False)
        self.tree.column("source", width=620)
        self.tree.grid(row=1, column=0, sticky="nsew")
        actions = ttk.Frame(tab)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Combobox(actions, values=list(MODES), textvariable=self.mode, state="readonly", width=35).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Add ZIPs…", command=self.add_zips).pack(side="left", padx=3)
        ttk.Button(actions, text="Add folder…", command=self.add_folder).pack(side="left", padx=3)
        ttk.Button(actions, text="Remove selected", command=self.remove).pack(side="left", padx=3)
        ttk.Button(actions, text="Check queue", command=self.check).pack(side="right")

    def _activity_tab(self, tabs):
        tab = ttk.Frame(tabs, padding=12)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        tabs.add(tab, text="  Activity  ")
        self.logbox = tk.Text(tab, wrap="word", state="disabled", font=("Consolas", 10))
        self.logbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tab, command=self.logbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.logbox.configure(yscrollcommand=scroll.set)

    def selected(self):
        sel = self.library.curselection()
        return CATALOG[sel[0]] if sel else None

    def select_catalog(self, _event=None):
        item = self.selected()
        if item:
            self.title_label.configure(text=item[0])
            self.detail_label.configure(text=item[3])

    def open_page(self):
        item = self.selected()
        if item:
            webbrowser.open(item[1])

    def path_changed(self, *_):
        self.path_revision += 1

    def detect_game(self):
        if self.detecting or self.busy:
            return
        self.detecting = True
        self.detect_button.configure(state="disabled")
        self.status.set("Looking for SnowRunner in Steam, Epic Games, and PC Game Pass…")
        initial_revision = self.path_revision
        def work():
            try:
                self.events.put(("detected", (detect_game_installations(), initial_revision)))
            except Exception as exc:
                self.events.put(("detect_error", (str(exc), initial_revision)))
        threading.Thread(target=work, daemon=True).start()

    def show_detected(self, found, initial_revision):
        if self.busy:
            self.log("Detection finished while an operation was running; the selected folder was not changed. Use Detect to search again.")
            return
        if self.path_revision != initial_revision:
            self.status.set("Detection finished; your manually entered folder was kept.")
            return
        if not found:
            self.status.set("No accessible installation detected. Use Browse to select the game folder.")
        elif len(found) == 1:
            store, path = found[0]
            self.game.set(path)
            self.status.set(f"Detected {store}: {path}")
        else:
            self.status.set("Multiple installations found. Choose one below or use Browse.")
            window = tk.Toplevel(self)
            window.title("Choose SnowRunner installation")
            window.transient(self)
            window.columnconfigure(0, weight=1)
            ttk.Label(window, text="Select the installation to use:", padding=10).grid(row=0, column=0, sticky="w")
            choices = tk.Listbox(window, width=90, height=min(len(found), 8), exportselection=False)
            choices.grid(row=1, column=0, sticky="nsew", padx=10)
            for store, path in found:
                choices.insert(tk.END, f"{store} — {path}")
            choices.selection_set(0)
            def choose():
                if self.busy:
                    self.log("Selection was not changed while an operation is running.")
                    window.destroy()
                    return
                if self.path_revision != initial_revision:
                    self.status.set("Your manually entered folder was kept.")
                    window.destroy()
                    return
                if choices.curselection():
                    store, path = found[choices.curselection()[0]]
                    self.game.set(path)
                    self.status.set(f"Selected {store}: {path}")
                    window.destroy()
            ttk.Button(window, text="Use selected", command=choose).grid(row=2, column=0, sticky="e", padx=10, pady=10)

    def browse_game(self):
        value = filedialog.askdirectory(title="Select SnowRunner game folder")
        if value:
            self.game.set(value)
            try:
                base, _, _ = game_paths(value)
                self.status.set(f"Game found: {base}")
            except InstallError as exc:
                messagebox.showwarning("Game folder", str(exc))

    def append_job(self, source, mode, label=""):
        self.jobs.append(Job(source, mode, label))
        self.tree.insert("", "end", values=(label or mode, source))
        self.status.set(f"{len(self.jobs)} package(s) in queue.")

    def add_catalog(self, folder):
        item = self.selected()
        if not item:
            return
        if item[2] == "external":
            messagebox.showinfo("Author's installation required", item[3])
            return
        path = filedialog.askdirectory(title="Choose extracted mod folder") if folder else filedialog.askopenfilename(title="Choose downloaded mod ZIP", filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")])
        if path:
            self.append_job(path, item[2], item[0])

    def add_zips(self):
        for path in filedialog.askopenfilenames(title="Choose mod ZIP archives", filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")]):
            self.append_job(path, MODES[self.mode.get()])

    def add_folder(self):
        path = filedialog.askdirectory(title="Choose extracted mod payload folder")
        if path:
            self.append_job(path, MODES[self.mode.get()])

    def remove(self):
        selected = set(self.tree.selection())
        rows = self.tree.get_children()
        self.jobs = [job for row, job in zip(rows, self.jobs) if row not in selected]
        for row in selected:
            self.tree.delete(row)
        self.status.set(f"{len(self.jobs)} package(s) in queue.")

    def log(self, text):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", text + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def run_work(self, operation, success):
        if self.busy:
            return
        self.busy = True
        self.install_button.configure(state="disabled")
        self.restore_button.configure(state="disabled")
        self.status.set("Working… see Activity for progress. Do not start the game.")
        def work():
            try:
                result = operation(lambda text: self.events.put(("log", text)))
                self.events.put(("success", (success, result)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log(value)
                    continue
                if kind in {"detected", "detect_error"}:
                    self.detecting = False
                    self.detect_button.configure(state="normal")
                    if kind == "detected":
                        self.show_detected(*value)
                    else:
                        error, initial_revision = value
                        self.log("Detection error: " + error)
                        if not self.busy and self.path_revision == initial_revision:
                            self.status.set("Detection failed. Use Browse to select the game folder.")
                    continue
                self.busy = False
                self.install_button.configure(state="normal")
                self.restore_button.configure(state="normal")
                if kind == "error":
                    self.log("ERROR: " + value)
                    self.status.set("Operation stopped. See Activity.")
                    messagebox.showerror("Operation stopped", value)
                else:
                    message, result = value
                    self.status.set(message)
                    messagebox.showinfo("SnowRunner Manual Mod Installer", f"{message}\n\n{result}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def check(self):
        root, jobs = self.game.get(), list(self.jobs)
        def check_queue(log):
            count, issues = preflight(root, jobs)
            if issues:
                raise InstallError("Pre-flight found these issues:\n" + "\n".join(issues))
            return f"{count} unique target files; no known conflicts found. Install rechecks before writing."
        self.run_work(check_queue, "Queue checked.")

    def install(self):
        if not self.jobs:
            messagebox.showwarning("Empty queue", "Add at least one downloaded mod first.")
            return
        if not messagebox.askyesno("Install queue", "Close SnowRunner first. Game files will be modified; backups can use several GB.\n\nA file collision does not merge two mods. Review authors' compatibility and game versions. Continue?"):
            return
        root, jobs = self.game.get(), list(self.jobs)
        self.run_work(lambda log: install(root, jobs, log), "Installation finished. Backups are outside the game PAK directory.")

    def restore(self):
        if self.busy:
            return
        root = self.game.get()
        try:
            backup, labels = latest_batch(root)
        except (InstallError, OSError, ValueError) as exc:
            messagebox.showerror("Cannot uninstall", str(exc))
            return
        names = "\n".join(f"• {label}" for label in labels)
        if not messagebox.askyesno("Uninstall last batch", f"Close SnowRunner first. This restores the entire latest batch, not individual mods:\n\n{names}\n\nBackup: {backup}\n\nChanges made afterward by other tools block restoration. Continue?"):
            return
        self.run_work(lambda log: restore_latest(root, log, expected_backup=backup), "Last batch uninstalled; previous state restored.")


if __name__ == "__main__":
    App().mainloop()
