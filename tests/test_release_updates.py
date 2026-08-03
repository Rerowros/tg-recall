from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tg_recall.paths import AppRoots
from tg_recall.release_updates import (
    GITHUB_LATEST_RELEASE_URL,
    CachedRelease,
    GitHubReleaseChecker,
    HttpResponse,
    InstallationProvenance,
    ReleaseCache,
    ReleaseInfo,
    ReleaseTransportError,
    ReleaseUpdateError,
    ReleaseWheel,
    SemanticVersion,
    apply_verified_wheel,
    detect_installation_provenance,
    parse_github_release,
    stage_verified_wheel,
)


def _wheel_bytes(version: str = "0.6.0") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            f"tg_recall-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: tg-recall\nVersion: {version}\n",
        )
    return output.getvalue()


def _release(version: str = "0.6.0", payload: bytes | None = None) -> ReleaseInfo:
    payload = payload or _wheel_bytes(version)
    parsed = SemanticVersion.parse(version)
    name = f"tg_recall-{version}-py3-none-any.whl"
    return ReleaseInfo(
        version=parsed,
        tag=parsed.tag,
        url=f"https://github.com/Rerowros/tg-recall/releases/tag/{parsed.tag}",
        published_at="2026-08-01T12:00:00Z",
        wheel=ReleaseWheel(
            name=name,
            url=f"https://github.com/Rerowros/tg-recall/releases/download/{parsed.tag}/{name}",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        ),
    )


def _release_response(release: ReleaseInfo) -> bytes:
    return json.dumps(
        {
            "draft": False,
            "prerelease": False,
            "tag_name": release.tag,
            "html_url": release.url,
            "published_at": release.published_at,
            "assets": [
                {
                    "name": release.wheel.name,
                    "browser_download_url": release.wheel.url,
                    "digest": f"sha256:{release.wheel.sha256}",
                    "size": release.wheel.size_bytes,
                }
            ],
        }
    ).encode()


def _cache(tmp_path: Path) -> ReleaseCache:
    return ReleaseCache(AppRoots.resolve(tmp_path / "portable-home"))


def test_release_cache_uses_global_or_portable_cache_not_a_profile(tmp_path: Path) -> None:
    cache = _cache(tmp_path)

    assert cache.path == tmp_path / "portable-home" / "cache" / "updates" / "v1" / "latest.json"
    assert "profiles" not in cache.path.parts


@pytest.mark.parametrize("value", ["v0.6", "v0.6.0-rc1", "v0.6.0;rm", "v01.6.0"])
def test_stable_semver_is_strict(value: str) -> None:
    with pytest.raises(ReleaseUpdateError):
        SemanticVersion.parse(value)


def test_release_parser_requires_one_exact_wheel_and_github_digest() -> None:
    release = _release()
    parsed = parse_github_release(_release_response(release))

    assert parsed == release
    malicious = json.loads(_release_response(release))
    malicious["assets"][0]["browser_download_url"] = "https://example.test/wheel.whl"
    with pytest.raises(ReleaseUpdateError):
        parse_github_release(json.dumps(malicious).encode())

    malicious = json.loads(_release_response(release))
    malicious["assets"].append(malicious["assets"][0].copy())
    with pytest.raises(ReleaseUpdateError):
        parse_github_release(json.dumps(malicious).encode())


def test_checker_uses_fixed_endpoint_etag_and_fresh_global_cache(tmp_path: Path) -> None:
    release = _release()
    calls: list[tuple[str, dict[str, str]]] = []

    def http_get(url: str, headers: dict[str, str], timeout: float, maximum: int) -> HttpResponse:
        calls.append((url, dict(headers)))
        assert timeout > 0
        assert maximum > 0
        return HttpResponse(200, {"ETag": '"release-1"'}, _release_response(release))

    checker = GitHubReleaseChecker(_cache(tmp_path), http_get=http_get)
    first = checker.check("0.5.0")
    second = checker.check("0.5.0")

    assert first.status == "update_available"
    assert first.cache_status == "network"
    assert second.status == "update_available"
    assert second.cache_status == "fresh"
    assert len(calls) == 1
    assert calls[0][0] == GITHUB_LATEST_RELEASE_URL
    assert calls[0][1]["Accept"] == "application/vnd.github+json"


