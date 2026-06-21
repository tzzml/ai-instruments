"""ai-instruments: UTG962 + SDS824X HD + UT61E + UT612 仪器驱动核心库。

只含仪器驱动层 (_backend, awg, scope, dmm, lcr)。
应用实验 (Q 值测量等) 见 experiments/ 包。
"""
from . import _backend, awg, dmm, lcr, scope

__all__ = ["_backend", "awg", "scope", "dmm", "lcr"]
