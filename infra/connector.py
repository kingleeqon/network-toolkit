# -*- coding: utf-8 -*-
"""连接层：双通道 netmiko / paramiko（+ telnetlib）（对应 PRD FR-130）。

对上层暴露统一 Session 接口：send(cmd) / disconnect()。
工具不得感知底层通道差异；通道由 vendor_profiles.json 的 driver 决定，默认 netmiko。
第三方库均为可选依赖，缺失时抛 DependencyMissing（由异常映射归类）。
"""
import logging
import re
import socket
import time
from typing import Optional

from core.errors import DependencyMissing
from infra.vendor_profiles import get_profile

logger = logging.getLogger(__name__)

# 提示符判定：行尾是 # / > / ]（与现场脚本 PROMPT_RE 一致）
_PROMPT_RE = re.compile(r"[\#\>\]]\s*$")


class Session(object):
    """统一会话接口。"""

    def send(self, cmd, timeout=30, end_marker=None):
        """执行一条命令并返回完整回显。

        end_marker：可选的结束标记（如烽火配置的 !end）。给了就以它为准，
        没有才回退到「提示符 + 空闲」判定。
        """
        raise NotImplementedError

    def send_timing(self, cmd, timeout=10):
        """敲一条命令、不等完整回显。

        E1000 那种"进 vty 改屏显"的场景要用：那些命令没有可识别的结束标志，
        用 send_command 会一直等到超时。
        """
        return self.send(cmd, timeout=timeout)

    def disconnect(self):
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.disconnect()
        return False


def _decode(data):
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class NetmikoSession(Session):
    """通道 A：netmiko，自动处理分页与 enable。"""

    def __init__(self, device, credential, device_type, timeout=30, paging_cmd=""):
        try:
            from netmiko import ConnectHandler
        except ImportError:
            raise DependencyMissing("未安装 netmiko，请执行 pip install 'netmiko>=4.1,<5.0'")
        self.device = device
        timeout = int(timeout or 30)
        kwargs = {
            "device_type": device_type or "autodetect",
            "host": device.ip,
            "username": credential.username,
            "password": credential.secret,
            # 连接超时/读超时分开给：现场脚本用的是 conn_timeout=60、
            # read_timeout_override=120，命令输出慢时按工具的超时值读会截断
            "timeout": timeout,
            "conn_timeout": min(timeout, 60),
            "read_timeout_override": max(timeout, 120),
            "session_timeout": timeout * 3,
            "fast_cli": False,
        }
        if device.port:
            kwargs["port"] = device.port
        if getattr(credential, "enable", ""):
            kwargs["secret"] = credential.enable
        self.conn = ConnectHandler(**kwargs)
        # 登录后先关分屏，否则长输出会被 --More-- 截断
        if paging_cmd:
            try:
                self.conn.send_command_timing(paging_cmd)
            except Exception:
                pass

    def send(self, cmd, timeout=30, end_marker=None):
        return self.conn.send_command(cmd, read_timeout=timeout)

    def send_timing(self, cmd, timeout=10):
        try:
            return self.conn.send_command_timing(cmd)
        except Exception:
            return ""

    def disconnect(self):
        try:
            self.conn.disconnect()
        except Exception as exc:
            logger.debug("netmiko 断开异常（忽略）：%s", exc)


class ParamikoSession(Session):
    """通道 B：paramiko 原生，自行处理分页与提示符。

    读回显的判定方式对齐现场脚本 netmiko_SSH_device_system.py：
    分页提示 → 回空格继续；命中结束标记（烽火配置的 !end）→ 收工；
    最后一行是提示符且之后 0.6 秒没有新数据 → 收工。
    """

    IDLE_WAIT = 0.6        # 见到提示符后再等这么久，确认后面没有数据
    POLL_INTERVAL = 0.15   # 轮询间隔
    DEFAULT_PAGING = ("--More--", "--More --", "More")

    def __init__(self, device, credential, timeout=30, paging="", paging_off=""):
        try:
            import paramiko
        except ImportError:
            raise DependencyMissing("未安装 paramiko，请执行 pip install 'paramiko>=3.0,<4.0'")
        self.paging = paging
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=device.ip,
            port=device.port or 22,
            username=credential.username,
            password=credential.secret,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        self.shell = self.client.invoke_shell()
        self.shell.settimeout(timeout)
        time.sleep(0.3)
        self._drain()
        # 迪普/烽火等需先发送分页关闭命令；回显必须等到静默，
        # 否则它的残留会被下一条命令当成回显读走
        if paging_off:
            try:
                self.shell.send(paging_off + "\n")
                time.sleep(0.3)
                self._drain_until_idle()
            except Exception:
                pass

    def _drain(self):
        try:
            data = b""
            while self.shell.recv_ready():
                data += self.shell.recv(65535)
            return _decode(data)
        except socket.timeout:
            return ""

    def _drain_until_idle(self, idle=0.8, max_wait=6.0):
        """丢弃通道里已有/将有的数据，直到静默一段时间。

        用于登录后和关分屏命令之后：不清干净的话，残留回显会被下一条
        命令读走（表现为「配置备份文件里是接口信息」）。
        """
        deadline = time.time() + max_wait
        last_data = time.time()
        while time.time() < deadline:
            if self.shell.recv_ready():
                try:
                    self.shell.recv(65535)
                except Exception:
                    break
                last_data = time.time()
            else:
                if time.time() - last_data >= idle:
                    break
                time.sleep(self.POLL_INTERVAL)

    def _is_paging(self, text):
        """最后一行是不是分页提示（--More-- 之类）。"""
        tail = text[-300:]
        if not tail.strip():
            return False
        last = tail.rstrip().splitlines()[-1].strip()
        if last in self.DEFAULT_PAGING:
            return True
        return bool(self.paging) and last.endswith(self.paging)

    def send(self, cmd, timeout=30, end_marker=None):
        # 先清掉上一条命令的残留，否则会被当成本条命令的回显
        self._drain_until_idle(idle=0.3, max_wait=1.5)
        self.shell.send(cmd + "\n")
        return self._read(timeout, end_marker=end_marker)

    def send_timing(self, cmd, timeout=10):
        """敲一条命令后短暂读一下，不等提示符。"""
        try:
            self.shell.send(cmd + "\n")
            time.sleep(0.5)
            return self._drain()
        except Exception:
            return ""

    def _read(self, timeout=30, end_marker=None):
        buffer = b""
        deadline = time.time() + timeout
        idle_until = None
        while time.time() < deadline:
            try:
                if self.shell.recv_ready():
                    chunk = self.shell.recv(65535)
                    if not chunk:
                        break
                    buffer += chunk
                    text = _decode(buffer)

                    # 结束标记（烽火配置以 !end 收尾）：命中即算读全
                    if end_marker and end_marker in text:
                        break

                    # 分页提示：回一个空格继续读
                    if self._is_paging(text):
                        self.shell.send(" ")
                        time.sleep(0.2)
                        continue

                    lines = text.rstrip().splitlines()
                    last = lines[-1] if lines else ""
                    if idle_until is None and _PROMPT_RE.search(last):
                        idle_until = time.time() + self.IDLE_WAIT

                # 见到提示符后静默一段时间即收工（末尾可能还有数据在路上）
                if idle_until and time.time() > idle_until and not self.shell.recv_ready():
                    break
            except socket.timeout:
                if not self.shell.recv_ready():
                    break
            except Exception:
                break
            time.sleep(self.POLL_INTERVAL)
        return _decode(buffer)

    def disconnect(self):
        try:
            self.client.close()
        except Exception as exc:
            logger.debug("paramiko 断开异常（忽略）：%s", exc)


