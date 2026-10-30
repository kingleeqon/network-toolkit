# -*- coding: utf-8 -*-
"""工具注册中心（对应 PRD FR-101）。

扫描 tools/ 目录，自动发现 BaseTool 子类；单个工具加载失败不影响整体启动。
"""
import importlib
import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from core.tool_base import BaseTool

logger = logging.getLogger(__name__)


def _iter_tool_modules(tools_dir):
    for path in sorted(Path(tools_dir).glob("*.py")):
        if path.name.startswith("_"):
            continue
        yield path


# 左侧工具树的分类显示顺序（未列出的排在后面，按名称排）
CATEGORY_ORDER = ["采集分析类", "会话管理类", "解析类", "检查类", "工具类", "示例"]


class Registry:
    def __init__(self, tools_dir):
        self.tools_dir = Path(tools_dir)
        self.tools = []          # List[BaseTool 实例]
        self.load_errors = []    # List[Tuple[文件名, 错误信息]]

    def discover(self):
        self.tools = []
        self.load_errors = []
        if not self.tools_dir.exists():
            self.load_errors.append((str(self.tools_dir), "工具目录不存在"))
            return self

        seen_ids = {}
        for path in _iter_tool_modules(self.tools_dir):
            mod_name = "tools." + path.stem
            try:
                if mod_name in sys.modules:
                    mod = importlib.reload(sys.modules[mod_name])
                else:
                    mod = importlib.import_module(mod_name)
            except Exception as exc:
                msg = "%s: %s" % (type(exc).__name__, exc)
                self.load_errors.append((path.name, msg))
                logger.warning("工具模块加载失败 %s -> %s", path.name, msg)
                continue

            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if not issubclass(obj, BaseTool) or obj is BaseTool:
                    continue
                if inspect.isabstract(obj):
                    continue
                if getattr(obj, "id", "") == "":
                    continue
                try:
                    inst = obj()
                except Exception as exc:
                    self.load_errors.append((path.name, "实例化失败 %s" % exc))
                    continue

                tid = inst.id
                if tid in seen_ids:
                    new_id = "%s_%d" % (tid, seen_ids[tid])
                    logger.warning("工具 id 冲突：%s 已存在，重命名为 %s", tid, new_id)
                    self.load_errors.append((path.name, "id 冲突：%s -> %s" % (tid, new_id)))
                    inst.id = new_id
                    seen_ids[tid] += 1
                else:
                    seen_ids[tid] = 1
                self.tools.append(inst)

        self.tools.sort(key=lambda t: (t.category, t.name))
        return self

    def get(self, tool_id):
        for t in self.tools:
            if t.id == tool_id:
                return t
        return None

    def by_category(self):
        """按分类分组，并按 CATEGORY_ORDER 排序。

        左侧树默认按注册顺序排，"采集分析类"会掉到最底部——760 高的窗口
        只露得出 3 个工具，"设备配置备份"还得滚动才看得到。把常用的
        采集/分析类提到最前面。
        """
        grouped = {}
        for t in self.tools:
            grouped.setdefault(t.category, []).append(t)
        order = {name: i for i, name in enumerate(CATEGORY_ORDER)}
        return dict(sorted(grouped.items(),
                           key=lambda kv: (order.get(kv[0], 99), kv[0])))
