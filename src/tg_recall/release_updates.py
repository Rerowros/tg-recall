"""Private-data-free GitHub Release discovery and conservative ``uv`` updates.

This module intentionally does not import ``config``, ``storage``, Telegram,
or profile paths.  It deals only with public release metadata, the global
application cache, installed distribution metadata, and a verified wheel.
CLI policy and human confirmation belong at the command boundary.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, url2pathname

from .paths import AppRoots


UPDATE_SCHEMA_VERSION = 1
GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/Rerowros/tg-recall/releases/latest"
GITHUB_RELEASE_DOWNLOAD_PREFIX = "https://github.com/Rerowros/tg-recall/releases/download/"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_RELEASE_RESPONSE_BYTES = 128 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024
_SEMVER_RE = re.compile(r"^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")


class ReleaseUpdateError(ValueError):
    """Release metadata, cache, provenance, or artifact is unsafe."""


class ReleaseTransportError(ReleaseUpdateError):
    """A bounded release request could not be completed."""


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        match = _SEMVER_RE.fullmatch(value) if isinstance(value, str) else None
        if match is None:
            raise ReleaseUpdateError("release version must be stable vMAJOR.MINOR.PATCH")
        return cls(*(int(item) for item in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def tag(self) -> str:
        return f"v{self}"


@dataclass(frozen=True)
class ReleaseWheel:
    name: str
    url: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.endswith(".whl"):
            raise ReleaseUpdateError("release wheel name is invalid")
        if not _safe_github_download_url(self.url):
            raise ReleaseUpdateError("release wheel URL is not an exact GitHub Release download URL")
        if not _DIGEST_RE.fullmatch(f"sha256:{self.sha256}"):
            raise ReleaseUpdateError("release wheel SHA-256 is invalid")
        if not isinstance(self.size_bytes, int) or not 0 < self.size_bytes <= MAX_WHEEL_BYTES:
            raise ReleaseUpdateError("release wheel size is invalid")

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "sha256": self.sha256, "size_bytes": self.size_bytes, "url": self.url}


@dataclass(frozen=True)
class ReleaseInfo:
    version: SemanticVersion
    tag: str
    url: str
    published_at: str
    wheel: ReleaseWheel

    def __post_init__(self) -> None:
        if self.tag != self.version.tag:
            raise ReleaseUpdateError("release tag does not match release version")
        if not _safe_release_page_url(self.url):
            raise ReleaseUpdateError("release page URL is invalid")
        _parse_timestamp(self.published_at)
        expected_name = f"tg_recall-{self.version}-py3-none-any.whl"
        if self.wheel.name != expected_name:
            raise ReleaseUpdateError("release wheel name does not match stable release version")
        expected_url = f"{GITHUB_RELEASE_DOWNLOAD_PREFIX}{self.tag}/{expected_name}"
        if self.wheel.url != expected_url:
            raise ReleaseUpdateError("release wheel URL does not match release tag and asset name")

    def as_json(self) -> dict[str, Any]:
        return {
            "published_at": self.published_at,
            "tag": self.tag,
            "url": self.url,
            "version": str(self.version),
            "wheel": self.wheel.as_json(),
        }


@dataclass(frozen=True)
class CachedRelease:
    release: ReleaseInfo
    fetched_at: str
    etag: str | None = None

    def __post_init__(self) -> None:
        _parse_timestamp(self.fetched_at)
        if self.etag is not None and (not isinstance(self.etag, str) or len(self.etag) > 512):
            raise ReleaseUpdateError("release cache ETag is invalid")

    def is_fresh(self, now: datetime, ttl: timedelta) -> bool:
        if ttl.total_seconds() < 0:
            raise ValueError("cache TTL must not be negative")
        return _parse_timestamp(self.fetched_at) + ttl >= now.astimezone(UTC)

    def as_json(self) -> dict[str, Any]:
        return {"etag": self.etag, "fetched_at": self.fetched_at, "release": self.release.as_json(), "schema_version": UPDATE_SCHEMA_VERSION}


@dataclass(frozen=True)
class ManagedWheelInstall:
    """Private record tying a ``file://`` provenance entry to our verified wheel."""

    environment: Path
    artifact: Path
    sha256: str
    version: SemanticVersion

    def __post_init__(self) -> None:
        if not _DIGEST_RE.fullmatch(f"sha256:{self.sha256}"):
            raise ReleaseUpdateError("managed wheel SHA-256 is invalid")

    def as_json(self) -> dict[str, Any]:
        return {
            "artifact": str(self.artifact),
            "environment": str(self.environment),
            "schema_version": UPDATE_SCHEMA_VERSION,
            "sha256": self.sha256,
            "version": str(self.version),
        }


