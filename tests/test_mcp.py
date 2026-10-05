"""Drive the real /mcp endpoint with the official MCP client over streamable HTTP."""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from mcp.client import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from sebastian.app import create_app


@pytest.fixture
def server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(f"sqlite:///{tmp_path / 'mcp.db'}")
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    thread.join(timeout=5)


def authed(url, key="test-key"):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {key}"})
    return Client(streamable_http_client(url, http_client=http))


def data(result):
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


def test_mcp_requires_bearer_token(server):
    async def go():
        with pytest.raises(Exception):  # noqa: B017  (401 surfaces as a transport error)
            async with authed(server, key="wrong") as client:
                await client.list_tools()

    asyncio.run(go())


def test_mcp_agent_flow(server):
    async def go():
        async with authed(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"get_due", "complete_task", "add_entry", "entry_summary"} <= names

            await client.call_tool(
                "create_task", {"title": "Pay credit card", "note": "ING", "nag_interval_min": 30}
            )
            due = data(await client.call_tool("get_due", {}))
            assert due["count"] == 1
            tid = due["items"][0]["id"]
            await client.call_tool("mark_notified", {"task_id": tid})
            assert data(await client.call_tool("get_due", {}))["count"] == 0

            found = data(await client.call_tool("list_tasks", {"title_contains": "credit"}))
            assert [t["id"] for t in found] == [tid]
            done = data(await client.call_tool("complete_task", {"task_id": tid, "note": "paid"}))
            assert done["status"] == "done" and "paid" in done["remarks"]

            # categories are enforced and tool errors carry the valid choices
            bad = await client.call_tool("add_entry", {"category": "transit"})
            assert bad.is_error
            assert "Note" in bad.content[0].text
            await client.call_tool(
                "create_category",
                {"name": "Public Transport", "description": "count each trip"},
            )
            await client.call_tool("add_entry", {"category": "public transport"})
            await client.call_tool("add_entry", {"category": "Note", "text": "Pizza 18 EUR"})
            summ = data(await client.call_tool("entry_summary", {"category": "Public Transport"}))
            assert summ["categories"][0]["total_entries"] == 1
            cats = data(await client.call_tool("list_categories", {}))
            assert {c["name"] for c in cats} == {"Note", "Public Transport"}

    asyncio.run(go())
