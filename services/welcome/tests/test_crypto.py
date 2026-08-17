import pytest
from cryptography.fernet import Fernet

from gramly_welcome.crypto import TokenDecryptionError, TokenKeyring, decrypt_legacy_django_token


def test_keyring_encrypts_with_latest_version_and_decrypts_old_versions() -> None:
    first = Fernet.generate_key().decode()
    second = Fernet.generate_key().decode()
    keyring = TokenKeyring.parse(f'{{"1":"{first}","2":"{second}"}}')

    old = "v1:" + Fernet(first.encode()).encrypt(b"123:token").decode()
    assert keyring.decrypt(old) == "123:token"
    encrypted = keyring.encrypt("456:token")
    assert encrypted.startswith("v2:")
    assert keyring.decrypt(encrypted) == "456:token"


def test_keyring_rejects_unknown_version_without_leaking_ciphertext() -> None:
    keyring = TokenKeyring.parse(f'{{"2":"{Fernet.generate_key().decode()}"}}')
    with pytest.raises(TokenDecryptionError, match="Unable to decrypt"):
        keyring.decrypt("v1:not-a-token")


def test_legacy_django_token_is_decrypted_for_key_rotation() -> None:
    import base64
    import hashlib

    secret = "legacy-django-secret"
    material = f"gramly-welcome:v1:{secret}".encode()
    legacy_key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    ciphertext = "v1:" + Fernet(legacy_key).encrypt(b"123456:telegram-token").decode()

    assert decrypt_legacy_django_token(ciphertext, secret) == "123456:telegram-token"


def test_legacy_django_token_rejects_wrong_key_without_leaking_ciphertext() -> None:
    with pytest.raises(TokenDecryptionError, match="Unable to decrypt"):
        decrypt_legacy_django_token("v1:not-a-token", "wrong-key")
