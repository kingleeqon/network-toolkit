# -*- coding: utf-8 -*-
"""Windows 高 DPI 感知（对应 3K 屏 125%~175% 缩放场景）。

原理（实测 Tcl/Tk 8.6.15 @ Windows）：
    Tk 的 `tk scaling` 表示"每磅(point)多少像素"，1 磅 = 1/72 英寸，
    因此正确值恒为 dpi / 72：
        96  dpi(100%) -> 1.333   <- Tk 的内建基线值
        120 dpi(125%) -> 1.667
        144 dpi(150%) -> 2.0
        168 dpi(175%) -> 2.333
    Tk 自己算得很准，**不需要手工校正**。

真正的问题在别处：进程若未声明 DPI 感知，Windows 会对它谎报 96 DPI，
Tk 便按 100% 绘制整幅画面，再由系统把这张位图拉伸到 1.5 倍 —— 这就是
高分屏下 tkinter 界面发糊的全部原因。开启感知后 Windows 讲真话，Tk 自己
就会用正确的 scaling 重绘，文字由 ClearType 矢量呈现，边缘锐利。

因此本模块只做一件事：**在创建任何 Tk/Window 对象之前，先声明 DPI 感知。**

DPI 感知是进程级的、不可撤销 —— 一旦设置成功，后续调用会被 Windows 拒绝
并返回 0。这恰好是好事：ttkbootstrap.Window(hdpi=True) 内部还会调用
`SetProcessDPIAware()`，那次调用会失败并被它静默吞掉，我们的设置得以保留。

用法：
    import ui.dpi as dpi
    dpi.set_dpi_awareness()     # 必须在 Tk()/Window() 之前
    root = ttk.Window(...)
    dpi.verify_scaling(root)    # 可选：异常兜底校正
"""
import sys
from typing import Optional

IS_WINDOWS = sys.platform.startswith("win")

POINTS_PER_INCH = 72.0
DEFAULT_DPI = 96.0

# PROCESS_DPI_AWARENESS
PROCESS_DPI_UNAWARE = 0
PROCESS_SYSTEM_DPI_AWARE = 1
PROCESS_PER_MONITOR_DPI_AWARE = 2

# DPI_AWARENESS_CONTEXT
_CONTEXT_PER_MONITOR_AWARE_V2 = -4

# tkinter 不处理 WM_DPICHANGED，且 Tk 8.6.9 在多屏不同 DPI 下有已知定位
# 缺陷（tk ticket a9ee4410），故默认取 SYSTEM_AWARE：单屏 125%~175% 完全够用，
# 跨屏拖动时由 Windows 做位图拉伸（尺寸正确、略糊），胜于位置错乱。
DEFAULT_AWARENESS = PROCESS_SYSTEM_DPI_AWARE

# 判定交付: 实测 tolerable
SCALING_TOLERANCE = 0.08


def _win_dll():
    try:
        import ctypes
        return ctypes.windll
    except Exception:
        return None


def set_dpi_awareness(level=None):
    """在创建 Tk 对象前声明 DPI 感知。返回实际生效的级别名。

    三级降级，任何一级失败都不会抛异常：
        Per-Monitor V2 (Win10 1703+) -> System/Per-Monitor (Win8.1+) -> Vista API
    """
    if not IS_WINDOWS:
        return "not-windows"

    windll = _win_dll()
    if windll is None:
        return "ctypes-unavailable"

    level = DEFAULT_AWARENESS if level is None else level

    # Win8.1+ 首选：SetProcessDpiAwareness(PROCESS_*)
    try:
        ok = windll.shcore.SetProcessDpiAwareness(level)
        if ok:
            return {PROCESS_SYSTEM_DPI_AWARE: "system-aware",
                    PROCESS_PER_MONITOR_DPI_AWARE: "per-monitor"}.get(
                        level, "awareness-%s" % level)
    except Exception:
        pass

    # Win10 1703+：SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)
    try:
        import ctypes
        ok = windll.shcore.SetProcessDpiAwarenessContext(
            ctypes.c_ssize_t(_CONTEXT_PER_MONITOR_AWARE_V2))
        if ok:
            return "per-monitor-v2"
    except Exception:
        pass

    # Win Vista+ 兜底
    try:
        ok = windll.user32.SetProcessDPIAware()
        if ok:
            return "legacy-aware"
    except Exception:
        pass

    return "unaware"


