# -*- coding: utf-8 -*-
"""设备清单导入（对应 PRD FR-120 / 15.6.4）。

统一字段：ip / hostname / vendor / protocol / port / credential_ref / group / remark
来源：xlsx（openpyxl，可选依赖）、xls（xlrd，可选依赖）、csv（标准库）、txt（标准库）

**按文件头判断真实格式，不看扩展名**：实测用户的清单扩展名是 .xlsx、内容却是老式
Excel 二进制（OLE2 复合文档），只看扩展名会让 openpyxl 报 "File is not a zip file"。
"""
import csv
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_CREDENTIAL_REF = ""

# 表头映射（大小写/空格不敏感）
FIELD_ALIASES = {
    "ip": ["ip", "ip地址", "管理ip", "管理地址", "host", "address", "地址", "设备管理ip"],
    "hostname": ["设备名", "hostname", "name", "名称", "sysname", "设备名称",
                 "资源名称", "资源名"],
    "vendor": ["厂商", "vendor", "brand", "品牌", "资源类型", "设备类型"],
    "protocol": ["协议", "protocol", "登录方式"],
    "port": ["端口", "port"],
    "credential_ref": ["凭证", "credential", "账密组", "账号组", "auth", "认证组"],
    # 现场设备清单直接带账号密码（设备名称/厂商/IP/用户名/密码/port/enable password）
    "username": ["用户名", "username", "账号", "登录用户", "登录名", "user"],
    "password": ["密码", "password", "pwd", "口令"],
    "enable_password": ["enable password", "enablepassword", "enable密码",
                         "enable口令", "特权密码", "super密码"],
    "group": ["分组", "group", "区域", "site"],
    "remark": ["备注", "remark", "note", "描述"],
}

# 疑似密码列（导入时告警）
PASSWORD_LIKE_HEADERS = ("password", "pwd", "密码", "口令", "secret", "enable")


@dataclass
class Device:
    ip: str = ""
    hostname: str = ""
    vendor: str = ""
    protocol: str = "ssh"
    port: int = 22
    credential_ref: str = ""
    username: str = ""          # 清单里直接带的登录用户名
    password: str = ""          # 清单里直接带的登录密码（只在内存里，不落盘/不进日志）
    enable_password: str = ""   # 特权密码（enable）
    group: str = ""
    remark: str = ""

    def __repr__(self):
        return "Device(%s)" % (self.hostname or self.ip)


def _normalize(text):
    return re.sub(r"[\s_\-]", "", str(text or "")).lower()


_PLACEHOLDER = ("none", "null", "nan", "nil", "-", "--", "n/a")


def _clean(value):
    """把占位字符串当空值。

    现场 Excel 里"enable password""port"这些空单元格存的是文本 "None"，
    直接当密码传给设备会导致认证失败。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in _PLACEHOLDER:
        return ""
    return text


def _build_alias_index():
    index = {}
    for field_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            index[_normalize(alias)] = field_name
    return index


_ALIAS_INDEX = _build_alias_index()


def _map_header(header):
    return _ALIAS_INDEX.get(_normalize(header))


def _valid_ip(text):
    try:
        ipaddress.ip_address(str(text).strip())
        return True
    except ValueError:
        return False


def _extract_ip(text):
    """从单元格里取出第一个合法 IP。

    清单里一个 IP 单元格可能写成 "10,182.15.201"（逗号当小数点）或
    "10.182.15.201,10.182.15.202"（多个地址），直接整串校验会全部判非法。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _valid_ip(raw):
        return raw
    for sep in (",", "，", ";", "；", " ", "\t"):
        if sep in raw:
            for part in raw.replace("，", ",").replace("；", ";").split(sep):
                part = part.strip()
                if _valid_ip(part):
                    return part
    # 逗号当小数点用的情况："10,182,15,201" -> "10.182.15.201"
    if raw.count(",") == 3 and "." not in raw:
        cand = raw.replace(",", ".")
        if _valid_ip(cand):
            return cand
    return raw


def resolve_default_credential():
    """清单里没有「凭证」列时的兜底：优先名字叫 default 的，否则取唯一一条。

    返回 ("", 说明) 表示没有可用的默认凭证。
    """
    try:
        from infra.credential_store import list_credentials
        names = [n for n in list_credentials() if n]
    except Exception:
        return "", ""
    if not names:
        return "", ""
    if "default" in names:
        return "default", ""
    if len(names) == 1:
        return names[0], ""
    return "", ""


