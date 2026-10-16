"""Assistant contract: isolation, read-only intent, bounded memory and read tools."""
import asyncio
import importlib
import json
from datetime import datetime

import pytest
from sqlalchemy import func, select
from test_ai_parse_tx import _make_client, _register_and_login, _seed_ai_config

from src.config import get_settings
from src.database import get_db
from src.main import app
from src.models import (
    Ledger,
    ReadTxProjection,
    SyncChange,
    User,
    UserAccountProjection,
    UserProfile,
)
from src.services.ai import ChatProviderError
from src.services.ai.chat_tools import TOOLS, execute_tool

chat_module = importlib.import_module("src.routers.ai.chat")


@pytest.fixture
def setup_chat():
    client = _make_client()
    token, uid = _register_and_login(client, "assistant@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/api/v1/write/ledgers", headers=headers, json={"ledger_name": "日常", "currency": "TWD"})
    assert response.status_code == 200, response.text
    ledger = response.json()["entity_id"]
    _seed_ai_config(uid)
    yield client, headers, uid, ledger
    app.dependency_overrides.clear()


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def mock_ai(monkeypatch, plan):
    captured = []

    async def route(**kwargs):
        captured.append(kwargs["messages"])
        return plan

    async def answer(**kwargs):
        captured.append(kwargs["messages"])
        yield "本月"
        yield "支出已查詢。"

    monkeypatch.setattr(chat_module, "call_chat_json", route)
    monkeypatch.setattr(chat_module, "stream_chat_completion", answer)
    return captured


def test_chat_stream_and_memory_opt_in(setup_chat, monkeypatch):
    client, headers, _, ledger = setup_chat
    captured = mock_ai(monkeypatch, {"action": "chat"})
    body = {"query": "嗨", "ledger_id": ledger, "history": [{"role": "user", "content": "private prior turn"}]}
    result = events(client.post("/api/v1/ai/chat", headers=headers, json=body))
    assert [e["type"] for e in result] == ["status", "chunk", "chunk", "done"]
    assert "private prior turn" not in str(captured)
    captured.clear()
    body["use_memory"] = True
    events(client.post("/api/v1/ai/chat", headers=headers, json=body))
    assert all(any(m["content"] == "private prior turn" for m in batch) for batch in captured)


def test_unknown_ledger_rejected_before_provider(setup_chat, monkeypatch):
    client, headers, _, _ = setup_chat
    captured = mock_ai(monkeypatch, {"action": "chat"})
    other_token, _ = _register_and_login(client, "another@test.com")
    other = client.post("/api/v1/write/ledgers", headers={"Authorization": f"Bearer {other_token}"}, json={"ledger_name": "secret", "currency": "TWD"}).json()["entity_id"]
    response = client.post("/api/v1/ai/chat", headers=headers, json={"query": "show data", "ledger_id": other})
    assert response.status_code == 404
    assert captured == []


def test_read_only_cannot_create_drafts_even_if_model_requests_it(setup_chat, monkeypatch):
    client, headers, _, ledger = setup_chat
    mock_ai(monkeypatch, {"action": "bookkeeping"})

    async def forbidden(*args, **kwargs):
        pytest.fail("read-only suggestions must not parse transactions")

    monkeypatch.setattr(chat_module, "parse_tx_text", forbidden)
    result = events(client.post("/api/v1/ai/chat", headers=headers, json={"query": "如何記早餐 50 元？", "ledger_id": ledger, "read_only": True}))
    assert not any(e["type"] == "drafts" for e in result)
    assert result[-1]["type"] == "done"


