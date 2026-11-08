"""Manage the Station's native ADB guard and OTA launcher over root ADB."""

import argparse
import copy
import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

Q = "/system/vendor/quasar"
ADB_SCRIPT = "activate_adb.sh"
ADB_BACKUP = "activate_adb.root_original.sh"
LAUNCHER = "launch.json"
LAUNCH_BACKUP = "launch.root_original.json"
LOCK = "/data/local/tmp/station-root.lock"
UPDATER_PROBE = f"""set -eu
proc=/proc
want=$(mktemp /tmp/station-updater.XXXXXX)
trap 'rm -f "$want"' EXIT
printf '%s\\000%s\\000' {Q}/maind --updatesd > "$want"
seen=0
for d in "$proc"/[0-9]*; do
    [ -d "$d" ] || continue
    seen=1
    if cmp -s "$d/cmdline" "$want"; then
        exe=$(readlink -f "$d/exe") || {{ echo unknown; exit 0; }}
        [ "$exe" = {Q}/maind ] || continue
        printf 'pid %s\\n' "${{d##*/}}"
    else
        code=$?
        if [ "$code" -gt 1 ] && [ -d "$d" ]; then echo unknown; exit 0; fi
    fi
done
[ "$seen" = 1 ] || echo unknown
"""
WRAPPER = """#!/bin/sh
case " $* " in
    *" deactivate "*) exit 0 ;;
    *" activate "*)
        case " $* " in
            *" --network "*) : ;;
            *) set -- "$@" --network ;;
        esac
        ;;
esac
exec {backup} "$@"
"""


def executable_lines(content):
    return [
        line.strip()
        for line in content.decode().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def native_adb(content):
    lines = executable_lines(content)
    if not lines or not re.fullmatch(
        r'\. "`dirname \$0\s*`/adb_options\.inc" \$\*', lines[0]
    ):
        return False
    body = "\n".join(lines[1:])
    return (
        re.fullmatch(
            r'if \[ "\$ARG_CMD" = "activate" \]; then\n'
            r'if \[ "\$ARG_NETWORK" = "true" \]; then\n'
            r"start-stop-daemon -S -b -x adbd\nelse\n"
            r'if \[ -f "\$MARKER_NETWORK" \]; then\n'
            r'start-stop-daemon -K -b -x adbd\nrm "\$MARKER_NETWORK"\nfi\n'
            r"/usr/bin/adb\.sh start\nfi\nelse\n"
            r"/usr/bin/adb\.sh stop\nstart-stop-daemon -K -b -x adbd\nfi",
            body,
        )
        is not None
    )


def wrapper_backup(content):
    lines = executable_lines(content)
    if not lines:
        return None
    match = re.fullmatch(
        rf'exec ({Q}/activate_adb\.[A-Za-z0-9_.-]*original\.sh) "\$@"',
        lines[-1],
    )
    if not match:
        return None
    backup = match[1]
    if lines != executable_lines(WRAPPER.format(backup=backup).encode()):
        return None
    return Path(backup).name


def unique_original(candidates):
    if len(set(candidates.values())) > 1:
        raise ValueError(
            "Conflicting original backups; leave files untouched and inspect them"
        )
    return next(iter(candidates), None)


def prepare_adb(files, restore):
    live = files[ADB_SCRIPT]
    reference = wrapper_backup(live)
    if reference:
        if reference not in files or not native_adb(files[reference]):
            raise ValueError(
                "ADB guard's referenced native backup is missing or unsupported"
            )
        return (files[reference] if restore else live), None, "guard installed"
    if not native_adb(live):
        raise ValueError("Unsupported ADB activation script")
    backups = {
        k: v
        for k, v in files.items()
        if k.startswith("activate_adb.") and k.endswith("original.sh")
    }
    if any(value != live for value in backups.values()):
        raise ValueError("ADB original backup differs from the live native script")
    if restore:
        return live, None, "native script"
    backup = unique_original(backups) or ADB_BACKUP
    replacement = WRAPPER.format(backup=f"{Q}/{backup}").encode()
    return replacement, None if backup in files else backup, "native script"


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key}")
        result[key] = value
    return result


def reject_number(value):
    raise ValueError(f"Unsupported launcher JSON number {value}")


