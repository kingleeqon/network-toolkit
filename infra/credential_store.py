# -*- coding: utf-8 -*-
"""凭证库（对应 PRD FR-121 / 15.6.5）。

加密优先 Windows DPAPI（pywin32），降级 Fernet（cryptography + 本地密钥文件）。
红线：任何情况下禁止明文落盘（SEC-01）。
"""
import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.errors import DependencyMissing
from core.result_writer import app_data_dir

STORE_FILE = "credentials.enc"
KEY_FILE = "vault.key"


@dataclass
class Credential:
    name: str = ""
    username: str = ""
    secret: str = ""
    enable: str = ""
    auth_type: str = "password"
    remark: str = ""


def _store_path():
    return app_data_dir() / STORE_FILE


def _key_path():
    return app_data_dir() / KEY_FILE


def _dpapi_encrypt(data):
    try:
        import win32crypt
    except ImportError:
        return None
    blob = win32crypt.CryptProtectData(data, "NetworkToolkit", None, None, None, 0)
    return base64.b64encode(blob).decode("ascii")


def _dpapi_decrypt(text):
    try:
        import win32crypt
    except ImportError:
        return None
    try:
        blob = base64.b64decode(text)
        _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data
    except Exception:
        return None


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    key_path = _key_path()
    if not key_path.exists():
        key = Fernet.generate_key()
        with open(str(key_path), "wb") as f:
            f.write(key)
        try:
            os.chmod(str(key_path), 0o600)
        except Exception:
            pass
    with open(str(key_path), "rb") as f:
        return Fernet(f.read())


def _encrypt(plain):
    data = plain.encode("utf-8")
    out = _dpapi_encrypt(data)
    if out is not None:
        return "dpapi", out
    f = _fernet()
    if f is None:
        raise DependencyMissing("缺少加密依赖：请安装 pywin32（DPAPI）或 cryptography（Fernet 降级）")
    return "fernet", f.encrypt(data).decode("ascii")


def _decrypt(cipher, text):
    if cipher == "dpapi":
        data = _dpapi_decrypt(text)
        return data.decode("utf-8") if data else None
    if cipher == "fernet":
        f = _fernet()
        if f is None:
            return None
        try:
            return f.decrypt(text.encode("ascii")).decode("utf-8")
        except Exception:
            return None
    return None


def _load_raw():
    path = _store_path()
    if not path.exists():
        return {"version": 1, "cipher": "dpapi", "items": []}
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 1, "cipher": "dpapi", "items": []}


def _save_raw(raw):
    with open(str(_store_path()), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def list_credentials():
    return [item.get("name", "") for item in _load_raw().get("items", [])]


def get_credential(ref):
    """按引用名取解密后的凭证；不存在或解密失败返回 None。"""
    if not ref:
        return None
    raw = _load_raw()
    for item in raw.get("items", []):
        if item.get("name") != ref:
            continue
        secret = _decrypt(item.get("cipher", "dpapi"), item.get("secret_enc", ""))
        enable = _decrypt(item.get("cipher", "dpapi"), item.get("enable_enc", "")) if item.get("enable_enc") else ""
        if secret is None:
            return None
        return Credential(name=ref, username=item.get("username", ""),
                          secret=secret, enable=enable or "", auth_type=item.get("auth_type", "password"))
    return None


def set_credential(name, username, secret, enable="", auth_type="password", remark=""):
    cipher, enc_secret = _encrypt(secret)
    cipher2, enc_enable = _encrypt(enable) if enable else (cipher, "")
    raw = _load_raw()
    items = [i for i in raw.get("items", []) if i.get("name") != name]
    items.append({
        "name": name,
        "username": username,
        "secret_enc": enc_secret,
        "enable_enc": enc_enable,
        "auth_type": auth_type,
        "remark": remark,
        "cipher": cipher,
    })
    raw["items"] = items
    raw["cipher"] = cipher
    _save_raw(raw)
    return True


def delete_credential(name):
    raw = _load_raw()
    raw["items"] = [i for i in raw.get("items", []) if i.get("name") != name]
    _save_raw(raw)
    return True
