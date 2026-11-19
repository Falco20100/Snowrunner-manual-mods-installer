# SnowRunner Manual Mod Installer

A Windows desktop app for manually downloaded SnowRunner mods. Select a game folder, add one or more mod ZIPs or extracted payload folders, review the queue, and install recognized binary and PAK layouts. Mod files are **not** included or downloaded automatically; use the catalog to open the authors' pages and download them yourself.

**Release status:** the source and previously uploaded EXE have not been rebuilt and verified together on Windows. The EXE may be older than this source; do not treat it as a current release.

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
4. Use **Check queue** to inspect the whole batch for known conflicts with other queued files, previous installs, and existing binary files before writing. The app rechecks when you select **Install queue**. Follow messages in **Activity**. Do not launch the game until it finishes.
5. Start the game and verify each mod. Game updates and store file verification may remove or invalidate mods.

PAK modes need an actual payload such as `[media]`, `[strings]`, `[meshes]`, `[textures]` or `initial.cache_block`, not a full replacement PAK. Unless a dedicated catalog preset handles its variants, extract multi-option archives and select the particular option folder specified by the author. ZIP and extracted folders are supported; extract RAR and 7z using a trusted archive tool first. The installer does not execute downloaded mods or their installers; the separate SnowPatcher shortcut runs only an executable you explicitly select. Never select an untrusted executable.

### Catalog and compatibility

