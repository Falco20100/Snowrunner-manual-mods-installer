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
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
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


# Paths from the author's Effects Removal 1.5 package; changed releases need review.
EFFECTS_VARIANTS = {
    "effects:bokeh": ("Bokeh Effect Removal", "boot.pak", (
        "[textures]/pct/env_camera_bokeh.pct", "[textures]/pct/sfx_flare_sky__s_d.pct")),
    "effects:indicators": ("Indicators Removal", "boot.pak", (
        "[textures]/pct/gui_block__d_a.pct", "[textures]/pct/gui_zone_dash.pct")),
    "effects:truck": ("Truck SFX Removal", "initial.pak", (
        "[media]/classes/particles/engine_fire.xml", "[media]/classes/particles/engine_sparks.xml",
        "[media]/classes/particles/intake.xml", "[media]/classes/particles/intake_distortion.xml")),
    "effects:horn": ("Horn SFX Removal", "initial.pak", (
        "[media]/classes/particles/honk_distortion.xml", "[media]/classes/particles/honk_dust.xml")),
}


def _effects_payload(source, names, read, mode):
    option, pak, expected = EFFECTS_VARIANTS[mode]
    roots = set()
    for name in names:
        parts = name.split("/")
        roots.update("/".join(parts[:i + 1]) for i, part in enumerate(parts) if part.lower() == option.lower())
    path = Path(source).expanduser()
    if path.is_dir() and path.name.lower() == option.lower():
        roots.add("")
    if len(roots) != 1:
        raise InstallError(f"Effects Removal option {option} is missing or ambiguous. Select the original ZIP or the named option folder.")
    root = next(iter(roots))
    actual = {}
    for name in names:
        rel = _under(name, root)
        if rel is not None:
            key = rel.lower()
            if key in actual:
                raise InstallError(f"Duplicate Effects Removal payload path: {rel}")
            actual[key] = name
    expected_paths = {f"to {pak}/{target}" for target in expected}
    if set(actual) != expected_paths:
        raise InstallError(f"Effects Removal {option} does not match the supported 1.5 layout; missing or unexpected files were found. Check the author's instructions for this release.")
    return {f"pak:{pak}": {target: read(actual[f"to {pak}/{target}"]) for target in expected}}


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
        elif job.mode in EFFECTS_VARIANTS:
            groups = _effects_payload(job.source, names, read, job.mode)
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