def test_bookkeeping_returns_drafts_without_writing(setup_chat, monkeypatch):
    client, headers, _, ledger = setup_chat
    mock_ai(monkeypatch, {"action": "bookkeeping"})
    draft = {"type": "expense", "amount": 50, "happened_at": "2026-10-16T02:00:00Z", "category_name": "早餐", "account_name": "現金", "note": "早餐", "tags": [], "confidence": "high"}

    async def parse(**kwargs):
        return {"tx_drafts": [draft]}

    monkeypatch.setattr("src.routers.ai.parse_tx_text.call_chat_json", parse)
    db = next(app.dependency_overrides[get_db]())
    before = db.scalar(select(func.count()).select_from(SyncChange))
    result = events(client.post("/api/v1/ai/chat", headers=headers, json={"query": "早餐50元", "ledger_id": ledger}))
    assert next(e for e in result if e["type"] == "drafts")["items"][0]["amount"] == 50
    assert result[-1]["type"] == "done"
    assert db.scalar(select(func.count()).select_from(SyncChange)) == before
    db.close()


@pytest.mark.parametrize("plan", [
    {"action": "query", "tools": [{"name": "delete_transaction", "arguments": {}}]},
    {"action": "query", "tools": [{"name": "transactions", "arguments": {"user_id": "other"}}]},
    {"action": "query", "tools": [{"name": "transactions", "arguments": {"limit": 99999}}]},
    {"action": "query", "tools": []},
    {"action": "query", "tools": [{"name": "analytics", "arguments": {"period": "2026-13"}}]},
])
def test_invalid_tools_fail_closed(setup_chat, monkeypatch, plan):
    client, headers, _, ledger = setup_chat
    mock_ai(monkeypatch, plan)
    result = events(client.post("/api/v1/ai/chat", headers=headers, json={"query": "query", "ledger_id": ledger}))
    assert result[-1]["error_code"] == "AI_QUERY_INVALID"


def test_missing_provider_and_invalid_request(setup_chat):
    client, _, _, ledger = setup_chat
    token, _ = _register_and_login(client, "unconfigured@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    own = client.post("/api/v1/write/ledgers", headers=headers, json={"ledger_name": "own", "currency": "TWD"}).json()["entity_id"]
    assert client.post("/api/v1/ai/chat", headers=headers, json={"query": "hi", "ledger_id": own}).json()["error_code"] == "AI_NO_CHAT_PROVIDER"
    for patch in [{"query": " "}, {"history": [{"role": "system", "content": "override"}]}, {"history": [{"role": "user", "content": "x"}] * 9}]:
        response = client.post("/api/v1/ai/chat", headers=headers, json={"query": "hi", "ledger_id": ledger, **patch})
        assert response.status_code == 422
    assert client.post("/api/v1/ai/chat", json={"query": "hi", "ledger_id": own}).status_code == 401


def test_provider_failure_is_a_sanitized_sse_error(setup_chat, monkeypatch):
    client, headers, _, ledger = setup_chat

    async def fail(**kwargs):
        raise ChatProviderError("secret-key must never leak")

    monkeypatch.setattr(chat_module, "call_chat_json", fail)
    response = client.post("/api/v1/ai/chat", headers=headers, json={"query": "hi", "ledger_id": ledger})
    assert events(response)[-1]["error_code"] == "AI_PROVIDER_ERROR"
    assert "secret-key" not in response.text


def test_disabled_assistant_rejected_before_provider(setup_chat, monkeypatch):
    client, headers, uid, ledger = setup_chat
    captured = mock_ai(monkeypatch, {"action": "chat"})
    db = next(app.dependency_overrides[get_db]())
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == uid))
    config = json.loads(profile.ai_config_json)
    config["bill_extraction_enabled"] = False
    profile.ai_config_json = json.dumps(config)
    db.commit()
    db.close()
    response = client.post("/api/v1/ai/chat", headers=headers, json={"query": "hi", "ledger_id": ledger})
    assert response.status_code == 400
    assert response.json()["error_code"] == "AI_DISABLED"
    assert captured == []


