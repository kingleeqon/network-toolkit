# -*- coding: utf-8 -*-
"""Schema 驱动的表单生成器（对应 PRD FR-103）。

新增工具无需手写界面：声明 params 即可自动生成控件。

ttkbootstrap 升级点：
    1. 帮助文本从常驻的第 4 列改为鼠标悬停的 ToolTip，横向空间还给控件
    2. 校验失败提示移到控件正下方，使用主题的 danger 语义色
    3. 参数多于 4 项时自动双列；否则单列，避免短表单被无谓拉宽
    4. choice 类型取值修正：返回 value 而非展示用的 label

对外接口保持不变（ui/main_window.py 依赖）：
    validate()   -> Tuple[bool, List[Tuple[str, str]]]
    get_values() -> Dict[str, Any]
    set_enabled(bool)
    refresh_device_count(path) -> Tuple[int, List[str]]

Python 3.8 兼容：类型注解一律使用 typing。
"""
import os
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.tooltip import ToolTip
from typing import Any, Dict, List, Optional, Tuple

# 超过此数量切换到双列
TWO_COLUMN_THRESHOLD = 5
TWO_COLUMN_MIN_W = 700   # 参数区窄于此宽度就不排两列（否则第二列被切在边缘）

# 有 help 时在标签后追加的细体提示符（U+2009 细空格 + 问号）
HELP_MARK = "\u2009?"

ENTRY_WIDTH = 28
COMBO_WIDTH = 26
SPIN_WIDTH = 10
LABEL_MIN_W = 120     # 标签列最小宽度（窄窗口下不再被压缩到看不见）
WIDGET_MIN_W = 170    # 控件列最小宽度（Dropbox/Spinbox 至少能放下内容）