def load_devices(path, default_credential_ref=DEFAULT_CREDENTIAL_REF):
    """返回 (devices, errors)。errors 为字符串列表，用于界面展示。"""
    path = Path(path)
    errors = []
    if not path.exists():
        return [], ["文件不存在：%s" % path]

    suffix = path.suffix.lower()
    fmt = _sniff_format(path)
    if fmt == "xlsx":
        rows = _read_xlsx(path, errors)
    elif fmt == "xls":
        rows = _read_xls(path, errors)
    elif suffix in (".csv", ".tsv"):
        rows = _read_csv(path, errors)
    else:
        rows = _read_txt(path, errors)

    return _parse_rows(rows, errors, default_credential_ref)


def _sniff_format(path):
    """按文件头判断真实格式：xlsx(zip) / xls(OLE2) / 文本。

    扩展名不可靠：Excel 里"另存为 CSV"再改成 .xlsx、或老版另存为 .xls 改后缀，
    都会出现扩展名和真实格式对不上的情况。
    """
    try:
        with open(str(path), "rb") as f:
            head = f.read(8)
    except Exception:
        return "text"
    if head[:2] == b"PK":
        return "xlsx"
    if head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"
    return "text"


def _read_xls(path, errors):
    """读老式 Excel(.xls)，需要 xlrd（xlrd 2.x 只支持 .xls，正好）。"""
    try:
        import xlrd
    except ImportError:
        errors.append("这是老式 Excel(.xls) 文件，需要 xlrd 才能读取；"
                      "请执行 pip install xlrd，或在 Excel 里另存为 .xlsx / .csv")
        return []
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
        sheet = book.sheet_by_index(0)
        rows = []
        for r in range(sheet.nrows):
            vals = sheet.row_values(r)
            if all(str(v).strip() == "" for v in vals):
                continue
            rows.append(list(vals))
        try:
            book.release_resources()
        except Exception:
            pass
        return rows
    except Exception as exc:
        errors.append("老式 Excel(.xls) 读取失败：%s" % exc)
        return []


