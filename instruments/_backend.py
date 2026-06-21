"""
底层 USBTMC 通信后端。

两台仪器都是 USB488 (USBTMC, interface class 0xFE/0x03/0x01)，
统一用 pyvisa-py + libusb 控制。macOS 上无需 NI-VISA。

关键经验 (2026-06-20 实测):
1. rm.list_resources() / USB?*INSTR 发现不到设备 (pyvisa-py USBTMC 发现有 bug)，
   必须用精确资源字符串手动 open_resource。
2. 每台仪器在整个进程内只开一个长期会话, 不要反复 open/close ——
   反复打开会导致 USB 接口被占用 ("Access denied")。
3. UTG962 的 USBTMC 读取有缺陷: 第一条 query 成功后, 后续 query 超时且不释放,
   会污染共享的 ResourceManager 状态。因此 AWG 采用"只写不查"策略;
   所有需要读取的场景都用示波器完成。
4. SDS824X HD 在单会话内连续 query 完全正常。
"""
from __future__ import annotations

import contextlib
import threading
import warnings
from dataclasses import dataclass
from typing import Iterator, Optional

import pyvisa

# UTG962 固件不返回 \n 终止符，抑制 pyvisa 的误报警告
warnings.filterwarnings("ignore", message="read string doesn.t end with termination")


@dataclass(frozen=True)
class InstrumentSpec:
    """一台仪器的 USB 标识。"""
    name: str
    vendor_id: int
    product_id: int
    serial: str
    dialect: str  # "scpi" (UTG) 或 "lecroy" (Siglystyle)

    @property
    def resource_string(self) -> str:
        return "USB0::0x%04X::0x%04X::%s::INSTR" % (
            self.vendor_id, self.product_id, self.serial
        )


INSTRUMENTS: dict[str, InstrumentSpec] = {
    "awg": InstrumentSpec("UTG962", 0x6656, 0x0834, "1021472514", "scpi"),
    "scope": InstrumentSpec("SDS824X HD", 0xF4EC, 0x1017, "SDS08A0C801504", "lecroy"),
}


class InstrumentError(RuntimeError):
    """仪器通信或命令错误。"""


# 全局单例: 每台仪器一个长期会话 + 一个专用 ResourceManager (隔离污染)
_RM: dict[str, pyvisa.ResourceManager] = {}
_SESSION: dict[str, pyvisa.resources.MessageBasedResource] = {}
_LOCK = threading.Lock()


def _get_session(key: str, timeout_ms: int = 5000) -> pyvisa.resources.MessageBasedResource:
    """获取/创建一台仪器的长期会话 (线程安全单例)。"""
    if key in _SESSION:
        return _SESSION[key]
    spec = INSTRUMENTS.get(key)
    if spec is None:
        raise InstrumentError("未知仪器 '%s'，可选: %s" % (key, ", ".join(INSTRUMENTS)))
    with _LOCK:
        if key not in _SESSION:
            # 每台仪器用独立的 ResourceManager, 避免互相污染
            rm = pyvisa.ResourceManager("@py")
            _RM[key] = rm
            try:
                inst = rm.open_resource(spec.resource_string, timeout=timeout_ms)
            except Exception as e:
                raise InstrumentError(
                    "无法打开 %s (%s)。请确认仪器已 USB 连接并开机。%s"
                    % (spec.name, spec.resource_string, e)
                ) from e
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            _SESSION[key] = inst
    return _SESSION[key]


def session(key: str, timeout_ms: int = 5000) -> pyvisa.resources.MessageBasedResource:
    """返回一台仪器的长期会话 (不关闭, 复用)。"""
    inst = _get_session(key, timeout_ms)
    inst.timeout = timeout_ms
    return inst


@contextlib.contextmanager
def borrow(key: str, timeout_ms: int = 5000) -> Iterator[pyvisa.resources.MessageBasedResource]:
    """借用会话上下文 (不关闭, 用完归还给池)。

    与 connect() 不同, 这里不 close —— 会话被复用, 避免接口竞争。
    """
    yield session(key, timeout_ms)


def write(key: str, command: str) -> None:
    """发送一条 SCPI 命令 (无返回值)。"""
    session(key).write(command)


def query(key: str, command: str, timeout_ms: int = 5000) -> str:
    """查询并返回响应 (已去尾空白)。"""
    return session(key, timeout_ms).query(command).strip()


def idn(key: str) -> str:
    return query(key, "*IDN?")


def ping(key: str) -> bool:
    try:
        idn(key)
        return True
    except InstrumentError:
        return False


def reset_session(key: str) -> None:
    """强制关闭并重建一台仪器的会话 (出错恢复用)。"""
    with _LOCK:
        inst = _SESSION.pop(key, None)
        if inst is not None:
            with contextlib.suppress(Exception):
                inst.close()
        _RM.pop(key, None)


def close_all() -> None:
    """关闭所有会话 (进程退出前调用)。"""
    with _LOCK:
        for inst in _SESSION.values():
            with contextlib.suppress(Exception):
                inst.close()
        _SESSION.clear()
        _RM.clear()


import atexit
atexit.register(close_all)


if __name__ == "__main__":
    # 自检: 只对 SDS 做 *IDN? (AWG 查询不稳定, 这里只验写通道用 configure)
    for k, spec in INSTRUMENTS.items():
        try:
            print("%-6s -> %s" % (k, idn(k)))
        except InstrumentError as e:
            print("%-6s -> IDN 失败 (写通道可能仍正常): %s" % (k, str(e)[:60]))
