# -*- coding: utf-8 -*-
"""厂商识别 + 配置解析 + CRC 提取（收敛自 getIp.py、extract_ips.py、ALL_CHECK_CRC-2.py）。

纯离线文本解析，无网络依赖。数据驱动：厂商 -> 正则表。
Python 3.8 兼容。
"""
import re
from typing import Dict, List, Optional, Tuple

# ============ 厂商识别 ============

# 阶段一：精确设备类型标识（采集脚本写入文件头部的 device_type，优先级最高）
# 顺序敏感：dptech/fiberhome 需排在 huawei 之前
_DEVICE_TYPE_MARKERS = [
    ("dptech", ("dptech", "DPtech", "DPTECH")),
    ("fiberhome", ("fiberhome", "FiberHome", "FIBERHOME")),
    ("ruijie", ("ruijie_os",)),
    ("zte", ("zte_zxros",)),
    ("h3c", ("hp_comware",)),
    ("cisco", ("cisco_ios",)),
    ("huawei", ("huawei",)),
]

# 阶段二：泛化特征关键词（正文兜底，仅当阶段一未命中时使用）
_VENDOR_KEYWORDS = [
    ("huawei", ("huawei", "Huawei", "VRP", "MEth", "Vlanif")),
    ("h3c", ("h3c", "H3C", "Comware", "Vlan-interface")),
    ("cisco", ("cisco", "Cisco", "IOS")),
    ("ruijie", ("ruijie", "Ruijie", "RGOS")),
    ("zte", ("zte", "ZTE", "mgmt_eth", "ZXR10")),
]


def detect_vendor(content):
    """从配置文本识别厂商，返回小写厂商名或 'unknown'。

    两阶段：先精确匹配文件头部的设备类型标识（ruijie_os/hp_comware 等），
    未命中再降级到正文泛化关键词。避免正文描述中的偶然关键词（如 "H3C"）误判。
    """
    text = content or ""
    head = text[:2000].lower()

    # 阶段一：设备类型标识
    for vendor, markers in _DEVICE_TYPE_MARKERS:
        for m in markers:
            if m.lower() in head:
                return vendor

    # 阶段二：泛化关键词
    for vendor, keywords in _VENDOR_KEYWORDS:
        for kw in keywords:
            if kw.lower() in text[:5000]:
                return vendor
    return "unknown"


# ============ 接口 IP / 管理口 / 环回口 ============

# 管理口 / 环回口接口名模式（getIp.py 逻辑）
_MGMT_IFACE_PATTERNS = {
    "huawei": [
        r"interface\s+MEth\d+/\d+/\d+",
        r"interface\s+M-GigabitEthernet\d+/\d+/\d+",
        r"interface\s+Eth-Trunk\d+.*[Mm]anagement",
        r"interface\s+Vlanif\d+.*[Mm]anagement",
    ],
    "zte": [r"interface\s+mgmt_eth", r"interface\s+GEI_\d+/\d+/\d+"],
    "h3c": [
        r"interface\s+M-GigabitEthernet\d+/\d+/\d+",
        r"interface\s+Vlan-interface\d+.*[Mm]anagement",
    ],
    "ruijie": [
        r"interface\s+Mgmt\s+0",
        r"interface\s+VLAN\s+\d+.*[Mm]anagement",
    ],
    "unknown": [
        r"interface\s+[Mm]anagement\d*",
        r"interface\s+[Mm]gmt\d*",
        r"interface\s+[Vv]lan\d*",
    ],
}
_LOOPBACK_IFACE_PATTERNS = {
    "huawei": [r"interface\s+LoopBack\d+"],
    "zte": [r"interface\s+loopback\d+", r"interface\s+LoopBack\d+"],
    "h3c": [r"interface\s+LoopBack\d+"],
    "ruijie": [r"interface\s+Loopback\s+\d+"],
    "unknown": [r"interface\s+[Ll]oopback\d*", r"interface\s+[Ll]o\d*"],
}

_IP_ADDR_PATTERNS = [
    r"ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)",
    r"ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)",
    r"ip\s+address\s+(\d+\.\d+\.\d+\.\d+)",
]


def extract_mgmt_loopback(content):
    """提取管理口/环回口地址，返回 {'vendor','管理地址':[...], '环回口地址':[...]}。

    对应 getIp.py 的 NetworkConfigParser.extract_interfaces。
    """
    vendor = detect_vendor(content)
    mgmt = _extract_addresses(content, _MGMT_IFACE_PATTERNS.get(vendor, _MGMT_IFACE_PATTERNS["unknown"]))
    loop = _extract_addresses(content, _LOOPBACK_IFACE_PATTERNS.get(vendor, _LOOPBACK_IFACE_PATTERNS["unknown"]))
    return {"vendor": vendor, "管理地址": mgmt, "环回口地址": loop}


