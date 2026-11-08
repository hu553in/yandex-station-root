"""Check local source, embedded device shell, and the frozen firmware manifest."""

import ast
import json

# Uses only /bin/sh -n, which parses bundled shell without executing it.
import subprocess  # nosec B404
from pathlib import Path
from urllib.parse import urlsplit

from firmware_offer import validate_manifest
from persistence import SNAPSHOT, UPDATER_PROBE, WRAPPER


def main() -> None:
    root = Path(__file__).resolve().parent
    for path in root.glob("*.py"):
        ast.parse(path.read_text(), filename=str(path))
    scripts = (
        SNAPSHOT,
        UPDATER_PROBE,
        WRAPPER.format(backup="/system/vendor/quasar/activate_adb.root_original.sh"),
    )
    for script in scripts:
        subprocess.run(  # nosec B603
            ["/bin/sh", "-n"], input=script, text=True, check=True
        )
    content = (root / "firmware/update_manifest.json").read_bytes()
    platform = urlsplit(json.loads(content)["downloadUrl"]).path.split("/")[1]
    validate_manifest(content, platform)
    print("Source syntax and firmware manifest verified")


if __name__ == "__main__":
    main()
