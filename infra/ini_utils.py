# -*- coding: utf-8 -*-
"""SecureCRT 会话 .ini 文件的读写公共工具（收敛自多个会话管理脚本）。

统一处理编码自适应、字段提取、批量修改。所有写操作默认先备份。
"""
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 多脚本尝试的编码顺序
_ENCODINGS = ("utf-8", "gbk", "gb2312", "latin-1")

# SecureCRT 会话 ini 常见字段（键名 -> 提取正则）
_FIELD_PATTERNS = {
    "username": re.compile(r'S:"Username"\s*=\s*(.+)'),
    "hostname": re.compile(r'S:"Hostname"\s*=\s*(.+)'),
    "password": re.compile(r'S:"Password V2"\s*=\s*(.+)'),
    "login_script": re.compile(r'S:"Login Script V3"\s*=\s*(.+)'),
}


def read_ini(path, encodings=_ENCODINGS):
    """按多种编码读文本，全部失败返回 None。"""
    for enc in encodings:
        try:
            with open(str(path), "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    return None


def extract_field(content, field):
    """从 ini 文本提取字段值，未找到返回 None。"""
    pattern = _FIELD_PATTERNS.get(field)
    if pattern is None:
        return None
    m = pattern.search(content or "")
    return m.group(1).strip() if m else None


def iter_ini_files(directory, recursive=True):
    """遍历目录下所有 .ini / .INI 文件，返回 Path 列表。"""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    pattern = "**/*.ini" if recursive else "*.ini"
    files = list(directory.glob(pattern))
    files += list(directory.glob(pattern.upper()))
    # 去重（大小写不敏感文件系统下 glob 可能重复）
    seen, out = set(), []
    for p in files:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def backup_ini(path, backup_root=None):
    """复制 ini 到备份目录，返回备份路径；失败返回 None。"""
    path = Path(path)
    backup_root = Path(backup_root) if backup_root else path.parent / "_backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_root / ("%s.%s.bak" % (path.name, stamp))
    try:
        shutil.copy2(str(path), str(dest))
        return dest
    except Exception:
        return None


def replace_fields(content, replacements):
    """按字段名批量替换/追加值。replacements: {field: value}。

    返回 (new_content, changed_fields)。
    存在则替换，不存在则追加到文件末尾。
    """
    changed = []
    new_content = content
    for field, value in (replacements or {}).items():
        if value is None:
            continue
        key = _field_key(field)
        if not key:
            continue
        pattern = re.compile(re.escape(key) + r"=.*")
        replacement = "%s=%s" % (key, value)
        if pattern.search(new_content):
            new_line = replacement
            if new_content.endswith(("\n", "\r")):
                new_line += "\n"
            new_content = pattern.sub(lambda _m: replacement, new_content, count=1)
            changed.append(field)
        else:
            new_content = new_content.rstrip("\n") + "\n" + replacement + "\n"
            changed.append(field)
    return new_content, changed


def _field_key(field):
    """字段名 -> ini 键名（如 username -> S:"Username"）。"""
    mapping = {
        "username": 'S:"Username"',
        "hostname": 'S:"Hostname"',
        "password": 'S:"Password V2"',
        "login_script": 'S:"Login Script V3"',
        "session_password_saved": 'D:"Session Password Saved"',
        "pre_connect": 'S:"Local Shell Command Pre-connect V2"',
        "emulation": 'S:"Emulation"',
        "initial_cr": 'D:"Send Initial Carriage Return"',
    }
    return mapping.get(field, field)