def _extract_addresses(content, iface_patterns):
    lines = (content or "").split("\n")
    current_iface, current_type = None, None
    out = []
    for line in lines:
        s = line.strip()
        matched = False
        for pattern in iface_patterns:
            if re.match(pattern, s, re.IGNORECASE):
                current_iface = s
                current_type = "iface"
                matched = True
                break
        if matched:
            continue
        if current_iface:
            for p in _IP_ADDR_PATTERNS:
                m = re.search(p, s)
                if m:
                    ip = m.group(1)
                    if len(m.groups()) > 1 and m.group(2):
                        mask = m.group(2)
                        if mask.isdigit() and 0 <= int(mask) <= 32:
                            from infra.ip_utils import cidr_to_mask
                            mask = cidr_to_mask(int(mask))
                        full = "%s/%s" % (ip, mask)
                    else:
                        full = ip
                    if full not in out:
                        out.append(full)
                    break
        if s.startswith("#") or s.startswith("!") or (s.startswith("interface ") and s != current_iface):
            current_iface = None
    return out


# ============ 接口 IP + VRRP 网关（extract_ips.py 逻辑） ============

_INTERFACE_LINE = {
    "huawei": r"^interface\s+(?P<if_name>\S+)",
    "h3c": r"^interface\s+(?P<if_name>\S+)",
    "cisco": r"^interface\s+(?P<if_name>\S+)",
    "ruijie": r"^interface\s+(?P<if_name>\S+)",
    "zte": r"^interface\s+(?P<if_name>\S+)",
}
_IPV4_PATTERN = r"ip\s+address\s+(?P<ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+(?P<mask>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
_IPV6_PATTERN = r"ipv6\s+address\s+(?P<ipv6>[\da-fA-F:]+)/?(?P<prefix>\d{1,3})?"
_VRRP_VIP = {
    "huawei": r"vrrp\s+vrid\s+\d+\s+virtual-ip\s+(?P<vip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    "h3c": r"vrrp\s+vrid\s+\d+\s+virtual-ip\s+(?P<vip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    "cisco": r"vrrp\s+\d+\s+ip\s+(?P<vip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    "ruijie": r"vrrp\s+\d+\s+ip\s+(?P<vip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    "zte": r"vrrp\s+\d+\s+ip\s+(?P<vip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
}
_VLAN_HINT = r"Vlanif|Vlan-interface|VLAN"


def extract_interface_ips(content, vendor=None):
    """提取接口 IP + VRRP 网关（extract_ips.py 逻辑），返回 dict 列表。

    每项含 interface/is_vlan/cidr/gateway/version/has_vrrp/interface_ip/vrrp_ip。
    """
    vendor = (vendor or detect_vendor(content)).lower()
    iface_line = _INTERFACE_LINE.get(vendor, _INTERFACE_LINE["huawei"])
    vrrp_vip = _VRRP_VIP.get(vendor, _VRRP_VIP["huawei"])

    from infra.ip_utils import mask_to_cidr

    # 先按接口切块（块：接口行之后到下一个接口行之前）
    blocks = {}  # iface_name -> [lines]
    order = []
    current_iface = None
    for raw in (content or "").split("\n"):
        line = raw.strip()
        m = re.match(iface_line, line, re.IGNORECASE)
        if m:
            current_iface = m.group("if_name")
            if current_iface not in blocks:
                blocks[current_iface] = []
                order.append(current_iface)
            continue
        if current_iface:
            blocks[current_iface].append(line)

    results = []
    for iface in order:
        block_lines = blocks[iface]
        # 先在整块内收集 VRRP 虚拟 IP（避免 VRRP 行在 IP 行之后漏配）
        vrrp4 = None
        for line in block_lines:
            vm = re.search(vrrp_vip, line, re.IGNORECASE)
            if vm:
                vip = vm.group("vip")
                if not vip.startswith("169.254."):
                    vrrp4 = vip
        # 再解析 IP
        for line in block_lines:
            m4 = re.search(_IPV4_PATTERN, line, re.IGNORECASE)
            if m4:
                ip, mask = m4.group("ip"), m4.group("mask")
                prefix = mask_to_cidr(mask)
                if prefix is not None:
                    results.append({
                        "interface": iface,
                        "is_vlan": bool(re.search(_VLAN_HINT, iface, re.IGNORECASE)),
                        "cidr": "%s/%d" % (ip, prefix),
                        "gateway": vrrp4 if vrrp4 else ip,
                        "version": 4,
                        "has_vrrp": vrrp4 is not None,
                        "interface_ip": ip,
                        "vrrp_ip": vrrp4,
                    })
            m6 = re.search(_IPV6_PATTERN, line, re.IGNORECASE)
            if m6:
                ipv6_full = m6.group("ipv6")
                prefix = m6.group("prefix")
                if "/" in ipv6_full:
                    ipv6_addr, _, inline_prefix = ipv6_full.partition("/")
                    if not prefix:
                        prefix = inline_prefix or "64"
                else:
                    ipv6_addr = ipv6_full
                    prefix = prefix or "64"
                if not (ipv6_addr.lower().startswith("fe80:") or "auto" in line.lower()):
                    results.append({
                        "interface": iface,
                        "is_vlan": bool(re.search(_VLAN_HINT, iface, re.IGNORECASE)),
                        "cidr": "%s/%s" % (ipv6_addr, prefix),
                        "gateway": ipv6_addr,
                        "version": 6,
                        "has_vrrp": False,
                        "interface_ip": ipv6_addr,
                        "vrrp_ip": None,
                    })
    return results


# ============ CRC 提取（ALL_CHECK_CRC-2.py 逻辑） ============

_INTERFACE_PATTERNS = {
    "huawei": r"((?:Eth-Trunk|GigabitEthernet|XGigabitEthernet|100GE|40GE|25GE)\d+(?:/\d+/\d+)?)\s+current state",
    "zte": r"^([a-z]+-\d+/\d+/\d+/\d+|[a-z]+\d+/\d+/\d+)\s+is\s+",
    "ruijie": r"=+\s+((?:GigabitEthernet|HundredGigabitEthernet|TFGigabitEthernet|TenGigabitEthernet)\s+\d+/\d+|AggregatePort\s+\d+)\s*=+",
    "h3c": r"^((?:GigabitEthernet|XGigabitEthernet|HundredGigE|M-GigabitEthernet|Ten-GigabitEthernet)\d+/\d+/\d+|(?:Eth-Trunk|Route-Aggregation)\d+)\s*$",
    "cisco": r"^(Ethernet\d+/\d+|GigabitEthernet\d+/\d+/\d+|TenGigabitEthernet\d+/\d+/\d+|FastEthernet\d+/\d+)\s+is\s+(up|down|administratively down)",
    "fiberhome": r"^\s*Interface\s+(\S+)\s+admin state",
    "dptech": r"^Interface\s+(\S+)\s*$",
}

_CRC_PATTERNS = {
    "huawei": [r"CRC:\s*(\d+)"],
    "zte": [r"In_CRC_ERROR\s+(\d+)"],
    "ruijie": [r"(\d+)\s+input errors,\s*(\d+)\s+CRC,", r"(\d+)\s+CRC"],
    "h3c": [r"CRC:\s*(\d+)", r"(\d+)\s+CRC"],  # 华三真实格式为「N CRC」（数字在前）
    "cisco": [
        r"(\d+)\s+runts\s+(\d+)\s+giants\s+(\d+)\s+CRC/FCS\s+(\d+)\s+no buffer",
        r"(\d+)\s+CRC/FCS",
        r"runts.*?giants.*?(\d+)\s+CRC/FCS",
    ],
    "fiberhome": [r"CRC\s*:\s*(\d+)"],
    "dptech": [r"(\d+)\s+crc_errors", r"crc_errors\s*:\s*(\d+)", r"crc_errors\D*(\d+)"],
}


def _match_crc(line, vendor):
    for pattern in _CRC_PATTERNS.get(vendor, []):
        m = re.search(pattern, line)
        if not m:
            continue
        groups = m.groups()
        if vendor == "ruijie" and len(groups) >= 2:
            return int(groups[1])
        if vendor == "cisco" and pattern == _CRC_PATTERNS["cisco"][0] and len(groups) >= 3:
            return int(groups[2])
        for g in groups:
            try:
                return int(g)
            except (ValueError, TypeError):
                continue
    return None


def extract_crc_info(content, vendor=None):
    """按接口提取 CRC 值，返回 {接口名: crc值}（ALL_CHECK_CRC-2.py 逻辑）。"""
    vendor = (vendor or detect_vendor(content)).lower()
    if vendor not in _INTERFACE_PATTERNS:
        return {}
    iface_pattern = _INTERFACE_PATTERNS[vendor]
    lines = (content or "").split("\n")
    crc_info = {}
    current = None
    scan = 0
    max_scan = 60 if vendor in ("fiberhome", "dptech") else 40

    for line in lines:
        s = line.strip()
        m = re.search(iface_pattern, s) if vendor in ("fiberhome", "dptech") else re.match(iface_pattern, s)
        if m:
            if current and current not in crc_info:
                crc_info[current] = 0
            current = m.group(1)
            if vendor == "ruijie":
                current = current.replace(" ", "")
            scan = 0
            continue
        if current and scan < max_scan:
            val = _match_crc(s, vendor)
            if val is not None:
                crc_info[current] = val
                current = None
                scan = 0
            else:
                scan += 1
                if scan >= max_scan:
                    crc_info[current] = 0
                    current = None
    if current and current not in crc_info:
        crc_info[current] = 0
    return crc_info