def test_checker_revalidates_stale_cache_with_etag(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    release = _release()
    cache.store(CachedRelease(release, "2026-07-01T00:00:00Z", '"old"'))
    seen: dict[str, str] = {}

    def http_get(url: str, headers: dict[str, str], timeout: float, maximum: int) -> HttpResponse:
        seen.update(headers)
        return HttpResponse(304, {}, b"")

    checker = GitHubReleaseChecker(
        cache,
        http_get=http_get,
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
        cache_ttl=timedelta(hours=1),
    )

    result = checker.check("0.5.0")

    assert result.status == "update_available"
    assert result.cache_status == "revalidated"
    assert seen["If-None-Match"] == '"old"'


def test_checker_returns_stable_result_when_cache_is_locked_or_acl_protected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    release = _release()

    def reject_store(*args: object, **kwargs: object) -> None:
        raise PermissionError("cache is locked")

    monkeypatch.setattr(cache, "store", reject_store)
    uncached = GitHubReleaseChecker(
        cache,
        http_get=lambda *_: HttpResponse(200, {"ETag": '"new"'}, _release_response(release)),
    ).check("0.5.0")

    assert uncached.status == "update_available"
    assert uncached.cache_status == "network_uncached"
    assert uncached.as_json()["status"] == "update_available"

    persisted = _cache(tmp_path / "persisted")
    persisted.store(CachedRelease(release, "2026-07-01T00:00:00Z", '"old"'))
    monkeypatch.setattr(persisted, "store", reject_store)
    stale = GitHubReleaseChecker(
        persisted,
        http_get=lambda *_: HttpResponse(304, {}, b""),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
        cache_ttl=timedelta(hours=1),
    ).check("0.5.0")

    assert stale.status == "update_available"
    assert stale.cache_status == "stale"
    assert stale.network_attempted is True


def test_checker_has_safe_stale_and_offline_fallbacks(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.store(CachedRelease(_release(), "2026-07-01T00:00:00Z"))
    checker = GitHubReleaseChecker(
        cache,
        http_get=lambda *_: (_ for _ in ()).throw(ReleaseTransportError("down")),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
        cache_ttl=timedelta(hours=1),
    )

    stale = checker.check("0.5.0")
    offline = checker.check("0.5.0", offline=True)

    assert (stale.status, stale.cache_status, stale.network_attempted) == ("update_available", "stale", True)
    assert (offline.status, offline.cache_status, offline.network_attempted) == ("update_available", "stale", False)


def test_checker_rejects_invalid_or_oversized_network_response_without_false_success(tmp_path: Path) -> None:
    invalid = GitHubReleaseChecker(_cache(tmp_path), http_get=lambda *_: HttpResponse(200, {}, b"{}"))
    oversized = GitHubReleaseChecker(_cache(tmp_path), http_get=lambda *args: HttpResponse(200, {}, b"x" * (args[3] + 1)))

    assert invalid.check("0.5.0").status == "unsupported"
    assert oversized.check("0.5.0").status == "unsupported"


class _FakeDistribution:
    def __init__(self, version: str, direct_url: str | None) -> None:
        self.version = version
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return self._direct_url


def test_provenance_allows_only_non_editable_uv_tool_github_wheel(tmp_path: Path) -> None:
    release = _release()
    tool_prefix = tmp_path / "tools" / "tg-recall"
    github = _FakeDistribution("0.5.0", json.dumps({"url": release.wheel.url}))
    supported = detect_installation_provenance(
        distribution=github, executable_prefix=tool_prefix, find_uv=lambda _: "C:/bin/uv.exe"
    )
    editable = detect_installation_provenance(
        distribution=_FakeDistribution("0.5.0", json.dumps({"url": "file:///work", "dir_info": {"editable": True}})),
        executable_prefix=tool_prefix,
        find_uv=lambda _: "uv",
    )
    index = detect_installation_provenance(
        distribution=_FakeDistribution("0.5.0", None), executable_prefix=tool_prefix, find_uv=lambda _: "uv"
    )

    assert supported.kind == "uv_tool_github_wheel"
    assert supported.supported_for_apply is True
    assert editable.kind == "editable"
    assert editable.supported_for_apply is False
    assert index.kind == "index_or_system"
    assert "tool_environment" not in supported.as_json()
    assert editable.manual_action == ("Reinstall from the original editable checkout path.",)


def test_stage_verifies_digest_size_and_wheel_metadata_before_writing(tmp_path: Path) -> None:
    payload = _wheel_bytes()
    release = _release(payload=payload)
    cache = _cache(tmp_path)

    staged = stage_verified_wheel(cache, release, lambda url, maximum: payload)

    assert staged.read_bytes() == payload
    with pytest.raises(ReleaseUpdateError):
        stage_verified_wheel(cache, release, lambda url, maximum: payload + b"modified")
    assert list(cache.artifacts_dir.glob("*.whl")) == [staged]


def test_apply_uses_fixed_argv_and_never_reports_windows_lock_or_version_mismatch_as_success(tmp_path: Path) -> None:
    payload = _wheel_bytes()
    release = _release(payload=payload)
    cache = _cache(tmp_path)
    artifact = stage_verified_wheel(cache, release, lambda url, maximum: payload)
    provenance = InstallationProvenance("uv_tool_github_wheel", "0.5.0", "uv", tmp_path / "tools" / "tg-recall", ())
    commands: list[list[str]] = []

    def good_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    applied = apply_verified_wheel(
        provenance,
        release,
        artifact,
        runner=good_runner,
        version_reader=lambda _: "0.6.0",
        release_cache=cache,
    )
    locked = apply_verified_wheel(
        provenance,
        release,
        artifact,
        runner=lambda _: (_ for _ in ()).throw(PermissionError("locked")),
        version_reader=lambda _: "0.6.0",
    )
    mismatch = apply_verified_wheel(provenance, release, artifact, runner=good_runner, version_reader=lambda _: "0.5.0")

    assert applied.status == "applied"
    assert commands[0] == ["uv", "tool", "install", "--force", str(artifact)]
    assert locked.status == "installer_failed"
    assert mismatch.status == "verification_failed"


def test_managed_cached_wheel_provenance_remains_supported_after_apply(tmp_path: Path) -> None:
    payload = _wheel_bytes()
    release = _release(payload=payload)
    cache = _cache(tmp_path)
    artifact = stage_verified_wheel(cache, release, lambda url, maximum: payload)
    tool_prefix = tmp_path / "tools" / "tg-recall"
    initial = InstallationProvenance("uv_tool_github_wheel", "0.5.0", "uv", tool_prefix, ())
    result = apply_verified_wheel(
        initial,
        release,
        artifact,
        runner=lambda command: subprocess.CompletedProcess(command, 0),
        version_reader=lambda _: "0.6.0",
        release_cache=cache,
    )
    next_install = detect_installation_provenance(
        distribution=_FakeDistribution("0.6.0", json.dumps({"url": artifact.as_uri()})),
        executable_prefix=tool_prefix,
        find_uv=lambda _: "uv",
        release_cache=cache,
    )
    arbitrary = detect_installation_provenance(
        distribution=_FakeDistribution("0.6.0", json.dumps({"url": (tmp_path / "other.whl").as_uri()})),
        executable_prefix=tool_prefix,
        find_uv=lambda _: "uv",
        release_cache=cache,
    )

    assert result.status == "applied"
    assert next_install.kind == "uv_tool_github_wheel"
    assert next_install.supported_for_apply is True
    assert arbitrary.kind == "local_file"
