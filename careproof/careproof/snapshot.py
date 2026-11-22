"""Build a local Databricks App snapshot using canonical ranking files."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = (
    "attribution.py", "location.py", "recommend.py", "review_holds.py",
    "coordinate_holds.json", "review_holds.json",
)
APP_FILES = ("app.py", "data.py", "decision_trace.py", "app.yaml", "requirements.txt",
             ".streamlit/config.toml", "careproof/__init__.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_snapshot(directory):
    """Check the exact artifact contents against its build manifest."""
    directory = Path(directory)
    expected = set(APP_FILES) | {f"careproof/{name}" for name in CANONICAL}
    entries = list(directory.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError("Snapshot contains a symbolic link")
    files = {path.relative_to(directory).as_posix() for path in entries if path.is_file()}
    if files != expected | {"snapshot_manifest.json"}:
        raise ValueError("Snapshot file set differs from the deployment allowlist")
    manifest = json.loads((directory / "snapshot_manifest.json").read_text(encoding="utf-8"))
    if manifest != {name: digest(directory / name) for name in sorted(expected)}:
        raise ValueError("Snapshot hashes differ from the build manifest")
    return manifest


def build_snapshot(destination):
    """Stage only deployable files. Refuse to replace an existing directory."""
    destination = Path(destination).resolve()
    if destination.is_relative_to(ROOT / "app"):
        raise ValueError("Choose a snapshot destination outside the source app directory")
    sources = {name: ROOT / "app" / name for name in APP_FILES}
    sources.update({f"careproof/{name}": ROOT / "careproof" / name for name in CANONICAL})
    hashes = {name: digest(source) for name, source in sorted(sources.items())}
    destination.mkdir(parents=True)
    for name, source in sources.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if digest(target) != hashes[name]:
            raise ValueError(f"Snapshot input changed during copy: {name}")
    (destination / "snapshot_manifest.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    return hashes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--verify", action="store_true", help="Verify an existing artifact")
    args = parser.parse_args()
    operation = verify_snapshot if args.verify else build_snapshot
    print(json.dumps(operation(args.destination), indent=2))
