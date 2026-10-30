# -*- coding: utf-8 -*-
"""凭证管理窗口。

采集类工具要登录设备，必须有账号密码。凭证用 Windows DPAPI 加密后存在
用户目录（infra/credential_store.py），**明文不落盘**。

没有这个窗口，凭证库永远是空的，采集第一步就会报
「凭证「default」不存在或解密失败」。
"""
import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox

from infra.credential_store import (delete_credential, get_credential,
                                    list_credentials, set_credential)

AUTH_TYPES = ("password", "keyboard-interactive", "key")


class CredentialDialog(object):
    """凭证列表 + 新增/编辑/删除。"""

    def __init__(self, master):
        self.master = master
        self.win = ttk.Toplevel(master)
        self.win.title("凭证管理")
        self.win.geometry("680x520")
        self.win.minsize(600, 440)
        try:
            self.win.transient(master)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._edit_name = None      # 正在编辑的凭证名（None = 新增）

        root = ttk.Frame(self.win, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        ttk.Label(root, text="设备登录凭证（密码用 Windows DPAPI 加密保存，不落明文）",
                  bootstyle="secondary").grid(row=0, column=0, sticky="w", pady=(0, 8))

        # 列表
        cols = ("name", "username", "auth", "remark")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=8)
        for key, title, width in (("name", "名称（清单里引用）", 160),
                                  ("username", "用户名", 130),
                                  ("auth", "认证方式", 130),
                                  ("remark", "备注", 180)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        vs = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview)
        vs.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.bind("<Double-1>", lambda e: self.edit())

        # 表单
        form = ttk.LabelFrame(root, text="凭证", padding=10)
        form.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        form.columnconfigure(1, weight=1)

        self.var_name = tk.StringVar()
        self.var_user = tk.StringVar()
        self.var_pass = tk.StringVar()
        self.var_enable = tk.StringVar()
        self.var_auth = tk.StringVar(value="password")
        self.var_remark = tk.StringVar()

        def frow(r, label, var, secret=False):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=3)
            e = ttk.Entry(form, textvariable=var, width=40,
                          show="*" if secret else "")
            e.grid(row=r, column=1, sticky="ew", pady=3)
            return e

        frow(0, "名称", self.var_name)
        frow(1, "用户名", self.var_user)
        frow(2, "密码", self.var_pass, secret=True)
        frow(3, "enable（可选）", self.var_enable)
        ttk.Label(form, text="认证方式").grid(row=4, column=0, sticky="w", pady=3)
        self.cmb_auth = ttk.Combobox(form, textvariable=self.var_auth,
                                      values=list(AUTH_TYPES), state="readonly", width=18)
        self.cmb_auth.grid(row=4, column=1, sticky="w", pady=3)
        frow(5, "备注", self.var_remark)

        btns = ttk.Frame(form)
        btns.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="保存", width=8, bootstyle="success",
                   command=self.save).pack(side="left")
        ttk.Button(btns, text="编辑选中", width=10, bootstyle="secondary-outline",
                   command=self.edit).pack(side="left", padx=6)
        ttk.Button(btns, text="删除", width=8, bootstyle="danger-outline",
                   command=self.delete).pack(side="left")
        ttk.Button(btns, text="清空表单", width=10, bootstyle="secondary",
                   command=self.clear_form).pack(side="left", padx=6)

        self.lbl_status = ttk.Label(root, text="", bootstyle="secondary")
        self.lbl_status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.reload()

    # ---------- 列表 ----------
    def reload(self):
        try:
            self.tree.delete(*self.tree.get_children())
        except Exception:
            pass
        names = list_credentials()
        for name in names:
            cred = get_credential(name) or None
            remark = ""
            if cred is not None:
                remark = cred.remark
            self.tree.insert("", "end", iid=name,
                             values=(name, cred.username if cred else "",
                                     cred.auth_type if cred else "", remark))
        self.lbl_status.configure(text="共 %d 条凭证。设备清单里的「凭证」列填这里的名称。"
                                  % len(names))

    # ---------- 表单 ----------
    def clear_form(self):
        self._edit_name = None
        self.var_name.set("")
        self.var_user.set("")
        self.var_pass.set("")
        self.var_enable.set("")
        self.var_auth.set("password")
        self.var_remark.set("")

    def edit(self):
        sel = self.tree.selection()
        if not sel:
            Messagebox.show_info("请先在列表里选中一条凭证。", title="提示", parent=self.win)
            return
        name = sel[0]
        cred = get_credential(name)
        if cred is None:
            Messagebox.show_error("读取凭证「%s」失败（解密失败？）" % name,
                                  title="读取失败", parent=self.win)
            return
        self._edit_name = name
        self.var_name.set(cred.name)
        self.var_user.set(cred.username)
        self.var_pass.set(cred.secret)
        self.var_enable.set(cred.enable)
        self.var_auth.set(cred.auth_type or "password")
        self.var_remark.set(cred.remark)

    def save(self):
        name = self.var_name.get().strip()
        if not name:
            Messagebox.show_warning("请填写凭证名称（设备清单里用它引用）。",
                                    title="信息不全", parent=self.win)
            return
        if not self.var_user.get().strip():
            Messagebox.show_warning("请填写用户名。", title="信息不全", parent=self.win)
            return
        if not self.var_pass.get():
            Messagebox.show_warning("请填写密码。", title="信息不全", parent=self.win)
            return
        try:
            set_credential(name, self.var_user.get().strip(), self.var_pass.get(),
                           self.var_enable.get().strip(), self.var_auth.get(),
                           self.var_remark.get().strip())
        except Exception as exc:
            Messagebox.show_error("保存失败：%s\n\n（DPAPI 需要 pywin32；"
                                  "否则会降级用本地密钥加密）" % exc,
                                  title="保存失败", parent=self.win)
            return
        Messagebox.show_info("已保存凭证「%s」。" % name, title="已保存", parent=self.win)
        self.clear_form()
        self.reload()

    def delete(self):
        sel = self.tree.selection()
        if not sel:
            Messagebox.show_info("请先在列表里选中一条凭证。", title="提示", parent=self.win)
            return
        name = sel[0]
        if not Messagebox.yesno("确定删除凭证「%s」？" % name, title="确认删除",
                                parent=self.win):
            return
        try:
            delete_credential(name)
        except Exception as exc:
            Messagebox.show_error("删除失败：%s" % exc, title="删除失败", parent=self.win)
            return
        self.clear_form()
        self.reload()

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def open_dialog(master):
    old = getattr(master, "_credential_dlg", None)
    if old is not None:
        try:
            old.win.deiconify()
            old.win.lift()
            old.win.focus_force()
            return old
        except Exception:
            pass
    dlg = CredentialDialog(master)
    try:
        master._credential_dlg = dlg
    except Exception:
        pass
    return dlg