def _read_xlsx(path, errors):
    try:
        from openpyxl import load_workbook
    except ImportError:
        errors.append("未安装 openpyxl，无法读取 xlsx；请执行 pip install openpyxl")
        return []
    try:
        wb = load_workbook(filename=str(path), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = []
        for r in ws.iter_rows(values_only=True):
            if r is None or all(c is None or str(c).strip() == "" for c in r):
                continue
            rows.append(list(r))
        wb.close()
        return rows
    except Exception as exc:
        errors.append("Excel 读取失败（文件是否被 Excel 占用？）：%s" % exc)
        return []


def _read_csv(path, errors):
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(str(path), "r", encoding=enc, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
                if delimiter == "," and sample.count(";") > sample.count(","):
                    delimiter = ";"
                return [row for row in csv.reader(f, delimiter=delimiter)]
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            errors.append("CSV 读取失败：%s" % exc)
            return []
    errors.append("CSV 编码无法识别，请用 UTF-8 或 GBK 保存")
    return []


def _read_txt(path, errors):
    """每行一台设备：IP 或 IP,描述 或 IP 描述"""
    rows = []
    try:
        with open(str(path), "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in re.split(r"[,\t; ]+", line) if p.strip()]
                rows.append(parts)
    except UnicodeDecodeError:
        try:
            with open(str(path), "r", encoding="gbk") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in re.split(r"[,\t; ]+", line) if p.strip()]
                    rows.append(parts)
        except Exception as exc:
            errors.append("TXT 读取失败：%s" % exc)
    except Exception as exc:
        errors.append("TXT 读取失败：%s" % exc)
    return rows


def _parse_rows(rows, errors, default_credential_ref):
    if not rows:
        return [], errors

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    mapped = [_map_header(h) for h in header]

    # 密码列：现场清单本来就带账号密码（设备名称/厂商/IP/用户名/密码），
    # 直接用于登录，**不会**写进日志、结果表或任何落盘文件。
    # 标准列名不提示（否则每次选清单都弹一条黄条）；列名不认识的才提醒确认。
    _STD_CRED_COLS = {"username", "password", "enable_password"}
    for h in header:
        n = _normalize(h)
        if any(p in n for p in PASSWORD_LIKE_HEADERS):
            if _map_header(h) in _STD_CRED_COLS:
                continue
            errors.append("检测到疑似密码列「%s」，请确认是否用于登录" % h)

    has_header = any(mapped)
    if has_header:
        data_rows = rows[1:]
    else:
        data_rows = rows
        mapped = None

    devices = []
    seen = set()
    by_ip = {}
    dup_count = 0
    no_cred_rows = 0
    bad_ip_count = 0
    bad_ip_samples = []
    # 表里根本没有「凭证」列时，逐行报"缺少凭证"会刷出几千条；
    # 这种情况只提示一次（凭证可用默认引用兜底）
    # 清单自带用户名/密码列时，直接用清单里的账号登录，不需要凭证引用；
    # 只有既没有账号列、又没指定默认凭证时才算"无法确定登录凭证"
    has_login_cols = bool(mapped) and ("username" in mapped or "password" in mapped)
    cred_col_present = bool(mapped) and ("credential_ref" in mapped)
    if not cred_col_present and not has_login_cols and not default_credential_ref:
        # 清单没有凭证列：拿凭证库里唯一/default 的那条兜底
        auto_ref, _note = resolve_default_credential()
        if auto_ref:
            default_credential_ref = auto_ref
            errors.append("清单里没有「凭证」列，已自动使用凭证「%s」" % auto_ref)
    for idx, raw in enumerate(data_rows, start=2 if has_header else 1):
        cells = [_clean(c) for c in raw]
        if not any(cells):
            continue

        if mapped:
            d = Device()
            for i, field_name in enumerate(mapped):
                if not field_name or i >= len(cells):
                    continue
                value = cells[i]
                if field_name == "port":
                    d.port = int(value) if value.isdigit() else (
                        23 if d.protocol == "telnet" else 22)
                else:
                    setattr(d, field_name, value)
        else:
            d = Device(ip=cells[0])
            if len(cells) > 1:
                d.hostname = cells[1]
            if len(cells) > 2:
                d.remark = ",".join(cells[2:])

        if not d.protocol:
            d.protocol = "ssh"
        d.port = d.port or (23 if d.protocol.lower() == "telnet" else 22)
        if not d.credential_ref:
            d.credential_ref = default_credential_ref

        # 校验
        d.ip = _extract_ip(d.ip)
        if not _valid_ip(d.ip):
            bad_ip_count += 1
            if bad_ip_samples and d.ip not in bad_ip_samples:
                bad_ip_samples.append(d.ip)
                if len(bad_ip_samples) >= 5:
                    bad_ip_samples.append("…")
            continue
        if d.ip in seen:
            # 同一台设备在「告警配置列表」里会出现很多行（一个接口一行），
            # 逐行报错会刷出几千条；改成自动合并，只在最后汇总一条
            dup_count += 1
            prev = by_ip.get(d.ip)
            if prev is not None:
                if not prev.hostname and d.hostname:
                    prev.hostname = d.hostname
                if not prev.vendor and d.vendor:
                    prev.vendor = d.vendor
            continue
        if not d.credential_ref and not (d.username or d.password):
            if cred_col_present:
                errors.append("第 %d 行：缺少凭证引用（可在参数中指定默认凭证）" % idx)
                continue
            # 表里既没有账号列也没有凭证列：跳过这行，最后统一提示
            no_cred_rows += 1
            continue

        seen.add(d.ip)
        if not d.hostname:
            d.hostname = d.ip
        by_ip[d.ip] = d
        devices.append(d)

    if dup_count:
        errors.append("有 %d 行与前面的 IP 重复，已自动合并为同一台设备" % dup_count)
    if bad_ip_count:
        errors.append("有 %d 行 IP 格式非法，已跳过（例：%s）"
                      % (bad_ip_count, "、".join(bad_ip_samples)))
    if no_cred_rows and not cred_col_present:
        errors.append("清单里没有「凭证」列，%d 行无法确定登录凭证；"
                      "请加一列凭证引用，或在工具参数里指定默认凭证" % no_cred_rows)
    return devices, errors