def parse_launcher(content):
    document = json.loads(
        content,
        object_pairs_hook=unique_keys,
        parse_float=reject_number,
        parse_constant=reject_number,
    )
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("nodes"), list)
        or not document["nodes"]
    ):
        raise ValueError("Expected launcher nodes")
    for node in document["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("tasks"), list):
            raise TypeError("Expected launcher task lists")
        if any(
            not isinstance(task, dict) or not isinstance(task.get("name"), str)
            for task in node["tasks"]
        ):
            raise ValueError("Expected named launcher tasks")
    return document


def without_updater(document):
    result = copy.deepcopy(document)
    found = []
    for node in result["nodes"]:
        for task in node["tasks"]:
            if task.get("name") == "updatesd" or "--updatesd" in task.get("arg", []):
                if (
                    task.get("name") != "updatesd"
                    or task.get("path") != f"{Q}/maind"
                    or task.get("arg") != ["--updatesd"]
                ):
                    raise ValueError("Unsupported updater task")
                found.append((node, task))
    if len(found) > 1:
        raise ValueError("Ambiguous updater tasks")
    if found:
        node, task = found[0]
        node["tasks"].remove(task)
    return result, bool(found)


def same_json(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            same_json(left[k], right[k]) for k in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            same_json(a, b) for a, b in zip(left, right)
        )
    return left == right


def prepare_launcher(files, restore):
    live = parse_launcher(files[LAUNCHER])
    disabled, enabled = without_updater(live)
    backups = {
        k: v
        for k, v in files.items()
        if k.startswith("launch.") and k.endswith("original.json")
    }
    for content in backups.values():
        original = parse_launcher(content)
        expected, original_enabled = without_updater(original)
        if not original_enabled or not same_json(
            live, original if enabled else expected
        ):
            raise ValueError(
                "Launcher differs from its original backup; refusing replacement"
            )
    backup = unique_original(backups)
    if not enabled and not backup:
        raise ValueError("Updater absent without a usable original launcher backup")
    if restore:
        return (
            files[backup] if not enabled else files[LAUNCHER],
            None,
            "enabled" if enabled else "disabled",
        )
    if not enabled:
        return files[LAUNCHER], None, "disabled"
    content = (json.dumps(disabled, ensure_ascii=False, indent=2) + "\n").encode()
    return content, None if backup else LAUNCH_BACKUP, "enabled"


SNAPSHOT = f"""set -eu
[ "$(id -u)" = 0 ] || {{ echo 'Root ADB required' >&2; exit 1; }}
cd {Q}
set -- version_full activate_adb.sh launch.json
for f in activate_adb.*original.sh launch.*original.json; do
    if [ -e "$f" ] || [ -L "$f" ]; then set -- "$@" "$f"; fi
done
for f do
    [ -f "$f" ] && [ ! -L "$f" ] || {{ echo 'Expected regular native files' >&2; exit 1; }}
done
printf '%s\\n' "$@"
"""


def adb_run(adb, serial, *args):
    result = subprocess.run(
        [adb, "-s", serial, *args],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            f"ADB command failed: {result.stderr.decode(errors='replace').strip() or result.stdout.decode(errors='replace').strip()}"
        )
    return result.stdout


def adb_shell(adb, serial, script):
    command = (
        f"sh -c {shlex.quote(script)}; station_root_status=$?; "
        "printf '\\n__STATION_ROOT_STATUS__%s\\n' \"$station_root_status\""
    )
    output = adb_run(adb, serial, "shell", command)
    completion = re.search(rb"\r?\n__STATION_ROOT_STATUS__([0-9]+)\r?\n\Z", output)
    if completion is None:
        raise ValueError("ADB shell did not report command completion")
    content = output[: completion.start()].replace(b"\r\n", b"\n")
    if int(completion[1]):
        raise ValueError(
            f"Station command failed ({int(completion[1])}): "
            + content.decode(errors="replace").strip()
        )
    return content


def adb_read(adb, serial, path):
    with tempfile.TemporaryDirectory(prefix="station-root-") as local:
        destination = Path(local) / "file"
        adb_run(adb, serial, "pull", path, str(destination))
        return destination.read_bytes()


def read_snapshot(adb, serial):
    names = adb_shell(adb, serial, SNAPSHOT).decode().splitlines()
    files = {}
    for name in names:
        valid_name = name in ("version_full", ADB_SCRIPT, LAUNCHER) or re.fullmatch(
            r"(?:activate_adb\.[A-Za-z0-9_.-]*original\.sh|launch\.[A-Za-z0-9_.-]*original\.json)",
            name,
        )
        if not valid_name or name in files:
            raise ValueError("Unexpected native file in ADB snapshot")
        files[name] = adb_read(adb, serial, f"{Q}/{name}")
    if not {"version_full", ADB_SCRIPT, LAUNCHER} <= files.keys():
        raise ValueError("Incomplete Station snapshot")
    return files


def updater_status(adb, serial):
    try:
        lines = adb_shell(adb, serial, UPDATER_PROBE).decode().splitlines()
        if any(re.fullmatch(r"pid [1-9][0-9]*", line) is None for line in lines):
            return "unknown"
        pids = [line[4:] for line in lines]
        return "running (PID " + ", ".join(pids) + ")" if pids else "not observed"
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return "unknown"


def apply_change(adb, serial, files, destination, replacement, backup, markers):
    stage = (
        adb_shell(adb, serial, "mktemp -d /data/local/tmp/station-root.XXXXXX")
        .decode()
        .strip()
    )
    if not re.fullmatch(r"/data/local/tmp/station-root\.[A-Za-z0-9]+", stage):
        raise ValueError("Unexpected remote staging path")
    try:
        with tempfile.TemporaryDirectory(prefix="station-root-") as local:
            payload = Path(local) / "payload"
            payload.mkdir()
            for name, content in files.items():
                (payload / name).write_bytes(content)
            (payload / "replacement").write_bytes(replacement)
            adb_run(adb, serial, "push", str(payload), stage)
        if adb_read(adb, serial, stage + "/payload/replacement") != replacement:
            raise ValueError("Replacement upload differs from prepared content")
        script = f"""set -eu
q={Q}
stage={stage}/payload
lock={LOCK}
mkdir "$lock" || {{ echo 'Persistence lock exists; inspect it before removing it' >&2; exit 1; }}
remounted=false
new=''
cleanup() {{
    result=$?
    trap '' HUP INT TERM
    trap - EXIT
    if [ -n "$new" ]; then rm -f "$new" || result=1; fi
    if [ "$remounted" = true ]; then sync || result=1; mount -o remount,ro / || result=1; fi
    rmdir "$lock" || result=1
    exit "$result"
}}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
[ "$(id -u)" = 0 ]
cd "$q"
set -- version_full activate_adb.sh launch.json
for f in activate_adb.*original.sh launch.*original.json; do
    if [ -e "$f" ] || [ -L "$f" ]; then set -- "$@" "$f"; fi
done
[ "$#" = {len(files)} ] || {{ echo 'Native file set changed; retry' >&2; exit 1; }}
for f do
    [ -f "$f" ] && [ ! -L "$f" ] && cmp -s "$f" "$stage/$f" || {{ echo 'Native files changed; retry' >&2; exit 1; }}
done
"""
        if backup or replacement != files[destination]:
            script += """root_options=$(awk '$2 == "/" {print $4}' /proc/mounts)
case ",$root_options," in
    *,ro,*) remounted=true; mount -o remount,rw / ;;
    *,rw,*) : ;;
    *) echo 'Cannot determine root mount mode' >&2; exit 1 ;;
esac
new=$(mktemp "$q/.station-root.XXXXXX")
"""
        if backup:
            script += f"""[ ! -e {shlex.quote(backup)} ] && [ ! -L {shlex.quote(backup)} ]
cp -p {shlex.quote(destination)} "$new"
cmp -s "$new" "$stage/{destination}"
ln "$new" {shlex.quote(backup)}
rm "$new"
new=$(mktemp "$q/.station-root.XXXXXX")
sync
"""
        if destination == ADB_SCRIPT:
            script += 'sh -n "$stage/replacement"\n'
        if replacement != files[destination]:
            script += f"""cp -p {shlex.quote(destination)} "$new"
cat "$stage/replacement" > "$new"
cmp -s "$new" "$stage/replacement"
mv "$new" {shlex.quote(destination)}
sync
"""
        if markers:
            script += """mkdir -p /data/quasar
touch /data/quasar/activate_adb_quasmodrom /data/quasar/activate_network_adb
"""
        adb_shell(adb, serial, script)
        if adb_read(adb, serial, f"{Q}/{destination}") != replacement:
            raise ValueError(
                "Station file differs after installation; inspect before retrying"
            )
    finally:
        adb_shell(adb, serial, "rm -rf " + shlex.quote(stage))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument(
        "operation",
        choices=(
            "status",
            "install-adb",
            "restore-adb",
            "disable-updates",
            "restore-updates",
        ),
    )
    args = parser.parse_args()
    try:
        files = read_snapshot(args.adb, args.serial)
        if args.operation == "status":
            _, _, adb_state = prepare_adb(files, restore=True)
            _, _, ota_state = prepare_launcher(files, restore=True)
            print("Firmware " + files["version_full"].decode().strip())
            print("ADB " + adb_state)
            print("OTA " + ota_state + " in launcher configuration")
            print("Updater in this boot: " + updater_status(args.adb, args.serial))
            markers = adb_shell(
                args.adb,
                args.serial,
                'for m in activate_adb_quasmodrom activate_network_adb; do if [ -f "/data/quasar/$m" ]; then echo "$m present"; else echo "$m missing"; fi; done\n',
            )
            print(markers.decode().strip())
            return
        is_adb = args.operation.endswith("adb")
        destination = ADB_SCRIPT if is_adb else LAUNCHER
        prepare = prepare_adb if is_adb else prepare_launcher
        replacement, backup, _ = prepare(
            files, restore=args.operation.startswith("restore")
        )
        apply_change(
            args.adb,
            args.serial,
            files,
            destination,
            replacement,
            backup,
            args.operation == "install-adb",
        )
        if is_adb:
            detail = "; activation markers retained"
        elif replacement != files[destination]:
            detail = "; reboot to apply the launcher change"
        else:
            detail = "; launcher unchanged"
        print("Applied " + args.operation + detail)
        if not is_adb:
            print("Updater in this boot: " + updater_status(args.adb, args.serial))
    except (
        OSError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
