# -*- coding: utf-8 -*-
"""IP / 掩码 / CIDR 公共工具（收敛自 getIp.py、extract_ips.py、IP&Mask 转换器）。

纯函数、无第三方依赖，Python 3.8 兼容。
"""
import ipaddress
from typing import Optional, Tuple

# 常见点分十进制掩码 → 前缀长度
_MASK_TO_CIDR = {
    "255.255.255.255": 32, "255.255.255.254": 31, "255.255.255.252": 30,
    "255.255.255.248": 29, "255.255.255.240": 28, "255.255.255.224": 27,
    "255.255.255.192": 26, "255.255.255.128": 25, "255.255.255.0": 24,
    "255.255.254.0": 23, "255.255.252.0": 22, "255.255.248.0": 21,
    "255.255.240.0": 20, "255.255.224.0": 19, "255.255.192.0": 18,
    "255.255.128.0": 17, "255.255.0.0": 16, "255.254.0.0": 15,
    "255.252.0.0": 14, "255.248.0.0": 13, "255.240.0.0": 12,
    "255.224.0.0": 11, "255.192.0.0": 10, "255.128.0.0": 9,
    "255.0.0.0": 8, "254.0.0.0": 7, "252.0.0.0": 6, "248.0.0.0": 5,
    "240.0.0.0": 4, "224.0.0.0": 3, "192.0.0.0": 2, "128.0.0.0": 1,
    "0.0.0.0": 0,
}


def detect_ip_version(text):
    """按字符串特征判断 IP 版本：'IPv4' | 'IPv6' | 'Unknown'。"""
    s = str(text or "").strip()
    if ":" in s:
        return "IPv6"
    if "." in s:
        return "IPv4"
    return "Unknown"


def mask_to_cidr(mask):
    """点分十进制掩码 → 前缀长度；非法返回 None。"""
    mask = str(mask or "").strip()
    if mask.isdigit() and 0 <= int(mask) <= 32:
        return int(mask)
    if mask in _MASK_TO_CIDR:
        return _MASK_TO_CIDR[mask]
    try:
        octets = mask.split(".")
        if len(octets) != 4:
            return None
        binary = "".join(bin(int(o))[2:].zfill(8) for o in octets)
        cidr = binary.count("1")
        if binary != "1" * cidr + "0" * (32 - cidr):
            return None  # 非连续掩码
        return cidr
    except Exception:
        return None


def cidr_to_mask(cidr):
    """前缀长度 → 点分十进制掩码（IPv4）。越界返回全 1。"""
    try:
        cidr = int(cidr)
    except (TypeError, ValueError):
        return "255.255.255.255"
    if cidr < 0 or cidr > 32:
        return "255.255.255.255"
    mask = (0xFFFFFFFF << (32 - cidr)) & 0xFFFFFFFF if cidr else 0
    return ".".join(str((mask >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def normalize_to_cidr(ip, mask):
    """把 (ip, mask) 归一化为 'ip/prefix' 字符串；mask 可为点分或数字。

    失败抛 ValueError（调用方决定是否隔离）。
    """
    ip = str(ip or "").strip()
    mask = str(mask or "").strip()
    version = detect_ip_version(ip)
    if version == "IPv4":
        cidr = mask_to_cidr(mask)
        if cidr is None:
            raise ValueError("无效子网掩码: %s" % mask)
        return "%s/%d" % (ip, cidr)
    if version == "IPv6":
        # IPv6 的掩码应为前缀长度
        prefix = mask if mask.isdigit() else None
        if prefix is None:
            raise ValueError("IPv6 掩码须为前缀长度: %s" % mask)
        return "%s/%s" % (ip, prefix)
    raise ValueError("无法识别的 IP 地址: %s" % ip)


def network_address(cidr):
    """返回 (network_address, prefixlen)。cidr 如 '10.0.0.1/24'。"""
    s = str(cidr).strip()
    if "/" not in s:
        raise ValueError("缺少 CIDR 前缀: %s" % s)
    ip_part = s.split("/")[0]
    if detect_ip_version(ip_part) == "IPv6":
        net = ipaddress.IPv6Network(s, strict=False)
    else:
        net = ipaddress.IPv4Network(s, strict=False)
    return str(net.network_address), net.prefixlen


def network_str(cidr):
    """返回 'network_address/prefixlen' 形式（IP&Mask 转换器的核心输出）。"""
    addr, prefix = network_address(cidr)
    return "%s/%s" % (addr, prefix)


def is_valid_ip(text):
    """是否为合法 IPv4/IPv6 地址。"""
    try:
        ipaddress.ip_address(str(text).strip())
        return True
    except ValueError:
        return False
