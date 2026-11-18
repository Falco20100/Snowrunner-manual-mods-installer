import json
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
            self.assertEqual(archive.read(bokeh.replace("/", "\\")), b"effects:bokeh")
            self.assertEqual(archive.read("unchanged"), b"keep")
        with zipfile.ZipFile(self.paks / "initial.pak") as archive:
            self.assertEqual(archive.read(horn.replace("/", "\\")), b"effects:horn")
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
