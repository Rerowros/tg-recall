from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

from tg_recall.cli import main
from tg_recall.config import AIAccessPolicy, AppConfig, save_config
from tg_recall.knowledge_catalog import EvidenceMemberKind, EvidenceSetMember, EvidenceSetReference, KnowledgeScope
from tg_recall.mcp_server import ReadOnlyMCPServer
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.storage import Database


def source_version(timestamp: str, text: str) -> str:
    return sha256(f"{timestamp}\0{text}".encode("utf-8")).hexdigest()


def archive(tmp_path) -> tuple[AppConfig, Database]:
    home = tmp_path / "home"
    cfg = AppConfig.default(home, "work")
    save_config(cfg, home=home)
    db = Database(cfg.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(10, "Allowed", "group"))
    db.upsert_chat(ChatRecord(20, "Other", "group"))
    db.upsert_message(MessageRecord(10, 7, datetime(2026, 1, 2, tzinfo=UTC), "Payment deadline is Friday."))
    db.upsert_message(MessageRecord(20, 8, datetime(2026, 1, 2, tzinfo=UTC), "Hidden payment deadline."))
    db.create_scope("work-scope", [10], "2026-01-01", None, "none", "off")
    citation = "tg://chat/10/message/7"
    current = db.message_context(10, 7, radius=0)[0]
    version = source_version(current.timestamp, current.text)
    evidence = EvidenceSetReference(
        "payment-set", KnowledgeScope("work", (10,)), "Verify payment", "payment deadline",
        (EvidenceSetMember("raw-deadline", EvidenceMemberKind.RAW, "message-7", (citation,), version),),
        "Prior compact payment note.", "2026-01-03T00:00:00Z", "set-v1", ("payment", "deadline"),
    )
    db.record_evidence_set(
        profile_id="work", scope_id="work-scope", evidence_set=evidence,
        source_versions={citation: version},
    )
    return cfg, db


def cli_root(tmp_path) -> list[str]:
    return ["--home", str(tmp_path / "home"), "--profile", "work", "--json"]