def _preflight(root, jobs):
    """Inspect the queue once and return the validated plan and any issues."""
    base, bin_dir, pak_dir = game_paths(root)
    _check_unfinished(base)
    if not jobs:
        raise InstallError("The queue is empty.")
    issues = []
    variants = {}
    for job in jobs:
        try:
            job_plan = plan_jobs([job])
        except (InstallError, OSError, zipfile.BadZipFile) as exc:
            issues.append(f"{job.label or job.source}: {exc}")
            continue
        for key, (name, data) in job_plan.items():
            variants.setdefault(key, []).append((job, name, data))

    previous_batches = [(previous, json.loads((previous / "manifest.json").read_text(encoding="utf-8")))
                        for previous in backups(str(base))]
    spotter = any(job.mode == "spotter" for job in jobs)
    nextgen_queued = any(job.mode in {"gameplay", "tire"}
                         for job, _, _ in variants.get(("bin", "version.dll"), []))
    version_path = bin_dir / "version.dll"
    known_loader = False
    if spotter and version_path.is_file():
        current_hash = digest(version_path)
        for _, manifest in previous_batches:
            modes = set(manifest.get("modes", []))
            # Older manifests have no per-loader provenance. Trust them only when
            # every job in the batch was a Next Gen preset.
            loader_hash = manifest.get("nextgen_loader_hash")
            if "nextgen_loader_hash" not in manifest and modes and modes <= {"gameplay", "tire"}:
                loader_hash = current_hash
            if loader_hash == current_hash and any(
                claim["group"] == "bin" and claim["path"].lower() == "version.dll" and claim["hash"] == current_hash
                for claim in manifest.get("claims", [])
            ):
                known_loader = True
                break
        if not (known_loader or nextgen_queued):
            issues.append("Spotter found an unknown existing version.dll. Follow the author instructions for your existing loader.")
    if spotter and (bin_dir / "SnowMap.asi").exists():
        issues.append("SnowMap.asi is installed. Disable it before installing Spotter to avoid two minimaps.")

    loader_key = ("bin", "dinput8.dll")
    loader_items = variants.get(loader_key, [])
    omit_loader = bool(loader_items and (known_loader or nextgen_queued)
                       and all(job.mode == "spotter" for job, _, _ in loader_items))
    if omit_loader:
        del variants[loader_key]

    for (group, _), items in variants.items():
        if len({data for _, _, data in items}) > 1:
            owners = ", ".join(job.label or job.source for job, _, _ in items)
            issues.append(f"Conflict: {group}/{items[0][1]} differs between {owners}.")

    for previous, manifest in previous_batches:
        for claim in manifest.get("claims", []):
            key = (claim["group"], claim["path"].lower())
            if key in variants and any(hashlib.sha256(data).hexdigest() != claim["hash"] for _, _, data in variants[key]):
                issues.append(f"Previously installed mod also changes {claim['group']}/{claim['path']} (backup {previous.name}).")

    bin_keys = {key for group, key in variants if group == "bin"}
    for key in sorted(bin_keys):
        parts = key.split("/")
        for end in range(1, len(parts)):
            parent = "/".join(parts[:end])
            if parent in bin_keys:
                issues.append(f"Conflict: bin/{parent} is both a file and a parent folder of bin/{key}.")

    checked_archives = set()
    for (group, key), items in variants.items():
        if group == "bin":
            for _, name, data in items:
                try:
                    target = _safe_target(bin_dir, name)
                    blocked_parent = next((parent for parent in target.parents if parent != bin_dir and parent.exists() and not parent.is_dir()), None)
                    if blocked_parent:
                        issue = f"Not a directory: {blocked_parent}"
                    elif target.exists() and not target.is_file():
                        issue = f"Not a file: {target}"
                    elif target.exists() and target.read_bytes() != data:
                        issue = f"Existing file differs: {target}"
                    else:
                        continue
                    if issue not in issues:
                        issues.append(issue)
                except (InstallError, OSError) as exc:
                    issues.append(str(exc))
        else:
            target = pak_dir / group[4:]
            if target in checked_archives:
                continue
            checked_archives.add(target)
            if not target.is_file():
                issues.append(f"Game archive not found: {target}")
                continue
            try:
                with zipfile.ZipFile(target) as archive:
                    seen = set()
                    for info in archive.infolist():
                        archive_key = normalize(info.orig_filename).lower()
                        if archive_key in seen:
                            issues.append(f"Duplicate path in {target.name}: {info.orig_filename}")
                        seen.add(archive_key)
            except (InstallError, OSError, zipfile.BadZipFile) as exc:
                issues.append(f"Cannot inspect {target.name}: {exc}")
    plan = {key: (items[-1][1], items[-1][2]) for key, items in variants.items()}
    return base, bin_dir, pak_dir, plan, issues, omit_loader, nextgen_queued


def preflight(root, jobs):
    """Inspect the whole queue and existing targets without modifying game files."""
    _, _, _, plan, issues, _, _ = _preflight(root, jobs)
    return len(plan), issues


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
            external_attr = info.external_attr
            if key in patches:
                data = patches[key][1]
                out.writestr(info, data)
                seen.add(key)
            else:
                # Copy streams to avoid loading game archives into memory.
                with original.open(info) as src, out.open(info, "w", force_zip64=info.file_size >= 0x7fffffff) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            # zipfile substitutes default permissions for zero; preserve the original
            # in the central directory, which is written when the archive closes.
            info.external_attr = external_attr
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


def _write_manifest(path, manifest):
    fd, name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


_operation_locks = {}
_operation_locks_guard = threading.Lock()


@contextmanager
def _operation_lock(base):
    """Serialize cooperating threads and processes; OS locks release on exit."""
    key = os.path.normcase(str(base))
    with _operation_locks_guard:
        lock = _operation_locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise InstallError("Cannot start: another installer operation is in progress.")
    try:
        with _process_lock(base):
            yield
    finally:
        lock.release()


