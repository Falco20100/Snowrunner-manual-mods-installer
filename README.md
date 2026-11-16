# SnowRunner Manual Mod Installer

A Windows desktop app for manually downloaded SnowRunner mods. Select a game folder, add one or more mod ZIPs or extracted payload folders, review the queue, and install recognized binary and PAK layouts. Mod files are **not** included or downloaded automatically; use the catalog to open the authors' pages and download them yourself.

## Run on Windows

The source version needs **Python 3.9 or newer with Tkinter**. Install Python for Windows from [python.org](https://www.python.org/downloads/windows/), then double-click `Launch.bat`. Alternatively run `py -3 app.py`.

### Build or rebuild the portable EXE

1. Download or clone this repository onto a Windows PC and extract it if needed. The repository includes `icon.png` and an uploaded `SnowRunnerManualInstaller.exe`.
2. Install **Python 3.13** with pip and Tkinter from [python.org](https://www.python.org/downloads/windows/). Python 3.15 is not compatible with the pinned PyInstaller 6.11.1.
3. Close any running instance of the app, then double-click `Build-Portable.bat`. The script uses Python 3.13, installs PyInstaller 6.11.1 and Pillow, and bundles `icon.png` as the EXE and window icon.
4. The rebuilt executable is `dist\SnowRunnerManualInstaller.exe`. Copy it anywhere on a Windows PC; Python is not needed to run the EXE.

Alternatively, open Command Prompt in the project folder and run:

```bat
py -3.13 -m pip install pyinstaller==6.11.1 pillow
py -3.13 -m PyInstaller --noconfirm --clean --onefile --windowed --icon "icon.png" --add-data "icon.png;." --name SnowRunnerManualInstaller app.py
```

Build the EXE on Windows; PyInstaller does not cross-compile from Linux. The repository includes no installation wizard. The separately uploaded EXE is not automatically rebuilt when the source changes.

## Install

1. **Close SnowRunner. Back up your saves separately.** Check each mod's current requirements, game version, dependencies and multiplayer notes on its author page.
2. The app looks for Steam, Epic Games and PC Game Pass installations on startup. If several are found, select one; use **Detect** to search again or **Browse** to choose a folder manually. The folder must contain `Sources\Bin\SnowRunner.exe` and `preload\paks\client\initial.pak`; the containing `en_us` folder or `Sources\Bin` is also accepted. Protected Game Pass installs or unavailable store metadata may require manual selection or may not be modifiable.
3. In **Mod library**, select a mod, open its author page, and manually download its Windows ZIP. Add the ZIP or its extracted folder. For other mods use **Install queue** and select the specific destination. Multiple ZIPs can be selected at once with one method.
4. Use **Check queue** to detect differing files in the same batch, then **Install queue**. The app checks existing file conflicts during installation. Follow messages in **Activity**. Do not launch the game until it finishes.
5. Start the game and verify each mod. Game updates and store file verification may remove or invalidate mods.

PAK modes need an actual payload such as `[media]`, `[strings]`, `[meshes]`, `[textures]` or `initial.cache_block`, not a full replacement PAK. If an archive contains multiple variants, extract it and select the particular option folder specified by the author. ZIP and extracted folders are supported; extract RAR and 7z using a trusted archive tool first. Never select an untrusted mod executable; this app does not execute downloaded mods or their installers.

### Catalog and compatibility

- **Real Life Mod**: the dedicated mode applies the base `[media]`, `[strings]`, and `initial.cache_block` to `initial.pak` and the `0. MUST-HAVE/[meshes]` to `shared.pak`. Optional RLM variants are not automatically applied. Check that the downloaded RLM release matches your game.
- **Spotter, Next Gen Gameplay/Tire Inflation, Cargo Straps and Headlight Shadows**: dedicated modes copy their recognized binary layouts to `Sources\Bin`. If Spotter is selected alongside a Next Gen loader in the same queue, or with a matching loader previously installed by this app, its bundled `dinput8.dll` is omitted as described by Spotter's author. An unknown existing `version.dll` is not identified automatically; follow the author instructions for that setup. Spotter's current releases are Steam-only. SnowMap must be disabled separately.
- **Next Gen Graphics / skies**, **LOD Optimizer**, **Gameplay Tweaks**, and **Persistent mud and realism** use their authors' own patchers/installers; open the catalog page and follow those instructions. Next Gen Graphics requires its separate sky pack and should be applied last. The app does not run these tools for you.
- Weather Overhaul, Interface Refinement and Effects Removal can contain several options or target archives. Follow the author instructions, extract the intended option, and add it through the relevant PAK mode. The library links to them but does not choose their options for you.
- In-Cabin View, Models Redesign and Serj4ever's QoL Pack have PAK payload presets. SnowRunner Cruise Control is a ReShade add-on: follow its author page.

**Compatibility is not guaranteed.** Many mods change the same files or ship different `version.dll`, `dinput8.dll`, `dxgi.dll` or ReShade settings. Differing files in the same batch, or PAK entries previously installed by this app, are blocked; differing existing binary files are never overwritten. The app cannot detect overlaps already applied by other tools inside a PAK, merge XML, determine loader identity, select optional variants, or ensure mods work together in the game. Resolve such conflicts using the authors' guidance; do not rename or overwrite a loader blindly. The catalog is an aid, not an endorsement or compatibility guarantee.

The referenced [EquDevel installer](https://github.com/equdevel/SnowRunner_mod_installer) and [Nexus listing](https://www.nexusmods.com/snowrunner/mods/35) focus on mod.io subscriptions. This app is independent and handles local manual downloads instead; it does not manage mod.io subscriptions or bypass mod hosting or login requirements.

## Backups and restore

Each successful batch stores the prior contents of changed game PAKs in `SnowRunnerManualBackups` under the selected game root, **outside** `preload\paks\client`. New binary files are recorded so they can be removed on restore. Backups represent the state before that batch, which may already contain mods; they are not verified clean originals. Allow several GB of free space for backups and temporary PAKs.

Click **Restore last install** to undo the most recent completed batch. Restore refuses if any affected file has changed since the batch, including changes made by another tool or a game update. Restore in reverse order and check your game version first. If interrupted or restoration is blocked, inspect the backup's `manifest.json` and numbered files manually; use the store's file verification if needed. Backup files are never automatically deleted.

PAK files are ZIP-compatible but have game-specific conventions. The app preserves original entry names and metadata, uses backslashes for added entries, and validates CRCs and replacement contents before committing. This has only been checked with synthetic archives in this repository, **not with a real SnowRunner installation**; retain independent backups and test before relying on it.

## Roadmap

Planned work for the SnowRunner Manual Mod Installer. Checked items describe the current source tree; they do not guarantee that the separately uploaded `SnowRunnerManualInstaller.exe` was built from that source. Unchecked items are plans, not commitments, and have no release dates. Manual mod installation is a PC workflow, so consoles are out of scope; "cross-platform" here means Windows and Linux/Steam Deck.

### Phase 1 — Core launch and folder detection (current / next)

Focus: launch stability, automatic folder detection, and accurate presets.

- [x] **Core installer engine** — ZIP and folder parsing with validated injection into `initial.pak` and `shared.pak`.
- [x] **Loader conflict handling** — omit Spotter's duplicate `dinput8.dll` when a Next Gen loader is queued or was previously installed by this app. An unrecognized existing loader is refused rather than guessed.
- [x] **Snapshot backups and restore** — pre-install state recorded under `SnowRunnerManualBackups`, restored from the recorded manifest.
- [x] **Preset catalog** — built-in rules for Real Life Mod, Spotter, Next Gen Gameplay, Next Gen Tire Inflation, Cargo Straps, Headlight Shadows, Serj4ever's QoL Pack, and others. Mods that ship their own patcher (Next Gen Graphics, LOD Optimizer, Gameplay Tweaks, Persistent mud) are linked to their author's instructions instead of being copied.
- [x] **Game path auto-detection** — discover valid Steam, Epic Games Store, and PC Game Pass installs where accessible, with manual selection as a fallback.
- [ ] **Batch pre-flight validation** — show every collision point in the queue before anything is written. Today **Check queue** reports differing files within one batch, and existing game-file conflicts are found during install.

### Phase 2 — Linux and Steam Deck (planned, short term)

Focus: native Linux and Proton/Wine support, including Steam Deck.

- [ ] **Cross-platform path engine** — handle Linux `/` paths alongside the game's `\` PAK entry convention.
- [ ] **Steam Deck and Proton detection** — find libraries under `~/.local/share/Steam` and SD-card mounts, and resolve `steamapps/compatdata/1465360/pfx/drive_c` prefixes.
- [ ] **Proton DLL override helper** — explain or offer the required `WINEDLLOVERRIDES` launch option for Steam, for example `WINEDLLOVERRIDES="dinput8=n,b" %command%`. Guidance only; the app will not silently rewrite Steam configuration.
- [ ] **Linux packaging** — AppImage or a Linux PyInstaller build, plus documented `python app.py` use.
- [ ] **Handheld UI scaling** — larger controls and scaling for 7-inch screens such as the Steam Deck and ROG Ally.

### Phase 3 — Custom mods and in-app tweaks (planned, mid term)

- [ ] **Settings and graphics tweaker** — edit supported preset values before injection. This cannot replace mods that own their installer, such as Next Gen Graphics.
- [ ] **Custom mod wizard** — map an unlisted ZIP or folder to a game target (`initial.pak`, `shared.pak`, `boot.pak`, `Sources\Bin`, and similar) and save it as a local preset.
- [ ] **Remote preset rule sync** — optionally fetch community rule files from GitHub without reinstalling the app, with review before use.

### Phase 4 — Advanced engine and integrity (planned, long term)

- [ ] **XML smart-merge** — merge key/value differences instead of overwriting whole XML files, so more XML-based mods can coexist.
- [ ] **Multi-profile management** — switchable mod profiles, for example vanilla co-op, realism, and graphics testing.
- [ ] **Game update guard** — compare build data to detect a game update before allowing a restore or patch.

### Community feedback

Suggestions, bug reports, and preset rule submissions are welcome via GitHub Issues and Pull Requests. Include your game version, store (Steam, Epic, or Game Pass), and the affected mod version.

## Development checks

```text
python -m unittest -v test_installer
python -m py_compile app.py installer.py
```

This is an independent project, not affiliated with SnowRunner, mod.io, or any mod author. Each mod remains subject to its author's license and hosting terms.
