from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDIST_FILES = {
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "ROADMAP.md",
    ".gitignore",
    "pyproject.toml",
    "uv.lock",
    "PKG-INFO",
}
SDIST_PREFIXES = ("src/tg_recall/", "tests/", "docs/")
FORBIDDEN_PATH_PARTS = (
    ".tg-ecosystem",
    ".tg-recall",
    ".codex",
    "exports",
    "reports",
    ".session",
    ".sqlite",
    ".db",
    ".log",
    ".pid",
    ".mp3",
    ".m4a",
    ".ogg",
    ".wav",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".srt",
    ".webp",
    "wiki/raw",
    "wiki/pages",
    "wiki/revisions",
)
FORBIDDEN_GENERATED_PATTERNS = (
    "/cache/updates/v1/latest.json",
    "/cache/updates/v1/artifacts/",
    "/state/updates/v1/managed-install.json",
    "/state/integrations/",
    "/integration-state/",
    ".tg-recall-backup-",
)
FORBIDDEN_HARNESS_ARTIFACTS = {
    "agents.md",
    "claude.md",
    ".mcp.json",
    ".cursor/mcp.json",
    ".cursor/rules/tg-recall.mdc",
}


def _build_distributions(output_dir: Path) -> tuple[Path, Path]:
    subprocess.run(
        ["uv", "build", "--out-dir", str(output_dir)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    sdist = next(output_dir.glob("tg_recall-*.tar.gz"))
    wheel = next(output_dir.glob("tg_recall-*.whl"))
    return sdist, wheel


def _assert_no_private_paths(names: list[str]) -> None:
    for name in names:
        lowered = "/" + name.replace("\\", "/").lower().lstrip("/")
        assert not any(part in lowered for part in FORBIDDEN_PATH_PARTS), name
        assert not any(pattern in lowered for pattern in FORBIDDEN_GENERATED_PATTERNS), name
        relative = lowered.lstrip("/")
        assert relative not in FORBIDDEN_HARNESS_ARTIFACTS, name


def test_release_distributions_contain_only_public_files(tmp_path: Path) -> None:
    sdist, wheel = _build_distributions(tmp_path)

    with tarfile.open(sdist) as archive:
        members = [member.name for member in archive.getmembers() if member.isfile()]
    root_prefix = members[0].split("/", 1)[0] + "/"
    relative_members = [member.removeprefix(root_prefix) for member in members]
    _assert_no_private_paths(relative_members)
    assert all(
        member in SDIST_FILES or member.startswith(SDIST_PREFIXES)
        for member in relative_members
    ), relative_members

    with zipfile.ZipFile(wheel) as archive:
        wheel_members = archive.namelist()
    _assert_no_private_paths(wheel_members)
    assert all(
        member.startswith("tg_recall/")
        or (member.startswith("tg_recall-") and ".dist-info/" in member)
        for member in wheel_members
    ), wheel_members
