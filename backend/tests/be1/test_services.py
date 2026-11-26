"""Unit tests: auth application, worker adapters, LLM clients, MCP client, secret store, webhooks, audit."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.errors import ApiError
from app.services import adapters, llm, mcp_client, webhooks
from app.services.adapters.auth import apply_auth, clear_token_cache, mtls_context, sigv4_sign
from app.services.adapters.http import UpstreamError


def mt(handler):
    return httpx.MockTransport(handler)


# ── apply_auth ──

def test_api_key_header_and_query():
    r = apply_auth({"method": "GET", "url": "https://x/a"}, {"type": "api_key", "key": "K", "name": "x-api-key"})
    assert r["headers"]["x-api-key"] == "K"
    r = apply_auth({"method": "GET", "url": "https://x/a?b=1"}, {"type": "api_key", "key": "K", "placement": "query",
                                                                  "name": "key"})
    assert httpx.URL(r["url"]).params["key"] == "K" and httpx.URL(r["url"]).params["b"] == "1"
    r = apply_auth({"method": "GET", "url": "https://x"}, {"type": "api_key", "key": "K", "prefix": "Token "})
    assert r["headers"]["Authorization"] == "Token K"


def test_bearer_basic_none():
    assert apply_auth({"url": "https://x"}, {"type": "bearer", "token": "T"})["headers"]["Authorization"] == "Bearer T"
    b = apply_auth({"url": "https://x"}, {"type": "basic", "username": "u", "password": "p"})["headers"]["Authorization"]
    assert base64.b64decode(b.split()[1]) == b"u:p"
    assert "Authorization" not in apply_auth({"url": "https://x"}, None)["headers"]


def test_hmac_signs_exact_body():
    r = apply_auth({"method": "POST", "url": "https://x", "json": {"a": 1}}, {"type": "hmac", "secret": "s",
                                                                             "header": "X-Sig"})
    expected = hmac.new(b"s", r["headers"]["X-Timestamp"].encode() + b"." + r["content"], hashlib.sha256).hexdigest()
    assert r["headers"]["X-Sig"] == expected
    m = apply_auth({"method": "POST", "url": "https://x", "files": {"file": ("a.pdf", b"%PDF", "application/pdf")}},
                   {"type": "hmac", "secret": "s"})
    assert {k.lower(): v for k, v in m["headers"].items()}["content-type"].startswith("multipart/form-data") and b"%PDF" in m["content"]


def test_sigv4_aws_test_vector():
    """AWS SigV4 test suite 'get-vanilla'."""
    r = httpx.Request("GET", "https://example.amazonaws.com/")
    sigv4_sign(r, access_key="AKIDEXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", region="us-east-1",
               service="service", now=datetime(2015, 8, 30, 12, 36, tzinfo=timezone.utc), include_content_sha=False)
    assert r.headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_sigv4_via_apply_auth():
    r = apply_auth({"method": "GET", "url": "https://b.s3.me-central-1.amazonaws.com/"},
                   {"type": "aws_sigv4", "access_key_id": "AK", "secret_access_key": "SK", "session_token": "ST",
                    "region": "me-central-1"})
    assert r["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/")
    assert "/me-central-1/s3/" in r["headers"]["Authorization"] and r["headers"]["x-amz-security-token"] == "ST"


def test_oauth2_client_credentials_cached():
    clear_token_cache()
    hits = []

    def h(req):
        hits.append(dict(httpx.QueryParams(req.content.decode())))
        return httpx.Response(200, json={"access_token": f"at{len(hits)}", "expires_in": 3600})

    auth = {"type": "oauth2_client_credentials", "token_url": "https://idp/token", "client_id": "c",
            "client_secret": "s", "scopes": ["a", "b"]}
    a = apply_auth({"url": "https://api/x"}, auth, transport=mt(h))
    b = apply_auth({"url": "https://api/y"}, auth, transport=mt(h))
    assert a["headers"]["Authorization"] == b["headers"]["Authorization"] == "Bearer at1" and len(hits) == 1
    assert hits[0]["grant_type"] == "client_credentials" and hits[0]["scope"] == "a b"
    az = apply_auth({"url": "https://api/x"}, {"type": "azure_ad_client", "tenant_id": "t1", "client_id": "c",
                                               "client_secret": "s2"},
                    transport=mt(lambda req: httpx.Response(200, json={"access_token": "az"})
                                 if "login.microsoftonline.com/t1/" in str(req.url) else httpx.Response(404)))
    assert az["headers"]["Authorization"] == "Bearer az"
    clear_token_cache()
    with pytest.raises(UpstreamError):
        apply_auth({"url": "https://api"}, {**auth, "client_secret": "other"},
                   transport=mt(lambda req: httpx.Response(401, json={"error": "invalid_client"})))


def test_authorization_code_token():
    with pytest.raises(ApiError) as e:
        apply_auth({"url": "https://x"}, {"type": "oauth2_authorization_code", "client_id": "c", "client_secret": "s"})
    assert e.value.code == "authorization_pending"
    r = apply_auth({"url": "https://x"}, {"type": "oauth2_authorization_code", "access_token": "AT"})
    assert r["headers"]["Authorization"] == "Bearer AT"
    clear_token_cache()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = apply_auth({"url": "https://x"}, {"type": "oauth2_authorization_code", "access_token": "old", "expires_at": past,
                                          "refresh_token": "rt", "token_url": "https://idp/t", "client_id": "c"},
                   transport=mt(lambda req: httpx.Response(200, json={"access_token": "fresh"})))
    assert r["headers"]["Authorization"] == "Bearer fresh"


def _self_signed():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "client")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


def test_mtls_context(tmp_path):
    import tempfile

    cert, key = _self_signed()
    before = set(p.name for p in __import__("pathlib").Path(tempfile.gettempdir()).glob("di_*"))
    r = apply_auth({"url": "https://x"}, {"type": "mtls", "client_cert_pem": cert, "client_key_pem": key})
    assert isinstance(r["verify"], ssl.SSLContext)
    after = set(p.name for p in __import__("pathlib").Path(tempfile.gettempdir()).glob("di_*"))
    assert after <= before  # temp PEM files removed
    with pytest.raises(ApiError):
        mtls_context({"client_cert_pem": "x", "client_key_pem": "y"})


# ── worker adapters ──

W_EXCEL = {"base_url": "http://excel:8000", "adapter": "excel_parser_v1", "timeout_s": 5}


def test_excel_adapter_one_block_per_sheet():
    def h(req):
        assert str(req.url) == "http://excel:8000/to-text" and req.headers["x-api-key"] == "K"
        assert b'filename="q.xlsx"' in req.content
        return httpx.Response(200, json={"text_repr": "=== Sheet: Summary ===\nRevenue 1\n=== Sheet: By region ===\nUAE 61%",
                                         "sheet_names": ["Summary", "By region"], "total_cells": 4, "char_count": 50})

    out = adapters.invoke(W_EXCEL, {"type": "api_key", "key": "K", "name": "x-api-key"}, b"PK..", "application/vnd.ms-excel",
                          "q.xlsx", transport=mt(h))
    assert len(out["pages"]) == 2
    assert out["pages"][0]["blocks"][0]["text"].endswith("Revenue 1")
    assert "UAE 61%" in out["pages"][1]["blocks"][0]["text"] and "Revenue" not in out["pages"][1]["blocks"][0]["text"]
    assert out["text"].startswith("=== Sheet") and out["worker_latency_ms"] >= 0


def test_excel_split_fallback():
    assert adapters.ExcelParserV1.split_sheets("abc", ["X", "Y"]) == ["abc", ""]


def test_generic_adapter_validates_contract():
    w = {"base_url": "http://w", "adapter": "generic_pages_v1", "invoke_path": "/invoke"}
    good = {"pages": [{"n": 1, "width": 10, "height": 10, "blocks": [{"text": "a", "bbox": [0, 0, 1, 1]}]}]}
    assert adapters.invoke(w, None, b"x", "image/png", "a.png", transport=mt(lambda r: httpx.Response(200, json=good)))["pages"]
    with pytest.raises(UpstreamError) as e:
        adapters.invoke(w, None, b"x", "image/png", "a.png",
                        transport=mt(lambda r: httpx.Response(200, json={"pages": [{"n": 1}]})))
    assert e.value.code == "invalid_worker_response"
    with pytest.raises(UpstreamError):
        adapters.invoke(w, None, b"x", "image/png", "a.png", transport=mt(lambda r: (_ for _ in ()).throw(
            httpx.ConnectError("refused", request=r))))


def test_paddle_shapes():
    a = adapters.PaddleDocParseV1()
    ocr = a.normalise({"results": [[[[[0, 0], [10, 0], [10, 5], [0, 5]], ["TRN 100", 0.97]]]]})
    assert ocr["pages"][0]["blocks"][0] == {"text": "TRN 100", "bbox": [0.0, 0.0, 10.0, 5.0], "confidence": 0.97}
    pages = a.normalise({"pages": [{"page": 1, "width": 595, "height": 842, "items": [{"text": "A", "box": [1, 2, 3, 4],
                                                                                         "score": 0.9}],
                                    "tables": [{"bbox": [0, 0, 1, 1], "html": "<tr><th>h</th></tr><tr><td>v</td></tr>"}]}]})
    assert pages["pages"][0]["tables"][0]["rows"] == [["h"], ["v"]]
    adapters.validate_pages(pages)
    with pytest.raises(UpstreamError):
        a.normalise({"weird": True})


def test_worker_health_states():
    w = {"base_url": "http://w", "adapter": "generic_pages_v1"}
    assert adapters.health(w, None, transport=mt(lambda r: httpx.Response(200, json={"status": "ok"})))["status"] == "healthy"
    assert adapters.health(w, None, transport=mt(lambda r: httpx.Response(200, json={"status": "degraded"})))["status"] == "degraded"
    assert adapters.health(w, None, transport=mt(lambda r: httpx.Response(503)))["status"] == "unreachable"
    down = adapters.health(w, None, transport=mt(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r))))
    assert down["status"] == "unreachable" and down["checked_at"]


# ── LLM ──

def _prov(kind, **kw):
    return {"id": "prv_t", "kind": kind, "settings": kw.pop("settings", {}), "auth": kw.pop("auth", {"type": "api_key", "key": "K"}), **kw}


@pytest.mark.parametrize("kind,url_part,reply,header", [
    ("anthropic", "api.anthropic.com/v1/messages",
     {"content": [{"type": "text", "text": '{"a": 1}'}], "usage": {"input_tokens": 3, "output_tokens": 4}}, ("x-api-key", "K")),
    ("openai", "api.openai.com/v1/chat/completions",
     {"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3,
                                                                                            "completion_tokens": 4}},
     ("authorization", "Bearer K")),
    ("google_gemini", "generativelanguage.googleapis.com/v1beta/models/m:generateContent",
     {"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}], "usageMetadata": {"promptTokenCount": 3,
                                                                                         "candidatesTokenCount": 4}},
     ("x-goog-api-key", "K")),
    ("ollama", "localhost:11434/api/chat", {"message": {"content": '{"a": 1}'}, "prompt_eval_count": 3, "eval_count": 4},
     ("authorization", "Bearer K")),
])
def test_llm_complete_kinds(kind, url_part, reply, header):
    seen = {}

    def h(req):
        seen["url"], seen["h"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return httpx.Response(200, json=reply)

    out = llm.complete(None, _prov(kind), "m", [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                       json_schema={"type": "object"}, temperature=0.1, transport=mt(h))
    assert url_part in seen["url"] and seen["h"][header[0]] == header[1]
    assert out["json"] == {"a": 1} and out["usage"] == {"input_tokens": 3, "output_tokens": 4}
    if kind == "anthropic":
        assert "sys" in seen["body"]["system"] and seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_llm_azure_and_compatible():
    seen = {}

    def h(req):
        seen["url"], seen["h"] = req.url, req.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    llm.complete(None, _prov("azure_openai", settings={"endpoint": "https://acme.openai.azure.com", "api_version": "v1x"},
                             auth={"type": "api_key", "key": "AZ", "name": "api-key"}), "gpt-prod",
                 [{"role": "user", "content": "x"}], transport=mt(h))
    assert seen["url"].path == "/openai/deployments/gpt-prod/chat/completions" and seen["url"].params["api-version"] == "v1x"
    assert seen["h"]["api-key"] == "AZ"
    llm.complete(None, _prov("openai_compatible", base_url="http://vllm:8000", auth={"type": "none"}), "llama",
                 [{"role": "user", "content": "x"}], transport=mt(h))
    assert str(seen["url"]) == "http://vllm:8000/v1/chat/completions"


def test_llm_errors_and_models():
    with pytest.raises(UpstreamError) as e:
        llm.complete(None, _prov("openai"), "m", [{"role": "user", "content": "x"}],
                     transport=mt(lambda r: httpx.Response(429, json={"error": "rate"})))
    assert e.value.status == 502 and "429" in e.value.detail
    res = llm.test_provider(None, {**_prov("openai"), "connection_id": "con_x", "default_model": "m"},
                            transport=mt(lambda r: (_ for _ in ()).throw(httpx.ConnectError("no", request=r))))
    assert res["ok"] is False
    models = llm.list_models(None, _prov("google_gemini"), transport=mt(lambda r: httpx.Response(200, json={"models": [
        {"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 100}]})))
    assert models == [{"id": "gemini-x", "display_name": "gemini-x", "enabled": True, "context_window": 100,
                       "embedding": False}]
    tags = llm.list_models(None, _prov("ollama", auth={"type": "none"}),
                           transport=mt(lambda r: httpx.Response(200, json={"models": [{"name": "nomic-embed"}]})))
    assert tags[0]["embedding"] is True


# ── MCP ──

def test_mcp_legacy_sse_transport():
    import queue
    import threading

    outbox: "queue.Queue[bytes]" = queue.Queue()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"event: endpoint\ndata: /messages?session=1\n\n"
            while True:
                item = outbox.get(timeout=5)
                if item is None:
                    return
                yield item

    def h(req):
        if req.method == "GET":
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())
        msg = json.loads(req.content)
        assert str(req.url).startswith("https://mcp.test/messages?session=1")
        if "id" in msg:
            result = {"tools": [{"name": "t1", "inputSchema": {"type": "object"}}]} if msg["method"] == "tools/list" else {}
            outbox.put(f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})}\n\n".encode())
        return httpx.Response(202)

    tools = mcp_client.discover({"url": "https://mcp.test/sse", "transport": "sse"}, None, transport=mt(h))
    outbox.put(None)
    assert tools == [{"name": "t1", "description": "", "input_schema": {"type": "object"}, "enabled": True}]
    assert threading.active_count() >= 1


def test_mcp_error_and_validation():
    def h(req):
        msg = json.loads(req.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32602, "message": "bad"}})

    with pytest.raises(UpstreamError) as e:
        mcp_client.call_tool({"url": "https://m", "transport": "streamable_http"}, None, "x", {}, transport=mt(h))
    assert e.value.code == "mcp_error"
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}, "n": {"type": "integer"}},
              "additionalProperties": False}
    assert mcp_client.validate_arguments(schema, {"a": "x", "n": 2}) == []
    assert mcp_client.validate_arguments(schema, {"n": "2", "z": 1})
    assert mcp_client._mini_validate(schema, {"n": True, "z": 1}, "")  # bool is not an integer; extra prop; missing a


# ── secret store ──

def test_secret_store_roundtrip(db):
    from app.db.models import Secret
    from app.services import secret_store
    from app.services.secret_store import LocalSecretStore, hint

    ref = secret_store.put(db, "ws_default", {"type": "bearer", "token": "plain-TOKEN"}, purpose="test")
    assert ref.startswith("local:sec_")
    row = db.get(Secret, ref.split(":", 1)[1])
    assert "plain-TOKEN" not in row.ciphertext and row.purpose == "test"
    assert secret_store.get(db, ref)["token"] == "plain-TOKEN"
    assert secret_store.rotate(db, ref, {"type": "bearer", "token": "t2"}) == ref and row.version == 2
    assert secret_store.get(db, ref)["token"] == "t2"
    with pytest.raises(KeyError):
        LocalSecretStore("another-master-key").get(db, ref)
    secret_store.delete(db, ref)
    with pytest.raises(KeyError):
        secret_store.get(db, ref)
    assert hint("abcdefRk3w") == "••••Rk3w" and hint("") is None and hint(None) is None
    db.rollback()


# ── webhooks ──

def test_webhook_retries_then_fails(db, monkeypatch):
    from app.db.models import Webhook, WebhookDelivery
    from app.services import secret_store

    monkeypatch.setattr(webhooks, "RETRY_DELAYS_S", [0, 0])
    monkeypatch.setattr(webhooks, "SYNC_DELIVERY", True)
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(500, text="nope")

    webhooks.set_transport(mt(h))
    try:
        w = Webhook(workspace_id="ws_default", url="https://h.test/x", events=["run.failed"], enabled=True,
                    pipeline_ids=["pl_exp"],
                    secret_ref=secret_store.put(db, "ws_default", {"type": "hmac", "secret": "whsec_1"}))
        db.add(w)
        db.commit()
        first = webhooks.emit(db, "ws_default", "run.failed", {"pipeline_id": "pl_ap"})
        db.commit()
        assert len(first) == 1  # only the seeded all-pipelines webhook matches pl_ap
        n["c"] = 0
        ids = webhooks.emit(db, "ws_default", "run.failed", {"run_id": "r", "pipeline_id": "pl_exp"})
        db.commit()
        d = next(x for x in (db.get(WebhookDelivery, i) for i in ids) if x.webhook_id == w.id)
        db.refresh(d)
        assert d.status == "failed" and d.attempt == 3 and d.response_status == 500 and n["c"] == 3 * len(ids)
        sent = n["c"]
        # rollback → nothing delivered
        ids2 = webhooks.emit(db, "ws_default", "run.failed", {"run_id": "r2", "pipeline_id": "pl_exp"})
        db.rollback()
        assert db.get(WebhookDelivery, ids2[0]) is None and n["c"] == sent
    finally:
        webhooks.set_transport(None)


def test_webhook_background_thread(db, monkeypatch):
    from app.db.models import Webhook, WebhookDelivery

    monkeypatch.setattr(webhooks, "RETRY_DELAYS_S", [])
    webhooks.set_transport(mt(lambda r: httpx.Response(200)))
    try:
        db.add(Webhook(workspace_id="ws_default", url="https://h.test/y", events=["review.created"], enabled=True,
                       pipeline_ids=[]))
        db.commit()
        ids = webhooks.emit(db, "ws_default", "review.created", {"review_item_id": "rv9"})
        db.commit()
        webhooks.wait_for_deliveries(5)
        db.expire_all()
        assert all(db.get(WebhookDelivery, i).status == "succeeded" for i in ids) and ids
    finally:
        webhooks.set_transport(None)


# ── audit ──

def test_audit_scrubs_secrets(db):
    from sqlalchemy import select

    from app.core.security import AuthContext
    from app.db.models import AuditEvent
    from app.services import audit

    ctx = AuthContext(workspace_id="ws_default", kind="api_key", principal_id="key_x", name="K", scopes=frozenset())
    token = audit.current_ip.set("10.1.2.3")
    try:
        audit.record(db, ctx, "x.did", "thing", "t1", summary="s", details={"token": "T", "nested": {"password": "p",
                                                                                                    "ok": 1}})
    finally:
        audit.current_ip.reset(token)
    db.flush()
    e = db.scalar(select(AuditEvent).where(AuditEvent.resource_id == "t1"))
    assert e.details == {"nested": {"ok": 1}} and e.ip == "10.1.2.3" and e.actor["kind"] == "api_key"
    db.rollback()
