from __future__ import annotations

import json

import pytest

import tg_recall.hybrid_retrieval as hybrid
from tg_recall.assistant import local_embedding_provider
from tg_recall.cli import main
from tg_recall.config import AppConfig, load_config
from tg_recall.hybrid_retrieval import OpenRouterEmbeddingProvider, SemanticUnavailableError
from tg_recall.security import CONFIRMATION_PHRASE


def test_openrouter_provider_uses_fixed_endpoint_and_never_exposes_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit): return b'{"data":[{"embedding":[1,2]}]}'

    def fake_urlopen(request, *, timeout):
        seen["url"], seen["auth"], seen["timeout"] = request.full_url, request.headers["Authorization"], timeout
        seen["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(hybrid, "urlopen", fake_urlopen)
    provider = OpenRouterEmbeddingProvider("perplexity/pplx-embed-v1-0.6b", "secret-value", timeout_seconds=9)
    assert provider.embed(["archive text"]) == [(1.0, 2.0)]
    assert provider.metadata.dimensions == 2
    assert seen == {"url": "https://openrouter.ai/api/v1/embeddings", "auth": "Bearer secret-value", "timeout": 9, "payload": {"model": "perplexity/pplx-embed-v1-0.6b", "input": ["archive text"], "encoding_format": "float"}}


def test_openrouter_config_requires_consent_and_never_stores_key(tmp_path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    for marker in ("CI", "CODEX", "AI_TERMINAL", "TG_RECALL_AI_MODE", "TG_ECOSYSTEM_AI_MODE"):
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setenv("TG_RECALL_TRUSTED_AUTOMATION", "1")
    root = ["--home", str(tmp_path / "home"), "--profile", "work", "--json", "--confirm-risk", CONFIRMATION_PHRASE, "config", "embeddings"]
    assert main([*root, "choices"]) == 0
    assert "perplexity/pplx-embed-v1-0.6b" in capsys.readouterr().out
    assert main([*root, "setup", "--provider", "openrouter", "--model", "perplexity/pplx-embed-v1-0.6b"]) == 1
    assert "allow-remote-text" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert main([*root, "setup", "--provider", "openrouter", "--model", "perplexity/pplx-embed-v1-0.6b", "--allow-remote-text"]) == 0
    cfg = load_config(home=tmp_path / "home", profile="work")
    assert cfg.semantic.provider == "openrouter" and cfg.provider_policy.external_embeddings_enabled
    assert "secret" not in json.dumps(json.loads(capsys.readouterr().out))


def test_openrouter_requires_policy_and_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig.default()
    cfg.semantic.enabled, cfg.semantic.provider, cfg.semantic.model = True, "openrouter", "perplexity/pplx-embed-v1-0.6b"
    with pytest.raises(SemanticUnavailableError, match="external_embeddings_disabled"):
        local_embedding_provider(cfg)
    cfg.provider_policy.external_embeddings_enabled = True
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(SemanticUnavailableError, match="openrouter_api_key_missing"):
        local_embedding_provider(cfg)