@contextmanager
def _process_lock(base):
    if sys.platform == "win32":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        key = hashlib.sha256(os.path.normcase(str(base)).encode("utf-8")).hexdigest()
        handle = kernel.CreateMutexW(None, False, "Global\\SnowRunnerManualInstaller-" + key)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result == 0x102:  # WAIT_TIMEOUT
                raise InstallError("Cannot start: another installer operation is in progress.")
            if result not in (0, 0x80):  # WAIT_OBJECT_0 or WAIT_ABANDONED
                raise ctypes.WinError(ctypes.get_last_error())
            acquired = True
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        import fcntl
        fd = os.open(base, os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InstallError("Cannot start: another installer operation is in progress.") from exc
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _serialized(operation):
    @wraps(operation)
    def wrapper(root, *args, **kwargs):
        base, _, _ = game_paths(root)
        with _operation_lock(base):
            return operation(root, *args, **kwargs)
    return wrapper


def _validate_installed_manifest(base, manifest):
    changes = manifest.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("invalid changes")
    seen = set()
    for item in changes:
        if not isinstance(item, dict) or not isinstance(item.get("target"), str):
            raise ValueError("invalid change")
        relative = normalize(item["target"])
        target = _safe_target(base, relative)
        bin_dir = base / "Sources" / "Bin"
        pak_dir = base / "preload" / "paks" / "client"
        is_binary = target.is_relative_to(bin_dir) and target != bin_dir and target != bin_dir / "SnowRunner.exe"
        is_pak = target.parent == pak_dir and target.name in {"initial.pak", "shared.pak", "boot.pak", "main_menu_textures.pak"}
        if not (is_binary or is_pak):
            raise ValueError("invalid target")
        key = relative.lower()
        if key in seen:
            raise ValueError("duplicate target")
        seen.add(key)
        saved = item.get("backup")
        if "backup" not in item or (saved is not None and (not isinstance(saved, str) or not saved.isascii() or not saved.isdecimal())):
            raise ValueError("invalid backup filename")
        if (is_pak and saved is None) or (is_binary and saved is not None):
            raise ValueError("backup does not match target type")
        for field in ("installed_hash", "backup_hash"):
            value = item.get(field)
            if field == "backup_hash" and value is None:
                continue  # Legacy backups did not record a hash.
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"invalid {field}")
    created = manifest.get("created_ns")
    if created is not None and (not isinstance(created, int) or isinstance(created, bool)):
        raise ValueError("invalid creation time")
    for field in ("modes", "labels"):
        if field in manifest and (not isinstance(manifest[field], list) or not all(isinstance(v, str) for v in manifest[field])):
            raise ValueError(f"invalid {field}")
    claims = manifest.get("claims", [])
    if not isinstance(claims, list) or any(not isinstance(c, dict) or not all(isinstance(c.get(k), str) for k in ("group", "path", "hash")) for c in claims):
        raise ValueError("invalid claims")


def _check_unfinished(base):
    folder = _backup_root(base)
    if not folder.exists():
        return
    for path in folder.iterdir():
        if not path.is_dir():
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("game") != str(base):
                raise ValueError("invalid game or manifest")
            status = manifest.get("status")
            if status not in {"installed", "restored", "aborted"}:
                raise ValueError(f"unfinished status {status!r}")
            if status == "installed":
                _validate_installed_manifest(base, manifest)
        except (OSError, ValueError, UnicodeError, InstallError, TypeError) as exc:
            raise InstallError(f"Unresolved backup at {path}: {exc}. Close SnowRunner and review it manually before installing or uninstalling; no files were changed.") from exc