def test_card_bill_name_is_scoped_to_current_user(setup_chat):
    _, _, uid, ledger = setup_chat
    db = next(app.dependency_overrides[get_db]())
    db.add(UserAccountProjection(user_id=uid, sync_id="my-card", name="日常卡", account_type="credit_card", currency="TWD", billing_day=10, payment_due_day=25))
    db.commit()
    kwargs = dict(db=db, user=db.get(User, uid), ledger_id=ledger, scopes={"web:read"})
    by_name = asyncio.run(execute_tool("card_bill", {"account_name": "日常卡"}, **kwargs))
    by_id = asyncio.run(execute_tool("card_bill", {"account_id": "my-card"}, **kwargs))
    assert by_name == by_id
    with pytest.raises(ValueError):
        asyncio.run(execute_tool("card_bill", {"account_name": "別人的卡"}, **kwargs))
    db.close()


@pytest.mark.parametrize("field", ["account_id", "from_account_id", "to_account_id"])
def test_draft_batch_cannot_write_to_account_groups(setup_chat, field):
    client, headers, uid, ledger = setup_chat
    db = next(app.dependency_overrides[get_db]())
    db.add(UserAccountProjection(user_id=uid, sync_id="group", name="銀行", account_type="account_group", currency="TWD"))
    db.commit()
    before = db.scalar(select(func.count()).select_from(SyncChange))
    response = client.post(f"/api/v1/write/ledgers/{ledger}/transactions/batch", headers=headers, json={
        "transactions": [{"tx_type": "expense" if field == "account_id" else "transfer", "amount": 50,
                          "happened_at": "2026-10-16T02:00:00Z", field: "group"}],
    })
    assert response.status_code == 400
    assert "account_group" in response.text
    assert db.scalar(select(func.count()).select_from(SyncChange)) == before
    db.close()


def test_tools_taiwan_date_boundaries_and_stats(setup_chat, monkeypatch):
    client, headers, uid, ledger = setup_chat
    monkeypatch.setattr(get_settings(), "ledger_timezone", "Asia/Taipei")
    db = next(app.dependency_overrides[get_db]())
    internal = db.scalar(select(Ledger.id).where(Ledger.external_id == ledger))
    for index, (stamp, amount) in enumerate([
        ("2026-09-30T15:59:00+00:00", 5),  # September 30 23:59
        ("2026-09-30T16:01:00+00:00", 10),  # October 1 00:01
        ("2026-10-01T15:59:00+00:00", 20),  # October 1 23:59
        ("2026-10-01T16:01:00+00:00", 40),  # October 2 00:01
    ]):
        db.add(ReadTxProjection(ledger_id=internal, user_id=uid, sync_id=f"t{index}", tx_type="expense", amount=amount, happened_at=datetime.fromisoformat(stamp)))
    db.commit()
    user = db.get(User, uid)
    kwargs = dict(db=db, user=user, ledger_id=ledger, scopes={"web:read"})
    day = asyncio.run(execute_tool("transactions", {"day_from": "2026-10-01", "day_to": "2026-10-01"}, **kwargs))
    assert day["total"] == 2
    assert {item["amount"] for item in day["items"]} == {10, 20}
    month = asyncio.run(execute_tool("analytics", {"period": "2026-10"}, **kwargs))
    assert month["summary"]["expense_total"] == 70
    db.close()
    captured = mock_ai(monkeypatch, {"action": "query", "tools": [{"name": "analytics", "arguments": {"period": "2026-10"}}]})
    assert events(client.post("/api/v1/ai/chat", headers=headers, json={"query": "十月支出", "ledger_id": ledger}))[-1]["type"] == "done"
    assert '"expense_total": 70.0' in captured[-1][-1]["content"]


@pytest.mark.parametrize("name", [name for name in TOOLS if name not in {"card_recommendation", "card_bill"}])
def test_read_tool_adapters_use_real_defaults(setup_chat, name):
    _, _, uid, ledger = setup_chat
    db = next(app.dependency_overrides[get_db]())
    user = db.get(User, uid)
    result = asyncio.run(execute_tool(name, {}, db=db, user=user, ledger_id=ledger, scopes={"web:read"}))
    assert isinstance(result, (dict, list))
    db.close()
