# -*- coding: utf-8 -*-
"""厂商解析 golden test：用 7 个厂商的真实回显样本（脱敏截取）锁住解析结果。

样本来源与脱敏规则见 tests/samples/README.md。
这里断言三件事，任一变了都说明解析逻辑动了：
  1. detect_vendor 识别出的厂商
  2. extract_crc_info 抽到的接口与 CRC 值（含一个真实非零值）
  3. extract_interface_desc 抽到的描述

运行：python tests/test_vendor_parser.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from infra.vendor_parser import (detect_vendor, extract_crc_info,
                                 extract_interface_desc)

SAMPLES = Path(__file__).resolve().parent / "samples"

# 样本文件 → (期望厂商, 期望 CRC {接口: 值})
CASES = {
    "cisco.log": ("cisco", {"Ethernet2/11": 28407803, "Ethernet2/12": 0}),
    "dptech.log": ("dptech", {"bond2": 6, "bond10": 0}),
    "fiberhome.log": ("fiberhome", {"10gigaethernet1/0/1": 0,
                                    "10gigaethernet1/0/2": 0}),
    "h3c.log": ("h3c", {"Ten-GigabitEthernet4/0/43": 30964,
                        "Ten-GigabitEthernet4/0/44": 0}),
    "huawei.log": ("huawei", {"GigabitEthernet6/0/9": 1201101608,
                              "GigabitEthernet6/0/10": 0}),
    "ruijie.log": ("ruijie", {"GigabitEthernet0/37": 146051,
                              "GigabitEthernet0/38": 0}),
    "zte.log": ("zte", {"xgei-0/1/1/4": 1, "xgei-0/1/1/5": 1}),
}

fails = []


def check(name, got, expect):
    ok = got == expect
    print("  %-56s %s" % (name, "OK" if ok else "FAIL got=%r" % (got,)))
    if not ok:
        fails.append(name)


print("=== ① 7 个厂商样本：识别 + CRC 提取 + 描述提取 ===")
for fname, (vendor, expect_crc) in sorted(CASES.items()):
    path = SAMPLES / fname
    if not path.exists():
        check("%s 样本存在" % fname, False, True)
        continue
    text = path.read_bytes().decode("utf-8", "replace")
    check("%s 识别厂商" % fname, detect_vendor(text), vendor)
    check("%s CRC" % fname, extract_crc_info(text, vendor), expect_crc)
    descs = extract_interface_desc(text, vendor)
    first = sorted(expect_crc)[0]
    check("%s 描述(%s)" % (fname, first), descs.get(first), "DESC-%s" % first)
    check("%s 描述条目数" % fname, len(descs), len(expect_crc))

print("=== ② 占位描述要过滤掉（不返回） ===")
# 华三没配描述时会自动生成「<接口名> Interface」，不能当成真描述
H3C = ("hp_comware\n\nGigabitEthernet1/0/0\nCurrent state: DOWN\n"
       "Description: GigabitEthernet1/0/0 Interface\n")
check("华三占位描述被过滤", extract_interface_desc(H3C, "h3c"), {})

NO_USE = ("zte_zxros\n\nxgei-0/1/1/1 is administratively down\n"
          "  Description: NO_USE\n")
check("NO_USE 占位被过滤", extract_interface_desc(NO_USE, "zte"), {})

REAL = ("zte_zxros\n\nxgei-0/1/1/1 is administratively down\n"
        "  Description: TO-[CORE]-10G-1.1.1.1\n")
check("真描述保留", extract_interface_desc(REAL, "zte"),
      {"xgei-0/1/1/1": "TO-[CORE]-10G-1.1.1.1"})

print("=== ③ 未知厂商不炸 ===")
check("空文本识别为 unknown", detect_vendor(""), "unknown")
check("未知厂商 CRC 为空", extract_crc_info("随便一些文本\n", "unknown"), {})
check("未知厂商描述为空", extract_interface_desc("随便一些文本\n", "unknown"), {})

print()
if fails:
    print("[FAIL] %d 项不通过：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("[OK] 全部通过")
