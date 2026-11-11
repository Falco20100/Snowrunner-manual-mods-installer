import tempfile
import unittest
import zipfile
from pathlib import Path

from installer import InstallError, Job, backups, install, plan_jobs, restore_latest


def write_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "SnowRunner"
        self.bin = self.root / "Sources" / "Bin"
        self.paks = self.root / "preload" / "paks" / "client"
        self.bin.mkdir(parents=True)
        self.paks.mkdir(parents=True)
        (self.bin / "SnowRunner.exe").write_bytes(b"game")
        write_zip(self.paks / "initial.pak", {"[media]\\truck.xml": b"original", "initial.cache_block": b"cache", "untouched": b"keep"})
        write_zip(self.paks / "shared.pak", {"[meshes]\\mesh": b"original mesh"})

    def tearDown(self):
        self.tmp.cleanup()

    def package(self, name, files):
        path = Path(self.tmp.name) / name
        write_zip(path, files)
        return str(path)

    def test_real_life_base_and_restore(self):
        rlm = self.package("rlm.zip", {"RLM/[media]/truck.xml": b"rlm", "RLM/[strings]/en.txt": b"strings", "RLM/initial.cache_block": b"new cache", "RLM/0. MUST-HAVE/[meshes]/mesh": b"new mesh", "RLM/C. GAMEPLAY MODES/initial.cache_block": b"optional"})
        before = [(self.paks / name).read_bytes() for name in ("initial.pak", "shared.pak")]
        backup = install(str(self.root), [Job(rlm, "rlm")])
        self.assertNotEqual(backup.parent, self.paks)
        with zipfile.ZipFile(self.paks / "initial.pak") as z:
            self.assertEqual(z.read("[media]\\truck.xml"), b"rlm")
            self.assertEqual(z.read("initial.cache_block"), b"new cache")
            self.assertEqual(z.read("untouched"), b"keep")
            self.assertEqual(z.read("[strings]\\en.txt"), b"strings")
        with zipfile.ZipFile(self.paks / "shared.pak") as z:
            self.assertEqual(z.read("[meshes]\\mesh"), b"new mesh")
        restore_latest(str(self.root))
        self.assertEqual(before, [(self.paks / name).read_bytes() for name in ("initial.pak", "shared.pak")])
        self.assertEqual(backups(str(self.root)), [])

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


if __name__ == "__main__":
    unittest.main()
