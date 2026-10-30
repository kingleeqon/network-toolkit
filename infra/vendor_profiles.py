# -*- coding: utf-8 -*-
"""厂商适配配置加载（对应 PRD FR-140）。

driver: netmiko | paramiko；netmiko 需同时提供 netmiko_device_type。
新增厂商原则上只改 vendor_profiles.json，不改代码。
"""
import json
from pathlib import Path
from typing import Dict, Optional

DEFAULT_PROFILE = {
    "driver": "netmiko",
    "netmiko_device_type": "autodetect",
    "paging": "",
    "encoding": "utf-8",
    "cmds": {"config": "display current-configuration"},
}

_PROFILES = None
_PROFILES_PATH = Path(__file__).parent / "vendor_profiles.json"


def _load():
    global _PROFILES
    if _PROFILES is not None:
        return _PROFILES
    try:
        with open(str(_PROFILES_PATH), "r", encoding="utf-8") as f:
            _PROFILES = json.load(f)
    except Exception:
        _PROFILES = {}
    return _PROFILES


def reload_profiles():
    global _PROFILES
    _PROFILES = None
    return _load()


def get_profile(vendor):
    """按厂商名或别名取配置；未识别返回默认模板（调用方需告警，见 EH-15）。"""
    profiles = _load()
    key = (vendor or "").strip().lower()
    if not key:
        return dict(DEFAULT_PROFILE), False
    if key in profiles.get("vendors", {}):
        return dict(profiles["vendors"][key]), True
    for name, cfg in profiles.get("vendors", {}).items():
        if key in [a.lower() for a in cfg.get("aliases", [])]:
            return dict(cfg), True
    return dict(DEFAULT_PROFILE), False


def all_profiles():
    return _load().get("vendors", {})


def match_vendor(vendor):
    """返回该厂商的标准 key；未收录返回 ""。

    清单里的厂商值五花八门（HUAWEI / 华为 / h3c / CISCO / 锐捷），
    统一归一到标准 key，未收录的直接判定为"不支持采集"。
    """
    profiles = _load().get("vendors", {})
    key = (vendor or "").strip().lower()
    if not key:
        return ""
    if key in profiles:
        return key
    for name, cfg in profiles.items():
        if key in [a.lower() for a in cfg.get("aliases", [])]:
            return name
    return ""


def supported_vendors():
    """已收录（可采集）的厂商标准 key 集合。"""
    return set(_load().get("vendors", {}).keys())


def filter_supported(devices):
    """把设备分成 (可采集, 不支持)。不支持的按厂商汇总返回。"""
    ok, bad = [], {}
    for d in devices:
        if match_vendor(d.vendor):
            ok.append(d)
        else:
            bad[d.vendor or "(空)"] = bad.get(d.vendor or "(空)", 0) + 1
    return ok, bad


def get_cmd(vendor, cmd_key, device_name=None):
    """取命令；烽火这类按产品分流的厂商会按设备名选（DP/FW1000 → 防火墙）。"""
    cfg, _ = get_profile(vendor)
    product = get_product(vendor, device_name)
    if product:
        sub = cfg.get("products", {}).get(product, {})
        cmd = sub.get("cmds", {}).get(cmd_key)
        if cmd:
            return cmd
    return cfg.get("cmds", {}).get(cmd_key, "")


def get_product(vendor, device_name=None):
    """返回厂商产品类型：switch | firewall | None。

    只有配置里声明了 products 的厂商才按设备名（name_marks）分产品；
    烽火只有交换机，products 里只剩 switch 分支，恒返回 switch。
    其余厂商直接用 product 字段（如迪普恒为 firewall）。
    """
    cfg, _ = get_profile(vendor)
    products = cfg.get("products") or {}
    if products:
        default = cfg.get("product") or "switch"
        marks = products.get("firewall", {}).get("name_marks") or []
        name = (device_name or "").upper()
        if marks and any(m.upper() in name for m in marks):
            return "firewall"
        return default
    return cfg.get("product")


def get_paging_cmd(vendor, device_ip=None, device_name=None, for_backup=False):
    """登录后要发的"关闭分屏"命令。

    - 华为：screen-length 0 temporary
    - 思科/锐捷/中兴：terminal length 0
    - 烽火：**接口采集**统一 terminal length 0（交换机），
      **配置备份**按 IP 名单分（现场脚本 netmiko_SSH.py 的那批 IP 用 terminal line 0）
    """
    cfg, _ = get_profile(vendor)
    by_ip = cfg.get("paging_off_by_ip") or {}
    if for_backup and by_ip:
        ips = by_ip.get("ips") or []
        if device_ip in ips:
            return by_ip.get("cmd", "")
        return by_ip.get("default", "")
    if cfg.get("driver") == "paramiko":
        product = get_product(vendor, device_name)
        sub = cfg.get("products", {}).get(product, {})
        if sub.get("paging_off"):
            return sub["paging_off"]
        return cfg.get("cmds", {}).get("paging_off", "")
    return cfg.get("paging_cmd", "")


def get_device_type_label(vendor, device_name=None):
    """写进日志首行的设备类型标识（解析器靠它准确识别厂商）。

    迪普与烽火是两个独立厂商，必须按厂商本身判定，不能按产品类型判断：
    烽火只有交换机，此前若按 get_product 判断，名字含 DP 的烽火交换机
    会被误判成 firewall，进而被误标成 DPtech，导致 CRC 对比阶段把烽火设备
    按迪普规则解析、厂商错判。
    """
    key = match_vendor(vendor)
    if key in ("dipu", "dptech", "dp"):
        # 迪普统一标 DPtech
        return "DPtech"
    if key == "fiberhome":
        # 烽火统一标 FiberHome（只有交换机）
        return "FiberHome"

    # 其余厂商：优先用配置里的 netmiko_device_type，缺失则回退到标准 key
    cfg, _ = get_profile(vendor)
    label = cfg.get("netmiko_device_type") or ""
    return label or key


def get_e1000(ip):
    """该 IP 是否需要 E1000 特殊处理（进 vty 改屏显）。"""
    cfg = _load().get("e1000") or {}
    return bool(ip) and ip in (cfg.get("ips") or [])


def get_e1000_sequence():
    """返回 (进入命令列表, 退出命令列表)。"""
    cfg = _load().get("e1000") or {}
    return list(cfg.get("enter") or []), list(cfg.get("exit") or [])

