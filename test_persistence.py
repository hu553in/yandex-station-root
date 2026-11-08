import shlex
import subprocess  # nosec B404 # explicit local argv, no host shell
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import persistence


class UpdaterStatusTest(unittest.TestCase):
    def test_status_never_turns_a_failed_probe_into_absence(self):
        for result, expected in (
            (b"", "not observed"),
            (b"pid 123\npid 456\n", "running (PID 123, 456)"),
            (b"pid 123\nunknown\n", "unknown"),
            (b"unrecognized\n", "unknown"),
        ):
            with (
                self.subTest(result=result),
                patch.object(persistence, "adb_shell", return_value=result),
            ):
                self.assertEqual(persistence.updater_status("adb", "station"), expected)
        with patch.object(persistence, "adb_shell", side_effect=ValueError("failed")):
            self.assertEqual(persistence.updater_status("adb", "station"), "unknown")

    def test_probe_matches_the_executable_and_complete_argument_list(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            executable = root / "maind"
            executable.touch()
            other = root / "other"
            other.touch()
            for pid, argv, target in (
                (101, [str(executable), "--updatesd"], executable),
                (102, [str(executable), "--updatesd-extra"], executable),
                (103, [str(executable), "--updatesd", "extra"], executable),
                (104, ["--updatesd", str(executable)], executable),
                (105, [str(executable), "--updatesd"], other),
            ):
                process = root / str(pid)
                process.mkdir()
                (process / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
                (process / "exe").symlink_to(target)
            script = persistence.UPDATER_PROBE.replace(
                "proc=/proc", "proc=" + shlex.quote(name)
            ).replace(persistence.Q + "/maind", shlex.quote(str(executable)))
            result = subprocess.run(  # nosec B603 B607 # local synthetic proc fixture through sh
                ["sh"], input=script, capture_output=True, text=True, check=True
            )
            self.assertEqual(result.stdout, "pid 101\n")

            (root / "101" / "cmdline").unlink()
            result = subprocess.run(  # nosec B603 B607 # local synthetic proc fixture through sh
                ["sh"], input=script, capture_output=True, text=True, check=True
            )
            self.assertEqual(result.stdout, "unknown\n")


if __name__ == "__main__":
    unittest.main()