class FormBuilder(object):
    """按 ParamSpec 列表渲染表单；提供校验与取值。"""

    def __init__(self, master, params, avail_width=None):
        self.master = master
        self.params = list(params or [])
        # 参数区实际宽度：决定排两列还是一列。
        # 两列至少要 700px（标签 120 + 控件 230 各两组），960×620 下参数区只有
        # 635px，硬排两列会把第二列切在右边缘，只能横向拖动才能看到
        self.avail_width = avail_width
        self.widgets = {}            # key -> 控件（可能是 Frame）
        self.vars = {}               # key -> tk.Variable
        self.error_labels = {}       # key -> 错误提示 Label
        self._choice_map = {}        # key -> Dict[label, value]
        self._device_count_label = None
        self._build()

    def footer_row(self):
        """最后一行参数之后可用的 grid 行号。

        外部要在参数区下面挂东西（如「定时执行」条）时用，
        避免用 pack 与本组件的 grid 混用（tkinter 会直接报错）。
        """
        n = len(self.params)
        if not n:
            return 0
        rows = (n + 1) // 2 if getattr(self, "two_col", False) else n
        return rows * 2

    # —— 渲染 ——
    def _build(self):
        two_col = len(self.params) >= TWO_COLUMN_THRESHOLD
        if two_col and self.avail_width:
            try:
                two_col = float(self.avail_width) >= TWO_COLUMN_MIN_W
            except (TypeError, ValueError):
                pass
        self.two_col = two_col
        for index, spec in enumerate(self.params):
            row = index // 2 if two_col else index
            col = (index % 2) * 2 if two_col else 0

            label_text = spec.label + (" *" if spec.required else "")
            if spec.help:
                label_text += HELP_MARK
            label = ttk.Label(self.master, text=label_text, bootstyle="secondary")
            label.grid(row=row * 2, column=col, sticky="w", padx=(10, 6), pady=(4, 1))
            if spec.help:
                ToolTip(label, text=spec.help, delay=400)

            var = tk.StringVar()
            if spec.default is not None:
                var.set(str(spec.default))
            widget = self._make_widget(row, col, spec, var)
            # bool 类型在 _make_widget 中已建立 BooleanVar，这里不能再被 StringVar 覆盖
            # （否则取值为字符串 "False"，bool("False") 为 True，勾选框会永远生效）
            if spec.type != "bool":
                self.vars[spec.key] = var
            self.widgets[spec.key] = widget

            err = ttk.Label(self.master, text="", bootstyle="danger")
            err.grid(row=row * 2 + 1, column=col, sticky="w", padx=(10, 6))
            # 默认隐藏：空标签也占约 20px 高度，参数多时会挤占结果区空间
            err.grid_remove()
            self.error_labels[spec.key] = err

        # 给控件列设最小宽度：容器变窄时 ttk.grid 会把带 weight 的列按比例压缩，
        # 960×620 下参数区只剩 630px，Spinbox 被压到 20px、下拉框只剩一个"最"字。
        # 定了 minsize 之后，空间不够时宁可撑出横向滚动，也不会把输入框压没。
        if two_col:
            self.master.columnconfigure(1, weight=1, minsize=WIDGET_MIN_W)
            self.master.columnconfigure(3, weight=1, minsize=WIDGET_MIN_W)
            self.master.columnconfigure(0, minsize=LABEL_MIN_W)
            self.master.columnconfigure(2, minsize=LABEL_MIN_W)
        else:
            self.master.columnconfigure(1, weight=1, minsize=WIDGET_MIN_W)
            self.master.columnconfigure(0, minsize=LABEL_MIN_W)
        # 排布所需的最小宽度：参数区比它还窄时才需要横向滚动
        cols = 2 if two_col else 1
        self.min_width = cols * (LABEL_MIN_W + WIDGET_MIN_W) + cols * 16 + 12

    def _make_widget(self, row, col, spec, var):
        grid_row = row * 2

        if spec.type == "int":
            w = ttk.Spinbox(self.master, from_=spec.min or 0, to=spec.max or 9999,
                            textvariable=var, width=SPIN_WIDTH, bootstyle="primary")
            w.grid(row=grid_row, column=col + 1, sticky="w", padx=(0, 10), pady=(4, 1))
            return w

        if spec.type == "bool":
            bv = tk.BooleanVar(value=bool(spec.default))
            self.vars[spec.key] = bv
            # 部分主题/版本下 round-toggle 布局缺失会抛 TclError，
            # 回退到原生 Checkbutton，保证表单一定能渲染出来
            w = None
            try:
                w = ttk.Checkbutton(self.master, variable=bv, text="",
                                    bootstyle="round-toggle")
                w.grid(row=grid_row, column=col + 1, sticky="w", padx=(0, 10), pady=(4, 1))
            except Exception:
                try:
                    if w is not None:
                        w.destroy()
                except Exception:
                    pass
                w = tk.Checkbutton(self.master, variable=bv, text="")
                w.grid(row=grid_row, column=col + 1, sticky="w", padx=(0, 10), pady=(4, 1))
            return w

        if spec.type == "choice":
            # 优先使用动态选项（依赖运行时数据，如历史日期/可选设备）
            opts = spec.options or []
            if getattr(spec, "options_provider", None) is not None:
                try:
                    dyn = spec.options_provider()
                    if dyn:
                        opts = dyn
                except Exception:
                    pass
            labels, mapping = [], {}
            for opt in opts:
                lab = opt.get("label", opt.get("value", ""))
                labels.append(lab)
                mapping[lab] = opt.get("value", lab)
            self._choice_map[spec.key] = mapping
            w = ttk.Combobox(self.master, textvariable=var, values=labels,
                             state="readonly", width=COMBO_WIDTH, bootstyle="primary")
            if spec.default is None:
                if labels:
                    var.set(labels[0])
            else:
                for lab, val in mapping.items():
                    if val == spec.default:
                        var.set(lab)
                        break
                else:
                    var.set(str(spec.default))
            w.grid(row=grid_row, column=col + 1, sticky="w", padx=(0, 10), pady=(4, 1))
            return w

        if spec.type == "password":
            w = ttk.Entry(self.master, textvariable=var, show="*", width=ENTRY_WIDTH)
            w.grid(row=grid_row, column=col + 1, sticky="w", padx=(0, 10), pady=(4, 1))
            return w

        if spec.type in ("file", "dir", "device_list"):
            frame = ttk.Frame(self.master)
            frame.grid(row=grid_row, column=col + 1, sticky="w", padx=(0, 10), pady=(4, 1))
            entry = ttk.Entry(frame, textvariable=var, width=ENTRY_WIDTH - 8)
            entry.pack(side="left")
            ttk.Button(frame, text="选择…", width=8, bootstyle="secondary",
                       command=lambda s=spec, v=var: self._pick_path(s, v)).pack(side="left", padx=4)
            if spec.type == "device_list":
                self._device_count_label = ttk.Label(frame, text="未选择设备清单文件",
                                                     bootstyle="secondary")
                self._device_count_label.pack(side="left", padx=4)
            return frame

        w = ttk.Entry(self.master, textvariable=var, width=ENTRY_WIDTH)
        w.grid(row=grid_row, column=col + 1, sticky="w", padx=(0, 10), pady=(4, 1))
        return w

    def _pick_path(self, spec, var):
        from tkinter import filedialog
        if spec.type == "dir":
            path = filedialog.askdirectory()
        else:
            exts = [e if e.startswith(".") else "." + e for e in (spec.ext or "").split(";") if e]
            if exts:
                filetypes = [("支持的文件", " ".join(exts)), ("所有文件", "*.*")]
            else:
                filetypes = [("所有文件", "*.*")]
            path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)
            if spec.type == "device_list":
                self.refresh_device_count(path)

    def refresh_device_count(self, path):
        """选择清单后立即校验并回显（FR-120 导入交互）。

        异常原因放进悬浮提示：只显示"异常 N 行"用户根本不知道该改什么
        （比如"清单里没有凭证列"这种一句话就能定位的问题）。
        """
        if not self._device_count_label:
            return 0, []
        from infra.device_loader import load_devices
        devices, errors = load_devices(path)
        text = "已加载 %d 台" % len(devices)
        if errors:
            text += "，%d 条提示" % len(errors)
        self._device_count_label.configure(text=text,
                                           bootstyle="warning" if errors else "success")
        tip = "\n".join(errors[:8])
        if len(errors) > 8:
            tip += "\n… 还有 %d 条" % (len(errors) - 8)
        try:
            from ttkbootstrap.tooltip import ToolTip
            old = getattr(self, "_device_count_tip", None)
            if old is not None:
                old.toplevel.destroy()
                self._device_count_tip = None
            if tip:
                self._device_count_tip = ToolTip(self._device_count_label, text=tip, delay=200)
        except Exception:
            pass
        return len(devices), errors

    # —— 校验与取值 ——
    def validate(self):
        """返回 (ok, errors)；errors 为 (key, 文案) 列表。"""
        errors = []
        for key in self.error_labels:
            self.error_labels[key].configure(text="")
            try:
                self.error_labels[key].grid_remove()
            except Exception:
                pass

        for spec in self.params:
            value = self.vars[spec.key].get()
            if spec.type == "bool":
                continue
            if spec.required and not str(value).strip():
                self._show_error(spec, "必填")
                errors.append((spec.key, "%s 必填" % spec.label))
                continue
            if spec.type == "int":
                try:
                    iv = int(value)
                except ValueError:
                    self._show_error(spec, "必须为整数")
                    errors.append((spec.key, "%s 必须为整数" % spec.label))
                    continue
                if spec.min is not None and iv < spec.min:
                    self._show_error(spec, "不能小于 %d" % spec.min)
                    errors.append((spec.key, "%s 不能小于 %d" % (spec.label, spec.min)))
                elif spec.max is not None and iv > spec.max:
                    self._show_error(spec, "不能大于 %d" % spec.max)
                    errors.append((spec.key, "%s 不能大于 %d" % (spec.label, spec.max)))
            if spec.type in ("file", "device_list") and value and not os.path.exists(value):
                self._show_error(spec, "文件不存在")
                errors.append((spec.key, "%s 文件不存在" % spec.label))
        return (len(errors) == 0), errors

    def _show_error(self, spec, text):
        label = self.error_labels.get(spec.key)
        if label is None:
            return
        if text:
            label.configure(text=text)
            try:
                label.grid()          # 有错误才显示，占用一行
            except Exception:
                pass
        else:
            label.configure(text="")
            try:
                label.grid_remove()   # 无错误时收起，不占高度
            except Exception:
                pass

    def get_values(self):
        values = {}
        for spec in self.params:
            var = self.vars[spec.key]
            raw = var.get()
            if spec.type == "int":
                try:
                    values[spec.key] = int(raw)
                except (ValueError, TypeError):
                    values[spec.key] = spec.default if spec.default is not None else 0
            elif spec.type == "bool":
                # 容错：无论拿到 bool 还是 "true"/"false" 字符串都正确解析
                if isinstance(raw, str):
                    values[spec.key] = raw.strip().lower() in ("1", "true", "yes", "on")
                else:
                    values[spec.key] = bool(raw)
            elif spec.type == "choice":
                mapping = self._choice_map.get(spec.key, {})
                values[spec.key] = mapping.get(raw, raw)
            else:
                values[spec.key] = raw
        return values

    # —— 状态 ——
    def set_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for spec in self.params:
            widget = self.widgets.get(spec.key)
            if widget is None:
                continue
            self._apply_state(widget, state)

    def _apply_state(self, widget, state):
        # 仅对容器 Frame 递归；叶子控件（Entry/Combobox/Spinbox/Button）直接 configure。
        # 注意：不能用 winfo_children() 判断容器——ttk.Entry 等叶子控件也有内部子部件。
        if isinstance(widget, ttk.Frame):
            for child in widget.winfo_children():
                self._apply_state(child, state)
            return
        try:
            widget.configure(state=state)
        except Exception:
            pass
