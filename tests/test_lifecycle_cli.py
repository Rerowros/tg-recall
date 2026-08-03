from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tg_recall import __version__
from tg_recall import cli
from tg_recall.cli import main
from tg_recall.lifecycle_settings import UpdateSettingsStore
from tg_recall.paths import AppRoots


AUTOMATION_ENV = ("CI", "CODEX", "AI_TERMINAL", "TG_RECALL_AI_MODE", "TG_ECOSYSTEM_AI_MODE")


def _human_environment(monkeypatch) -> None:
    for name in AUTOMATION_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TG_RECALL_TRUSTED_AUTOMATION", raising=False)


def test_every_update_command_is_denied_before_config_or_cache_access(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    def unexpected_config(*args, **kwargs):
        raise AssertionError("lifecycle gate must run before load_config")

    monkeypatch.setattr(cli, "load_config", unexpected_config)
    assert main(["--home", str(tmp_path / "home"), "--json", "update", "status"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "agent_operation_forbidden"
    assert not (tmp_path / "home").exists()


def test_trusted_automation_cannot_bypass_update_gate(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    monkeypatch.setenv("TG_RECALL_TRUSTED_AUTOMATION", "1")

    assert main(["--home", str(tmp_path / "home"), "--json", "update", "check", "--offline"]) == 1

    assert json.loads(capsys.readouterr().out)["error"]["code"] == "agent_operation_forbidden"


def test_update_configure_is_global_portable_and_disabled_by_default(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    home = tmp_path / "portable"

    assert UpdateSettingsStore(AppRoots.resolve(home)).load().enabled is False
    assert main(["--home", str(home), "--json", "update", "configure", "--interval-hours", "12"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "schema_version": 1,
        "action": "configure",
        "status": "configured",
        "installed_version": payload["installed_version"],
        "release": None,
        "provenance": None,
        "update_checks": {"enabled": True, "interval_hours": 12, "schema_version": 1},
        "cache_status": None,
        "network_attempted": False,
        "error_code": None,
        "changed_paths": [],
        "warnings": [],
        "next_actions": [],
    }
    assert UpdateSettingsStore(AppRoots.resolve(home)).load().interval_hours == 12
    assert "profiles" not in str(UpdateSettingsStore(AppRoots.resolve(home)).path)


def test_update_check_has_stable_json_and_no_profile_load(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    observed: dict[str, object] = {}

    class Checker:
        def __init__(self, cache, **kwargs):
            observed["cache"] = cache

        def check(self, installed_version, **kwargs):
            observed["installed"] = installed_version
            observed["check_kwargs"] = kwargs
            return SimpleNamespace(
                status="current",
                release=None,
                cache_status="miss",
                network_attempted=False,
                error_code="offline",
            )

    monkeypatch.setattr(cli, "GitHubReleaseChecker", Checker)
    monkeypatch.setattr(
        cli, "load_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no profile config"))
    )
    monkeypatch.setattr(cli, "runtime_package_version", lambda: "0.5.0")

    assert main(["--home", str(tmp_path / "home"), "--json", "update", "check", "--offline"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert observed["installed"] == "0.5.0"
    assert observed["check_kwargs"] == {"refresh": False, "offline": True}
    assert payload["action"] == "check"
    assert payload["status"] == "current"
    assert payload["schema_version"] == 1
    assert payload["release"] is None
    assert "home" not in json.dumps(payload).lower()


def test_update_apply_passes_the_same_release_cache_to_provenance_and_apply(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    release = SimpleNamespace(as_json=lambda: {"version": "0.6.0"}, version="0.6.0")
    calls: dict[str, object] = {}

    class Checker:
        def __init__(self, cache, **kwargs):
            calls["checker_cache"] = cache

        def check(self, installed_version, **kwargs):
            return SimpleNamespace(
                status="update_available",
                release=release,
                cache_status="network",
                network_attempted=True,
                error_code=None,
            )

    def provenance(*, release_cache):
        calls["provenance_cache"] = release_cache
        return SimpleNamespace(
            as_json=lambda: {"installation_source": "uv_tool_github_wheel", "supported_for_apply": True},
            supported_for_apply=True,
        )

    def staged(cache, checked_release, downloader):
        calls["staged_cache"] = cache
        assert checked_release is release
        return tmp_path / "verified.whl"

    def applied(provenance_value, checked_release, artifact, *, release_cache):
        calls["apply_cache"] = release_cache
        return SimpleNamespace(
            status="manual_required", error_code=None, manual_action=("Use a verified manual command.",)
        )

    monkeypatch.setattr(cli, "GitHubReleaseChecker", Checker)
    monkeypatch.setattr(cli, "detect_installation_provenance", provenance)
    monkeypatch.setattr(cli, "stage_verified_wheel", staged)
    monkeypatch.setattr(cli, "apply_verified_wheel", applied)
    monkeypatch.setattr(cli, "runtime_package_version", lambda: "0.5.0")

    assert main(["--home", str(tmp_path / "home"), "--json", "update", "apply"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert calls["checker_cache"] is calls["provenance_cache"] is calls["staged_cache"] is calls["apply_cache"]
    assert payload["status"] == "manual_required"
    assert payload["next_actions"] == ["Use a verified manual command."]


def test_unsupported_update_apply_never_downloads_or_invokes_installer(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    release = SimpleNamespace(as_json=lambda: {"version": "0.6.0"}, version="0.6.0")

    class Checker:
        def __init__(self, cache, **kwargs):
            pass

        def check(self, installed_version, **kwargs):
            return SimpleNamespace(
                status="update_available",
                release=release,
                cache_status="network",
                network_attempted=True,
                error_code=None,
            )

    monkeypatch.setattr(cli, "GitHubReleaseChecker", Checker)
    monkeypatch.setattr(
        cli,
        "detect_installation_provenance",
        lambda *, release_cache: SimpleNamespace(
            as_json=lambda: {"installation_source": "editable", "supported_for_apply": False},
            supported_for_apply=False,
            manual_action=("Reinstall from the editable checkout.",),
        ),
    )
    monkeypatch.setattr(
        cli,
        "stage_verified_wheel",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not download unsupported install")),
    )

    assert main(["--home", str(tmp_path / "home"), "--json", "update", "apply"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "manual_required"
    assert payload["next_actions"] == ["Reinstall from the editable checkout."]


def test_periodic_check_is_disabled_by_default_and_skips_json_commands(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setattr(
        cli,
        "GitHubReleaseChecker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("periodic network check must stay disabled")),
    )

    assert main(["--home", str(home), "--json", "setup"]) == 0
    capsys.readouterr()
    assert main(["--home", str(home), "--json", "agent", "guide"]) == 0


def test_agent_guide_reports_runtime_version_without_legacy_v0_2_key(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    home = tmp_path / "home"
    assert main(["--home", str(home), "--json", "setup"]) == 0
    capsys.readouterr()

    assert main(["--home", str(home), "--json", "agent", "guide"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["agent_guide"]["tg_recall_version"] == __version__
    assert "current_v0_2" not in payload["agent_guide"]


def test_integrate_list_is_allowed_in_automation_without_profile_or_home_access(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no config")))

    assert main(["--home", str(tmp_path / "home"), "--json", "integrate", "list"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "list"
    assert payload["status"] == "available"
    assert not (tmp_path / "home").exists()


def test_integrate_list_reports_documented_capabilities_without_profile_read(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    monkeypatch.setattr(
        cli, "load_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no profile config"))
    )

    assert main(["--home", str(tmp_path / "home"), "--json", "integrate", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["action"] == "list"
    assert payload["status"] == "available"
    assert payload["installed_version"]
    assert payload["requested_harnesses"] == []
    assert payload["scope"] is None
    assert payload["components"] == []
    assert {item["harness"] for item in payload["harnesses"]} == {"codex", "claude-code", "cursor", "generic"}


def test_integrate_project_requires_explicit_project_root(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)

    assert (
        main(
            [
                "--home",
                str(tmp_path / "home"),
                "--json",
                "integrate",
                "preview",
                "--target",
                "codex",
                "--scope",
                "project",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "conflict"
    assert payload["conflicts"] == ["--project-root is required for --scope project"]


def test_integrate_preview_is_no_write_and_install_creates_only_global_state(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    command = [
        "--home",
        str(home),
        "--json",
        "integrate",
        "preview",
        "--target",
        "cursor",
        "--scope",
        "project",
        "--project-root",
        str(project),
    ]

    assert main(command) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "installed"
    assert not (project / ".cursor").exists()
    assert not (home / "state").exists()

    install = command.copy()
    install[4] = "install"
    assert main(install) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "installed"
    assert (project / ".cursor/rules/tg-recall.mdc").exists()
    assert (home / "state/integrations/v1/integration-ownership.json").exists()


def test_integrate_generic_requires_all_explicit_destinations(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()

    assert (
        main(
            [
                "--home",
                str(tmp_path / "home"),
                "--json",
                "integrate",
                "preview",
                "--target",
                "generic",
                "--scope",
                "project",
                "--project-root",
                str(project),
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "conflict"
    assert "generic integration requires" in payload["conflicts"][0]


def test_integrate_write_gate_precedes_home_state_config_and_harness_work(tmp_path, monkeypatch, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    monkeypatch.setenv("TG_RECALL_TRUSTED_AUTOMATION", "1")
    monkeypatch.setattr(
        cli.Path, "home", classmethod(lambda cls: (_ for _ in ()).throw(AssertionError("no home lookup")))
    )
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no config")))
    monkeypatch.setattr(
        cli, "run_harness_lifecycle", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no harness work"))
    )

    assert (
        main(
            [
                "--home",
                str(tmp_path / "home"),
                "--json",
                "integrate",
                "install",
                "--target",
                "codex",
                "--scope",
                "project",
                "--project-root",
                str(project),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "agent_operation_forbidden"


@pytest.mark.parametrize(("action", "expected_exit"), [("preview", 0), ("status", 1)])
def test_integrate_read_only_discovery_in_automation_is_data_blind_and_no_write(
    tmp_path, monkeypatch, capsys, action, expected_exit
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no profile config")))
    monkeypatch.setattr(cli, "Database", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no SQLite")))
    monkeypatch.setattr(
        cli, "TelegramArchiveClient", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no Telegram session"))
    )
    monkeypatch.setattr(cli, "build_opener", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no network")))

    assert (
        main(
            [
                "--home",
                str(home),
                "--json",
                "integrate",
                action,
                "--target",
                "codex",
                "--scope",
                "project",
                "--project-root",
                str(project),
            ]
        )
        == expected_exit
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == action
    assert payload["status"] != "conflict"
    assert not (project / "AGENTS.md").exists()
    assert not (project / ".codex").exists()
    assert not (home / "state").exists()


def test_integrate_user_scope_uses_explicit_home_and_second_install_is_noop(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    home = tmp_path / "tg-home"
    user_home = tmp_path / "harness-home"
    user_home.mkdir()
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: user_home))
    command = [
        "--home",
        str(home),
        "--json",
        "integrate",
        "install",
        "--target",
        "codex",
        "--scope",
        "user",
    ]

    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "installed"
    assert (user_home / ".codex/AGENTS.md").exists()
    assert (user_home / ".codex/config.toml").exists()

    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["status"] == "unchanged"
    assert second["changed_paths"] == []


def test_integrate_generic_paths_are_explicit_and_json_does_not_serialize_unrelated_secret(
    tmp_path, monkeypatch, capsys
) -> None:
    _human_environment(monkeypatch)
    monkeypatch.setenv("TG_RECALL_UNRELATED_SECRET", "do-not-serialize")
    project = tmp_path / "project"
    generic = tmp_path / "generic"
    project.mkdir()
    generic.mkdir()
    command = [
        "--home",
        str(tmp_path / "home"),
        "--json",
        "integrate",
        "install",
        "--target",
        "generic",
        "--scope",
        "project",
        "--project-root",
        str(project),
        "--generic-output-root",
        str(generic),
        "--generic-instructions",
        "instructions.md",
        "--generic-mcp",
        "mcp.json",
    ]

    assert main(command) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "installed"
    assert (generic / "instructions.md").exists()
    assert (generic / "mcp.json").exists()
    assert "do-not-serialize" not in json.dumps(payload)


def test_integrate_all_project_installs_documented_harnesses_without_generic_paths(
    tmp_path, monkeypatch, capsys
) -> None:
    _human_environment(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()

    assert (
        main(
            [
                "--home",
                str(tmp_path / "home"),
                "--json",
                "integrate",
                "install",
                "--target",
                "all",
                "--scope",
                "project",
                "--project-root",
                str(project),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "installed"
    assert payload["requested_harnesses"] == ["claude-code", "codex", "cursor"]
    assert (project / "AGENTS.md").exists()
    assert (project / "CLAUDE.md").exists()
    assert (project / ".cursor/rules/tg-recall.mdc").exists()


def test_integrate_user_mixed_manual_result_is_nonzero(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    user_home = tmp_path / "harness-home"
    user_home.mkdir()
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: user_home))

    assert (
        main(
            [
                "--home",
                str(tmp_path / "home"),
                "--json",
                "integrate",
                "install",
                "--target",
                "codex",
                "--target",
                "cursor",
                "--scope",
                "user",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "partial"
    assert any(item["status"] == "manual_required" for item in payload["components"])


def test_integrate_rejects_selected_project_symlink_root_before_write(tmp_path, monkeypatch, capsys) -> None:
    _human_environment(monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    project_link = tmp_path / "project-link"
    try:
        project_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows privilege policy varies
        pytest.skip(f"symlinks unavailable: {exc}")

    assert (
        main(
            [
                "--home",
                str(tmp_path / "home"),
                "--json",
                "integrate",
                "preview",
                "--target",
                "codex",
                "--scope",
                "project",
                "--project-root",
                str(project_link),
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "conflict"
    assert not (outside / "AGENTS.md").exists()
