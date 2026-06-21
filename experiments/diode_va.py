"""
二极管伏安特性曲线自动化测量。

原理: AWG 输出慢三角波 (RAMP) 扫描电压, 串联采样电阻 R_sense (一端接地),
示波器双通道同步采集一整个扫描周期:
  CH1 = 二极管正极电压 (相对地) = V_AWG
  CH2 = R_sense 上端电压 (相对地) = I · R_sense
  → V_二极管 = CH1 - CH2
  → I = CH2 / R_sense
画 V_二极管 vs I 即伏安曲线。

接线 (R_sense 接地, 单端测量等效差分):
  AWG CH1 ──┬── 二极管 D (正极朝 AWG) ──┬── R_sense ── 地
           │                              │
  示波器 CH1 (测 V_AWG)         示波器 CH2 (测 I·R_sense)

需要扫到更高电压 (如 >10 V) 时, 在 AWG 与回路之间加外部放大器。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from instruments import awg, scope


@dataclass
class VAResult:
    voltage: np.ndarray    # 二极管两端电压 V (= V_AWG - I·R_sense)
    current: np.ndarray    # 电流 A
    v_awg: np.ndarray      # CH1 原始 (AWG 输出电压)
    v_sense: np.ndarray    # CH2 原始 (R_sense 上电压)
    time: np.ndarray       # 时间轴 s
    period_s: float
    rsense_ohm: float


def measure_diode_va(
    *,
    v_scan: float = 5.0,
    period_s: float = 10.0,
    rsense_ohm: float = 1000.0,
    awg_channel: int = 1,
    v_ch: int = 1,
    i_ch: int = 2,
    vdiv: Optional[float] = None,
    settle_s: float = 1.0,
) -> VAResult:
    """自动化测量二极管伏安曲线。

    Args:
        v_scan: 扫描电压单边幅度 (V), 三角波 ±v_scan, 即 Vpp = 2·v_scan。
        period_s: 三角波周期 (秒)。秒级慢扫让回路始终准静态, 减小动态误差。
        rsense_ohm: 采样电阻 Ω (一端接地)。
        v_ch/i_ch: 示波器测电压/电流的通道号。
        vdiv: 垂直档位 V/div, None 则不动 (用当前值/自动)。
        settle_s: 开启 AWG 后等待稳态的秒数。
    """
    # 1. 配 AWG: 慢对称三角波 (ramp + symmetry 50)
    awg.configure(awg_channel, wave="ramp",
                  amplitude=2 * v_scan, offset=0, load=10000, output=False)
    awg.set_period(awg_channel, period_s)
    awg.set_ramp_symmetry(awg_channel, 50)

    # 2. 配示波器双通道 DC 耦合
    scope.channel_on(v_ch, True)
    scope.channel_on(i_ch, True)
    scope.set_coupling(v_ch, "DC")
    scope.set_coupling(i_ch, "DC")
    if vdiv:
        scope.set_scale(v_ch, vdiv)
        scope.set_scale(i_ch, vdiv)
    # 时基: 一个三角波周期占满 ~10 div → 单帧覆盖完整正反向扫描
    scope.set_timebase(period_s / 10.0)
    scope.set_acquire_mode("YT")

    # 3. 开启扫描, 等进入稳态
    awg.output_on(awg_channel)
    scope.run()
    import time as _t
    _t.sleep(settle_s + period_s * 0.1)

    # 4. 双通道同步采集 (同一帧, 时间轴天然对齐)
    try:
        data = scope.get_waveforms([v_ch, i_ch])
    finally:
        awg.output_off(awg_channel)  # 测完即关, 保护二极管

    t, v_awg = data[v_ch]
    _, v_sense = data[i_ch]
    current = v_sense / rsense_ohm
    voltage = v_awg - v_sense
    return VAResult(voltage=voltage, current=current, v_awg=v_awg,
                    v_sense=v_sense, time=t, period_s=period_s,
                    rsense_ohm=rsense_ohm)


def plot_result(r: VAResult, path: str) -> None:
    """画双通道时域 + 伏安曲线, 存 PNG。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    ax1.plot(r.time, r.v_awg, label="CH1 V_AWG")
    ax1.plot(r.time, r.v_sense, label="CH2 I·R_sense")
    ax1.set_xlabel("时间 (s)"); ax1.set_ylabel("电压 (V)")
    ax1.set_title("原始双通道时域"); ax1.legend(); ax1.grid(alpha=0.3)
    ax2.plot(r.voltage, r.current * 1e3, ".", markersize=2)
    ax2.set_xlabel("二极管电压 (V)"); ax2.set_ylabel("电流 (mA)")
    ax2.set_title("二极管伏安特性"); ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
