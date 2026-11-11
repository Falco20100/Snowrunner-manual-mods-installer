# SnowRunner Manual Mod Installer

A Windows desktop app for manually downloaded SnowRunner mods. Select a game folder, add one or more mod ZIPs or extracted payload folders, review the queue, and install recognized binary and PAK layouts. Mod files are **not** included or downloaded automatically; use the catalog to open the authors' pages and download them yourself.

## Run on Windows

The source version needs **Python 3.9 or newer with Tkinter**. Install Python for Windows from [python.org](https://www.python.org/downloads/windows/), then double-click `Launch.bat`. Alternatively run `py -3 app.py`.

To make a standalone, portable Windows executable, double-click `Build-Portable.bat` on Windows with Python and pip installed. It installs PyInstaller 6.11.1 and builds `dist\SnowRunnerManualInstaller.exe`. The executable can then be copied to another Windows PC without installing Python. Build the EXE on Windows; PyInstaller does not cross-compile from Linux. The repository does not contain a prebuilt EXE or an installation wizard.

## Install

1. **Close SnowRunner. Back up your saves separately.** Check each mod's current requirements, game version, dependencies and multiplayer notes on its author page.
2. Select the folder containing `Sources\Bin\SnowRunner.exe` and `preload\paks\client\initial.pak`. The app also accepts the containing `en_us` folder or `Sources\Bin`.
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

## Development checks

```text
python -m unittest -v test_installer
python -m py_compile app.py installer.py
```

This is an independent project, not affiliated with SnowRunner, mod.io, or any mod author. Each mod remains subject to its author's license and hosting terms.
