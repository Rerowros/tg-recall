"""The public surface that 1.x releases keep compatible (docs/compatibility.md).

``tests/fixtures/contract.json`` is the recorded contract. A change that
removes or changes something in it fails as breaking; an addition fails until
the snapshot is refreshed, so every change to the contract is deliberate:

    TG_RECALL_UPDATE_CONTRACT=1 uv run pytest tests/test_public_contract.py
"""

from __future__ import annotations

import argparse
import io
import json
import os
import zipfile
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from tg_recall import __version__
from tg_recall.backup import create_backup
from tg_recall.cli import build_parser, main
from tg_recall.config import AIAccessPolicy, AppConfig, TelegramConfig, TranscriptionConfig, save_config
from tg_recall.mcp_lifecycle import serve_stdio
from tg_recall.mcp_server import PROTOCOL_VERSIONS, ReadOnlyMCPServer
from tg_recall.storage import Database

CONTRACT = Path(__file__).parent / "fixtures" / "contract.json"
UPDATE = os.environ.get("TG_RECALL_UPDATE_CONTRACT") == "1"
# Derived from the profile location, never set by the user.
_DERIVED_CONFIG_KEYS = {"telegram.session_path"}


def current_contract(tmp_path: Path) -> dict[str, Any]:
    config = AppConfig.default(tmp_path, "default")
    config.ensure_dirs()
    save_config(config, home=tmp_path)
    config.ai_access = AIAccessPolicy(enabled=True, allow_all_chats=True, allow_sync=True, allow_transcribe=True)
    db = Database(config.db_path)
    db.migrate()
    tools = {}
    for tool in ReadOnlyMCPServer(config, db).tools():
        schema = tool["inputSchema"]
        tools[tool["name"]] = {
            "properties": {name: _shape(value) for name, value in sorted(schema.get("properties", {}).items())},
            "required": sorted(schema.get("required", [])),
        }
    backup = create_backup(config, tmp_path / "backup.zip")
    with zipfile.ZipFile(backup["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return {
        "mcp_tools": tools,
        "cli": dict(sorted(_cli_commands(build_parser(), "tg-recall").items())),
        "config": {
            f"{section}.{item.name}": str(item.type)
            for section, cls in (
                ("telegram", TelegramConfig),
                ("ai_access", AIAccessPolicy),
                ("transcription", TranscriptionConfig),
            )
            for item in fields(cls)
            if f"{section}.{item.name}" not in _DERIVED_CONFIG_KEYS
        },
        "citation": "tg://chat/<chat_id>/message/<message_id>",
        "backup": {"format": manifest["format"], "version": manifest["version"]},
    }


def compare(old: dict[str, Any], new: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (breaking, additive) differences between two contracts."""

    breaking: list[str] = []
    additive: list[str] = []
    for name, tool in old["mcp_tools"].items():
        current = new["mcp_tools"].get(name)
        if current is None:
            breaking.append(f"MCP tool removed: {name}")
            continue
        for prop, shape in tool["properties"].items():
            if prop not in current["properties"]:
                breaking.append(f"MCP {name}: argument removed: {prop}")
            elif current["properties"][prop] != shape:
                breaking.append(f"MCP {name}: argument changed: {prop}")
        for prop in set(current["required"]) - set(tool["required"]):
            breaking.append(f"MCP {name}: argument became required: {prop}")
        additive += [
            f"MCP {name}: new argument: {prop}" for prop in current["properties"] if prop not in tool["properties"]
        ]
    additive += [f"MCP tool added: {name}" for name in new["mcp_tools"] if name not in old["mcp_tools"]]
    for command, options in old["cli"].items():
        current = new["cli"].get(command)
        if current is None:
            breaking.append(f"CLI command removed: {command}")
            continue
        breaking += [f"CLI {command}: removed {option}" for option in options if option not in current]
        additive += [f"CLI {command}: new {option}" for option in current if option not in options]
    additive += [f"CLI command added: {command}" for command in new["cli"] if command not in old["cli"]]
    for key, kind in old["config"].items():
        if key not in new["config"]:
            breaking.append(f"config key removed: {key}")
        elif new["config"][key] != kind:
            breaking.append(f"config key changed type: {key} {kind} -> {new['config'][key]}")
    additive += [f"config key added: {key}" for key in new["config"] if key not in old["config"]]
    for key in ("citation", "backup"):
        if old[key] != new[key]:
            breaking.append(f"{key} format changed: {old[key]} -> {new[key]}")
    return breaking, additive


def test_public_contract_has_no_breaking_changes(tmp_path: Path) -> None:
    contract = current_contract(tmp_path)
    if UPDATE:
        CONTRACT.write_text(json.dumps(contract, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        pytest.skip("contract snapshot rewritten")
    breaking, _ = compare(json.loads(CONTRACT.read_text(encoding="utf-8")), contract)
    assert not breaking, (
        "Breaking change to the 1.x contract (docs/compatibility.md): keep the old form working "
        "or wait for 2.0:\n" + "\n".join(breaking)
    )


def test_public_contract_snapshot_is_current(tmp_path: Path) -> None:
    _, additive = compare(json.loads(CONTRACT.read_text(encoding="utf-8")), current_contract(tmp_path))
    assert not additive, (
        "The contract grew; record it with TG_RECALL_UPDATE_CONTRACT=1 uv run pytest tests/test_public_contract.py "
        "and mention it in CHANGELOG:\n" + "\n".join(additive)
    )


def test_contract_comparison_classifies_changes() -> None:
    old = {
        "mcp_tools": {
            "search": {"properties": {"q": {"type": "string"}, "limit": {"type": "integer"}}, "required": []}
        },
        "cli": {"tg-recall search": ["--chat", "query"]},
        "config": {"ai_access.enabled": "bool"},
        "citation": "tg://chat/<chat_id>/message/<message_id>",
        "backup": {"format": "tg-recall-backup", "version": 1},
    }
    new = json.loads(json.dumps(old))
    new["mcp_tools"]["search"]["properties"]["limit"] = {"type": "string"}
    new["mcp_tools"]["search"]["properties"]["since"] = {"type": "string"}
    new["mcp_tools"]["search"]["required"] = ["q"]
    new["cli"]["tg-recall search"] = ["query", "--since"]
    del new["config"]["ai_access.enabled"]

    breaking, additive = compare(old, new)

    assert breaking == [
        "MCP search: argument changed: limit",
        "MCP search: argument became required: q",
        "CLI tg-recall search: removed --chat",
        "config key removed: ai_access.enabled",
    ]
    assert additive == ["MCP search: new argument: since", "CLI tg-recall search: new --since"]


def test_version_flag_prints_version(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"tg-recall {__version__}"


@pytest.mark.parametrize("requested", [*PROTOCOL_VERSIONS, "2099-01-01", None])
def test_mcp_answers_in_the_protocol_revision_the_client_asked_for(tmp_path: Path, requested: str | None) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    server = ReadOnlyMCPServer(AppConfig.default(tmp_path), db)
    params = {"clientInfo": {"name": "client"}, "capabilities": {}}
    if requested:
        params["protocolVersion"] = requested

    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})["result"]

    assert result["protocolVersion"] == (requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0])
    assert result["capabilities"] == {"tools": {}}


def test_mcp_stdio_survives_ping_bad_json_and_batches(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    server = ReadOnlyMCPServer(AppConfig.default(tmp_path), db)
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        "{not json",
        [{"jsonrpc": "2.0", "id": 3, "method": "ping"}],
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
    ]
    stdin = io.StringIO("".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines))
    stdout = io.StringIO()

    assert serve_stdio(server.handle, stdin=stdin, stdout=stdout, parent=None) == 0

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [response.get("id") for response in responses] == [1, 2, None, None, 4]
    assert responses[1]["result"] == {}
    assert responses[2]["error"]["code"] == -32700
    assert responses[3]["error"]["code"] == -32600
    assert responses[4]["result"]["tools"]


def _shape(schema: Any) -> Any:
    """A JSON schema without descriptions: wording may change, accepted values may not."""

    if isinstance(schema, dict):
        return {key: _shape(value) for key, value in sorted(schema.items()) if key != "description"}
    if isinstance(schema, list):
        return [_shape(value) for value in schema]
    return schema


def _cli_commands(parser: argparse.ArgumentParser, path: str) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    options: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                commands.update(_cli_commands(subparser, f"{path} {name}"))
        elif not isinstance(action, argparse._HelpAction) and action.help != argparse.SUPPRESS:
            options.extend(action.option_strings or [action.dest])
    commands[path] = sorted(options)
    return commands


# Handshakes captured from real clients (Claude Code 2.1.278 and Codex 0.151.0).
_CLIENT_HANDSHAKES = {
    "claude-code": [
        {
            "jsonrpc": "2.0",
            "id": "server-discover-probe-1",
            "method": "server/discover",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"roots": {"listChanged": True}, "elicitation": {}},
                "clientInfo": {"name": "claude-code", "version": "2.1.278"},
            },
        },
    ],
    "codex": [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {"form": {}, "url": {}}},
                "clientInfo": {"name": "codex-mcp-client", "version": "0.151.0"},
            },
        },
    ],
}


@pytest.mark.parametrize("client", sorted(_CLIENT_HANDSHAKES))
def test_real_client_handshakes_reach_the_tools(tmp_path: Path, client: str) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    server = ReadOnlyMCPServer(AppConfig.default(tmp_path), db)
    lines = [
        *_CLIENT_HANDSHAKES[client],
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {"progressToken": 0}}},
    ]
    stdout = io.StringIO()

    serve_stdio(
        server.handle, stdin=io.StringIO("".join(json.dumps(line) + "\n" for line in lines)), stdout=stdout, parent=None
    )

    responses = {response["id"]: response for response in map(json.loads, stdout.getvalue().splitlines())}
    initialize = next(line for line in _CLIENT_HANDSHAKES[client] if line["method"] == "initialize")
    assert responses[0]["result"]["protocolVersion"] == initialize["params"]["protocolVersion"]
    assert [tool["name"] for tool in responses[1]["result"]["tools"]][:2] == ["search", "read"]
    if client == "claude-code":
        # An unknown probe gets a JSON-RPC error, so the client falls back to initialize.
        assert responses["server-discover-probe-1"]["error"]["code"] == -32601
