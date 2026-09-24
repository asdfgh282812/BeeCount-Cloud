import os

# Keep guardrail tests deterministic regardless of local .env defaults.
os.environ.setdefault("ALLOW_APP_RW_SCOPES", "false")
# Self-hosted registration defaults to off; test suite needs it on so the
# fixture helpers that create users via POST /auth/register keep working.
os.environ.setdefault("REGISTRATION_ENABLED", "true")
# 授權金鑰(docs/LICENSE_KEYS.md)會擋所有非 admin 帳號;既有測試大量用
# 一般帳號打 API,這裡整體關掉,需要驗證授權行為的測試(test_license_*.py)
# 再用 monkeypatch 開回來。`services/license.py::is_enforcement_enabled`
# 只在 APP_ENV=development/test 時才認這個開關。
os.environ.setdefault("LICENSE_ENFORCEMENT", "false")
