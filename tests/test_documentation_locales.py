from __future__ import annotations

import re
import shlex
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RUSSIAN_DOCS = (
    Path("README.ru.md"),
    Path("SECURITY.ru.md"),
    Path("CONTRIBUTING.ru.md"),
    Path("CHANGELOG.ru.md"),
    Path("ROADMAP.ru.md"),
    Path("docs/archive-maintenance.ru.md"),
    Path("docs/backup-restore.ru.md"),
    Path("docs/local-transcription.ru.md"),
    Path("docs/compatibility.ru.md"),
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCED_BLOCK = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)
EXTERNAL_URL = re.compile(r"https?://[^\s)`>]+")
HEADING_PREFIX = re.compile(r"^(#{1,6})\s", re.MULTILINE)
INLINE_CODE = re.compile(r"(?<!`)`([^`]+)`(?!`)")
TRANSLATION_PAIRS = (
    (Path("README.md"), Path("README.ru.md")),
    (Path("SECURITY.md"), Path("SECURITY.ru.md")),
    (Path("CONTRIBUTING.md"), Path("CONTRIBUTING.ru.md")),
    (Path("CHANGELOG.md"), Path("CHANGELOG.ru.md")),
    (Path("ROADMAP.md"), Path("ROADMAP.ru.md")),
    (Path("docs/archive-maintenance.md"), Path("docs/archive-maintenance.ru.md")),
    (Path("docs/backup-restore.md"), Path("docs/backup-restore.ru.md")),
    (Path("docs/local-transcription.md"), Path("docs/local-transcription.ru.md")),
    (Path("docs/compatibility.md"), Path("docs/compatibility.ru.md")),
)


def _read(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_required_russian_public_documents_exist() -> None:
    missing = [str(path) for path in REQUIRED_RUSSIAN_DOCS if not (ROOT / path).is_file()]

    assert not missing
    for path in REQUIRED_RUSSIAN_DOCS:
        text = _read(path)
        assert len(text) > 200, path
        assert re.search(r"[А-Яа-яЁё]", text), path


def test_readmes_have_reciprocal_navigation() -> None:
    english = _read(Path("README.md"))
    russian = _read(Path("README.ru.md"))

    assert any("README.ru.md" in line for line in english.splitlines()[:8])
    assert any("README.md" in line for line in russian.splitlines()[:8])


def test_translations_preserve_every_fenced_command_and_contract_block() -> None:
    for canonical, translation in TRANSLATION_PAIRS:
        assert FENCED_BLOCK.findall(_read(canonical)) == FENCED_BLOCK.findall(_read(translation)), translation


def test_documented_local_transcription_commands_parse() -> None:
    from tg_recall.cli import build_parser

    commands = (
        "tg-recall --json doctor",
        "tg-recall --json transcribe run --provider local --citation tg://chat/-1001234567890/message/42 --limit 1",
    )
    parser = build_parser()
    for document in (Path("docs/local-transcription.md"), Path("docs/local-transcription.ru.md")):
        text = _read(document)
        for command in commands:
            assert command in text, (document, command)
            parser.parse_args(command.split()[1:])


def test_public_markdown_places_json_before_the_subcommand() -> None:
    public_documents = (*ROOT.glob("README*.md"), *(ROOT / "docs").rglob("*.md"))
    for document in public_documents:
        for line in document.read_text(encoding="utf-8").splitlines():
            command_start = line.find("tg-recall ")
            if command_start < 0:
                continue
            command = line[command_start:].rstrip("` .")
            if "--json" in command:
                assert command.startswith("tg-recall --json "), (document, line)


def test_translations_preserve_section_structure_and_inline_identifiers() -> None:
    for canonical, translation in TRANSLATION_PAIRS:
        canonical_text = _read(canonical)
        translated_text = _read(translation)
        assert HEADING_PREFIX.findall(canonical_text) == HEADING_PREFIX.findall(translated_text), translation
        canonical_prose = FENCED_BLOCK.sub("", canonical_text)
        translated_prose = FENCED_BLOCK.sub("", translated_text)
        canonical_identifiers = sorted(re.sub(r"\s+", " ", item) for item in INLINE_CODE.findall(canonical_prose))
        translated_identifiers = sorted(re.sub(r"\s+", " ", item) for item in INLINE_CODE.findall(translated_prose))
        assert canonical_identifiers == translated_identifiers, translation


def test_translations_preserve_external_urls() -> None:
    for canonical, translation in TRANSLATION_PAIRS:
        assert set(EXTERNAL_URL.findall(_read(canonical))) == set(EXTERNAL_URL.findall(_read(translation))), translation


def test_repository_local_links_in_localized_documents_resolve() -> None:
    localized_documents = {path for pair in TRANSLATION_PAIRS for path in pair}
    for relative_document in localized_documents:
        document = ROOT / relative_document
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            resolved = (document.parent / target).resolve()
            assert resolved.is_relative_to(ROOT.resolve()), (relative_document, raw_target)
            assert resolved.exists(), (relative_document, raw_target)


def test_russian_documents_do_not_fall_back_to_english_when_a_translation_exists() -> None:
    for relative_document in REQUIRED_RUSSIAN_DOCS:
        document = ROOT / relative_document
        text = document.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(text):
            raw_target = match.group(1)
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            resolved = (document.parent / target).resolve()
            if resolved.name.endswith(".ru.md"):
                continue
            russian_peer = resolved.with_name(f"{resolved.stem}.ru.md")
            line_number = text.count("\n", 0, match.start()) + 1
            assert not russian_peer.exists() or line_number <= 8, (relative_document, raw_target, line_number)


def test_documented_cli_commands_parse() -> None:
    from tg_recall.cli import _normalize_global_arguments, build_parser

    parser = build_parser()
    documents = (*ROOT.glob("README*.md"), *(ROOT / "docs").rglob("*.md"))
    checked = 0
    for document in documents:
        for _, block in FENCED_BLOCK.findall(document.read_text(encoding="utf-8")):
            for line in block.splitlines():
                if line.startswith("tg-recall "):
                    argv = shlex.split(line, posix=False)[1:]
                    parser.parse_args(_normalize_global_arguments(argv))
                    checked += 1
    assert checked > 20
