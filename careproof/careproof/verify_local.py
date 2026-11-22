"""Local verification of dependencies, package isolation, and archived search replay."""
import argparse
from importlib.metadata import version
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

from careproof.snapshot import ROOT, build_snapshot, digest, verify_snapshot

SECRET_PATTERNS = {
    "Databricks PAT": re.compile(rb"\bdapi[0-9a-fA-F]{32,}\b"),
    "Private key": re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def scan(paths, root):
    findings = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        findings.extend((str(path.relative_to(root)), label) for label, value in SECRET_PATTERNS.items()
                        if value.search(data))
    return findings


def isolated_import(snapshot):
    for module in snapshot.rglob("*.py"):
        compile(module.read_bytes(), str(module), "exec")
    subprocess.run([sys.executable, "-I", "-B", "-c",
                    "import sys; from pathlib import Path; "
                    "root = Path(sys.argv[1]); sys.path.insert(0, str(root)); "
                    "import data, decision_trace; from careproof import recommend, review_holds; "
                    "assert Path(recommend.__file__).is_relative_to(root); "
                    "assert len(review_holds.load_review_holds()) == 6; "
                    "assert len(review_holds.load_coordinate_holds()) == 2",
                    str(snapshot)], check=True, cwd=snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", action="store_true", help="Compare a fresh Sprint 2 replay with its archive")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 14):
        raise SystemExit("The local lock targets Python 3.14")
    for line in (ROOT / "requirements-local-py314.lock").read_text().splitlines():
        if line and not line.startswith("#"):
            name, expected = line.split("==")
            if version(name) != expected:
                raise SystemExit(f"Local lock mismatch for {name}. Expected {expected}.")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], check=True, cwd=ROOT)
    with TemporaryDirectory(prefix="careproof-ci-") as directory:
        temp = Path(directory)
        snapshot = temp / "app"
        build_snapshot(snapshot)
        verify_snapshot(snapshot)
        findings = scan(snapshot.rglob("*"), snapshot)
        if findings:
            raise SystemExit(f"Secret indicators in app snapshot: {findings}")
        isolated_import(snapshot)
        source_files = [p for directory in ("careproof", "app", "careproof_agent", "sql", "tests")
                        for p in (ROOT / directory).rglob("*")
                        if p.suffix in {".py", ".sql", ".yaml", ".toml"}
                        and ".databricks" not in p.relative_to(ROOT).parts]
        if findings := scan(source_files, ROOT):
            raise SystemExit(f"Secret indicators in project source: {findings}")
        if args.replay:
            output = temp / "replay"
            subprocess.run([sys.executable, "-m", "careproof.sprint2", "--output", str(output)], check=True, cwd=ROOT)
            archive = ROOT / "reports/engineering_hardening/sprint2"
            actual = sorted(output.iterdir())
            mismatches = [path.name for path in actual if digest(path) != digest(archive / path.name)]
            if mismatches:
                raise SystemExit(f"Replay artifacts diverged: {mismatches}")
            print(f"Replay matched all {len(actual)} generated files")
    print("Dependency, test, snapshot import and secret-pattern checks passed")


if __name__ == "__main__":
    main()