@_serialized
def install(root, jobs, log=lambda _: None):
    assert_game_closed()
    base, bin_dir, pak_dir, plan, issues, omit_loader, nextgen_queued = _preflight(root, jobs)
    if issues:
        raise InstallError("Pre-flight stopped installation:\n" + "\n".join(issues))
    groups = {}
    for (group, _), item in plan.items():
        groups.setdefault(group, []).append(item)
    if omit_loader:
        log("Spotter: keeping the Next Gen ASI loader instead of installing Spotter's dinput8.dll.")
    staged = []
    snapshot_hashes = {}
    backup = _backup_root(base) / uuid.uuid4().hex
    manifest = {"game": str(base), "status": "preparing", "created_ns": time.time_ns(), "changes": [], "modes": [job.mode for job in jobs], "labels": [job.label or Path(job.source).name or job.mode for job in jobs], "claims": [{"group": group, "path": rel, "hash": hashlib.sha256(data).hexdigest()} for group, entries in groups.items() for rel, data in entries], "nextgen_loader_hash": hashlib.sha256(plan[("bin", "version.dll")][1]).hexdigest() if nextgen_queued else None}
    backup.mkdir(parents=True)
    _write_manifest(backup / "manifest.json", manifest)
    committed = []
    try:
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
                saved = backup / str(len(staged) - 1)
                original_hash = digest(target)
                shutil.copy2(target, saved)
                if digest(saved) != original_hash or not target.is_file() or digest(target) != original_hash:
                    raise InstallError(f"Target changed during backup: {target}")
                snapshot_hashes[target] = original_hash
                _rewrite_pak(saved, Path(tmp), entries)
        if not staged:
            raise InstallError("Nothing to install; all selected binary files are already present.")
        for i, (target, tmp, existed) in enumerate(staged):
            saved = str(i) if existed else None
            original_hash = None
            if existed:
                original_hash = snapshot_hashes[target]
                if digest(backup / saved) != original_hash:
                    raise InstallError(f"Backup changed during installation: {backup / saved}")
                if not target.is_file() or digest(target) != original_hash:
                    raise InstallError(f"Target changed during installation: {target}")
            manifest["changes"].append({"target": str(target.relative_to(base)), "backup": saved, "backup_hash": original_hash, "installed_hash": digest(tmp)})
        manifest["status"] = "ready"
        _write_manifest(backup / "manifest.json", manifest)
        for (target, _, existed), item in zip(staged, manifest["changes"]):
            if existed:
                if not target.is_file() or digest(target) != item["backup_hash"]:
                    raise InstallError(f"Target changed during installation: {target}")
            elif target.exists():
                raise InstallError(f"Target appeared during installation: {target}")
        for (target, tmp, existed), item in zip(staged, manifest["changes"]):
            if not tmp.is_file() or digest(tmp) != item["installed_hash"]:
                raise InstallError(f"Staged file changed during installation: {tmp}")
            if existed:
                if not target.is_file() or digest(target) != item["backup_hash"]:
                    raise InstallError(f"Target changed during installation: {target}")
                os.replace(tmp, target)
            else:
                # A hard link creates a new target atomically without replacing an
                # existing file, even if another process creates it after our check.
                try:
                    os.link(tmp, target)
                except FileExistsError as exc:
                    raise InstallError(f"Target appeared during installation: {target}") from exc
            committed.append(target)
        manifest["status"] = "installed"
        _write_manifest(backup / "manifest.json", manifest)
    except Exception as original_error:
        if committed:
            manifest["status"] = "rolling_back"
            try:
                _write_manifest(backup / "manifest.json", manifest)
            except Exception as exc:
                raise InstallError(f"Installation failed: {original_error}. Could not record rollback: {exc}. No rollback was attempted; preserve and review {backup} manually.") from original_error
        rollback_errors = []
        for index in reversed(range(len(committed))):
            target = committed[index]
            item = manifest["changes"][index]
            tmp_rollback = None
            try:
                if not target.is_file() or digest(target) != item["installed_hash"]:
                    raise InstallError(f"target changed after installation: {target}")
                if item["backup"] is None:
                    target.unlink()
                else:
                    source = backup / item["backup"]
                    if not source.is_file() or digest(source) != item["backup_hash"]:
                        raise InstallError(f"backup changed: {source}")
                    fd, name = tempfile.mkstemp(prefix=".srmi-rollback-", dir=target.parent)
                    os.close(fd)
                    tmp_rollback = Path(name)
                    shutil.copy2(source, tmp_rollback)
                    if digest(tmp_rollback) != item["backup_hash"]:
                        raise InstallError(f"rollback copy failed verification: {source}")
                    if not target.is_file() or digest(target) != item["installed_hash"]:
                        raise InstallError(f"target changed during rollback: {target}")
                    os.replace(tmp_rollback, target)
            except Exception as exc:
                rollback_errors.append(str(exc))
            finally:
                if tmp_rollback is not None:
                    tmp_rollback.unlink(missing_ok=True)
        if rollback_errors:
            raise InstallError(f"Installation failed: {original_error}. Rollback failed: {'; '.join(rollback_errors)}. Preserve and review {backup} manually before further operations.") from original_error
        manifest["status"] = "aborted"
        _write_manifest(backup / "manifest.json", manifest)
        raise
    finally:
        for _, tmp, _ in staged:
            tmp.unlink(missing_ok=True)
    log(f"Installed {len(staged)} target(s). Backup: {backup}")
    return backup


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


