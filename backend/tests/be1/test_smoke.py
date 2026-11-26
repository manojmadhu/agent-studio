import pytest

GETS = ["/v1/me", "/v1/api-keys", "/v1/members", "/v1/catalog/step-types", "/v1/catalog/services", "/v1/connections",
        "/v1/providers", "/v1/workers", "/v1/mcp-servers", "/v1/knowledge-collections", "/v1/skills", "/v1/policies",
        "/v1/metrics/overview", "/v1/audit-events", "/v1/webhooks", "/v1/settings",
        "/v1/connections/con_anthropic", "/v1/connections/con_anthropic/usage", "/v1/providers/prv_anthropic",
        "/v1/workers/wrk_paddle", "/v1/mcp-servers/mcp_vendors", "/v1/skills/skl_invoice"]


@pytest.mark.parametrize("path", GETS)
def test_get_ok(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.text[:500])
    body = r.json()
    assert isinstance(body, dict)
