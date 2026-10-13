"""Host tests only: never execute a real module loader or reboot syscall."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "upstream.lock.json").read_text())
# Packaged KMIs -> a sample real kernel release that must select them.
KMIS = {f"{kmi.split('-', 1)[1]}.0-{kmi.split('-', 1)[0]}-1-test": kmi
        for kmi in LOCK["supported_kmis"]}
# Releases that must never select a KMI: an (os, kver) pair upstream does not ship, a tag
# that names the wrong os for a packaged kver, or a tag-less version that two KMIs share.
UNPACKAGED = ("6.6.118-android14-1-test", "5.4.0-android12-1-test",
              "3.18.0-android10-1-test", "6.10.0-android14-1-test",
              "5.109.0-android12-1-test", "5.10.200-custom", "5.15.148-custom",
              "4.19.0-custom")
# Tag-less custom kernels: osrelease keeps the GKI version but drops the Android tag, so the
# strict match misses. They resolve by version alone, but only when it maps to one packaged
# KMI. 6.6.50-mainline is the documented tradeoff: version-only cannot tell it from GKI 6.6.
TAGLESS = {"6.1.176-Radioactive-V1.3-+": "android14-6.1", "6.6.118-vendor": "android15-6.6",
           "6.12.9-custom": "android16-6.12", "6.18.2-custom": "android17-6.18",
           "6.6.50-mainline": "android15-6.6"}
# Releases that mention a packaged (os, kver) but in a non-canonical shape: the Android
# tag must directly follow a numeric patch level. Guessing here would load a binary whose
# real ABI is unknown, so these must be refused like any other unrecognized release.
MANGLED = ("6.6.bad-android15-vendor", "6.6.118-vendor-android15-8",
           "6.6-android15-8", "6.6.118.android15-8")
# Same KMI, variants of the canonical shape: the KMI generation (and anything after it) is
# optional and must not stop a correct match.
CANONICAL_SUFFIXES = ("", "-8", "-8-g3a3f1c2-ab1234", "-0-gbce1b2c3")


def write_kmis(directory):
    (directory / "kmis").write_text("".join(k + "\n" for k in LOCK["supported_kmis"]))


class LoaderTests(unittest.TestCase):
    def run_loader(self, probe, kernel=None, marker=None, after=None, missing=False,
                   loader_result=0, expect_kmi=None):
        kernel = kernel or next(iter(KMIS))
        # Only a kernel that is expected to select resolves to a packaged KMI; rejection
        # cases (UNPACKAGED/MANGLED) have no entry and must not blow up before the run.
        kmi = expect_kmi or KMIS.get(kernel)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            shutil.copy(ROOT / "module/post-fs-data.sh", root)
            shutil.copy(ROOT / "module/kmi.sh", root)
            write_kmis(root)
            (root / "lib").mkdir()
            if not missing:
                for packaged in KMIS.values():
                    (root / "lib" / f"susfs_guard_lkm-{packaged}.ko").write_bytes(b"mock")
            scripts = {
                "susfs_probe": (f'[ ! -f "$TEST_ROOT/loaded" ] || exit {after if after is not None else probe}\n'
                                f"exit {probe}\n"),
                "susfs_insmod": 'echo "$*" > "$TEST_ROOT/loaded"\n' + f'exit {loader_result}\n',
                "cat": f"echo '{kernel}'\n",
            }
            for name, contents in scripts.items():
                path = root / "bin" / name
                path.write_text("#!/bin/sh\n" + contents)
                path.chmod(0o755)
            if marker:
                (root / marker).touch()
            result = subprocess.run(["sh", str(root / "post-fs-data.sh")],
                                    env={**os.environ, "TEST_ROOT": str(root),
                                         "PATH": str(root / "bin") + ":" + os.environ["PATH"]})
            loaded = root / "loaded"
            if loaded.exists():
                expected = f"{root}/lib/susfs_guard_lkm-{kmi}.ko expose_proc=1"
                self.assertEqual(loaded.read_text().strip(), expected)
            return result.returncode, loaded.exists()

    def test_existing_and_unknown_never_load(self):
        self.assertEqual(self.run_loader(0), (0, False))
        self.assertEqual(self.run_loader(12), (1, False))

    def test_disabled_removed_and_wrong_kernel_never_load(self):
        self.assertEqual(self.run_loader(10, marker="disable"), (0, False))
        self.assertEqual(self.run_loader(10, marker="remove"), (0, False))
        for kernel in UNPACKAGED:
            with self.subTest(kernel=kernel):
                self.assertEqual(self.run_loader(10, kernel=kernel), (1, False))

    def test_only_absent_interface_attempts_load(self):
        # Mock stays absent after load: this must report failure, not pretend success.
        self.assertEqual(self.run_loader(10), (1, True))

    def test_mangled_releases_never_select_a_kmi(self):
        for kernel in MANGLED:
            with self.subTest(kernel=kernel):
                self.assertEqual(self.run_loader(10, kernel=kernel), (1, False))

    def test_tagless_version_falls_back_only_when_unambiguous(self):
        # No Android tag and the version maps to a single packaged KMI: load it.
        for kernel, kmi in TAGLESS.items():
            with self.subTest(kernel=kernel):
                self.assertEqual(self.run_loader(10, kernel=kernel, after=0, expect_kmi=kmi),
                                 (0, True))

    def test_canonical_shapes_still_select(self):
        # The KMI generation suffix is optional; only a wrong <os>/<kver> pair refuses.
        for kmi in KMIS.values():
            os, kver = kmi.split("-", 1)
            for suffix in CANONICAL_SUFFIXES:
                kernel = f"{kver}.118-{os}{suffix}"
                with self.subTest(kernel=kernel):
                    self.assertEqual(self.run_loader(10, kernel=kernel, after=0,
                                                     expect_kmi=kmi), (0, True))

    def test_all_packaged_kmis_select_correct_binary(self):
        for kernel in KMIS:
            with self.subTest(kernel=kernel):
                self.assertEqual(self.run_loader(10, kernel=kernel, after=0), (0, True))

    def test_missing_binary_and_loader_failure(self):
        self.assertEqual(self.run_loader(10, missing=True), (1, False))
        self.assertEqual(self.run_loader(10, loader_result=7), (7, True))

    def test_install_selection_and_rejection(self):
        accepted = {*KMIS, *TAGLESS}
        for kernel in (*KMIS, *TAGLESS, *UNPACKAGED, *MANGLED):
            with self.subTest(kernel=kernel), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(ROOT / "module", root / "module")
                mod = root / "module"
                (mod / "lib").mkdir()
                for kmi in KMIS.values():
                    (mod / "lib" / f"susfs_guard_lkm-{kmi}.ko").write_bytes(b"mock")
                write_kmis(mod)
                harness = (f'cat() {{ echo "{kernel}"; }}\n'
                           'abort() { echo "$*"; exit 1; }\n'
                           'set_perm_recursive() { :; }\nset_perm() { :; }\nui_print() { :; }\n'
                           '. "$MODPATH/customize.sh"\n')
                result = subprocess.run(["sh", "-c", harness], capture_output=True,
                                        env={**os.environ, "KSU": "true", "ARCH": "arm64", "MODPATH": str(mod)})
                self.assertEqual(result.returncode, 0 if kernel in accepted else 1)
                self.assertEqual((mod / "skip_mount").exists(), kernel in accepted)


if __name__ == "__main__":
    unittest.main()
