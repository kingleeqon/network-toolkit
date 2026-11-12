# -*- coding: utf-8 -*-
"""UI 设计系统 v0.2：双主题语义 token（对应 design/tokens.css）。

本模块只提供颜色常量、主题映射与纯绘制函数：
    * 不创建任何控件、不持有窗口引用、不碰业务逻辑；
    * 所有组件只引用语义色，禁止在界面代码里硬编码 hex；
    * 9 套 ttkbootstrap 主题收敛为 darkly / flatly 两套，旧偏好平滑迁移。

约定：根节点（ttk.Style）的 theme 名决定模式，新增的 tk 原生控件
（AppBar / Sidebar / KpiStrip / 状态徽章）在主题切换时统一调用
palette(mode) 重新着色。

Python 3.8 兼容：类型注解一律使用 typing，不用 list[str] / X | None。
"""
import math
from typing import Dict, Optional

# —— 主题清单：由 9 套收敛为 2 套语义主题 ——
THEMES = [
    ("darkly", "深色 · 运维控制台"),
    ("flatly", "浅色 · 专业工具"),
]
DEFAULT_THEME = "darkly"

MODE_BY_THEME = {"darkly": "dark", "flatly": "light"}
THEME_BY_MODE = {"dark": "darkly", "light": "flatly"}

# 旧版本偏好文件里可能留着的名字：按深浅归到两套，别让 load_ui_pref
# 因白名单校验失败而静默把用户主题打回默认值。
LEGACY_THEME_MAP = {
    "flatly": "flatly", "litera": "flatly", "journal": "flatly",
    "minty": "flatly", "cosmo": "flatly",
    "darkly": "darkly", "cyborg": "darkly", "solar": "darkly",
    "superhero": "darkly",
}

# —— 语义色板（与 design/tokens.css 一一对应）——
# 键名即语义；soft 用于 chip / 选中态底色，实色用于文字、色条、强调。
PALETTES = {
    "dark": {
        "app": "#0a1120", "panel": "#0f1a2d", "surface": "#142138",
        "elevated": "#1a2b46", "hover": "#20334f", "active": "#263c5c",
        "inset": "#0c1627",
        "border": "#243753", "border_strong": "#33507a",
        "text": "#edf3fb", "text_secondary": "#a8bad2", "text_muted": "#7b8fac",
        "brand": "#22d3ee", "brand_soft": "#123243", "brand_on": "#04121c",
        "on_primary": "#ffffff",   # 主按钮上的图标/文字，深浅主题都用白
        "primary_fill": "#2b6cb0",  # 主操作按钮底色（对接设计稿的实心蓝）
        "accent": "#7c96ff", "accent_soft": "#222c4d",
        "success": "#34d399", "success_soft": "#14342f",
        "warning": "#fbbf24", "warning_soft": "#38301a",
        "danger": "#f87171", "danger_soft": "#3a2029",
        "info": "#60a5fa", "info_soft": "#1c2c45",
        "neutral": "#94a3b8", "neutral_soft": "#1e2a3c",
    },
    "light": {
        "app": "#f2f6fb", "panel": "#ffffff", "surface": "#ffffff",
        "elevated": "#f7fafd", "hover": "#eef3f9", "active": "#e3ecf7",
        "inset": "#f7fafd",
        "border": "#dce4ef", "border_strong": "#c2d0e2",
        "text": "#15243b", "text_secondary": "#50627d", "text_muted": "#6d7f99",
        "brand": "#0e8ba8", "brand_soft": "#dff2f6", "brand_on": "#ffffff",
        "on_primary": "#ffffff",
        "primary_fill": "#2563eb",
        "accent": "#4f6bed", "accent_soft": "#e8ecfd",
        "success": "#0b8a61", "success_soft": "#dff5ec",
        "warning": "#a3600a", "warning_soft": "#fdf1d9",
        "danger": "#c83232", "danger_soft": "#fdeaea",
        "info": "#2563eb", "info_soft": "#e6effd",
        "neutral": "#64748b", "neutral_soft": "#edf1f6",
    },
}

