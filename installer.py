"""Manual SnowRunner installation engine. No mod files are distributed here."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class InstallError(Exception):
    pass


@dataclass(frozen=True)
class Job:
    source: str
    mode: str
    label: str = ""


def normalize(name: str) -> str:
    name = name.replace("\\", "/")
    parts = name.split("/")
    if name.startswith("/") or any(p in ("..", ".") or p.endswith((".", " ")) for p in parts) or ":" in name or "\x00" in name:
        raise InstallError(f"Unsafe path in package: {name!r}")
    return "/".join(p for p in parts if p)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def game_paths(root: str):
    root = Path(root).expanduser().resolve()
    # Accept the game root, en_us root, or the directory containing the executable.
    candidates = [root, root / "en_us"]
    if root.name.lower() == "bin" and root.parent.name.lower() == "sources":
        candidates.insert(0, root.parent.parent)
    for base in candidates:
        bin_dir = base / "Sources" / "Bin"
        pak_dir = base / "preload" / "paks" / "client"
        if (bin_dir / "SnowRunner.exe").is_file() and (pak_dir / "initial.pak").is_file():
            return base, bin_dir, pak_dir
    raise InstallError("Choose the SnowRunner game folder (or Sources/Bin). SnowRunner.exe and preload/paks/client/initial.pak must both exist.")


def _steam_roots():
    roots = []
    if sys.platform != "win32":
        return roots
    import winreg
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as opened:
                path, _ = winreg.QueryValueEx(opened, value)
            if isinstance(path, str) and path:
                roots.append(Path(path))
        except OSError:
            continue
    program_files = os.environ.get("ProgramFiles(x86)")
    if program_files:
        roots.append(Path(program_files) / "Steam")
    return roots


def _vdf_pairs(text):
    # KeyValues paths escape backslashes and quotes. Leave other characters intact.
    pattern = r'"((?:\\.|[^"\\])*)"\s+"((?:\\.|[^"\\])*)"'
    def unescape(value):
        return re.sub(r'\\([\\"])', r'\1', value)
    return [(unescape(key), unescape(value)) for key, value in re.findall(pattern, text)]


def _read_vdf(path):
    try:
        return _vdf_pairs(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        return []


def _steam_candidates():
    libraries = []
    for steam in _steam_roots():
        libraries.append(steam)
        for vdf in (steam / "steamapps" / "libraryfolders.vdf", steam / "config" / "libraryfolders.vdf"):
            for key, value in _read_vdf(vdf):
                if key.lower() == "path" or key.isdigit():
                    # Older Steam versions use numbered string entries for paths.
                    if (Path(value) / "steamapps").is_dir():
                        libraries.append(Path(value))
    for library in libraries:
        pairs = _read_vdf(library / "steamapps" / "appmanifest_1465360.acf")
        values = {key.lower(): value for key, value in pairs}
        name = values.get("installdir", "")
        # An install directory must be a single folder within this Steam library.
        if values.get("appid") == "1465360" and name and name not in {".", ".."} and not any(c in name for c in '/\\:'):
            yield library / "steamapps" / "common" / name


def _epic_candidates():
    program_data = os.environ.get("PROGRAMDATA")
    if not program_data:
        return
    manifests = Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    try:
        paths = list(manifests.glob("*.item"))
    except OSError:
        return
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("bIsIncompleteInstall") is True:
            continue
        location = data.get("InstallLocation")
        if isinstance(location, str) and location:
            # Validate the game files below rather than relying on localized display names.
            yield Path(location)


def _xbox_roots():
    # Restrict probing to mounted local drives, not disconnected network drives.
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for index in range(26):
        if mask & (1 << index):
            root = f"{chr(ord('A') + index)}:\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) in (2, 3):
                yield Path(root) / "XboxGames"


def _game_pass_candidates():
    if sys.platform != "win32":
        return
    # Query the current user's package registrations without requesting elevation.
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "$p = @(Get-AppxPackage -Name '*SnowRunner*' -ErrorAction Stop | "
              "Where-Object { $_.InstallLocation } | ForEach-Object { $_.InstallLocation }); "
              "ConvertTo-Json -InputObject $p -Compress")
    try:
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, encoding="utf-8-sig", errors="replace", check=True,
                                timeout=10, creationflags=0x08000000)
        locations = json.loads(result.stdout)
        if isinstance(locations, list):
            for location in locations:
                if isinstance(location, str) and location:
                    yield Path(location)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # Flat-file Xbox installations may live outside protected package folders.
    for xbox in _xbox_roots():
        for name in ("SnowRunner", "SnowRunner - Windows"):
            yield xbox / name / "Content"


def detect_game_installations():
    """Find valid Windows installs from store metadata; never modify game files."""
    if sys.platform != "win32":
        return []
    found = []
    seen = set()
    for store, candidates in (("Steam", _steam_candidates()), ("Epic Games", _epic_candidates()),
                              ("PC Game Pass", _game_pass_candidates())):
        for candidate in candidates:
            # Store packages can point to the container instead of its Content folder.
            for root in (candidate, candidate / "Content"):
                try:
                    base, _, _ = game_paths(str(root))
                except (InstallError, OSError, RuntimeError, ValueError):
                    continue
                key = os.path.normcase(str(base))
                if key not in seen:
                    seen.add(key)
                    found.append((store, str(base)))
                break
    return found


def assert_game_closed():
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SnowRunner.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True, creationflags=0x08000000)
        if "snowrunner.exe" in result.stdout.lower():
            raise InstallError("Close SnowRunner before installing or restoring.")


def _scan(source: str):
    """Return safe relative paths and a reader, for a folder or zip."""
    path = Path(source).expanduser().resolve()
    files = {}
    if path.is_dir():
        for p in path.rglob("*"):
            if p.is_symlink():
                raise InstallError(f"Symbolic links are not supported: {p}")
            if p.is_file():
                key = normalize(p.relative_to(path).as_posix())
                files[key] = p
        def read(key):
            return files[key].read_bytes()
    elif path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            seen = set()
            for entry in z.infolist():
                if entry.is_dir():
                    continue
                if entry.flag_bits & 1:
                    raise InstallError("Encrypted ZIP files are not supported.")
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise InstallError("ZIP symbolic links are not supported.")
                key = normalize(entry.filename)
                if key.lower() in seen:
                    raise InstallError(f"Duplicate ZIP path: {key}")
                seen.add(key.lower())
                files[key] = entry.filename
        def read(key):
            with zipfile.ZipFile(path) as z:
                return z.read(files[key])
    else:
        raise InstallError(f"Not a folder or ZIP archive: {source}. Extract RAR/7z files first.")
    if not files:
        raise InstallError(f"Package is empty: {source}")
    return list(files), read


def _choose_root(names, marker):
    marker = marker.lower()
    roots = set()
    for name in names:
        parts = name.split("/")
        for i, part in enumerate(parts):
            if part.lower() == marker:
                roots.add("/".join(parts[:i]))
    if not roots:
        raise InstallError(f"Could not find {marker} in the package. Select the extracted payload folder instead.")
    depth = min(r.count("/") + bool(r) for r in roots)
    roots = sorted(r for r in roots if r.count("/") + bool(r) == depth)
    if len(roots) != 1:
        raise InstallError(f"Several possible {marker} payloads. Select the intended extracted folder instead.")
    return roots[0]


def _under(name, root):
    return name[len(root) + 1:] if root and name.startswith(root + "/") else (name if not root else None)


def _pak_payload(names, read, marker, allowed, root=None):
    root = _choose_root(names, marker) if root is None else root
    result = {}
    for name in names:
        rel = _under(name, root)
        if rel is not None and rel.split("/")[0].lower() in allowed:
            result[rel] = read(name)
    if not result:
        raise InstallError(f"No installable files found under {root or 'package root'}.")
    return result


def _bin_payload(names, read, mode):
    # Choose a single payload root using the mod's actual entry point, not a documentation or installer folder.
    markers = {"spotter": "Spotter.asi", "gameplay": "Gauges.asi", "tire": "TirePressure.asi", "cargo": "version.dll", "headlights": "d3d11.dll"}
    marker = markers.get(mode)
    if marker:
        root = _choose_root(names, marker)
    else:
        candidates = [n for n in names if n.lower().endswith((".asi", ".dll"))]
        if not candidates:
            raise InstallError("No DLL or ASI found. This package may need its author's installer.")
        roots = {n.rsplit("/", 1)[0] if "/" in n else "" for n in candidates}
        min_depth = min(r.count("/") + bool(r) for r in roots)
        shallow = [r for r in roots if r.count("/") + bool(r) == min_depth]
        if len(shallow) != 1:
            raise InstallError("Several binary payloads found. Select the intended extracted folder instead.")
        root = shallow[0]
    result = {}
    for name in names:
        rel = _under(name, root)
        if rel is None:
            continue
        pieces = rel.split("/")
        # Do not run or copy arbitrary installers, source code, readmes, or diagnostic scripts.
        if len(pieces) == 1 and (not rel.lower().endswith((".dll", ".asi", ".ini", ".json", ".addon", ".txt")) or rel.lower().startswith(("readme", "install", "license", "changes", "changelog"))):
            continue
        if any(p.lower().endswith((".exe", ".bat", ".cmd", ".py", ".pyw", ".ps1")) for p in pieces):
            continue
        result[rel] = read(name)
    if not result:
        raise InstallError("No binary payload found.")
    return result


def plan_jobs(jobs):
    """Map each file to its target. Colliding different bytes are never silently merged."""
    plan = {}
    owners = {}
    for job in jobs:
        names, read = _scan(job.source)
        groups = {}
        if job.mode == "rlm":
            root = _choose_root(names, "initial.cache_block")
            initial = _pak_payload(names, read, "initial.cache_block", {"[media]", "[strings]", "initial.cache_block"}, root)
            required = {p.split("/")[0].lower() for p in initial}
            if not {"[media]", "[strings]", "initial.cache_block"} <= required:
                raise InstallError("Real Life Mod base payload is incomplete; expected [media], [strings], and initial.cache_block together.")
            mesh_roots = set()
            for name in names:
                parts = name.split("/")
                for i in range(len(parts) - 1):
                    if parts[i].lower().startswith("0. must-have") and parts[i + 1].lower() == "[meshes]":
                        mesh_roots.add("/".join(parts[:i + 1]))
            if len(mesh_roots) != 1:
                raise InstallError("Real Life Mod MUST-HAVE [meshes] payload not found or ambiguous.")
            groups = {"pak:initial.pak": initial, "pak:shared.pak": _pak_payload(names, read, "[meshes]", {"[meshes]"}, next(iter(mesh_roots)))}
        elif job.mode.startswith("pak:"):
            pak = job.mode[4:]
            if pak not in {"initial.pak", "shared.pak", "boot.pak", "main_menu_textures.pak"}:
                raise InstallError("Unsupported target archive.")
            # Users choose a target; choose only the shallowest recognizable payload and never optional sibling folders.
            markers = {"initial.pak": ["[media]", "[strings]", "initial.cache_block"], "shared.pak": ["[meshes]"], "boot.pak": ["[textures]", "[media]"], "main_menu_textures.pak": ["[textures]"]}[pak]
            roots = []
            for marker in markers:
                try:
                    roots.append(_choose_root(names, marker))
                except InstallError:
                    pass
            if not roots:
                raise InstallError("No recognized archive payload. Select the exact extracted option folder.")
            depth = min(r.count("/") + bool(r) for r in roots)
            roots = set(r for r in roots if r.count("/") + bool(r) == depth)
            if len(roots) != 1:
                raise InstallError("Multiple archive payloads found. Select the exact extracted option folder.")
            groups = {job.mode: _pak_payload(names, read, markers[0], set(markers), next(iter(roots)))}
        else:
            groups = {"bin": _bin_payload(names, read, job.mode)}
        for group, entries in groups.items():
            for rel, data in entries.items():
                key = (group, rel.lower())
                if key in plan and plan[key][1] != data:
                    raise InstallError(f"Conflict: {job.label or job.source} and {owners[key]} both change {group}/{rel}. They cannot be automatically merged.")
                plan[key] = (rel, data)
                owners[key] = job.label or job.source
    if not plan:
        raise InstallError("The queue is empty.")
    return plan


def _safe_target(base: Path, rel: str):
    target = base.joinpath(*PurePosixPath(normalize(rel)).parts)
    if not target.resolve().is_relative_to(base.resolve()):
        raise InstallError(f"Target escapes game directory: {target}")
    return target


def _rewrite_pak(source: Path, dest: Path, entries):
    """Preserve existing entry names and metadata; new game entries use backslashes."""
    patches = {name.lower(): (name, data) for name, data in entries}
    seen = set()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(dest, "w", allowZip64=True) as out:
        out.comment = original.comment
        original_keys = set()
        for info in original.infolist():
            # ZipInfo.filename is normalized on Windows; retain the archive's exact name.
            info.filename = info.orig_filename
            key = normalize(info.filename).lower()
            if key in original_keys:
                raise InstallError(f"Duplicate path in {source.name}: {info.filename}")
            original_keys.add(key)
            if key in patches:
                data = patches[key][1]
                out.writestr(info, data)
                seen.add(key)
            else:
                # Copy streams to avoid loading game archives into memory.
                with original.open(info) as src, out.open(info, "w", force_zip64=info.file_size >= 0x7fffffff) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        for key, (name, data) in patches.items():
            if key not in seen:
                archive_name = name.replace("/", "\\")
                info = zipfile.ZipInfo(archive_name)
                # ZipInfo normalizes native Windows separators; PAK entries need backslashes.
                info.filename = archive_name
                out.writestr(info, data, compress_type=zipfile.ZIP_STORED)
    # Check all modified bytes and all unchanged entries via CRC, not just whether the ZIP opens.
    with zipfile.ZipFile(dest) as out:
        bad = out.testzip()
        if bad:
            raise InstallError(f"Validation failed for {source.name}: {bad}")
        lookup = {normalize(i.filename).lower(): i for i in out.infolist()}
        for key, (_, data) in patches.items():
            if key not in lookup or out.read(lookup[key]) != data:
                raise InstallError(f"Patched file validation failed: {key}")


def _backup_root(base):
    # Outside preload/paks/client: the game loads every .pak in that directory.
    return base / "SnowRunnerManualBackups"


def install(root, jobs, log=lambda _: None):
    assert_game_closed()
    base, bin_dir, pak_dir = game_paths(root)
    plan = plan_jobs(jobs)
    # Also guard files installed in earlier batches, not only collisions in this queue.
    previous_batches = backups(root)
    for previous in previous_batches:
        previous_manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
        for claim in previous_manifest.get("claims", []):
            key = (claim["group"], claim["path"].lower())
            if key in plan and hashlib.sha256(plan[key][1]).hexdigest() != claim["hash"]:
                raise InstallError(f"Previously installed mod also changes {claim['group']}/{claim['path']}. Restore its batch or resolve the conflict manually; files are not automatically merged.")
    groups = {}
    for (group, _), item in plan.items():
        groups.setdefault(group, []).append(item)
    # Spotter can use the known Next Gen ASI loader; do not replace a different loader.
    spotter = any(j.mode == "spotter" for j in jobs)
    nextgen_queued = any(j.mode in {"gameplay", "tire"} for j in jobs) and ("bin", "version.dll") in plan
    known_loader = False
    version_path = bin_dir / "version.dll"
    if version_path.is_file():
        current_hash = digest(version_path)
        for previous in previous_batches:
            old = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
            if any(mode in {"gameplay", "tire"} for mode in old.get("modes", [])) and any(c["group"] == "bin" and c["path"].lower() == "version.dll" and c["hash"] == current_hash for c in old.get("claims", [])):
                known_loader = True
                break
    nextgen = nextgen_queued or known_loader
    if spotter and version_path.is_file() and not nextgen:
        raise InstallError("Spotter found an unknown existing version.dll. Its loader cannot be identified safely; follow Spotter's author instructions for your existing loader.")
    if spotter and nextgen and "bin" in groups:
        # Only omit Spotter's loader if it is exclusively supplied by Spotter.
        other_loader = any(j.mode != "spotter" and any(k == ("bin", "dinput8.dll") for k in plan_jobs([j])) for j in jobs)
        if not other_loader:
            groups["bin"] = [(p, d) for p, d in groups["bin"] if p.lower() != "dinput8.dll"]
            log("Spotter: keeping the Next Gen ASI loader instead of installing Spotter's dinput8.dll.")
    if spotter and (bin_dir / "SnowMap.asi").exists():
        raise InstallError("SnowMap.asi is installed. Disable it before installing Spotter to avoid two minimaps.")
    staged = []
    backup = _backup_root(base) / uuid.uuid4().hex
    manifest = {"game": str(base), "status": "preparing", "created_ns": time.time_ns(), "changes": [], "modes": [job.mode for job in jobs], "claims": [{"group": group, "path": rel, "hash": hashlib.sha256(data).hexdigest()} for group, entries in groups.items() for rel, data in entries]}
    try:
        backup.mkdir(parents=True)
        for group, entries in groups.items():
            if group == "bin":
                for rel, data in entries:
                    target = _safe_target(bin_dir, rel)
                    if target.exists():
                        if not target.is_file():
                            raise InstallError(f"Not a file: {target}")
                        if target.read_bytes() == data:
                            continue
                        # Preserve any existing loader/config or mod file. Do not guess compatibility.
                        raise InstallError(f"Existing file differs: {target}. Resolve this conflict using the mod authors' instructions; nothing was installed.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(prefix=".srmi-", dir=target.parent)
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    staged.append((target, Path(tmp), False))
            else:
                target = pak_dir / group[4:]
                if not target.is_file():
                    raise InstallError(f"Game archive not found: {target}")
                log(f"Building and validating {target.name}; this may take several minutes...")
                fd, tmp = tempfile.mkstemp(prefix=".srmi-", suffix=".tmp", dir=target.parent)
                os.close(fd)
                staged.append((target, Path(tmp), True))
                _rewrite_pak(target, Path(tmp), entries)
        if not staged:
            raise InstallError("Nothing to install; all selected binary files are already present.")
        for i, (target, tmp, existed) in enumerate(staged):
            saved = str(i) if existed else None
            if existed:
                shutil.copy2(target, backup / saved)
            manifest["changes"].append({"target": str(target.relative_to(base)), "backup": saved, "installed_hash": digest(tmp)})
        manifest["status"] = "ready"
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        committed = []
        try:
            for target, tmp, existed in staged:
                os.replace(tmp, target)
                committed.append(target)
            manifest["status"] = "installed"
            (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except Exception:
            for index in reversed(range(len(committed))):
                target = committed[index]
                item = manifest["changes"][index]
                if item["backup"] is None:
                    target.unlink(missing_ok=True)
                else:
                    shutil.copy2(backup / item["backup"], target)
            raise
        log(f"Installed {len(staged)} target(s). Backup: {backup}")
        return backup
    finally:
        for _, tmp, _ in staged:
            tmp.unlink(missing_ok=True)


def backups(root):
    base, _, _ = game_paths(root)
    folder = _backup_root(base)
    if not folder.exists():
        return []
    result = []
    for path in folder.iterdir():
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("status") == "installed" and manifest.get("game") == str(base):
                result.append((manifest.get("created_ns", path.stat().st_mtime_ns), path))
        except (OSError, ValueError):
            continue
    return [path for _, path in sorted(result, key=lambda item: item[0], reverse=True)]


def restore_latest(root, log=lambda _: None):
    assert_game_closed()
    base, _, _ = game_paths(root)
    found = backups(root)
    if not found:
        raise InstallError("No completed backup found for this game folder.")
    backup = found[0]
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Check every target before changing anything; do not undo changes made by other tools.
    for item in manifest["changes"]:
        target = _safe_target(base, item["target"])
        if not target.is_file() or digest(target) != item["installed_hash"]:
            raise InstallError(f"Cannot restore: {target} has changed since this install. Restore manually from {backup} after reviewing it.")
        if item["backup"] is not None and not (backup / item["backup"]).is_file():
            raise InstallError(f"Missing backup file in {backup}.")
    for item in reversed(manifest["changes"]):
        target = _safe_target(base, item["target"])
        if item["backup"] is None:
            target.unlink()
        else:
            fd, tmp = tempfile.mkstemp(prefix=".srmi-restore-", dir=target.parent)
            os.close(fd)
            try:
                shutil.copy2(backup / item["backup"], tmp)
                os.replace(tmp, target)
            finally:
                Path(tmp).unlink(missing_ok=True)
    manifest["status"] = "restored"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"Restored the previous state from {backup}.")
    return backup