def latest_batch(root):
    """Return the latest completed batch and its display names without changing files."""
    base, _, _ = game_paths(root)
    _check_unfinished(base)
    found = backups(str(base))
    if not found:
        raise InstallError("No completed backup found for this game folder.")
    backup = found[0]
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    labels = manifest.get("labels") or manifest.get("modes") or ["Unknown mods (older backup)"]
    return backup, labels


@_serialized
def restore_latest(root, log=lambda _: None, expected_backup=None):
    assert_game_closed()
    base, _, _ = game_paths(root)
    backup, _ = latest_batch(str(base))
    if expected_backup is not None and backup != Path(expected_backup):
        raise InstallError("The latest batch changed after confirmation. Review it again before uninstalling.")
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    staged = []
    try:
        # Validate all targets and prepare all replacement files before writing.
        for item in manifest["changes"]:
            target = _safe_target(base, item["target"])
            if not target.is_file() or digest(target) != item["installed_hash"]:
                raise InstallError(f"Cannot restore: {target} has changed since this install. Restore manually from {backup} after reviewing it.")
            saved = item["backup"]
            if saved is None:
                staged.append((target, None, None))
                continue
            if not isinstance(saved, str) or not saved.isdecimal():
                raise InstallError(f"Invalid backup filename in {backup}.")
            source = _safe_target(backup, saved)
            if not source.is_file():
                raise InstallError(f"Missing backup file in {backup}.")
            source_hash = digest(source)
            if item.get("backup_hash") is not None and source_hash != item["backup_hash"]:
                raise InstallError(f"Backup file has changed: {source}. Restore manually after reviewing it.")
            fd, tmp = tempfile.mkstemp(prefix=".srmi-restore-", dir=target.parent)
            os.close(fd)
            staged.append((target, Path(tmp), source_hash))
            shutil.copy2(source, tmp)
            if digest(Path(tmp)) != source_hash:
                raise InstallError(f"Backup copy verification failed: {source}.")
        for item in manifest["changes"]:
            target = _safe_target(base, item["target"])
            if not target.is_file() or digest(target) != item["installed_hash"]:
                raise InstallError(f"Cannot restore: {target} changed while preparing the uninstall. Review it before retrying.")
        manifest["status"] = "restoring"
        _write_manifest(manifest_path, manifest)
        for (target, tmp, source_hash), item in reversed(list(zip(staged, manifest["changes"]))):
            if not target.is_file() or digest(target) != item["installed_hash"]:
                raise InstallError(f"Cannot restore: {target} changed during uninstall. Preserve {backup} and review manually.")
            if tmp is None:
                target.unlink()
            else:
                if not tmp.is_file() or digest(tmp) != source_hash:
                    raise InstallError(f"Backup copy changed during uninstall: {tmp}. Preserve {backup} and review manually.")
                if not target.is_file() or digest(target) != item["installed_hash"]:
                    raise InstallError(f"Cannot restore: {target} changed during uninstall. Preserve {backup} and review manually.")
                os.replace(tmp, target)
        manifest["status"] = "restored"
        _write_manifest(manifest_path, manifest)
    finally:
        for _, tmp, _ in staged:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    log(f"Restored the previous state from {backup}.")
    return backup