def create_session(tmp_path, capsys) -> None:
    assert main([
        *cli_root(tmp_path), "research-session", "create", "payment-session", "--scope", "work-scope",
        "--purpose", "Verify payment deadline", "--summary", "Known cited payment evidence.",
        "--evidence-set", "payment-set", "--at", "2026-01-04T00:00:00Z",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["session"]["scope"] == {"chat_ids": [10], "scope_id": "work-scope"}


def test_research_session_lifecycle_catalog_resume_and_selective_staleness(tmp_path, capsys) -> None:
    _, db = archive(tmp_path)
    create_session(tmp_path, capsys)

    assert main([*cli_root(tmp_path), "knowledge", "query", "payment", "--scope", "work-scope"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["operation"] == "knowledge_query"
    assert {hit["layer"] for hit in catalog["hits"]} == {"raw", "evidence_set"}
    assert "Payment deadline is Friday." not in json.dumps(catalog), "catalog does not duplicate raw content"

    assert main([
        *cli_root(tmp_path), "research-session", "checkpoint", "payment-session",
        "--summary", "Still need verify changes.", "--unresolved", "Was it edited?", "--evidence-set", "payment-set",
        "--at", "2026-01-05T00:00:00Z",
    ]) == 0
    checkpoint = json.loads(capsys.readouterr().out)
    assert checkpoint["session"]["checkpoint_count"] == 2

    assert main([*cli_root(tmp_path), "research-session", "resume", "payment-session"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["refresh"][0]["reusable"] is True

    db.upsert_message(MessageRecord(10, 7, datetime(2026, 1, 2, tzinfo=UTC), "Payment deadline moved to Monday."))
    assert main([*cli_root(tmp_path), "research-session", "selective-refresh", "payment-session"]) == 0
    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["refresh"][0]["stale_member_ids"] == ["raw-deadline"]


def test_agent_writes_are_denied_and_expansion_is_scope_and_budget_bounded(tmp_path, monkeypatch, capsys) -> None:
    cfg, _ = archive(tmp_path)
    create_session(tmp_path, capsys)
    cfg.ai_access = AIAccessPolicy(enabled=True, allowed_chat_ids=[10], max_results=3)
    save_config(cfg, home=tmp_path / "home")
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    assert main([
        *cli_root(tmp_path), "research-session", "create", "denied-session", "--scope", "work-scope",
        "--purpose", "Denied", "--summary", "Denied compact note.",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "agent_operation_forbidden"

    assert main([
        *cli_root(tmp_path), "research-session", "expand", "payment-session",
        "--citation", "tg://chat/10/message/7", "--limit", "1", "--context", "0", "--token-budget", "1000",
    ]) == 0
    expanded = json.loads(capsys.readouterr().out)
    assert expanded["operation"] == "source_expansion"
    assert expanded["items"][0]["citation"] == "tg://chat/10/message/7"
    assert expanded["items"][0]["metadata"]["authority"] == "authoritative"
    assert expanded["accounting"]["estimated_tokens"] <= expanded["accounting"]["usable_payload_budget"]
    assert "Hidden payment" not in json.dumps(expanded)

    assert main([
        *cli_root(tmp_path), "research-session", "expand", "payment-session",
        "--citation", "tg://chat/20/message/8", "--limit", "1", "--token-budget", "1000",
    ]) == 1


def test_agent_catalog_denies_a_partially_allowed_saved_scope_before_raw_search(tmp_path, monkeypatch, capsys) -> None:
    cfg, db = archive(tmp_path)
    db.create_scope("wide-scope", [10, 20], "2026-01-01", None, "none", "off")
    cfg.ai_access = AIAccessPolicy(enabled=True, allowed_chat_ids=[10], max_results=3)
    save_config(cfg, home=tmp_path / "home")
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    assert main([*cli_root(tmp_path), "knowledge", "query", "payment", "--scope", "wide-scope"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "knowledge_scope_narrowed"


def test_mcp_catalog_session_and_expansion_are_read_only_and_scope_checked(tmp_path) -> None:
    cfg, db = archive(tmp_path)
    # Create source session through the public CLI while not in automation mode.
    assert main([
        *cli_root(tmp_path), "research-session", "create", "payment-session", "--scope", "work-scope",
        "--purpose", "Verify payment deadline", "--summary", "Known cited payment evidence.",
        "--evidence-set", "payment-set", "--at", "2026-01-04T00:00:00Z",
    ]) == 0
    cfg.ai_access = AIAccessPolicy(enabled=True, allowed_chat_ids=[10], max_results=3, mcp_research_tools=True)
    server = ReadOnlyMCPServer(cfg, db)
    names = {tool["name"] for tool in server.tools()}
    assert {"query_knowledge_catalog", "inspect_research_session", "expand_cited_sources"} <= names
    assert not {"create_research_session", "checkpoint_research_session", "refresh_research_session"} & names

    catalog = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "query_knowledge_catalog", "arguments": {"query": "payment", "chat_id": 10, "scope_id": "work-scope"},
    }})
    assert "result" in catalog
    session = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "inspect_research_session", "arguments": {"session_id": "payment-session", "chat_id": 10},
    }})
    assert "payment-session" in session["result"]["content"][0]["text"]
    expansion = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "expand_cited_sources", "arguments": {"session_id": "payment-session", "chat_id": 10, "citations": ["tg://chat/10/message/7"], "limit": 1, "token_budget": 1000},
    }})
    payload = json.loads(expansion["result"]["content"][0]["text"])
    assert payload["items"][0]["citation"] == "tg://chat/10/message/7"


def test_mcp_catalog_and_session_reject_date_or_media_policy_narrowing(tmp_path, capsys) -> None:
    cfg, db = archive(tmp_path)
    create_session(tmp_path, capsys)
    cfg.ai_access = AIAccessPolicy(
        enabled=True,
        allowed_chat_ids=[10],
        max_results=3,
        allowed_since="2026-01-03",
        mcp_research_tools=True,
    )
    server = ReadOnlyMCPServer(cfg, db)

    for request_id, name, arguments in (
        (1, "query_knowledge_catalog", {"query": "payment", "chat_id": 10, "scope_id": "work-scope"}),
        (2, "inspect_research_session", {"session_id": "payment-session", "chat_id": 10}),
    ):
        denied = server.handle({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {
            "name": name, "arguments": arguments,
        }})
        assert denied["error"]["data"]["code"] == "knowledge_scope_narrowed"

    cfg.ai_access.allowed_since = None
    db.create_scope("voice-scope", [10], "2026-01-01", None, "voice", "off")
    assert main([
        *cli_root(tmp_path), "research-session", "create", "voice-session", "--scope", "voice-scope",
        "--purpose", "Inspect voice policy", "--summary", "Compact session.", "--at", "2026-01-04T00:00:00Z",
    ]) == 0
    capsys.readouterr()
    cfg.ai_access.allowed_media_types = "photo"
    denied = ReadOnlyMCPServer(cfg, db).handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "inspect_research_session", "arguments": {"session_id": "voice-session", "chat_id": 10},
    }})
    assert denied["error"]["data"]["code"] in {"media_not_allowed", "knowledge_scope_narrowed"}


def test_cross_profile_session_is_not_visible(tmp_path, capsys) -> None:
    _, _ = archive(tmp_path)
    create_session(tmp_path, capsys)
    other_home = tmp_path / "home"
    other = AppConfig.default(other_home, "personal")
    save_config(other, home=other_home)
    assert main([
        "--home", str(other_home), "--profile", "personal", "--json", "research-session", "inspect", "payment-session"
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "KeyError"