class TelnetSession(Session):
    """Telnet 通道（标准库 telnetlib；Python 3.13 起移除，需 telnetlib3）。"""

    def __init__(self, device, credential, timeout=30, paging=""):
        try:
            import telnetlib
        except ImportError:
            raise DependencyMissing("当前 Python 版本已移除 telnetlib，请安装 telnetlib3 后适配")
        self.paging = paging
        self.tn = telnetlib.Telnet(device.ip, device.port or 23, timeout=timeout)
        self._expect_login(credential, timeout)

    def _expect_login(self, credential, timeout):
        self.tn.read_until(b"ogin: ", timeout=timeout)
        self.tn.write(credential.username.encode("ascii") + b"\n")
        if credential.secret:
            self.tn.read_until(b"assword: ", timeout=timeout)
            self.tn.write(credential.secret.encode("ascii") + b"\n")
        time.sleep(0.3)

    def send(self, cmd, timeout=30):
        self.tn.write(cmd.encode("ascii") + b"\n")
        data = self.tn.read_until(b"#", timeout=timeout)
        text = _decode(data)
        if self.paging and self.paging in text[-200:]:
            self.tn.write(b" ")
            text += _decode(self.tn.read_until(b"#", timeout=timeout))
        return text

    def disconnect(self):
        try:
            self.tn.close()
        except Exception as exc:
            logger.debug("telnet 断开异常（忽略）：%s", exc)


def open_session(device, timeout=30, ctx=None, paging_cmd=None):
    """按厂商配置选择通道，返回统一 Session（上下文管理器）。

    用法：with ctx.open_session(dev, 30) as s: text = s.send(cmd)

    paging_cmd：登录后要发的"关闭分屏"命令（各厂商不同，调用方按场景算好传进来；
    为空则按厂商配置里的默认值）。
    """
    from core.errors import CredentialError
    from infra.credential_store import get_credential

    profile, matched = get_profile(device.vendor)
    if not matched and ctx is not None:
        ctx.log("未识别厂商「%s」，使用通用命令，结果可能不完整" % (device.vendor or "空"),
                level="WARN", ip=device.ip)

    # 账号密码来源：清单里直接带的 > 凭证库引用（两者都支持）
    if getattr(device, "username", "") or getattr(device, "password", ""):
        from infra.credential_store import Credential
        credential = Credential(name=device.credential_ref or "设备清单",
                                username=device.username,
                                secret=device.password,
                                enable=getattr(device, "enable_password", "") or "")
    else:
        credential = get_credential(device.credential_ref)
        if credential is None:
            raise CredentialError(
                "设备清单里没有用户名/密码，凭证库也没有「%s」"
                % (device.credential_ref or "（未指定）"))

    protocol = (device.protocol or "ssh").lower()
    if protocol == "telnet":
        return TelnetSession(device, credential, timeout=timeout, paging=profile.get("paging", ""))

    if paging_cmd is None:
        from infra.vendor_profiles import get_paging_cmd
        paging_cmd = get_paging_cmd(device.vendor, device.ip, device.hostname)

    driver = (profile.get("driver") or "netmiko").lower()
    if driver == "netmiko":
        return NetmikoSession(device, credential,
                              device_type=profile.get("netmiko_device_type", "autodetect"),
                              timeout=timeout, paging_cmd=paging_cmd)
    return ParamikoSession(device, credential, timeout=timeout,
                           paging=profile.get("paging", ""),
                           paging_off=paging_cmd)
