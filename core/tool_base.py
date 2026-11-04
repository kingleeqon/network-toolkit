# -*- coding: utf-8 -*-
"""Tool 契约基类（对应 PRD 15.3 / FR-114 / FR-115）。

新工具只需：继承 BaseTool，声明属性与 params，实现 run()。
Python 3.8 兼容。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

TOOL_API_VERSION = "1.0"


@dataclass
class ParamSpec:
    key: str
    label: str
    type: str  # text|password|int|bool|choice|file|dir|device_list
    required: bool = False
    default: Any = None
    options: List[Dict[str, str]] = field(default_factory=list)  # choice 专用
    ext: str = ""          # file 专用，如 ".xlsx;.csv;.txt"
    min: Optional[int] = None
    max: Optional[int] = None
    placeholder: str = ""
    help: str = ""
    # choice 专用：动态选项提供者（渲染表单时调用，返回 [{label,value}]）。
    # 用于选项依赖运行时数据（如历史日期、可选设备名）的场景；返回空则回退 options。
    options_provider: Optional[Callable[[], List[Dict[str, str]]]] = None


class BaseTool(ABC):
    id = ""
    name = ""
    version = "1.0.0"
    category = "未分类"
    description = ""
    api_version = TOOL_API_VERSION

    params: List[ParamSpec] = []
    result_columns: List[str] = []

    # 输出标签页声明：result=结果表 chart=图表 log=运行日志 summary=任务摘要
    # 产图工具可去掉 result，纯结果工具可去掉 chart，主窗口据此按需渲染标签页
    output_tabs: List[str] = ["result", "log", "summary"]

    # —— 框架在运行时注入，工具不要直接改 ——
    _load_error: Optional[str] = None

    def __init_subclass__(cls, **kwargs):
        """子类未声明的可变属性从基类**复制**一份。

        否则漏写 `params = [...]` 的子类会和基类共享同一个 list，
        任何一处原地改动都会污染所有工具。
        """
        super().__init_subclass__(**kwargs)
        for attr in ("params", "result_columns", "output_tabs"):
            if attr not in cls.__dict__:
                setattr(cls, attr, list(getattr(BaseTool, attr)))

    @abstractmethod
    def run(self, params, ctx, emit):
        """params: 已校验参数；ctx: TaskContext；emit: emit(type, payload)。

        禁止操作 UI；禁止自行创建输出目录；
        每台设备处理前必须检查 ctx.cancel_event.is_set()。
        """
        raise NotImplementedError

    def validate_params(self, params):
        """工具级参数校验：返回错误文案，空串表示通过。

        界面在「启用定时」前会调用它——定时任务用的是参数快照，
        缺参到点必然失败（如 CRC 对比没选目录），必须当场拦住。
        默认不校验，需要跨字段规则的工具有选择地覆盖。
        """
        return ""

    # 0 条结果算不算正常：产图/纯落盘工具（如趋势分析）=True；
    # 采集类工具（设备清单跑完应有 N 行）=False，0 行说明没跑成，要判失败
    empty_result_ok = True

    # —— 历史结果面板（可选）：填了参数名，界面就在参数区右侧列出该目录的历史结果 ——
    result_files_param = ""

    def list_result_files(self, folder):
        """列出 folder 里的历史结果文件 → [(显示名, 完整路径)]，新的在前。"""
        return []

    def load_result_file(self, path):
        """读历史结果文件 → (columns, rows)，供结果表展示；不支持返回 None。"""
        return None
