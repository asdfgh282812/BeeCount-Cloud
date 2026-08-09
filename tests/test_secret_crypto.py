"""services/secret_crypto.py 加解密往返測試(Phase 14 抽出的共用實作,
totp.py 的 encrypt_totp_secret/decrypt_totp_secret 委派給這裡)。"""
from __future__ import annotations

import pytest

from src.services import secret_crypto, totp


def test_secret_crypto_roundtrip():
    plaintext = "ssm_c0f27646be11aba6db4ab8478af60990a9996ccec0c9f871"
    encrypted = secret_crypto.encrypt(plaintext)
    assert encrypted != plaintext
    assert secret_crypto.decrypt(encrypted) == plaintext


def test_secret_crypto_decrypt_garbage_raises_value_error():
    with pytest.raises(ValueError):
        secret_crypto.decrypt("not-a-valid-fernet-token")


def test_totp_secret_helpers_delegate_to_secret_crypto():
    secret = "JBSWY3DPEHPK3PXP"
    encrypted = totp.encrypt_totp_secret(secret)
    assert totp.decrypt_totp_secret(encrypted) == secret
    # 委派後仍應是同一把 Fernet key 派生邏輯:totp 加密的內容 secret_crypto
    # 也能直接解開,反之亦然。
    assert secret_crypto.decrypt(encrypted) == secret