# —— 工具分类：色 + 几何标记（tk 里不用 SVG，避免引入图标库）——
CATEGORY_COLORS = {
    "采集分析类": ("#22d3ee", "#0e8ba8"),
    "解析类":     ("#a78bfa", "#7c5cd6"),
    "工具类":     ("#60a5fa", "#2563eb"),
    "检查类":     ("#fbbf24", "#a3600a"),
    "会话管理类": ("#34d399", "#0b8a61"),
    "示例":       ("#94a3b8", "#64748b"),
}
CATEGORY_MARKS = {
    "采集分析类": "◆",
    "解析类":     "◇",
    "工具类":     "■",
    "检查类":     "▲",
    "会话管理类": "●",
    "示例":       "○",
}
DEFAULT_CATEGORY_COLOR = ("#94a3b8", "#64748b")
DEFAULT_CATEGORY_MARK = "◇"

# 分类 / 标签页 → icons.py 里的线性图标名
CATEGORY_ICONS = {
    "采集分析类": "activity",
    "解析类":     "scan",
    "工具类":     "calc",
    "检查类":     "shield",
    "会话管理类": "terminal",
    "示例":       "beaker",
}
DEFAULT_CATEGORY_ICON = "layers"

TAB_ICONS = {
    "result":  "table",
    "chart":   "chart",
    "log":     "terminal",
    "summary": "clipboard",
}

# —— 状态语义（状态栏徽章 / KPI 数值色）——
STATUS_SEMANTIC = {
    "idle": "neutral", "running": "brand", "cancelling": "warning",
    "succeeded": "success", "partial": "warning", "failed": "danger",
    "cancelled": "neutral",
}

FONT_SANS = "Microsoft YaHei UI"
FONT_MONO = "Consolas"


def migrate_theme(value):
    """把偏好文件里的主题名归一到当前两套之一。"""
    name = str(value or "").strip()
    if name in MODE_BY_THEME:
        return name
    return LEGACY_THEME_MAP.get(name, DEFAULT_THEME)


def mode_of(theme_name):
    """ttkbootstrap 主题名 → dark / light。"""
    return MODE_BY_THEME.get(migrate_theme(theme_name), "dark")


def theme_of(mode):
    """dark / light → ttkbootstrap 主题名。"""
    return THEME_BY_MODE.get(mode, DEFAULT_THEME)


def palette(mode):
    """取当前模式的语义色板（返回副本，调用方随便改不会污染常量）。"""
    return dict(PALETTES.get(mode, PALETTES["dark"]))


def category_color(category, mode):
    """分类色：深色/浅色各一份。"""
    pair = CATEGORY_COLORS.get(category, DEFAULT_CATEGORY_COLOR)
    return pair[1] if mode == "light" else pair[0]


def category_mark(category):
    return CATEGORY_MARKS.get(category, DEFAULT_CATEGORY_MARK)


def category_icon(category):
    return CATEGORY_ICONS.get(category, DEFAULT_CATEGORY_ICON)


def tab_icon(key):
    return TAB_ICONS.get(key, DEFAULT_CATEGORY_ICON)


def semantic(color_key, mode):
    """按语义键取色（brand / success / ...），找不到就退回主文字色。"""
    p = PALETTES.get(mode, PALETTES["dark"])
    return p.get(color_key, p["text"])


def draw_logo(canvas, color, size=34):
    """在 Canvas 上画六边形拓扑节点（品牌标识），纯 Tk 绘制、零资源依赖。

    Tk 的 PhotoImage 不认 SVG，与其引入图标库或图片资源，不如直接用
    Canvas 画：高 DPI 下矢量不糊，主题切换时只要重画一次换色即可。
    """
    try:
        canvas.delete("all")
        cx = cy = size / 2.0
        r = size * 0.40
        pts = []
        for i in range(6):
            a = math.radians(-90 + i * 60)
            pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])
        inner = []
        for i in range(6):
            a = math.radians(-90 + i * 60)
            inner.append((cx + r * 0.38 * math.cos(a),
                          cy + r * 0.38 * math.sin(a)))
        for i in range(6):
            x1, y1 = pts[i * 2], pts[i * 2 + 1]
            ix, iy = inner[i]
            canvas.create_line(x1, y1, ix, iy, fill=color, width=1)
        canvas.create_polygon(pts, fill="", outline=color, width=2)
        canvas.create_oval(cx - size * 0.11, cy - size * 0.11,
                           cx + size * 0.11, cy + size * 0.11,
                           fill=color, outline="")
    except Exception:
        pass
