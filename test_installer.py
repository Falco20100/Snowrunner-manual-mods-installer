import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import installer
from installer import InstallError, Job, backups, install, plan_jobs, preflight, restore_latest


def write_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.filename = name
            z.writestr(info, data)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "SnowRunner"
        self.bin = self.root / "Sources" / "Bin"
        self.paks = self.root / "preload" / "paks" / "client"
        self.bin.mkdir(parents=True)
        self.paks.mkdir(parents=True)
        (self.bin / "SnowRunner.exe").write_bytes(b"game")
        write_zip(self.paks / "initial.pak", {"[media]\\truck.xml": b"original", "[media]\\unchanged.xml": b"keep path", "initial.cache_block": b"cache", "untouched": b"keep"})
        write_zip(self.paks / "shared.pak", {"[meshes]\\mesh": b"original mesh"})

    def tearDown(self):
        self.tmp.cleanup()

    def package(self, name, files):
        path = Path(self.tmp.name) / name
        write_zip(path, files)
        return str(path)

    def test_invisible_wall_fixes_installs_documented_meshes_and_restores(self):
        package = self.package("walls.zip", {**{name: name.encode() for name in installer.INVISIBLE_WALLS}, "readme.txt": b"author notes"})
        plan = plan_jobs([Job(package, "invisiblewalls", "Invisible Wall Fixes")])
        self.assertEqual(plan, {("pak:shared.pak", f"[meshes]/{name}"): (f"[meshes]/{name}", name.encode()) for name in installer.INVISIBLE_WALLS})
        before = (self.paks / "shared.pak").read_bytes()
        install(str(self.root), [Job(package, "invisiblewalls", "Invisible Wall Fixes")])
        with zipfile.ZipFile(self.paks / "shared.pak") as archive:
            entries = {info.orig_filename: archive.read(info) for info in archive.infolist()}
            for name in installer.INVISIBLE_WALLS:
                self.assertEqual(entries[f"[meshes]\\{name}"], name.encode())
            self.assertEqual(entries["[meshes]\\mesh"], b"original mesh")
        restore_latest(str(self.root))
        self.assertEqual((self.paks / "shared.pak").read_bytes(), before)

    def test_invisible_wall_fixes_rejects_a_changed_layout(self):
        files = {name: b"mesh" for name in installer.INVISIBLE_WALLS}
        files["models_unexpected"] = b"unexpected"
        package = self.package("walls.zip", files)
        with self.assertRaisesRegex(InstallError, "supported 1.0 layout"):
            install(str(self.root), [Job(package, "invisiblewalls")])

    def test_flexible_frames_installs_both_documented_parts(self):
        package = self.package("flexible.zip", {
            "Flexible frames and soft suspension/Flexible frames/[media]/classes/trucks/ank_mk38.xml": b"frame",
            "Flexible frames and soft suspension/Soft Suspension/[media]/classes/suspensions/s_ank_mk38.xml": b"suspension",
            "readme.txt": b"author notes",
        })
        plan = plan_jobs([Job(package, "flexible", "Flexible frames")])
        self.assertEqual(plan[("pak:initial.pak", "[media]/classes/trucks/ank_mk38.xml")][1], b"frame")
        before = (self.paks / "initial.pak").read_bytes()
        install(str(self.root), [Job(package, "flexible")])
        with zipfile.ZipFile(self.paks / "initial.pak") as archive:
            entries = {info.orig_filename: archive.read(info) for info in archive.infolist()}
            self.assertEqual(entries["[media]\\classes\\trucks\\ank_mk38.xml"], b"frame")
            self.assertEqual(entries["[media]\\classes\\suspensions\\s_ank_mk38.xml"], b"suspension")
        restore_latest(str(self.root))
        self.assertEqual((self.paks / "initial.pak").read_bytes(), before)

    def test_flexible_frames_rejects_a_partial_or_changed_layout(self):
        partial = self.package("partial.zip", {"Flexible frames/[media]/classes/trucks/ank_mk38.xml": b"frame"})
        extra = self.package("extra.zip", {
            "Flexible frames/[media]/classes/trucks/ank_mk38.xml": b"frame",
            "Soft Suspension/[media]/classes/suspensions/s_ank_mk38.xml": b"suspension",
            "Unlisted option/[media]/classes/trucks/other.xml": b"other",
        })
        for package in (partial, extra):
            with self.subTest(package=package), self.assertRaisesRegex(InstallError, "two-part layout"):
                install(str(self.root), [Job(package, "flexible")])

    def test_revisual_chains_requires_one_variant_and_targets_boot_pak(self):
        variant = self.package("chain.zip", {"V1a/Chain - Normal/[textures]/pct/env_rope_winch__d_a.pct": b"chain"})
        plan = plan_jobs([Job(variant, "pak:boot.pak", "Revisual — Chains and Straps")])
        self.assertEqual(plan[("pak:boot.pak", "[textures]/pct/env_rope_winch__d_a.pct")][1], b"chain")
        several = self.package("variants.zip", {
            "V1a/Chain - Normal/[textures]/pct/env_rope_winch__d_a.pct": b"chain",
            "V1a/Strap - Red/[textures]/pct/env_rope_winch__d_a.pct": b"strap",
        })
        with self.assertRaisesRegex(InstallError, "exact extracted option folder"):
            plan_jobs([Job(several, "pak:boot.pak")])

    def test_snowpatcher_package_is_not_installed_as_manual_payload(self):
        package = self.package("patch.zip", {"snowpatcher.json": b"{}", "payloads/mod.dll": b"not a loader", "[media]/truck.xml": b"not a manual mod"})
        before = (self.paks / "initial.pak").read_bytes()
        for mode in ("bin", "pak:initial.pak"):
            with self.subTest(mode=mode), self.assertRaisesRegex(InstallError, "SnowPatcher package"):
                install(str(self.root), [Job(package, mode)])
        self.assertEqual((self.paks / "initial.pak").read_bytes(), before)
        self.assertFalse((self.bin / "payloads").exists())

    def test_camera_clipping_manual_payload_installs_and_restores(self):
        target = "[media]/classes/models/big_watchtower_01.xml"
        archive_name = target.replace("/", "\\")
        write_zip(self.paks / "initial.pak", {archive_name: b"original", "untouched": b"keep"})
        before = (self.paks / "initial.pak").read_bytes()
        package = self.package("camera.zip", {"Readme.txt": b"instructions", target: b"camera fix"})
        jobs = [Job(package, "pak:initial.pak", "Camera Clipping Fix")]
        self.assertEqual(plan_jobs(jobs), {("pak:initial.pak", target): (target, b"camera fix")})
        conflict = self.package("conflict.zip", {target: b"other"})
        with self.assertRaisesRegex(InstallError, "Conflict"):
            install(str(self.root), jobs + [Job(conflict, "pak:initial.pak")])
        self.assertEqual((self.paks / "initial.pak").read_bytes(), before)
        install(str(self.root), jobs)
        with zipfile.ZipFile(self.paks / "initial.pak") as archive:
            entries = {info.orig_filename: archive.read(info) for info in archive.infolist()}
            self.assertEqual(entries[archive_name], b"camera fix")
            self.assertEqual(archive.read("untouched"), b"keep")
        restore_latest(str(self.root))
        self.assertEqual((self.paks / "initial.pak").read_bytes(), before)

    # The 1.5 paths are kept independent of the implementation as regression fixtures.
    effects_variants = {
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

    def effects_files(self, mode):
        option, pak, targets = self.effects_variants[mode]
        return {f"Effects Removal/{option}/to {pak}/{target}": mode.encode() for target in targets}

    def test_effects_variants_select_only_requested_zip_or_named_folder(self):
        files = {}
        for mode in self.effects_variants:
            files.update(self.effects_files(mode))
        package = self.package("effects.zip", files)
        extracted = Path(self.tmp.name) / "extracted"
        for name, data in files.items():
            target = extracted / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for mode, (option, pak, targets) in self.effects_variants.items():
            for source in (package, str(extracted), str(extracted / "Effects Removal" / option)):
                with self.subTest(mode=mode, source=source):
                    self.assertEqual(plan_jobs([Job(source, mode)]),
                                     {(f"pak:{pak}", target): (target, mode.encode()) for target in targets})

    def test_effects_reject_incomplete_or_mislabeled_variants(self):
        for mode in self.effects_variants:
            files = self.effects_files(mode)
            files.pop(next(iter(files)))
            with self.subTest(mode=mode, case="missing file"):
                with self.assertRaisesRegex(InstallError, "supported 1.5 layout"):
                    plan_jobs([Job(self.package("incomplete.zip", files), mode)])
            files = self.effects_files(mode)
            option, pak, _ = self.effects_variants[mode]
            other_mode = "effects:indicators" if mode == "effects:bokeh" else "effects:bokeh"
            other_target = self.effects_variants[other_mode][2][0]
            files[f"Effects Removal/{option}/to {pak}/{other_target}"] = b"wrong variant"
            with self.subTest(mode=mode, case="mixed variant"):
                with self.assertRaisesRegex(InstallError, "supported 1.5 layout"):
                    plan_jobs([Job(self.package("mixed.zip", files), mode)])
            files.pop(next(iter(files)))
            with self.subTest(mode=mode, case="substituted file"):
                with self.assertRaisesRegex(InstallError, "supported 1.5 layout"):
                    plan_jobs([Job(self.package("substituted.zip", files), mode)])

    def test_effects_paths_are_case_insensitive_and_duplicate_files_are_rejected(self):
        files = self.effects_files("effects:bokeh")
        upper = {name.upper(): data for name, data in files.items()}
        expected = self.effects_variants["effects:bokeh"][2]
        self.assertEqual(set(plan_jobs([Job(self.package("uppercase.zip", upper), "effects:bokeh")])),
                         {("pak:boot.pak", target) for target in expected})
        duplicate = dict(files)
        name = next(iter(files))
        duplicate[name.upper()] = b"duplicate"
        with self.assertRaises(InstallError):
            plan_jobs([Job(self.package("duplicate.zip", duplicate), "effects:bokeh")])

    def test_effects_reject_ambiguous_or_changed_layouts(self):
        good = self.effects_files("effects:bokeh")
        cases = {
            "missing option": self.effects_files("effects:indicators"),
            "ambiguous": {**{f"A/{name}": data for name, data in good.items()},
                          **{f"B/{name}": data for name, data in good.items()}},
            "wrong archive": {name.replace("to boot.pak", "to initial.pak"): data for name, data in good.items()},
            "wrong marker": {name.replace("[textures]", "[media]"): data for name, data in good.items()},
            "extra file": {**good, "Effects Removal/Bokeh Effect Removal/to boot.pak/unknown/file": b"x"},
            "renamed variant": {name.replace("Bokeh Effect Removal", "Indicators Removal"): data for name, data in good.items()},
        }
        for label, files in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(InstallError):
                    plan_jobs([Job(self.package(label + ".zip", files), "effects:bokeh")])
        anonymous = Path(self.tmp.name) / "anonymous"
        for name, data in good.items():
            target = anonymous / name.split("Bokeh Effect Removal/", 1)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        with self.assertRaises(InstallError):
            plan_jobs([Job(str(anonymous), "effects:bokeh")])
        invalid = self.package("invalid-layout.zip", cases["extra file"])
        before = (self.paks / "initial.pak").read_bytes()
        _, issues = preflight(str(self.root), [Job(invalid, "effects:bokeh")])
        self.assertIn("supported 1.5 layout", "\n".join(issues))
        with self.assertRaisesRegex(InstallError, "supported 1.5 layout"):
            install(str(self.root), [Job(invalid, "effects:bokeh")])
        self.assertEqual(before, (self.paks / "initial.pak").read_bytes())
        self.assertFalse((self.root / "SnowRunnerManualBackups").exists())

    def test_effects_install_conflicts_and_restore_on_synthetic_paks(self):
        bokeh = "[textures]/pct/env_camera_bokeh.pct"
        horn = "[media]/classes/particles/honk_distortion.xml"
        write_zip(self.paks / "boot.pak", {bokeh.replace("/", "\\"): b"original", "unchanged": b"keep"})
        package = self.package("effects.zip", {**self.effects_files("effects:bokeh"), **self.effects_files("effects:horn")})
        before = {name: (self.paks / name).read_bytes() for name in ("boot.pak", "initial.pak")}
        jobs = [Job(package, "effects:bokeh"), Job(package, "effects:horn")]
        conflict = self.package("conflict.zip", {bokeh: b"different"})
        with self.assertRaisesRegex(InstallError, "Conflict"):
            install(str(self.root), jobs + [Job(conflict, "pak:boot.pak")])
        self.assertEqual(before, {name: (self.paks / name).read_bytes() for name in before})
        install(str(self.root), jobs)
        with zipfile.ZipFile(self.paks / "boot.pak") as archive:
            entries = {info.orig_filename: archive.read(info) for info in archive.infolist()}
            self.assertEqual(entries[bokeh.replace("/", "\\")], b"effects:bokeh")
            self.assertEqual(archive.read("unchanged"), b"keep")
        with zipfile.ZipFile(self.paks / "initial.pak") as archive:
            entries = {info.orig_filename: archive.read(info) for info in archive.infolist()}
            self.assertEqual(entries[horn.replace("/", "\\")], b"effects:horn")
        restore_latest(str(self.root))
        self.assertEqual(before, {name: (self.paks / name).read_bytes() for name in before})

    def test_rewrite_preserves_zero_and_nonzero_external_attributes(self):
        source = Path(self.tmp.name) / "source.pak"
        dest = Path(self.tmp.name) / "rewritten.pak"
        with zipfile.ZipFile(source, "w") as z:
            for name, attrs in (("[media]\\replace.xml", 0), ("[media]\\keep.xml", 0), ("[media]\\permissions.xml", 0o100644 << 16)):
                info = zipfile.ZipInfo(name)
                info.filename = name
                z.writestr(info, b"original")
                # zipfile assigns default permissions when attrs is zero.
                info.external_attr = attrs
        with zipfile.ZipFile(source) as z:
            before = {info.orig_filename: info.external_attr for info in z.infolist()}
        self.assertEqual(before["[media]\\replace.xml"], 0)
        self.assertEqual(before["[media]\\keep.xml"], 0)

        installer._rewrite_pak(source, dest, [("[media]/replace.xml", b"replaced"), ("[media]/new.xml", b"new")])
        with zipfile.ZipFile(dest) as z:
            infos = {info.orig_filename: info for info in z.infolist()}
            after = {name: info.external_attr for name, info in infos.items()}
            self.assertEqual({name: after[name] for name in before}, before)
            self.assertEqual(z.read(infos["[media]\\replace.xml"]), b"replaced")
            self.assertEqual(z.read(infos["[media]\\keep.xml"]), b"original")
            self.assertEqual(z.read(infos["[media]\\new.xml"]), b"new")
            self.assertIsNone(z.testzip())

    def test_uninstall_batches_in_reverse_order_and_preserve_older_mods(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"first"})
        second = self.package("second.zip", {"[media]/unchanged.xml": b"second"})
        original = (self.paks / "initial.pak").read_bytes()
        first_backup = install(str(self.root), [Job(first, "pak:initial.pak", "First mod")])
        after_first = (self.paks / "initial.pak").read_bytes()
        second_backup = install(str(self.root), [Job(second, "pak:initial.pak", "Second mod")])
        self.assertEqual(installer.latest_batch(str(self.root)), (second_backup, ["Second mod"]))
        with self.assertRaisesRegex(InstallError, "latest batch changed"):
            restore_latest(str(self.root), expected_backup=first_backup)
        restore_latest(str(self.root), expected_backup=second_backup)
        self.assertEqual((self.paks / "initial.pak").read_bytes(), after_first)
        restore_latest(str(self.root), expected_backup=first_backup)
        self.assertEqual((self.paks / "initial.pak").read_bytes(), original)

    def test_uninstall_removes_every_mod_in_the_latest_batch(self):
        first = self.package("first.zip", {"one.dll": b"one"})
        second = self.package("second.zip", {"two.dll": b"two"})
        backup = install(str(self.root), [Job(first, "bin", "Mod one"), Job(second, "bin", "Mod two")])
        self.assertEqual(installer.latest_batch(str(self.root)), (backup, ["Mod one", "Mod two"]))
        restore_latest(str(self.root), expected_backup=backup)
        self.assertFalse((self.bin / "one.dll").exists())
        self.assertFalse((self.bin / "two.dll").exists())

    def test_uninstall_refuses_corrupt_backup_before_any_changes(self):
        package = self.package("mixed.zip", {"mod.dll": b"binary"})
        pak = self.package("pak.zip", {"[media]/truck.xml": b"changed"})
        backup = install(str(self.root), [Job(package, "bin"), Job(pak, "pak:initial.pak")])
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        saved = next(item["backup"] for item in manifest["changes"] if item["backup"] is not None)
        (backup / saved).write_bytes(b"corrupted")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(InstallError, "Backup file has changed"):
            restore_latest(str(self.root))
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_unfinished_or_unreadable_backup_blocks_install_and_uninstall(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"first"})
        install(str(self.root), [Job(first, "pak:initial.pak")])
        candidate = self.package("candidate.zip", {"new.dll": b"new"})
        unresolved = self.root / "SnowRunnerManualBackups" / "unfinished"
        unresolved.mkdir()
        manifest_path = unresolved / "manifest.json"
        for content in (json.dumps({"game": str(self.root), "status": "ready"}),
                        json.dumps({"game": str(self.root), "status": "restoring"}), "not json", None):
            if content is None:
                manifest_path.unlink(missing_ok=True)
            else:
                manifest_path.write_text(content, encoding="utf-8")
            with self.subTest(content=content):
                before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
                for operation in (lambda: install(str(self.root), [Job(candidate, "bin")]), lambda: restore_latest(str(self.root))):
                    with self.assertRaisesRegex(InstallError, "Unresolved backup"):
                        operation()
                self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_process_interruption_during_install_blocks_later_operations(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"changed"})
        second = self.package("second.zip", {"[meshes]/mesh": b"changed"})
        before_shared = (self.paks / "shared.pak").read_bytes()
        script = '''
import os, sys
from pathlib import Path
import installer
real_replace = os.replace
initial = Path(sys.argv[1]) / "preload" / "paks" / "client" / "initial.pak"
def interrupt(src, dst):
    real_replace(src, dst)
    if Path(dst) == initial:
        os._exit(23)
installer.os.replace = interrupt
installer.install(sys.argv[1], [installer.Job(sys.argv[2], "pak:initial.pak"), installer.Job(sys.argv[3], "pak:shared.pak")])
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.root), first, second],
                                capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual((self.paks / "shared.pak").read_bytes(), before_shared)
        with zipfile.ZipFile(self.paks / "initial.pak") as archive:
            entries = {info.orig_filename: archive.read(info) for info in archive.infolist()}
            self.assertEqual(entries["[media]\\truck.xml"], b"changed")
        manifest_path = next((self.root / "SnowRunnerManualBackups").glob("*/manifest.json"))
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["status"], "ready")
        for operation in (lambda: restore_latest(str(self.root)), lambda: install(str(self.root), [Job(second, "pak:shared.pak")])):
            with self.assertRaisesRegex(InstallError, "Unresolved backup"):
                operation()

    def test_interrupted_restore_blocks_later_operations(self):
        binary = self.package("mod.zip", {"mod.dll": b"mod"})
        pak = self.package("pak.zip", {"[media]/truck.xml": b"changed"})
        backup = install(str(self.root), [Job(pak, "pak:initial.pak"), Job(binary, "bin")])
        real_replace = installer.os.replace
        def interrupt_restore(src, dst):
            if Path(dst) == self.paks / "initial.pak":
                raise OSError("simulated restore failure")
            return real_replace(src, dst)
        with patch.object(installer.os, "replace", side_effect=interrupt_restore):
            with self.assertRaisesRegex(OSError, "simulated restore failure"):
                restore_latest(str(self.root))
        self.assertFalse((self.bin / "mod.dll").exists())
        self.assertEqual(json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["status"], "restoring")
        with self.assertRaisesRegex(InstallError, "Unresolved backup"):
            installer.latest_batch(str(self.root))

    def test_install_refuses_target_changed_after_backup_before_replacement(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        target = self.paks / "initial.pak"
        original_writer = installer._write_manifest
        def mutate_after_ready(path, manifest):
            original_writer(path, manifest)
            if manifest["status"] == "ready":
                target.write_bytes(b"external change")
        with patch.object(installer, "_write_manifest", side_effect=mutate_after_ready):
            with self.assertRaisesRegex(InstallError, "Target changed during installation"):
                install(str(self.root), [Job(package, "pak:initial.pak")])
        self.assertEqual(target.read_bytes(), b"external change")

    def test_uninstall_refuses_target_changed_during_preparation(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        backup = install(str(self.root), [Job(package, "pak:initial.pak")])
        target = self.paks / "initial.pak"
        original_copy = installer.shutil.copy2
        def mutate_after_copy(src, dst, *args, **kwargs):
            result = original_copy(src, dst, *args, **kwargs)
            target.write_bytes(b"external change")
            return result
        with patch.object(installer.shutil, "copy2", side_effect=mutate_after_copy):
            with self.assertRaisesRegex(InstallError, "changed while preparing"):
                restore_latest(str(self.root))
        self.assertEqual(target.read_bytes(), b"external change")
        self.assertEqual(json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["status"], "installed")

    def test_install_detects_change_during_pak_build(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        target = self.paks / "initial.pak"
        rewrite = installer._rewrite_pak
        def change_after_build(source, dest, entries):
            rewrite(source, dest, entries)
            target.write_bytes(b"external update")
        with patch.object(installer, "_rewrite_pak", side_effect=change_after_build):
            with self.assertRaisesRegex(InstallError, "changed"):
                install(str(self.root), [Job(package, "pak:initial.pak")])
        self.assertEqual(target.read_bytes(), b"external update")

    def test_install_refuses_pak_snapshot_changed_during_build(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        target = self.paks / "initial.pak"
        original = target.read_bytes()
        rewrite = installer._rewrite_pak
        def change_snapshot(source, dest, entries):
            rewrite(source, dest, entries)
            source.write_bytes(b"corrupt backup")
        with patch.object(installer, "_rewrite_pak", side_effect=change_snapshot):
            with self.assertRaisesRegex(InstallError, "Backup changed"):
                install(str(self.root), [Job(package, "pak:initial.pak")])
        self.assertEqual(target.read_bytes(), original)

    def test_install_checks_each_target_and_preserves_concurrent_change_on_rollback(self):
        first = self.package("first.zip", {"first.dll": b"first"})
        second = self.package("second.zip", {"second.dll": b"second"})
        real_link = os.link
        def change_after_first(src, dst, *args, **kwargs):
            result = real_link(src, dst, *args, **kwargs)
            if Path(dst) == self.bin / "first.dll":
                (self.bin / "first.dll").write_bytes(b"external first")
                (self.bin / "second.dll").write_bytes(b"external second")
            return result
        with patch.object(installer.os, "link", side_effect=change_after_first):
            with self.assertRaises(InstallError):
                install(str(self.root), [Job(first, "bin"), Job(second, "bin")])
        self.assertEqual((self.bin / "first.dll").read_bytes(), b"external first")
        self.assertEqual((self.bin / "second.dll").read_bytes(), b"external second")
        record = next((self.root / "SnowRunnerManualBackups").glob("*/manifest.json"))
        self.assertEqual(json.loads(record.read_text())["status"], "rolling_back")
        with self.assertRaisesRegex(InstallError, "Unresolved backup"):
            installer.latest_batch(str(self.root))

    def test_install_failed_rollback_keeps_record_unresolved_and_backup_intact(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"mod"})
        second = self.package("second.zip", {"[meshes]/mesh": b"mod"})
        original = (self.paks / "initial.pak").read_bytes()
        replace = os.replace
        def fail_commit_and_rollback(src, dst):
            if Path(dst) == self.paks / "shared.pak":
                raise OSError("commit failed")
            if Path(dst) == self.paks / "initial.pak" and Path(src).name.startswith(".srmi-rollback-"):
                raise OSError("rollback failed")
            return replace(src, dst)
        with patch.object(installer.os, "replace", side_effect=fail_commit_and_rollback):
            with self.assertRaisesRegex(InstallError, "rollback failed"):
                install(str(self.root), [Job(first, "pak:initial.pak"), Job(second, "pak:shared.pak")])
        record = next((self.root / "SnowRunnerManualBackups").glob("*/manifest.json"))
        manifest = json.loads(record.read_text())
        self.assertEqual(manifest["status"], "rolling_back")
        self.assertEqual((record.parent / manifest["changes"][0]["backup"]).read_bytes(), original)
        with self.assertRaisesRegex(InstallError, "Unresolved backup"):
            restore_latest(str(self.root))

    def test_uninstall_checks_each_target_after_restore_starts(self):
        first = self.package("first.zip", {"first.dll": b"first"})
        second = self.package("second.zip", {"second.dll": b"second"})
        backup = install(str(self.root), [Job(first, "bin"), Job(second, "bin")])
        writer = installer._write_manifest
        def change_after_restoring(path, manifest):
            writer(path, manifest)
            if manifest["status"] == "restoring":
                (self.bin / "first.dll").write_bytes(b"external change")
        with patch.object(installer, "_write_manifest", side_effect=change_after_restoring):
            with self.assertRaisesRegex(InstallError, "changed"):
                restore_latest(str(self.root))
        self.assertEqual((self.bin / "first.dll").read_bytes(), b"external change")
        self.assertFalse((self.bin / "second.dll").exists())
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["status"], "restoring")
        with self.assertRaisesRegex(InstallError, "Unresolved backup"):
            install(str(self.root), [Job(first, "bin")])

    def test_malformed_completed_manifest_blocks_writes(self):
        first = self.package("first.zip", {"first.dll": b"first"})
        second = self.package("second.zip", {"second.dll": b"second"})
        backup = install(str(self.root), [Job(first, "bin")])
        record = backup / "manifest.json"
        manifest = json.loads(record.read_text())
        manifest["changes"] = "invalid"
        record.write_text(json.dumps(manifest))
        for operation in (lambda: install(str(self.root), [Job(second, "bin")]),
                          lambda: restore_latest(str(self.root))):
            with self.assertRaisesRegex(InstallError, "Unresolved backup"):
                operation()
        self.assertEqual((self.bin / "first.dll").read_bytes(), b"first")
        self.assertFalse((self.bin / "second.dll").exists())

    def test_concurrent_installer_operations_are_rejected(self):
        package = self.package("mod.zip", {"mod.dll": b"mod"})
        with installer._operation_lock(self.root):
            for operation in (lambda: install(str(self.root), [Job(package, "bin")]),
                              lambda: restore_latest(str(self.root))):
                with self.assertRaisesRegex(InstallError, "another.*operation"):
                    operation()
        self.assertFalse((self.bin / "mod.dll").exists())

    def test_second_process_cannot_install_while_operation_locked(self):
        package = self.package("mod.zip", {"mod.dll": b"mod"})
        script = '''