@dataclass(frozen=True)
class ReleaseCheck:
    status: str
    installed_version: str
    release: ReleaseInfo | None
    cache_status: str
    network_attempted: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"current", "update_available", "unreachable", "unsupported"}:
            raise ValueError("invalid release check status")
        SemanticVersion.parse(self.installed_version)
        if self.status in {"current", "update_available"} and self.release is None:
            raise ValueError("successful release check requires a release")

    def as_json(self) -> dict[str, Any]:
        return {
            "cache_status": self.cache_status,
            "error_code": self.error_code,
            "installed_version": self.installed_version,
            "network_attempted": self.network_attempted,
            "release": self.release.as_json() if self.release else None,
            "schema_version": UPDATE_SCHEMA_VERSION,
            "status": self.status,
        }


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


HttpGet = Callable[[str, Mapping[str, str], float, int], HttpResponse]
Download = Callable[[str, int], bytes]
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
VersionReader = Callable[[Path], str | None]


class ReleaseCache:
    """Atomic global/portable cache for public release metadata only."""

    def __init__(self, roots: AppRoots):
        self.roots = roots

    @property
    def path(self) -> Path:
        return self.roots.release_update_cache_path

    @property
    def artifacts_dir(self) -> Path:
        return self.roots.release_update_artifacts_dir

    @property
    def managed_install_path(self) -> Path:
        return self.roots.release_update_state_path

    def load(self) -> CachedRelease | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return _cached_release_from_json(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReleaseUpdateError, TypeError, ValueError):
            return None

    def store(self, entry: CachedRelease) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        _private_mode(directory, is_dir=True)
        payload = json.dumps(entry.as_json(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=".latest.", suffix=".tmp", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _private_mode(temporary, is_dir=False)
            os.replace(temporary, self.path)
            _private_mode(self.path, is_dir=False)
        finally:
            temporary.unlink(missing_ok=True)

    def remove_staged_artifact(self, artifact: Path) -> None:
        try:
            artifact.resolve().relative_to(self.artifacts_dir.resolve())
        except ValueError as exc:
            raise ReleaseUpdateError("refusing to remove an artifact outside the update cache") from exc
        artifact.unlink(missing_ok=True)

    def load_managed_install(self) -> ManagedWheelInstall | None:
        try:
            value = json.loads(self.managed_install_path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping) or set(value) != {"artifact", "environment", "schema_version", "sha256", "version"}:
                raise ReleaseUpdateError("managed wheel state has an unsupported schema")
            if value["schema_version"] != UPDATE_SCHEMA_VERSION:
                raise ReleaseUpdateError("managed wheel state schema is unsupported")
            artifact = Path(value["artifact"]).resolve()
            artifact.relative_to(self.artifacts_dir.resolve())
            install = ManagedWheelInstall(
                environment=Path(value["environment"]).resolve(),
                artifact=artifact,
                sha256=value["sha256"],
                version=SemanticVersion.parse(value["version"]),
            )
            if not install.artifact.is_file() or hashlib.sha256(install.artifact.read_bytes()).hexdigest() != install.sha256:
                return None
            return install
        except (OSError, TypeError, ValueError, json.JSONDecodeError, ReleaseUpdateError):
            return None

    def store_managed_install(self, environment: Path, artifact: Path, release: ReleaseInfo) -> None:
        artifact = artifact.resolve()
        try:
            artifact.relative_to(self.artifacts_dir.resolve())
        except ValueError as exc:
            raise ReleaseUpdateError("managed wheel must remain in the update artifact cache") from exc
        if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != release.wheel.sha256:
            raise ReleaseUpdateError("managed wheel is no longer verified")
        record = ManagedWheelInstall(environment.resolve(), artifact, release.wheel.sha256, release.version)
        _atomic_private_json(self.managed_install_path, record.as_json())


class GitHubReleaseChecker:
    """Opt-in bounded checker for the one fixed public GitHub endpoint."""

    def __init__(
        self,
        cache: ReleaseCache,
        *,
        http_get: HttpGet | None = None,
        now: Callable[[], datetime] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cache_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("release check timeout must be positive")
        self.cache = cache
        self.http_get = http_get or _stdlib_http_get
        self.now = now or (lambda: datetime.now(UTC))
        self.timeout_seconds = timeout_seconds
        self.cache_ttl = cache_ttl

    def check(self, installed_version: str, *, refresh: bool = False, offline: bool = False) -> ReleaseCheck:
        installed = SemanticVersion.parse(installed_version)
        now = self.now().astimezone(UTC)
        cached = self.cache.load()
        if cached is not None and not refresh and cached.is_fresh(now, self.cache_ttl):
            return _check_from_release(installed, cached.release, "fresh", False)
        if offline:
            if cached is not None:
                return _check_from_release(installed, cached.release, "stale", False)
            return ReleaseCheck("unreachable", str(installed), None, "miss", False, "offline")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "tg-recall-release-check"}
        if cached is not None and cached.etag:
            headers["If-None-Match"] = cached.etag
        try:
            response = self.http_get(GITHUB_LATEST_RELEASE_URL, headers, self.timeout_seconds, MAX_RELEASE_RESPONSE_BYTES)
        except ReleaseTransportError as exc:
            if cached is not None:
                return _check_from_release(installed, cached.release, "stale", True)
            return ReleaseCheck("unreachable", str(installed), None, "miss", True, _safe_error_code(exc))
        if response.status == 304 and cached is not None:
            refreshed = CachedRelease(cached.release, _format_timestamp(now), cached.etag)
            try:
                self.cache.store(refreshed)
            except OSError:
                # The response still revalidated the public release metadata,
                # but a locked/ACL-protected cache must not turn an otherwise
                # valid lifecycle response into a generic CLI exception.
                return _check_from_release(installed, cached.release, "stale", True)
            return _check_from_release(installed, refreshed.release, "revalidated", True)
        if response.status != 200:
            if cached is not None:
                return _check_from_release(installed, cached.release, "stale", True)
            return ReleaseCheck("unreachable", str(installed), None, "miss", True, f"http_{response.status}")
        try:
            release = parse_github_release(response.body)
        except ReleaseUpdateError as exc:
            if cached is not None:
                return _check_from_release(installed, cached.release, "stale", True)
            return ReleaseCheck("unsupported", str(installed), None, "miss", True, _safe_error_code(exc))
        etag = response.headers.get("ETag") or response.headers.get("etag")
        try:
            self.cache.store(CachedRelease(release, _format_timestamp(now), etag))
        except OSError:
            # A verified network result is still safe to report.  Mark it as
            # uncached so a caller never mistakes this for persisted state.
            return _check_from_release(installed, release, "network_uncached", True)
        return _check_from_release(installed, release, "network", True)


def parse_github_release(payload: bytes) -> ReleaseInfo:
    if not isinstance(payload, bytes) or len(payload) > MAX_RELEASE_RESPONSE_BYTES:
        raise ReleaseUpdateError("release response is too large")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseUpdateError("release response is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ReleaseUpdateError("release response must be an object")
    if value.get("draft") is not False or value.get("prerelease") is not False:
        raise ReleaseUpdateError("release is not a stable published release")
    tag = value.get("tag_name")
    version = SemanticVersion.parse(tag)
    page_url = value.get("html_url")
    published_at = value.get("published_at")
    assets = value.get("assets")
    if not isinstance(page_url, str) or not isinstance(published_at, str) or not isinstance(assets, list):
        raise ReleaseUpdateError("release response has invalid required fields")
    expected_name = f"tg_recall-{version}-py3-none-any.whl"
    matches = [item for item in assets if isinstance(item, Mapping) and item.get("name") == expected_name]
    if len(matches) != 1:
        raise ReleaseUpdateError("release must contain exactly one matching universal wheel")
    asset = matches[0]
    digest = asset.get("digest")
    digest_match = _DIGEST_RE.fullmatch(digest) if isinstance(digest, str) else None
    if digest_match is None:
        raise ReleaseUpdateError("release wheel has no valid GitHub SHA-256 digest")
    wheel = ReleaseWheel(
        name=expected_name,
        url=_required_string(asset, "browser_download_url"),
        sha256=digest_match.group(1),
        size_bytes=_required_positive_int(asset, "size"),
    )
    return ReleaseInfo(version, tag, page_url, published_at, wheel)


@dataclass(frozen=True)
class InstallationProvenance:
    kind: str
    installed_version: str | None
    uv_path: str | None
    tool_environment: Path | None
    manual_action: tuple[str, ...]

    @property
    def supported_for_apply(self) -> bool:
        return self.kind == "uv_tool_github_wheel" and self.uv_path is not None and self.tool_environment is not None

    def as_json(self) -> dict[str, Any]:
        # Paths are intentionally not returned: update JSON must not expose a
        # local checkout or tool directory.
        return {
            "installation_source": self.kind,
            "installed_version": self.installed_version,
            "supported_for_apply": self.supported_for_apply,
        }


def detect_installation_provenance(
    distribution_name: str = "tg-recall",
    *,
    distribution: importlib.metadata.Distribution | None = None,
    executable_prefix: Path | None = None,
    find_uv: Callable[[str], str | None] = shutil.which,
    release_cache: ReleaseCache | None = None,
) -> InstallationProvenance:
    try:
        dist = distribution or importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return InstallationProvenance("unknown", None, None, None, ())
    version = dist.version
    direct_text = dist.read_text("direct_url.json")
    direct: Mapping[str, Any] | None = None
    if direct_text:
        try:
            loaded = json.loads(direct_text)
            direct = loaded if isinstance(loaded, Mapping) else None
        except json.JSONDecodeError:
            return InstallationProvenance("unknown", version, None, None, ())
    prefix = (executable_prefix or Path(sys.prefix)).resolve()
    uv_path = find_uv("uv")
    if direct is None:
        return InstallationProvenance("index_or_system", version, uv_path, None, ())
    url = direct.get("url")
    if not isinstance(url, str):
        return InstallationProvenance("unknown", version, uv_path, None, ())
    dir_info = direct.get("dir_info")
    if isinstance(dir_info, Mapping) and dir_info.get("editable") is True:
        return InstallationProvenance(
            "editable",
            version,
            uv_path,
            None,
            ("Reinstall from the original editable checkout path.",),
        )
    if _safe_github_download_url(url):
        tool_environment = prefix if _looks_like_uv_tool_environment(prefix, distribution_name) else None
        kind = "uv_tool_github_wheel" if tool_environment is not None and uv_path else "github_release_wheel"
        return InstallationProvenance(kind, version, uv_path, tool_environment, ("uv", "tool", "install", "--force", url))
    if url.startswith("file:"):
        managed = (release_cache or ReleaseCache(AppRoots.resolve())).load_managed_install()
        artifact = _file_url_to_path(url)
        if (
            managed is not None
            and artifact is not None
            and artifact == managed.artifact
            and prefix == managed.environment
            and version == str(managed.version)
            and _looks_like_uv_tool_environment(prefix, distribution_name)
            and uv_path
        ):
            return InstallationProvenance(
                "uv_tool_github_wheel",
                version,
                uv_path,
                prefix,
                ("uv", "tool", "install", "--force", str(managed.artifact)),
            )
        return InstallationProvenance("local_file", version, uv_path, None, ())
    if isinstance(direct.get("vcs_info"), Mapping):
        return InstallationProvenance("git", version, uv_path, None, ())
    return InstallationProvenance("unknown", version, uv_path, None, ())


@dataclass(frozen=True)
class UpdateApplyResult:
    status: str
    attempted_version: str | None
    resulting_version: str | None
    artifact_path: Path | None
    manual_action: tuple[str, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"applied", "manual_required", "verification_failed", "installer_failed"}:
            raise ValueError("invalid update apply status")
        if self.status == "applied" and self.resulting_version != self.attempted_version:
            raise ValueError("an applied update requires matching post-install version")


def stage_verified_wheel(cache: ReleaseCache, release: ReleaseInfo, downloader: Download) -> Path:
    """Download a release wheel to global cache only after exact verification."""

    try:
        payload = downloader(release.wheel.url, MAX_WHEEL_BYTES)
    except ReleaseTransportError:
        raise
    except Exception as exc:
        raise ReleaseTransportError("wheel download failed") from exc
    if not isinstance(payload, bytes) or len(payload) > MAX_WHEEL_BYTES:
        raise ReleaseUpdateError("wheel download exceeds the configured size limit")
    if len(payload) != release.wheel.size_bytes:
        raise ReleaseUpdateError("wheel size does not match GitHub release metadata")
    if hashlib.sha256(payload).hexdigest() != release.wheel.sha256:
        raise ReleaseUpdateError("wheel SHA-256 does not match GitHub release metadata")
    _validate_wheel_bytes(payload, release.version)
    directory = cache.artifacts_dir
    directory.mkdir(parents=True, exist_ok=True)
    _private_mode(directory, is_dir=True)
    target = directory / release.wheel.name
    fd, temporary_name = tempfile.mkstemp(prefix=f".{release.version}.", suffix=".part", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _private_mode(temporary, is_dir=False)
        os.replace(temporary, target)
        _private_mode(target, is_dir=False)
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def apply_verified_wheel(
    provenance: InstallationProvenance,
    release: ReleaseInfo,
    artifact: Path,
    *,
    runner: Runner | None = None,
    version_reader: VersionReader | None = None,
    release_cache: ReleaseCache | None = None,
) -> UpdateApplyResult:
    """Run only the supported local-wheel ``uv tool`` command without a shell."""

    if not provenance.supported_for_apply:
        return UpdateApplyResult("manual_required", str(release.version), provenance.installed_version, None, provenance.manual_action)
    try:
        payload = artifact.read_bytes()
        if artifact.name != release.wheel.name or hashlib.sha256(payload).hexdigest() != release.wheel.sha256:
            raise ReleaseUpdateError("staged wheel no longer matches the verified release digest")
        _validate_wheel_bytes(payload, release.version)
    except (OSError, ReleaseUpdateError):
        return UpdateApplyResult("verification_failed", str(release.version), provenance.installed_version, artifact, error_code="artifact_verification_failed")
    command = [provenance.uv_path or "uv", "tool", "install", "--force", str(artifact)]
    execute = runner or _subprocess_runner
    try:
        completed = execute(command)
    except OSError:
        return UpdateApplyResult("installer_failed", str(release.version), provenance.installed_version, artifact, error_code="installer_unavailable")
    if completed.returncode != 0:
        return UpdateApplyResult("installer_failed", str(release.version), provenance.installed_version, artifact, error_code="installer_failed")
    read_version = version_reader or _version_in_tool_environment
    resulting = read_version(provenance.tool_environment or Path())
    if resulting != str(release.version):
        return UpdateApplyResult("verification_failed", str(release.version), resulting, artifact, error_code="post_install_version_mismatch")
    if release_cache is not None:
        try:
            release_cache.store_managed_install(provenance.tool_environment or Path(), artifact, release)
        except (OSError, ReleaseUpdateError):
            return UpdateApplyResult(
                "verification_failed",
                str(release.version),
                resulting,
                artifact,
                error_code="managed_provenance_state_failed",
            )
    return UpdateApplyResult("applied", str(release.version), resulting, artifact)


def _cached_release_from_json(value: Any) -> CachedRelease:
    if not isinstance(value, Mapping) or set(value) != {"etag", "fetched_at", "release", "schema_version"}:
        raise ReleaseUpdateError("release cache has an unsupported schema")
    if value["schema_version"] != UPDATE_SCHEMA_VERSION:
        raise ReleaseUpdateError("release cache schema is unsupported")
    release_data = value["release"]
    if not isinstance(release_data, Mapping) or set(release_data) != {"published_at", "tag", "url", "version", "wheel"}:
        raise ReleaseUpdateError("release cache has invalid release metadata")
    wheel_data = release_data["wheel"]
    if not isinstance(wheel_data, Mapping) or set(wheel_data) != {"name", "sha256", "size_bytes", "url"}:
        raise ReleaseUpdateError("release cache has invalid wheel metadata")
    return CachedRelease(
        ReleaseInfo(
            SemanticVersion.parse(release_data["version"]), release_data["tag"], release_data["url"], release_data["published_at"],
            ReleaseWheel(wheel_data["name"], wheel_data["url"], wheel_data["sha256"], wheel_data["size_bytes"]),
        ),
        value["fetched_at"], value["etag"],
    )


def _check_from_release(installed: SemanticVersion, release: ReleaseInfo, cache_status: str, network_attempted: bool) -> ReleaseCheck:
    status = "update_available" if release.version > installed else "current"
    return ReleaseCheck(status, str(installed), release, cache_status, network_attempted)


def _stdlib_http_get(url: str, headers: Mapping[str, str], timeout: float, maximum: int) -> HttpResponse:
    if url != GITHUB_LATEST_RELEASE_URL:
        raise ReleaseTransportError("release endpoint is not allowed")
    request = Request(url, headers=dict(headers), method="GET")
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(int(response.status), dict(response.headers.items()), _read_bounded(response, maximum))
    except HTTPError as exc:
        if exc.code == 304:
            return HttpResponse(304, dict(exc.headers.items()) if exc.headers else {}, b"")
        if 300 <= exc.code < 400:
            raise ReleaseTransportError("release endpoint redirected") from exc
        return HttpResponse(exc.code, dict(exc.headers.items()) if exc.headers else {}, b"")
    except (URLError, TimeoutError, OSError) as exc:
        raise ReleaseTransportError("release endpoint is unreachable") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _read_bounded(response: Any, maximum: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > maximum:
                raise ReleaseTransportError("release response exceeds the configured size limit")
        except ValueError as exc:
            raise ReleaseTransportError("release response has an invalid content length") from exc
    payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise ReleaseTransportError("release response exceeds the configured size limit")
    return payload


def _safe_release_page_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and parsed.netloc == "github.com" and re.fullmatch(r"/Rerowros/tg-recall/releases/tag/v\d+\.\d+\.\d+", parsed.path) is not None and not parsed.query and not parsed.fragment


def _safe_github_download_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and parsed.netloc == "github.com" and parsed.path.startswith("/Rerowros/tg-recall/releases/download/") and not parsed.query and not parsed.fragment


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise ReleaseUpdateError(f"release response field {key} is invalid")
    return result


def _required_positive_int(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result < 1:
        raise ReleaseUpdateError(f"release response field {key} is invalid")
    return result


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseUpdateError("release timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseUpdateError("release timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ReleaseUpdateError("release timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, ReleaseTransportError):
        return "network_error"
    return "invalid_release"


def _file_url_to_path(value: str) -> Path | None:
    parsed = urlsplit(value)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
        return None
    try:
        return Path(url2pathname(parsed.path)).resolve()
    except (OSError, ValueError):
        return None


def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    _private_mode(directory, is_dir=True)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _private_mode(temporary, is_dir=False)
        os.replace(temporary, path)
        _private_mode(path, is_dir=False)
    finally:
        temporary.unlink(missing_ok=True)


def _private_mode(path: Path, *, is_dir: bool) -> None:
    if os.name != "nt":
        try:
            path.chmod(0o700 if is_dir else 0o600)
        except OSError:
            pass


def _looks_like_uv_tool_environment(prefix: Path, distribution_name: str) -> bool:
    normalized = prefix.name.casefold().replace("_", "-")
    return normalized == distribution_name.casefold() and prefix.parent.name.casefold() == "tools"


def _validate_wheel_bytes(payload: bytes, expected_version: SemanticVersion) -> None:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
            metadata_names = [name for name in archive.namelist() if re.fullmatch(r"tg_recall-[^/]+\.dist-info/METADATA", name)]
            if len(metadata_names) != 1:
                raise ReleaseUpdateError("wheel metadata is missing or ambiguous")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        raise ReleaseUpdateError("downloaded artifact is not a valid wheel") from exc
    if metadata.get("Name") != "tg-recall" or metadata.get("Version") != str(expected_version):
        raise ReleaseUpdateError("wheel package metadata does not match the release")


def _subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, shell=False, text=True, capture_output=True)


def _version_in_tool_environment(environment: Path) -> str | None:
    candidates = [environment / "Lib" / "site-packages", environment / "lib"]
    candidates.extend((environment / "lib").glob("python*/site-packages"))
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        for distribution in importlib.metadata.distributions(path=[str(candidate)]):
            if distribution.metadata.get("Name") == "tg-recall":
                return distribution.version
    return None
