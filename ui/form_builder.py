# -*- coding: utf-8 -*-
"""Schema 驱动的表单生成器（对应 PRD FR-103）。

新增工具无需手写界面：声明 params 即可自动生成控件。

布局（v0.2 设计评审第二轮）：
    每个参数是一个独立单元格 —— 标签在上、控件在下且撑满整列。
    原来是「标签在左、控件在右」，标签列白占 120px 且控件宽度参差不齐，
    和设计稿差得最远；改成标签置顶后，控件宽度统一、纵向对齐，
    也顺手去掉了标签列的最小宽度限制。

ttkbootstrap 升级点：
    1. 帮助文本从常驻的第 4 列改为鼠标悬停的 ToolTip，横向空间还给控件
    2. 校验失败提示移到控件正下方，使用主题的 danger 语义色
    3. 参数多于 4 项时自动双列；否则单列，且每个单元格宽度封顶，
      避免短表单里输入框被拉成一整条横贯参数区的「长框」
    4. choice 类型取值修正：返回 value 而非展示用的 label

对外接口保持不变（ui/main_window.py 依赖）：
    validate()   -> Tuple[bool, List[Tuple[str, str]]]
    get_values() -> Dict[str, Any]
    set_enabled(bool)
    refresh_device_count(path) -> Tuple[int, List[str]]
    on_device_count(path, count, errors) 回调：清单加载完通知主窗口回填「设备总数」

Python 3.8 兼容：类型注解一律使用 typing。
"""
import os
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.tooltip import ToolTip
from typing import Any, Dict, List, Optional, Tuple

# 列数策略：参数多且窗口宽时自动加列，保证一屏能看全（标签置顶后每行更高，
# 2 列 × 3 行在 1180×760 下放不进参数区，只能靠滚动，体验很差）
THREE_COLUMN_THRESHOLD = 6
THREE_COLUMN_MIN_W = 860
TWO_COLUMN_THRESHOLD = 5
TWO_COLUMN_MIN_W = 700   # 参数区窄于此宽度就不排两列（否则第二列被切在边缘）
ASSUMED_WIDE = 1200      # 布局尚未完成（拿不到宽度）时按这个宽度算列数

# 有 help 时在标签后追加的细体提示符（U+2009 细空格 + 问号）
HELP_MARK = "\u2009?"

# 控件请求宽度（标签置顶后可适当收窄：控件会 sticky="ew" 撑满整列）
ENTRY_WIDTH = 22
COMBO_WIDTH = 20
SPIN_WIDTH = 12
WIDGET_MIN_W = 190    # 单元格最小宽度（下拉框/路径框至少能放下内容）
CELL_MAX_W = 320      # 单元格宽度上限：多余空间让给历史结果面板/留白
COL_GAP = 16          # 列间距


def columns_for(count, width):
    """按参数数量与可用宽度决定排几列（1 / 2 / 3）。"""
    try:
        w = float(width) if width else ASSUMED_WIDE
    except (TypeError, ValueError):
        w = ASSUMED_WIDE
    if count >= THREE_COLUMN_THRESHOLD and w >= THREE_COLUMN_MIN_W:
        return 3
    if count >= TWO_COLUMN_THRESHOLD and w >= TWO_COLUMN_MIN_W:
        return 2
    return 1


