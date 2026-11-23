#!/usr/bin/env python3
"""Check public repository boundaries without printing matched private values.

The default examines the working candidate, excluding local build/cache trees.
Use --tracked before pushing to inspect exactly the current Git index paths.
This is a local guard, not a content license or exhaustive security audit.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TREES = {".git", "build", "build-demo", "node_modules", "__pycache__", ".pytest_cache"}
PUBLIC_TREES = {".github", "assets", "content", "docs", "fixtures", "schemas", "scripts", "skills", "tests"}
PUBLIC_ROOT_FILES = {
    ".gitignore", ".gitattributes", ".python-version", "AGENTS.md", "CLAUDE.md",
    "CONTRIBUTING.md", "README.md", "LICENSE", "LICENSE.md", "NOTICE", "NOTICE.md",
    "sat-content-registry.json", "sat-set-registry.json", "sat-practice-migrations.json",
    "sat-release.json",
}
FORBIDDEN_TREES = {
    "runs", "audits", "reports", "00_待处理来源", "01_完整套题", "02_按题型分类",
    "03_刷题HTML汇总", "04_分类报告", "2025年 sat", "_过期文件归档",
    ".cloudbase", ".tcb", "node_modules",
}
FORBIDDEN_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".zip", ".7z", ".rar", ".tar", ".gz",
    ".mp4", ".mov", ".mp3", ".pem", ".key", ".p12", ".pfx", ".command",
    ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
RESOURCE_MANIFEST = "assets/content-resources.json"
ZEN_IMAGES = {"background.svg", "texture.svg", "pattern.svg"}
TEXT_SUFFIXES = {".py", ".json", ".jsonl", ".sql", ".html", ".js", ".mjs", ".ts", ".tsx", ".md", ".svg", ".yml", ".yaml", ".txt", ""}
SYNTHETIC_USERS = {"reviewer", "example", "test", "test-user", "fixture", "alice", "bob"}
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}
EMAIL = re.compile(r"(?i)(?<![\w+.-])[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})")
LOCAL_USER = re.compile(r"(?:/Users/|/home/)([A-Za-z0-9_. -]+)(?=/)")
WINDOWS_USER = re.compile(r"(?i)[A-Z]:\\Users\\([^\\\s\"'<>]+)")
PHONE = re.compile(r"(?<![\w])(?:\+86[- ]?)?1[3-9]\d{9}(?![\w])")
SECRETS = [
    re.compile(r"-----BEGIN [^-\n]*PRIVATE KEY-----"),
    re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{30,}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9_])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bAKID[A-Za-z0-9]{28,36}\b"),
    re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key|secretaccesskey|access[_-]?token|password)\s*[:=]\s*[\"'][A-Za-z0-9_+/=-]{20,}[\"']"),
    re.compile(r"(?i)(?:X-Amz-Signature|q-signature|access_token)=[A-Za-z0-9%_+/=-]{20,}"),
]
IMAGE_DATA = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+")


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    line: int | None = None


def candidate_paths(root: Path, tracked: bool = False) -> list[Path]:
    if tracked:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                                capture_output=True, check=True)
        return [root / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]
    files = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if set(relative.parts) & LOCAL_TREES:
            continue
        if path.is_file() or path.is_symlink():
            if path.name not in {".DS_Store"}:
                files.append(path)
    return sorted(files)


def image_path_allowed(relative: Path, approved_media: dict | None = None) -> bool:
    parts = relative.parts
    return ((parts[:2] in {("assets", "question-media"), ("categories", "chart_images")}
                and relative.as_posix() in (approved_media or {}))
            or (len(parts) == 3 and parts[:2] == ("assets", "zen-interface") and parts[-1] in ZEN_IMAGES)
            or (len(parts) == 5
                and parts[:4] == ("skills", "skill-sat-rw-classification", "assets", "zen-interface")
                and parts[-1] in ZEN_IMAGES))


def inspect_file(root: Path, path: Path, max_bytes: int, approved_media: dict | None = None) -> list[Finding]:
    relative = path.relative_to(root)
    name = relative.as_posix()
    findings: list[Finding] = []

    def add(category: str, line: int | None = None) -> None:
        value = Finding(name, category, line)
        if value not in findings:
            findings.append(value)

    if path.is_symlink():
        add("symlink-not-allowed")
        return findings
    if not path.is_file():
        add("indexed-file-missing")
        return findings
    if relative.parts[0] in FORBIDDEN_TREES:
        add("raw-history-or-private-tree")
    approved_category_image = (relative.parts[:2] == ("categories", "chart_images")
                               and name in (approved_media or {}))
    if (relative.parts[0] not in PUBLIC_TREES and name not in PUBLIC_ROOT_FILES
            and not approved_category_image):
        add("outside-public-allowlist")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        add("excluded-file-type")
    if path.name.startswith(".env") and path.name != ".env.example":
        add("local-environment-file")
    if re.search(r"(?i)credentials|service[-_]account|settings\.local", path.name):
        add("credential-or-local-settings-file")
    if path.stat().st_size > max_bytes:
        add("large-file")
    if path.suffix.lower() in IMAGE_SUFFIXES and not image_path_allowed(relative, approved_media):
        add("image-outside-essential-assets")
    if name in (approved_media or {}):
        expected = approved_media[name]
        payload = path.read_bytes()
        if (path.stat().st_size != expected.get("sizeBytes")
                or hashlib.sha256(payload).hexdigest() != expected.get("sha256")):
            add("essential-image-manifest-mismatch")
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            add("essential-image-not-png")
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        add("non-utf8-text-file")
        return findings
    # Binary images are separate assets. Inline image encodings are not text
    # credentials; preserve offsets so diagnostic line numbers remain useful.
    scanned = IMAGE_DATA.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)

    def line_at(offset: int) -> int:
        return scanned.count("\n", 0, offset) + 1

    for pattern in SECRETS:
        for match in pattern.finditer(scanned):
            add("possible-secret", line_at(match.start()))
    for match in EMAIL.finditer(scanned):
        domain = match.group(1).lower()
        if domain not in SAFE_EMAIL_DOMAINS and not domain.endswith((".test", ".invalid")):
            add("contact-email", line_at(match.start()))
    for pattern in (LOCAL_USER, WINDOWS_USER):
        for match in pattern.finditer(scanned):
            synthetic_test = "tests" in relative.parts and match.group(1).lower() in SYNTHETIC_USERS
            if not synthetic_test:
                add("personal-absolute-path", line_at(match.start()))
    for match in PHONE.finditer(scanned):
        add("possible-contact-phone", line_at(match.start()))
    return findings


def check_scope(root: Path, tracked: bool = False, max_bytes: int = 25 * 1024 * 1024) -> dict:
    paths = candidate_paths(root, tracked)
    approved_media = {}
    manifest_findings = []
    resource_manifest = root / RESOURCE_MANIFEST
    if resource_manifest.is_file():
        try:
            resources = json.loads(resource_manifest.read_text(encoding="utf-8"))["resources"]
            if not isinstance(resources, list):
                raise ValueError("Resource records must be a list")
            for resource in resources:
                if set(resource) != {"publicPath", "sha256", "sizeBytes"}:
                    raise ValueError("Resource records contain only path, hash and size")
                name = resource["publicPath"]
                relative = Path(name)
                if (relative.is_absolute() or ".." in relative.parts or name in approved_media
                        or len(relative.parts) != 3 or relative.as_posix() != name
                        or relative.suffix.lower() != ".png"
                        or relative.parts[:2] not in {("assets", "question-media"), ("categories", "chart_images")}):
                    raise ValueError("Unsafe or duplicate resource path")
                if (type(resource["sizeBytes"]) is not int or resource["sizeBytes"] < 1
                        or not re.fullmatch(r"[0-9a-f]{64}", resource["sha256"])):
                    raise ValueError("Invalid resource binding")
                approved_media[name] = resource
        except (OSError, ValueError, KeyError, TypeError):
            manifest_findings.append(Finding(RESOURCE_MANIFEST, "invalid-essential-image-manifest"))
            approved_media = {}
    findings = [*manifest_findings,
                *[item for path in paths for item in inspect_file(root, path, max_bytes, approved_media)]]
    checked_names = {p.relative_to(root).as_posix() for p in paths}
    if approved_media and RESOURCE_MANIFEST not in checked_names:
        findings.append(Finding(RESOURCE_MANIFEST, "essential-image-manifest-missing-from-scope"))
    findings.extend(Finding(name, "essential-image-missing-from-scope")
                    for name in approved_media if name not in checked_names)
    if tracked:
        # Inspecting clean worktree bytes is only sufficient when they are the
        # same bytes staged for the commit. Do not silently scan a newer safe
        # worktree over an older staged secret or stale resource manifest.
        changed = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "-z", "--"],
                                 capture_output=True, check=True)
        findings.extend(Finding(name.decode("utf-8"), "tracked-worktree-differs-from-index")
                        for name in changed.stdout.split(b"\0") if name)
    return {
        "mode": "tracked" if tracked else "working-candidate",
        "checkedFiles": len(paths),
        "checkedBytes": sum(p.stat().st_size for p in paths if p.is_file() and not p.is_symlink()),
        "passed": not findings,
        "findings": [asdict(item) for item in findings],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tracked", action="store_true")
    parser.add_argument("--max-file-mib", type=int, default=25)
    args = parser.parse_args()
    if args.max_file_mib < 1:
        parser.error("--max-file-mib must be positive")
    try:
        result = check_scope(args.root.resolve(), args.tracked, args.max_file_mib * 1024 * 1024)
    except (OSError, subprocess.CalledProcessError):
        print(json.dumps({"passed": False, "error": "Cannot enumerate the requested repository scope"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
