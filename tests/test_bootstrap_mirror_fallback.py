import os
from pathlib import Path
import subprocess
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).parents[1]


class BootstrapMirrorFallbackTests(unittest.TestCase):
    def test_auto_mirror_tries_tuna_before_official(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            runtime_root = temporary / "runtime"
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            log_path = temporary / "uv-calls.txt"
            fake_uv = fake_bin / "uv.ps1"
            fake_uv.write_text(
                """
$line = $args -join " "
[System.IO.File]::AppendAllText($env:FAKE_UV_LOG, $line + [Environment]::NewLine)
if ($args -contains "venv") {
    $scripts = Join-Path $env:TMALL_RUNTIME_ROOT ".venv\\Scripts"
    New-Item -ItemType Directory -Path $scripts -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $scripts "python.exe") -Force | Out-Null
    exit 0
}
if ($args -contains "pip") {
    exit 7
}
exit 0
""".strip()
                + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["TMALL_RUNTIME_ROOT"] = str(runtime_root)
            environment["FAKE_UV_LOG"] = str(log_path)
            environment["PATH"] = os.pathsep.join(
                [str(fake_bin), environment.get("PATH", "")]
            )

            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(REPOSITORY_ROOT / "scripts" / "bootstrap.ps1"),
                    "-Python",
                    sys.executable,
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            calls = log_path.read_text(encoding="utf-8")
            tuna = calls.index("uv-tuna.toml")
            official = calls.index("uv-official.toml", tuna + 1)
            self.assertNotEqual(completed.returncode, 0)
            self.assertLess(tuna, official)
            self.assertEqual(calls.count(" pip sync "), 2)
            self.assertIn("DEPENDENCY_SYNC_FAILED", completed.stderr)


if __name__ == "__main__":
    unittest.main()