- **Real Life Mod**: the dedicated mode applies the base `[media]`, `[strings]`, and `initial.cache_block` to `initial.pak` and the `0. MUST-HAVE/[meshes]` to `shared.pak`. Optional RLM variants are not automatically applied. Check that the downloaded RLM release matches your game.
- **Spotter, Next Gen Gameplay/Tire Inflation, Cargo Straps and Headlight Shadows**: dedicated modes copy their recognized binary layouts to `Sources\Bin`. If Spotter is selected alongside a Next Gen loader in the same queue, or with a matching loader previously installed by this app, its bundled `dinput8.dll` is omitted as described by Spotter's author. An unknown existing `version.dll` is not identified automatically; follow the author instructions for that setup. Spotter's current releases are Steam-only. SnowMap must be disabled separately.
- **Next Gen Graphics / skies**, **LOD Optimizer**, **Gameplay Tweaks**, and **Persistent mud and realism** use their authors' own patchers/installers; open the catalog page and follow those instructions. Next Gen Graphics requires its separate sky pack and should be applied last. The app does not run these tools for you.
- **Effects Removal**: choose Bokeh, Indicators, Truck SFX or Horn SFX in the catalog. Each choice accepts the original Effects Removal 1.5 ZIP or the named extracted option folder and selects only that option. It requires the exact 1.5 file layout; other layouts are rejected for review. File names alone do not authenticate the download or prove game-version compatibility. Bokeh and Indicators target `boot.pak`; Truck SFX and Horn SFX target `initial.pak`. Bokeh is already included in Weather Overhaul, and Indicators is already included in Gameplay Tweaks using a different method. Check the [author's current instructions](https://mod.io/g/snowrunner/m/effects-removal) and compatibility before installing.
- **Weather Overhaul and Interface Refinement** can contain several options or target archives. Follow the author instructions, extract the intended option, and add it through the relevant PAK mode. The library links to them but does not choose their options for you.
- **Camera Clipping Fix**: the original [manual release](https://mod.io/g/snowrunner/m/manual-camera-clipping-fix) has an `initial.pak` preset for its `[media]` payload. It is included in McKillem's Tweak Pack and can overlap No Collision Camera or other object XML mods. This preset is not for the separate SnowPatcher edition; check the author page and game version.
- **No Collision Camera**: use the [author's manual WinRAR instructions](https://mod.io/g/snowrunner/m/no-collision-camera), not this app's PAK writer. Choose exactly one of the default-wheel-grip or improved-wheel-grip folders. Close the game and keep your `initial.pak` backup outside the game's PAK folder. It can overlap Camera Clipping Fix and other object XML changes; check the mod and game versions.
- **Flexible frames and soft suspension**: the author documents a manual `[media]` merge into `initial.pak`. The dedicated preset accepts the 2.2 two-part layout and applies both parts. To install only one part, extract it and add that component with the `initial.pak` PAK mode. Truck and suspension XML conflicts with other mods are not merged.
- **Invisible Wall Fixes**: the author documents replacing four `models_*` files inside `shared.pak`'s `[meshes]`. The dedicated preset accepts exactly the 1.0 release's four named files and rejects missing or unexpected files. The release is older; verify game compatibility and keep a backup.
- **De-RC-fication** and **Snowrunner Prop Damage Rebalance**: both authors document manual `[media]` merges into `initial.pak`, so the `initial.pak` PAK mode accepts their current layouts. De-RC-fication conflicts with other truck XML mods and its author advises removing added roof racks in the garage before uninstalling. Prop Damage Rebalance changes plant and prop XML only; its author reports that co-op players need it too.
- **Revisual — Chains and Straps**: the author documents choosing one variant and adding its `[textures]` folder to `boot.pak`. Extract the ZIP and select exactly one variant folder with the `boot.pak` PAK mode; the full ZIP is intentionally rejected as ambiguous.
- **SnowFlyer — Free Camera**, **SnowRunner Tuning Shop**, **JUSTICE and addons for ALmost ALL**, **lexo1000's Mods Collection** and **Revisual — Lights and Beacons+** are guided links, not queue presets. SnowFlyer is a running-game executable; Tuning Shop is a separate editor with its own baseline; JUSTICE replaces the whole `initial.pak` and has DLC-dependent variants; the collections are mod.io subscriptions. Follow each author's current instructions and keep your own backup.
- **Nearby Repair** and **Repair Parts & Spare Wheel Transfer**: these use [SnowPatcher](https://mod.io/g/snowrunner/m/snowpatcher-mod-manager), not the manual install queue. Install or extract a trusted SnowPatcher release, then use **Open SnowPatcher** and select its `SnowPatcher.exe` (not the setup EXE). In SnowPatcher add each mod's author link or downloaded ZIP, enable the mods, check compatibility, and Apply with SnowRunner closed. Both require SnowPatcher 1.4.0 or newer; use Repair Transfer 1.0.5 or newer with Nearby Repair. Restore an older manual Repair Transfer installation before switching, as its author directs. The shortcut does not import, enable, apply, or check versions automatically. SnowPatcher has separate backups and settings, including optional automatic actions. Close other installers and pause automatic actions during manual operations: the apps do not share a lock or coordinate backups, and this app cannot uninstall SnowPatcher changes.
- In-Cabin View, Models Redesign and Serj4ever's QoL Pack have PAK payload presets. SnowRunner Cruise Control is a ReShade add-on: follow its author page.

**Compatibility is not guaranteed.** Many mods change the same files or ship different `version.dll`, `dinput8.dll`, `dxgi.dll` or ReShade settings. Differing files in the same batch, or PAK entries previously installed by this app, are blocked; differing existing binary files are never overwritten. The app cannot detect overlaps already applied by other tools inside a PAK, merge XML, determine loader identity, select arbitrary optional variants, or ensure mods work together in the game. Resolve such conflicts using the authors' guidance; do not rename or overwrite a loader blindly. The catalog is an aid, not an endorsement or compatibility guarantee.

The referenced [EquDevel installer](https://github.com/equdevel/SnowRunner_mod_installer) and [Nexus listing](https://www.nexusmods.com/snowrunner/mods/35) focus on mod.io subscriptions. This app is independent and handles local manual downloads instead; it does not manage mod.io subscriptions or bypass mod hosting or login requirements.

## Backups and restore

Each successful batch stores the prior contents of changed game PAKs in `SnowRunnerManualBackups` under the selected game root, **outside** `preload\paks\client`. New binary files are recorded so they can be removed on restore. Backups represent the state before that batch, which may already contain mods; they are not verified clean originals. Allow several GB of free space for backups and temporary PAKs.

Click **Uninstall last batch** to preview and undo the most recent completed batch made by this tool. A batch may contain multiple mods; individual mods cannot safely be removed from the same batch. Repeat in reverse order for earlier batches. Mods installed separately or by other tools are not managed. Uninstall refuses if an affected file has changed since the batch, including after a game update. New backups also contain hashes to detect corruption; older backups do not, so review them carefully. Backups are never automatically deleted.

If an install, rollback or uninstall is interrupted or a rollback fails, the app blocks further writes when it finds an unfinished, malformed or unreadable backup. Close the game, preserve the backup folder and inspect its `manifest.json`, numbered files and game files manually before deciding how to recover. Do not delete an unresolved record merely to bypass the warning; a store file verification can remove other mods. There is no automatic interrupted-operation recovery.

Only one operation from this tool may run on a game folder at a time. Close other installers, patchers, and store updates during installs and uninstalls: they do not participate in this lock, and file checks cannot eliminate every race with unrelated processes. Keep independent backups.

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
- [x] **Batch pre-flight validation** — report known queue, prior-install, and existing binary-file collisions before writing; recheck when installing. Changes already made inside PAKs by other tools cannot be identified.
- [x] **Additional mod research and support** — Flexible frames and soft suspension, Invisible Wall Fixes, De-RC-fication and Snowrunner Prop Damage Rebalance have validated manual presets; Revisual — Chains and Straps has a variant-folder `boot.pak` preset. SnowFlyer, Tuning Shop, JUSTICE, lexo1000's collection and Revisual's subscription packs are linked with author instructions instead of automated writes. No Collision Camera remains a manual WinRAR link. Camera Clipping Fix, Next Gen Tire Inflation, Next Gen Gameplay, Models Redesign, and Immersive In-Cabin View have presets; LOD Optimizer requires its own tool. Nearby Repair and Repair Parts & Spare Wheel Transfer use the SnowPatcher handoff; they are not manual PAK payloads.

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
python -m unittest -v test_installer test_detection_ui
python -m py_compile app.py installer.py
```

This is an independent project, not affiliated with SnowRunner, mod.io, or any mod author. Each mod remains subject to its author's license and hosting terms.