class FormBuilder(object):
    """按 ParamSpec 列表渲染表单；提供校验与取值。"""

    def __init__(self, master, params, avail_width=None, on_device_count=None):
        self.master = master
        self.params = list(params or [])
        self.on_device_count = on_device_count
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
        """最后一个参数行之后可用的 grid 行号。

        外部要在参数区下面挂东西（如「定时执行」条）时用，
        避免用 pack 与本组件的 grid 混用（tkinter 会直接报错）。
        标签置顶后每个参数占 1 个单元格、1 个 grid 行，不再 ×2。
        """
        n = len(self.params)
        if not n:
            return 0
        cols = max(1, getattr(self, "cols", 1))
        return (n + cols - 1) // cols

    def footer_span(self):
        """下面那行应该跨几列（与当前列数一致）。"""
        return max(1, getattr(self, "cols", 1))

    def refresh_dynamic_options(self):
        """重新拉取 options_provider 并更新 Combobox，保持用户已选的「值」不变。

        后台数据就绪时调用（如 CRC 历史缓存预热完成），避免用户必须切走再
        切回才能看到真实下拉选项。
        """
        for spec in self.params:
            provider = getattr(spec, "options_provider", None)
            if provider is None:
                continue
            try:
                opts = provider() or []
            except Exception:
                continue
            w = self.widgets.get(spec.key)
            var = self.vars.get(spec.key)
            if w is None or var is None:
                continue
            labels, mapping = [], {}
            for opt in opts:
                lab = opt.get("label", opt.get("value", ""))
                labels.append(lab)
                mapping[lab] = opt.get("value", lab)
            old_label = var.get()
            old_value = self._choice_map.get(spec.key, {}).get(old_label, old_label)
            self._choice_map[spec.key] = mapping
            try:
                w.configure(values=labels)
                new_label = next((l for l, v in mapping.items()
                                  if v == old_value), None)
                var.set(new_label if new_label is not None
                        else (labels[0] if labels else ""))
            except Exception:
                pass

    # —— 渲染 ——
    def _build(self):
        cols = columns_for(len(self.params), self.avail_width)
        self.cols = cols
        # two_col 保留给旧调用方（结果面板 / 历史逻辑）判断是否多列
        self.two_col = cols >= 2
        for index, spec in enumerate(self.params):
            row = index // cols
            col = index % cols

            # 一个参数 = 一个单元格容器（标签 → 控件 → 错误提示纵向排开）。
            # 用独立容器而不是直接往 master 上塞三行，是为了让 grid 行号
            # 只跟「第几个参数」有关，footer_row() 和外部挂的定时条才算得准。
            cell = ttk.Frame(self.master, width=CELL_MAX_W)
            # sticky="w" 而不是 "ew"：单元格保持 320px 上限，不跟着
            # 参数区的整幅宽度被拉长；内部控件仍 sticky="ew" 撑满该单元格
            cell.grid(row=row, column=col, sticky="w",
                      padx=(0 if col == 0 else COL_GAP, 0), pady=(0, 7))
            cell.columnconfigure(0, weight=1)

            label_text = spec.label + (" *" if spec.required else "")
            if spec.help:
                label_text += HELP_MARK
            label = ttk.Label(cell, text=label_text, bootstyle="secondary")
            label.grid(row=0, column=0, sticky="w", pady=(0, 2))
            if spec.help:
                ToolTip(label, text=spec.help, delay=400)

            var = tk.StringVar()
            if spec.default is not None:
                var.set(str(spec.default))
            widget = self._make_widget(cell, spec, var)
            widget.grid(row=1, column=0, sticky="ew")
            # bool 类型在 _make_widget 中已建立 BooleanVar，这里不能再被 StringVar 覆盖
            # （否则取值为字符串 "False"，bool("False") 为 True，勾选框会永远生效）
            if spec.type != "bool":
                self.vars[spec.key] = var
            self.widgets[spec.key] = widget

            # 用自定义样式而非 bootstyle="danger"：ttkbootstrap 的 danger.TLabel
            # 会带上 #222222 灰底，与深蓝主题不协调；Error.TLabel 由主窗口
            # 的 _configure_ttk_styles() 按语义 token 统一着色。
            err = ttk.Label(cell, text="", style="Error.TLabel")
            err.grid(row=2, column=0, sticky="w", pady=(2, 0))
            # 默认隐藏：空标签也占约 20px 高度，参数多时会挤占结果区空间
            err.grid_remove()
            self.error_labels[spec.key] = err

        # 先清掉列数重排前残留的权重/最小宽（3 列→1 列时旧的第 3 列
        # 还留着 weight=2，会把剩余宽度吞掉一半变成死空白）。
        for c in range(6):
            self.master.columnconfigure(c, weight=0, minsize=0)
        # 参数列不再分摊剩余宽度（weight=1 会把单列表单的输入框拉成 1000px+）。
        # 第 cols 列留给「历史结果」面板，第 cols+1 列是留白；
        # 面板列的 minsize 由 main_window._sync_hist_width 按窗口宽度动态设置，
        # 让面板和 KPI 卡一样随窗口/全屏等比伸缩，留白列吃掉其余空间。
        # 没有结果面板的工具这里保持 cols 列 0 权重、留白吃满，不会变长条。
        for c in range(cols):
            self.master.columnconfigure(c, weight=0, minsize=WIDGET_MIN_W)
        self.master.columnconfigure(cols, weight=0, minsize=0)
        self.master.columnconfigure(cols + 1, weight=1, minsize=0)
        # 排布所需的最小宽度：参数区比它还窄时才需要横向滚动
        self.min_width = cols * CELL_MAX_W + (cols - 1) * COL_GAP + 26

    def _make_widget(self, cell, spec, var):
        if spec.type == "int":
            w = ttk.Spinbox(cell, from_=spec.min or 0, to=spec.max or 9999,
                            textvariable=var, width=SPIN_WIDTH, bootstyle="primary")
            return w

        if spec.type == "bool":
            bv = tk.BooleanVar(value=bool(spec.default))
            self.vars[spec.key] = bv
            box = ttk.Frame(cell)
            # 部分主题/版本下 round-toggle 布局缺失会抛 TclError，
            # 回退到原生 Checkbutton，保证表单一定能渲染出来
            try:
                chk = ttk.Checkbutton(box, variable=bv, text="",
                                      bootstyle="round-toggle")
                chk.pack(side="left", pady=(4, 0))
            except Exception:
                chk = tk.Checkbutton(box, variable=bv, text="")
                chk.pack(side="left", pady=(4, 0))
            return box

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
            w = ttk.Combobox(cell, textvariable=var, values=labels,
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
            return w

        if spec.type == "password":
            return ttk.Entry(cell, textvariable=var, show="*", width=ENTRY_WIDTH)

        if spec.type in ("file", "dir", "device_list"):
            frame = ttk.Frame(cell)
            row = ttk.Frame(frame)
            row.pack(fill="x")
            entry = ttk.Entry(row, textvariable=var, width=ENTRY_WIDTH - 6)
            entry.pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="选择…", width=7, bootstyle="secondary",
                       command=lambda s=spec, v=var: self._pick_path(s, v)
                       ).pack(side="left", padx=(6, 0))
            if spec.type == "device_list":
                # 清单校验结果放在输入框下方（设计稿也是这样），
                # 否则会横着挤占输入框宽度、还把相邻列顶掉。
                # 初始不 pack：空态那句「未选择设备清单文件」纯属噪音，
                # 白白占掉 19px 行高，参数区的可视行数就是这么被吃掉的。
                self._device_count_label = ttk.Label(frame, text="",
                                                     bootstyle="secondary")
            return frame

        return ttk.Entry(cell, textvariable=var, width=ENTRY_WIDTH)

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
        同时通知主窗口把「设备总数」KPI 填上 —— 设计稿口径就是
        「本次清单已加载」，不是等任务跑完才有值。
        """
        if not self._device_count_label:
            return 0, []
        from infra.device_loader import load_devices
        devices, errors = load_devices(path)
        count = len(devices)
        self.set_device_count(count, errors)
        cb = getattr(self, "on_device_count", None)
        if cb:
            try:
                cb(path, count, errors)
            except Exception:
                pass
        return count, errors

    def set_device_count(self, count, errors=None):
        """只回显计数（切工具复用缓存时用，不重读 Excel）。"""
        if not self._device_count_label:
            return
        errors = list(errors or [])
        text = "已加载 %d 台" % count
        if errors:
            text += "，%d 条提示" % len(errors)
        self._device_count_label.configure(
            text=text, bootstyle="warning" if errors else "success")
        try:
            if not self._device_count_label.winfo_ismapped():
                self._device_count_label.pack(anchor="w", pady=(3, 0))
        except Exception:
            pass
        tip = "\n".join(str(e) for e in errors[:8])
        if len(errors) > 8:
            tip += "\n… 还有 %d 条" % (len(errors) - 8)
        try:
            from ttkbootstrap.tooltip import ToolTip
            old = getattr(self, "_device_count_tip", None)
            if old is not None:
                old.toplevel.destroy()
                self._device_count_tip = None
            if tip:
                self._device_count_tip = ToolTip(self._device_count_label,
                                                 text=tip, delay=200)
        except Exception:
            pass

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