def get_window_dpi(root):
    """读取窗口所在显示器的真实 DPI，失败返回 96。"""
    if not IS_WINDOWS:
        return DEFAULT_DPI
    windll = _win_dll()
    if windll is None:
        return DEFAULT_DPI
    try:
        hwnd = windll.user32.GetParent(root.winfo_id())
        dpi = windll.user32.GetDpiForWindow(hwnd)
        return float(dpi) if dpi else DEFAULT_DPI
    except Exception:
        return DEFAULT_DPI


def expected_scaling(dpi):
    """给定 DPI 下 tk scaling 的正确值。"""
    return dpi / POINTS_PER_INCH


def verify_scaling(root, tolerance=SCALING_TOLERANCE):
    """校验 Tk 自算的 scaling 是否匹配真实 DPI；证据充分时才校正。

    正常情况不会触发 —— Tk 自己算得很准，这是"够用就好"的兜底层。
    有两类情况必须**拒绝校正**，宁可保留 Tk 的原值：

    1. 窗口尚未映射到屏幕（withdraw / 未 update_idletasks）：
       此时 GetDpiForWindow 会返回系统默认值 96，与真实显示器无关。
       若据此校正，会把缩放屏上正确的 2.0 错改成 1.333，界面缩小三分之一。
    2. 读到的 DPI 是 96，但 Tk 的 scaling 明显属于高倍缩放大屏（> 1.5）：
       两者自相矛盾，说明 DPI 读数不可信，同样不该动手。

    返回 (dpi, percent, tk_scaling, 是否校正过)
    """
    dpi = get_window_dpi(root)
    try:
        got = float(root.tk.call("tk", "scaling"))
    except Exception:
        return dpi, int(round(dpi / DEFAULT_DPI * 100)), expected_scaling(dpi), False

    # 情形 1：窗口不可见于屏幕
    try:
        if not root.winfo_viewable():
            return dpi, int(round(dpi / DEFAULT_DPI * 100)), got, False
    except Exception:
        pass

    # 情形 2：DPI 读数与 scaling 自相矛盾
    if dpi <= DEFAULT_DPI and got > 1.5:
        return dpi, int(round(dpi / DEFAULT_DPI * 100)), got, False

    want = expected_scaling(dpi)
    if abs(got - want) <= tolerance:
        return dpi, int(round(dpi / DEFAULT_DPI * 100)), got, False

    try:
        root.tk.call("tk", "scaling", want)
        return dpi, int(round(dpi / DEFAULT_DPI * 100)), got, True
    except Exception:
        return dpi, int(round(dpi / DEFAULT_DPI * 100)), got, False


def configure_fonts(root, family="Microsoft YaHei UI", size=9):
    """统一中文字体，避免各处控件落回系统默认的点阵宋体。

    注意用 point size 而非 pixel size —— point 会随 DPI 缩放，
    这正是高分屏下让文字变大并保持锐利的正确方式。
    """
    import tkinter.font as tkfont
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkFixedFont",
                 "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
                 "TkIconFont", "TkTooltipFont"):
        try:
            f = tkfont.nametofont(name)
        except Exception:
            continue
        try:
            if name == "TkFixedFont":
                f.configure(family="Consolas", size=size)
            else:
                f.configure(family=family, size=size)
        except Exception:
            pass

    try:
        root.option_add("*Font", tkfont.nametofont("TkDefaultFont"))
    except Exception:
        pass
    # Treeview 列头默认字号偏大且不吃全局 Font，单独压制
    try:
        root.option_add("*Treeview.Heading.Font", tkfont.nametofont("TkHeadingFont"))
    except Exception:
        pass


def describe(root):
    """返回一行可读的 DPI 诊断信息，用于「关于」窗口。"""
    dpi, pct, got, corrected = verify_scaling(root)
    return "显示缩放 %d%%（DPI %d），tk scaling=%.3f（理论 %.3f）%s" % (
        pct, dpi, got, expected_scaling(dpi), "，已自动校正" if corrected else "")