import sys
import installer
try:
    installer.install(sys.argv[1], [installer.Job(sys.argv[2], "bin")])
except installer.InstallError as exc:
    print(exc)
    sys.exit(23)
sys.exit(0)
'''
        with installer._operation_lock(self.root):
            result = subprocess.run([sys.executable, "-c", script, str(self.root), package],
                                    capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn("another installer operation", result.stdout)
        self.assertFalse((self.bin / "mod.dll").exists())

    def test_interruption_during_rollback_blocks_later_operations(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"mod"})
        second = self.package("second.zip", {"[meshes]/mesh": b"mod"})
        script = '''
import os, sys
from pathlib import Path
import installer
real_replace = os.replace
root = Path(sys.argv[1]) / "preload" / "paks" / "client"
def interrupt(src, dst):
    if Path(dst) == root / "shared.pak":
        raise OSError("fail commit")
    if Path(dst) == root / "initial.pak" and Path(src).name.startswith(".srmi-rollback-"):
        os._exit(23)
    return real_replace(src, dst)
installer.os.replace = interrupt
installer.install(sys.argv[1], [installer.Job(sys.argv[2], "pak:initial.pak"), installer.Job(sys.argv[3], "pak:shared.pak")])
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.root), first, second],
                                capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 23, result.stderr)
        record = next((self.root / "SnowRunnerManualBackups").glob("*/manifest.json"))
        self.assertEqual(json.loads(record.read_text())["status"], "rolling_back")
        with self.assertRaisesRegex(InstallError, "Unresolved backup"):
            restore_latest(str(self.root))

    def test_install_rejects_staged_file_changed_after_manifest(self):
        package = self.package("mod.zip", {"mod.dll": b"mod"})
        writer = installer._write_manifest
        def change_staged(path, manifest):
            writer(path, manifest)
            if manifest["status"] == "ready":
                next(self.bin.glob(".srmi-*")).write_bytes(b"external change")
        with patch.object(installer, "_write_manifest", side_effect=change_staged):
            with self.assertRaisesRegex(InstallError, "Staged file changed"):
                install(str(self.root), [Job(package, "bin")])
        self.assertFalse((self.bin / "mod.dll").exists())

    def test_successful_rollback_restores_original_and_aborts(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"mod"})
        second = self.package("second.zip", {"[meshes]/mesh": b"mod"})
        original = (self.paks / "initial.pak").read_bytes()
        replace = os.replace
        def fail_second(src, dst):
            if Path(dst) == self.paks / "shared.pak":
                raise OSError("commit failed")
            return replace(src, dst)
        with patch.object(installer.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "commit failed"):
                install(str(self.root), [Job(first, "pak:initial.pak"), Job(second, "pak:shared.pak")])
        self.assertEqual((self.paks / "initial.pak").read_bytes(), original)
        record = next((self.root / "SnowRunnerManualBackups").glob("*/manifest.json"))
        self.assertEqual(json.loads(record.read_text())["status"], "aborted")

    def test_rollback_does_not_overwrite_change_during_copy(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"mod"})
        second = self.package("second.zip", {"[meshes]/mesh": b"mod"})
        target = self.paks / "initial.pak"
        replace = os.replace
        copy = installer.shutil.copy2
        def fail_second(src, dst):
            if Path(dst) == self.paks / "shared.pak":
                raise OSError("commit failed")
            return replace(src, dst)
        def change_target_during_rollback(src, dst, *args, **kwargs):
            result = copy(src, dst, *args, **kwargs)
            if Path(dst).name.startswith(".srmi-rollback-"):
                target.write_bytes(b"external change")
            return result
        with patch.object(installer.os, "replace", side_effect=fail_second), \
             patch.object(installer.shutil, "copy2", side_effect=change_target_during_rollback):
            with self.assertRaisesRegex(InstallError, "target changed during rollback"):
                install(str(self.root), [Job(first, "pak:initial.pak"), Job(second, "pak:shared.pak")])
        self.assertEqual(target.read_bytes(), b"external change")
        record = next((self.root / "SnowRunnerManualBackups").glob("*/manifest.json"))
        self.assertEqual(json.loads(record.read_text())["status"], "rolling_back")
        with self.assertRaisesRegex(InstallError, "Unresolved backup"):
            restore_latest(str(self.root))

    def test_uninstall_rejects_staged_backup_changed_after_validation(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        backup = install(str(self.root), [Job(package, "pak:initial.pak")])
        target = self.paks / "initial.pak"
        before = target.read_bytes()
        writer = installer._write_manifest
        def change_staged(path, manifest):
            writer(path, manifest)
            if manifest["status"] == "restoring":
                next(self.paks.glob(".srmi-restore-*")).write_bytes(b"corrupt copy")
        with patch.object(installer, "_write_manifest", side_effect=change_staged):
            with self.assertRaisesRegex(InstallError, "Backup copy changed"):
                restore_latest(str(self.root))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["status"], "restoring")

    def test_uninstall_revalidates_manifest_loaded_after_preview(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        backup = install(str(self.root), [Job(package, "pak:initial.pak")])
        target = self.paks / "initial.pak"
        before = target.read_bytes()
        latest = installer.latest_batch
        def alter_after_preview(root):
            result = latest(root)
            record = backup / "manifest.json"
            manifest = json.loads(record.read_text())
            manifest["changes"][0]["backup"] = None
            record.write_text(json.dumps(manifest))
            return result
        with patch.object(installer, "latest_batch", side_effect=alter_after_preview):
            with self.assertRaisesRegex(InstallError, "Unresolved backup"):
                restore_latest(str(self.root))
        self.assertEqual(target.read_bytes(), before)

    def test_uninstall_refuses_manifest_changed_while_staging(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        backup = install(str(self.root), [Job(package, "pak:initial.pak")])
        target = self.paks / "initial.pak"
        before = target.read_bytes()
        original_copy = installer.shutil.copy2
        def change_record(source, dest, *args, **kwargs):
            result = original_copy(source, dest, *args, **kwargs)
            record = backup / "manifest.json"
            manifest = json.loads(record.read_text())
            manifest["labels"] = ["changed externally"]
            record.write_text(json.dumps(manifest))
            return result
        with patch.object(installer.shutil, "copy2", side_effect=change_record):
            with self.assertRaisesRegex(InstallError, "manifest.*changed"):
                restore_latest(str(self.root))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["status"], "installed")

    def test_malformed_pak_record_cannot_delete_archive(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"mod"})
        backup = install(str(self.root), [Job(package, "pak:initial.pak")])
        record = backup / "manifest.json"
        manifest = json.loads(record.read_text())
        manifest["changes"][0]["backup"] = None
        record.write_text(json.dumps(manifest))
        before = (self.paks / "initial.pak").read_bytes()
        with self.assertRaisesRegex(InstallError, "Unresolved backup"):
            restore_latest(str(self.root))
        self.assertEqual((self.paks / "initial.pak").read_bytes(), before)

    def test_legacy_backup_without_labels_or_hash_remains_restorable(self):
        package = self.package("mod.zip", {"[media]/truck.xml": b"modified"})
        original = (self.paks / "initial.pak").read_bytes()
        backup = install(str(self.root), [Job(package, "pak:initial.pak")])
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("labels")
        for item in manifest["changes"]:
            item.pop("backup_hash")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(installer.latest_batch(str(self.root)), (backup, ["pak:initial.pak"]))
        restore_latest(str(self.root))
        self.assertEqual((self.paks / "initial.pak").read_bytes(), original)

    def test_real_life_base_and_restore(self):
        rlm = self.package("rlm.zip", {"RLM/[media]/truck.xml": b"rlm", "RLM/[strings]/en.txt": b"strings", "RLM/initial.cache_block": b"new cache", "RLM/0. MUST-HAVE/[meshes]/mesh": b"new mesh", "RLM/C. GAMEPLAY MODES/initial.cache_block": b"optional"})
        before = [(self.paks / name).read_bytes() for name in ("initial.pak", "shared.pak")]
        backup = install(str(self.root), [Job(rlm, "rlm")])
        self.assertNotEqual(backup.parent, self.paks)
        with zipfile.ZipFile(self.paks / "initial.pak") as z:
            entries = {info.orig_filename: z.read(info) for info in z.infolist()}
            self.assertEqual(entries["[media]\\truck.xml"], b"rlm")
            self.assertEqual(entries["[media]\\unchanged.xml"], b"keep path")
            self.assertEqual(entries["initial.cache_block"], b"new cache")
            self.assertEqual(entries["untouched"], b"keep")
            self.assertEqual(entries["[strings]\\en.txt"], b"strings")
        with zipfile.ZipFile(self.paks / "shared.pak") as z:
            entries = {info.orig_filename: z.read(info) for info in z.infolist()}
            self.assertEqual(entries["[meshes]\\mesh"], b"new mesh")
        restore_latest(str(self.root))
        self.assertEqual(before, [(self.paks / name).read_bytes() for name in ("initial.pak", "shared.pak")])
        self.assertEqual(backups(str(self.root)), [])

    def test_preflight_reports_multiple_conflicts_without_writes(self):
        a = self.package("a.zip", {"[media]/truck.xml": b"a", "[media]/unchanged.xml": b"one"})
        b = self.package("b.zip", {"[media]/truck.xml": b"b", "[media]/unchanged.xml": b"two"})
        cargo = self.package("cargo.zip", {"version.dll": b"new loader", "mods/CargoStrap.dll": b"new mod"})
        (self.bin / "version.dll").write_bytes(b"existing loader")
        (self.bin / "mods").mkdir()
        (self.bin / "mods" / "CargoStrap.dll").write_bytes(b"existing mod")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        jobs = [Job(a, "pak:initial.pak"), Job(b, "pak:initial.pak"), Job(cargo, "cargo")]
        count, issues = preflight(str(self.root), jobs)
        text = "\n".join(issues)
        self.assertEqual(count, 4)
        for name in ("truck.xml", "unchanged.xml", "version.dll", "CargoStrap.dll"):
            self.assertIn(name, text)
        with self.assertRaisesRegex(InstallError, "Pre-flight stopped"):
            install(str(self.root), jobs)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertFalse((self.root / "SnowRunnerManualBackups").exists())

    def test_preflight_checks_previous_batch_and_missing_archive(self):
        first = self.package("first.zip", {"[media]/truck.xml": b"first"})
        second = self.package("second.zip", {"[media]/truck.xml": b"second"})
        missing = self.package("missing.zip", {"[textures]/item": b"texture"})
        install(str(self.root), [Job(first, "pak:initial.pak")])
        _, issues = preflight(str(self.root), [Job(second, "pak:initial.pak"), Job(missing, "pak:boot.pak")])
        text = "\n".join(issues)
        self.assertIn("Previously installed mod", text)
        self.assertIn("boot.pak", text)

    def test_preflight_matches_spotter_loader_exception(self):
        gameplay = self.package("gameplay.zip", {"Gauges.asi": b"gameplay", "version.dll": b"loader"})
        install(str(self.root), [Job(gameplay, "gameplay")])
        (self.bin / "dinput8.dll").write_bytes(b"unrelated")
        spotter = self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"spotter loader"})
        _, issues = preflight(str(self.root), [Job(spotter, "spotter")])
        self.assertEqual(issues, [])
        install(str(self.root), [Job(spotter, "spotter")])
        self.assertEqual((self.bin / "dinput8.dll").read_bytes(), b"unrelated")

    def test_preflight_reports_blocked_parent_and_duplicate_pak(self):
        (self.bin / "mods").write_bytes(b"not a folder")
        cargo = self.package("cargo.zip", {"version.dll": b"loader", "mods/CargoStrap.dll": b"mod"})
        write_zip(self.paks / "shared.pak", {"[meshes]\\mesh": b"a", "[meshes]/mesh": b"b"})
        meshes = self.package("meshes.zip", {"[meshes]/mesh": b"new"})
        _, issues = preflight(str(self.root), [Job(cargo, "cargo"), Job(meshes, "pak:shared.pak")])
        text = "\n".join(issues)
        self.assertIn("Not a directory", text)
        self.assertIn("Duplicate path", text)

    def test_spotter_loader_exception_requires_nextgen_to_supply_version(self):
        (self.bin / "version.dll").write_bytes(b"cargo loader")
        (self.bin / "dinput8.dll").write_bytes(b"existing loader")
        gameplay = self.package("gameplay.zip", {"Gauges.asi": b"gameplay"})
        cargo = self.package("cargo.zip", {"version.dll": b"cargo loader"})
        spotter = self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"spotter loader"})
        jobs = [Job(gameplay, "gameplay"), Job(cargo, "cargo"), Job(spotter, "spotter")]
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        _, issues = preflight(str(self.root), jobs)
        self.assertIn("unknown existing version.dll", "\n".join(issues))
        self.assertIn("dinput8.dll", "\n".join(issues))
        with self.assertRaisesRegex(InstallError, "unknown existing version.dll"):
            install(str(self.root), jobs)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_mixed_batch_does_not_attribute_another_mods_loader_to_nextgen(self):
        gameplay = self.package("gameplay.zip", {"Gauges.asi": b"gameplay"})
        cargo = self.package("cargo.zip", {"version.dll": b"cargo loader"})
        backup = install(str(self.root), [Job(gameplay, "gameplay"), Job(cargo, "cargo")])
        spotter = self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"spotter loader"})
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIsNone(manifest["nextgen_loader_hash"])
        for legacy in (False, True):
            if legacy:
                del manifest["nextgen_loader_hash"]
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.subTest(legacy=legacy):
                before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
                _, issues = preflight(str(self.root), [Job(spotter, "spotter")])
                self.assertIn("unknown existing version.dll", "\n".join(issues))
                with self.assertRaisesRegex(InstallError, "unknown existing version.dll"):
                    install(str(self.root), [Job(spotter, "spotter")])
                self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_nextgen_loader_provenance_in_mixed_and_legacy_pure_batches(self):
        gameplay = self.package("gameplay.zip", {"Gauges.asi": b"gameplay", "version.dll": b"nextgen"})
        other = self.package("other.zip", {"other.dll": b"other"})
        spotter = self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"spotter loader"})
        backup = install(str(self.root), [Job(gameplay, "gameplay"), Job(other, "bin")])
        (self.bin / "dinput8.dll").write_bytes(b"existing")
        self.assertEqual(preflight(str(self.root), [Job(spotter, "spotter")])[1], [])
        restore_latest(str(self.root))
        backup = install(str(self.root), [Job(gameplay, "gameplay")])
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        del manifest["nextgen_loader_hash"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(preflight(str(self.root), [Job(spotter, "spotter")])[1], [])

    def test_omitted_spotter_loaders_do_not_conflict_with_each_other_or_prior_claims(self):
        first = self.package("first.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"first"})
        install(str(self.root), [Job(first, "spotter")])
        gameplay = self.package("gameplay.zip", {"Gauges.asi": b"gameplay", "version.dll": b"nextgen"})
        install(str(self.root), [Job(gameplay, "gameplay")])
        second = self.package("second.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"second"})
        third = self.package("third.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"third", "Spotter/options.json": b"{}"})
        jobs = [Job(second, "spotter"), Job(third, "spotter")]
        _, issues = preflight(str(self.root), jobs)
        self.assertEqual(issues, [])
        install(str(self.root), jobs)
        self.assertEqual((self.bin / "dinput8.dll").read_bytes(), b"first")
        self.assertEqual((self.bin / "Spotter" / "options.json").read_bytes(), b"{}")

    def test_preflight_reports_queued_file_folder_collision(self):
        first = self.package("first.zip", {"first.dll": b"first", "folder.ini": b"file"})
        second = self.package("second.zip", {"second.dll": b"second", "folder.ini/config.json": b"config"})
        jobs = [Job(first, "bin"), Job(second, "bin")]
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        _, issues = preflight(str(self.root), jobs)
        self.assertIn("both a file and a parent folder", "\n".join(issues))
        with self.assertRaisesRegex(InstallError, "both a file and a parent folder"):
            install(str(self.root), jobs)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertFalse((self.root / "SnowRunnerManualBackups").exists())

    def test_install_stages_the_preflight_plan_without_rereading_package(self):
        package = self.package("mod.zip", {"mod.dll": b"validated"})
        original_plan_jobs = installer.plan_jobs
        def plan_once(jobs):
            plan = original_plan_jobs(jobs)
            write_zip(Path(package), {"mod.dll": b"changed after validation"})
            return plan
        with patch.object(installer, "plan_jobs", side_effect=plan_once) as planner:
            install(str(self.root), [Job(package, "bin")])
        self.assertEqual(planner.call_count, 1)
        self.assertEqual((self.bin / "mod.dll").read_bytes(), b"validated")

    def test_preflight_reports_invalid_package_and_other_conflicts(self):
        a = self.package("a.zip", {"[media]/truck.xml": b"a"})
        b = self.package("b.zip", {"[media]/truck.xml": b"b"})
        _, issues = preflight(str(self.root), [Job("missing.zip", "bin"), Job(a, "pak:initial.pak"), Job(b, "pak:initial.pak")])
        self.assertIn("missing.zip", "\n".join(issues))
        self.assertIn("truck.xml", "\n".join(issues))

    def test_bulk_conflict_does_not_change_game(self):
        a = self.package("a.zip", {"[media]/truck.xml": b"a"})
        b = self.package("b.zip", {"[media]/truck.xml": b"b"})
        before = (self.paks / "initial.pak").read_bytes()
        with self.assertRaisesRegex(InstallError, "Conflict"):
            install(str(self.root), [Job(a, "pak:initial.pak"), Job(b, "pak:initial.pak")])
        self.assertEqual(before, (self.paks / "initial.pak").read_bytes())

    def test_separate_batch_conflict(self):
        a = self.package("a.zip", {"[media]/truck.xml": b"a"})
        b = self.package("b.zip", {"[media]/truck.xml": b"b"})
        install(str(self.root), [Job(a, "pak:initial.pak")])
        with self.assertRaisesRegex(InstallError, "Previously installed"):
            install(str(self.root), [Job(b, "pak:initial.pak")])

    def test_different_loader_blocked(self):
        (self.bin / "version.dll").write_bytes(b"existing")
        cargo = self.package("cargo.zip", {"version.dll": b"other", "mods/CargoStrap.dll": b"mod"})
        with self.assertRaisesRegex(InstallError, "Existing file differs"):
            install(str(self.root), [Job(cargo, "cargo")])
        self.assertEqual((self.bin / "version.dll").read_bytes(), b"existing")
        self.assertFalse((self.bin / "mods" / "CargoStrap.dll").exists())

    def test_spotter_keeps_next_gen_loader(self):
        gameplay = self.package("gameplay.zip", {"Gauges.asi": b"gameplay", "version.dll": b"loader"})
        install(str(self.root), [Job(gameplay, "gameplay")])
        spotter = self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"spotter loader", "Spotter/options.json": b"{}"})
        install(str(self.root), [Job(spotter, "spotter")])
        self.assertFalse((self.bin / "dinput8.dll").exists())
        self.assertEqual((self.bin / "Spotter.asi").read_bytes(), b"spotter")
        restore_latest(str(self.root))
        self.assertEqual((self.bin / "version.dll").read_bytes(), b"loader")
        self.assertFalse((self.bin / "Spotter.asi").exists())

    def test_spotter_does_not_assume_asi_file_is_loader(self):
        (self.bin / "Gauges.asi").write_bytes(b"gameplay")
        (self.bin / "version.dll").write_bytes(b"unknown loader")
        spotter = self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"loader"})
        with self.assertRaisesRegex(InstallError, "unknown existing version.dll"):
            install(str(self.root), [Job(spotter, "spotter")])
        self.assertFalse((self.bin / "dinput8.dll").exists())

    def test_restore_rejects_external_edits(self):
        spotter = self.package("spotter.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"loader"})
        install(str(self.root), [Job(spotter, "spotter")])
        (self.bin / "Spotter.asi").write_bytes(b"external edit")
        with self.assertRaisesRegex(InstallError, "changed since"):
            restore_latest(str(self.root))
        self.assertEqual((self.bin / "dinput8.dll").read_bytes(), b"loader")

    def test_unsafe_zip_rejected(self):
        evil = self.package("evil.zip", {"../version.dll": b"bad"})
        with self.assertRaisesRegex(InstallError, "Unsafe path"):
            plan_jobs([Job(evil, "bin")])

    def test_incomplete_rlm_rejected(self):
        bad = self.package("bad.zip", {"initial.cache_block": b"cache", "[media]/x": b"x"})
        with self.assertRaisesRegex(InstallError, "incomplete"):
            plan_jobs([Job(bad, "rlm")])

    def test_preflight_failure_does_not_commit_prepared_pak(self):
        a = self.package("a.zip", {"[media]/truck.xml": b"changed"})
        b = self.package("b.zip", {"Spotter.asi": b"spotter", "dinput8.dll": b"different"})
        (self.bin / "dinput8.dll").write_bytes(b"existing")
        before = (self.paks / "initial.pak").read_bytes()
        with self.assertRaises(InstallError):
            install(str(self.root), [Job(a, "pak:initial.pak"), Job(b, "spotter")])
        self.assertEqual(before, (self.paks / "initial.pak").read_bytes())


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_game(self, root):
        (root / "Sources" / "Bin").mkdir(parents=True)
        (root / "preload" / "paks" / "client").mkdir(parents=True)
        (root / "Sources" / "Bin" / "SnowRunner.exe").write_bytes(b"exe")
        (root / "preload" / "paks" / "client" / "initial.pak").write_bytes(b"pak")
        return root.resolve()

    def test_steam_secondary_library_and_manifest(self):
        steam = self.root / "steam"
        library = self.root / "other library"
        (steam / "steamapps").mkdir(parents=True)
        (library / "steamapps").mkdir(parents=True)
        (steam / "steamapps" / "libraryfolders.vdf").write_text(
            f'"libraryfolders" {{ "1" {{ "path" "{library}" }} }}', encoding="utf-8")
        game = self.make_game(library / "steamapps" / "common" / "SnowRunner")
        (library / "steamapps" / "appmanifest_1465360.acf").write_text(
            '"AppState" { "appid" "1465360" "installdir" "SnowRunner" }', encoding="utf-8")
        with patch.object(installer, "_steam_roots", return_value=[steam]):
            self.assertEqual(list(installer._steam_candidates()), [game])
        pairs = installer._vdf_pairs(r'"path" "D:\\SteamLibrary"')
        self.assertEqual(pairs, [("path", 'D:\\SteamLibrary')])

    def test_epic_manifest_nested_en_us_and_bad_records(self):
        manifests = self.root / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        manifests.mkdir(parents=True)
        epic_root = self.root / "Epic Games" / "SnowRunner"
        game = self.make_game(epic_root / "en_us")
        (manifests / "game.item").write_text(json.dumps({"InstallLocation": str(epic_root)}), encoding="utf-8")
        (manifests / "bad.item").write_text("not json", encoding="utf-8")
        (manifests / "incomplete.item").write_text(json.dumps({"InstallLocation": str(epic_root), "bIsIncompleteInstall": True}), encoding="utf-8")
        with patch.dict(installer.os.environ, {"PROGRAMDATA": str(self.root)}):
            with patch.object(installer.sys, "platform", "win32"), patch.object(installer, "_steam_candidates", return_value=[]), patch.object(installer, "_game_pass_candidates", return_value=[]):
                self.assertEqual(installer.detect_game_installations(), [("Epic Games", str(game))])

    def test_game_pass_package_location_and_content_fallback(self):
        package = self.root / "XboxGames" / "SnowRunner"
        game = self.make_game(package / "Content")
        completed = unittest.mock.Mock(stdout=json.dumps([str(package)]))
        with patch.object(installer.sys, "platform", "win32"), patch.object(installer.subprocess, "run", return_value=completed), patch.object(installer, "_steam_candidates", return_value=[]), patch.object(installer, "_epic_candidates", return_value=[]), patch.object(installer, "_xbox_roots", return_value=[]):
            self.assertEqual(installer.detect_game_installations(), [("PC Game Pass", str(game))])

    def test_game_pass_fallback_searches_mounted_xbox_folder(self):
        xbox = self.root / "XboxGames"
        game = self.make_game(xbox / "SnowRunner" / "Content")
        with patch.object(installer.sys, "platform", "win32"), patch.object(installer.subprocess, "run", side_effect=OSError("unavailable")), patch.object(installer, "_steam_candidates", return_value=[]), patch.object(installer, "_epic_candidates", return_value=[]), patch.object(installer, "_xbox_roots", return_value=[xbox]):
            self.assertEqual(installer.detect_game_installations(), [("PC Game Pass", str(game))])

    def test_stale_inaccessible_and_duplicate_candidates(self):
        game = self.make_game(self.root / "game")
        stale = self.root / "missing"
        with patch.object(installer.sys, "platform", "win32"), patch.object(installer, "_steam_candidates", return_value=[game, stale]), patch.object(installer, "_epic_candidates", return_value=[game]), patch.object(installer, "_game_pass_candidates", return_value=[]):
            self.assertEqual(installer.detect_game_installations(), [("Steam", str(game))])
        with patch.object(installer.sys, "platform", "win32"), patch.object(installer, "_steam_candidates", return_value=[stale]), patch.object(installer, "_epic_candidates", return_value=[]), patch.object(installer, "_game_pass_candidates", return_value=[]):
            self.assertEqual(installer.detect_game_installations(), [])

    def test_game_pass_query_failure_keeps_other_store_detection(self):
        game = self.make_game(self.root / "steam")
        with patch.object(installer.sys, "platform", "win32"), patch.object(installer.subprocess, "run", side_effect=OSError("unavailable")), patch.object(installer, "_steam_candidates", return_value=[game]), patch.object(installer, "_epic_candidates", return_value=[]), patch.object(installer, "_xbox_roots", return_value=[]):
            self.assertEqual(installer.detect_game_installations(), [("Steam", str(game))])

    def test_detection_does_not_run_on_other_platforms(self):
        with patch.object(installer.sys, "platform", "linux"), patch.object(installer, "_steam_candidates") as steam:
            self.assertEqual(installer.detect_game_installations(), [])
            steam.assert_not_called()

    def test_multiple_store_installs_preserved(self):
        steam = self.make_game(self.root / "steam")
        epic = self.make_game(self.root / "epic" / "en_us")
        with patch.object(installer.sys, "platform", "win32"), patch.object(installer, "_steam_candidates", return_value=[steam]), patch.object(installer, "_epic_candidates", return_value=[epic]), patch.object(installer, "_game_pass_candidates", return_value=[]):
            self.assertEqual(installer.detect_game_installations(), [("Steam", str(steam)), ("Epic Games", str(epic))])


if __name__ == "__main__":
    unittest.main()
