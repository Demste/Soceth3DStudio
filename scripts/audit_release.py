"""Audit source and history without printing matched secret values."""

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DIRS = {"apps", "docs", "integrations", "scripts", "services", "shared", "tests", ".github"}
ROOT_FILES = {"README.md", "ARCHITECTURE.md", "SETUP.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "NOTICE",
              "SECURITY.md", "requirements.txt", "requirements.lock.txt", ".gitignore", ".gitattributes",
              "Soceth 3D Studio.cmd", "setup.ps1", "start.ps1", "stop.ps1", "verify.ps1", "update.ps1",
              "upgrade-release.ps1", "dev.ps1"}
BLOCKED = {"runtime", "user_data", "node_modules", "__pycache__", "dist", ".venv", "venv", "logs", "build"}
SOURCE_SUFFIXES = {".py", ".cjs", ".ts", ".tsx", ".css", ".json", ".yml", ".yaml", ".md", ".cs", ".txt", ".html", ".ps1"}
PATTERNS = {
    "private key": rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----",
    "GitHub credential": rb"\b(?:gh[pousr]_[A-Za-z0-9]{35,}|github_pat_[A-Za-z0-9_]{50,})",
    "provider credential": rb"\b(?:hf_[A-Za-z0-9]{28,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|AKIA[A-Z0-9]{16})",
    "machine-specific path": rb"(?i)(?:[a-z]:[/\\]users[/\\][a-z0-9][a-z0-9_.-]{1,}[/\\]|/(?:home|Users)/[A-Za-z0-9_.-]{2,}/)",
}


def root_path(name):
    item = PurePosixPath(name)
    if (not item.parts or item.is_absolute() or any(part in BLOCKED or part == ".." for part in item.parts)):
        return False
    return name in ROOT_FILES or (item.parts[0] in DIRS and (
        item.suffix.lower() in SOURCE_SUFFIXES or name == "integrations/blender_addon/LICENSE"))


def problems(name, blob):
    errors = []
    if not root_path(name):
        errors.append("path outside the public source allowlist")
    if len(blob) > 8 * 1024 ** 2 or b"\0" in blob:
        errors.append("large or binary content")
    for label, pattern in PATTERNS.items():
        if re.search(pattern, blob):
            errors.append(label)
    return errors


def source_paths():
    for key in ROOT_FILES:
        if (ROOT / key).is_file():
            yield key
    for dirname in DIRS:
        for path in (ROOT / dirname).rglob("*"):
            relative = path.relative_to(ROOT).as_posix()
            if path.is_file() and root_path(relative):
                yield relative


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def audit(*, source=False, history=False):
    if source:
        files = sorted(source_paths())
        rows = [(name, (ROOT / name).read_bytes()) for name in files]
    else:
        files = [name.decode("utf-8") for name in git("ls-files", "-z").split(b"\0") if name]
        rows = [(name, git("show", ":" + name)) for name in files]
    issues = [(name, error) for name, blob in rows for error in problems(name, blob)]
    historical = 0
    if history:
        for line in git("rev-list", "--objects", "--all").decode("utf-8").splitlines():
            key, sep, name = line.partition(" ")
            if sep and git("cat-file", "-t", key).strip() == b"blob":
                historical += 1
                issues.extend((name, error) for error in problems(name, git("cat-file", "blob", key)))
    if issues:
        for name, reason in issues:
            print(f"{name}: {reason}")
        raise SystemExit("Release audit failed.")
    print(f"Release audit passed: {len(files)} source files, {historical} historical blobs.")
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="store_true")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    audit(source=args.source, history=args.history)
