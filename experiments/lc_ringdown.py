"""
LC 阻尼振荡衰减 (ring-down) — 时域法测 Q。

原理: AWG 低频方波激励 LC 回路, 每个上升沿给回路一次"踢",
回路以固有频率 f₀ 自由振荡, 幅度按 V(t) = V₀·e^(−t/τ_d)·cos(2πf₀t) 衰减。
示波器 SINGLE 触发捕获完整衰减包络, 从峰值序列提取:
  - f₀   = 1 / ⟨相邻峰间隔⟩
  - τ_d  = −1 / slope(ln(V_peak) vs t_peak)
  - Q    = π·f₀·τ_d    (与扫频法 Q = f₀/BW 本质等价)

接线 (并联谐振):
  AWG CH1 ──R(1kΩ)──┬── L ∥ C ── 地
                     │
  示波器 CH1 ────────┘  (测回路两端电压)

方波频率 << f₀ (如 f₀≈800 kHz 时方波 ~2 kHz), 让振荡在半周期内衰减完。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from instruments import awg, scope


@dataclass
class RingdownResult:
    time: np.ndarray
    voltage: np.ndarray
    f0: float               # 谐振频率 Hz
    tau_d: float            # 包络衰减时间常数 s
    q: float                # 品质因数
    peak_times: np.ndarray  # 各峰时间 s
    peak_volts: np.ndarray  # 各峰电压 V


def _find_peaks(v: np.ndarray, min_dist: int) -> np.ndarray:
    """纯 numpy 找局部极大值, 相邻峰间隔不小于 min_dist 个采样点。"""
    if len(v) < 3:
        return np.array([], dtype=int)
    d = np.diff(v)
    peaks = np.where((d[:-1] > 0) & (d[1:] < 0))[0] + 1
    if min_dist <= 1 or len(peaks) == 0:
        return peaks
    keep, last = [], -min_dist
    for p in peaks:
        if p - last >= min_dist:
            keep.append(p)
            last = p
    return np.array(keep, dtype=int)


def analyze_ringdown(t: np.ndarray, v: np.ndarray, f0_guess: float
                     ) -> Tuple[float, float, float, np.ndarray, np.ndarray]:
    """从衰减振荡提取 (f0, tau_d, Q, peak_times, peak_volts)。

    方法: 峰值对数回归。
      1) 去直流, 找正峰;
      2) ln(V_peak) 对 t_peak 线性回归 → 斜率 = −1/τ_d;
      3) f₀ 取相邻峰间隔倒数均值;
      4) Q = π·f₀·τ_d。

    替代方法 (更鲁棒, 需 scipy): Hilbert 变换求解析信号包络再拟合。
    本实现零依赖 (纯 numpy), 对无重叠噪声的振荡足够。
    """
    v = v - np.mean(v)
    dt = float(t[1] - t[0]) if len(t) > 1 else 1e-6
    period_pts = max(3, int(round(1.0 / (f0_guess * dt))))
    peaks = _find_peaks(v, period_pts // 2)
    if len(peaks) > 0:
        thr = float(np.max(v[peaks])) * 0.05
        peaks = peaks[v[peaks] > thr]
    if len(peaks) < 3:
        raise ValueError(
            "仅检测到 %d 个峰 (需 ≥3)。检查信号幅度/触发电平/时基是否覆盖足够周期。"
            % len(peaks))

    t_p = t[peaks]
    v_p = np.abs(v[peaks])
    # ln(V) = ln(V0) − t/τ_d
    A = np.vstack([t_p, np.ones_like(t_p)]).T
    slope, _intercept = np.linalg.lstsq(A, np.log(v_p), rcond=None)[0]
    tau_d = -1.0 / slope
    f0 = 1.0 / float(np.mean(np.diff(t_p)))
    q = np.pi * f0 * tau_d
    return f0, tau_d, q, t_p, v_p


def measure_ringdown(
    *,
    f0_guess: float = 806e3,
    awg_freq: float = 2000,
    awg_amp: float = 4.0,
    scope_channel: int = 1,
    trig_level: float = 1.0,
    cycles: int = 20,
    timeout: float = 5.0,
) -> RingdownResult:
    """自动化 LC ring-down 测量。

    Args:
        f0_guess: 预估谐振频率 (Hz), 用于设时基/采样率/峰距门限。
        awg_freq: 方波激励频率 (Hz), 应远小于 f0。
        awg_amp: 方波幅度 Vpp。
        cycles: 捕获窗口覆盖的振荡周期数 (定时间窗)。
        trig_level: 边沿触发电平 (V)。
        timeout: SINGLE 触发等待超时 (秒)。
    """
    # 1. AWG 方波激励
    awg.configure(1, wave="square", frequency=awg_freq,
                  amplitude=awg_amp, load=10000, output=True)
    # 2. 示波器: 时基显示 cycles 个周期; 采样率尽量 > 20·f0 (某些时基下会被锁定, 容错)
    scope.set_timebase(cycles / (f0_guess * 10))
    scope.set_acquire_mode("YT")
    try:
        scope.set_srate(20 * f0_guess)
    except Exception:
        pass

    # 3. SINGLE 单次捕获衰减瞬态
    ok = scope.acquire_single(scope_channel, trig_level, "POS", timeout)
    try:
        data = scope.get_waveforms([scope_channel])
    finally:
        awg.output_off(1)
    if not ok:
        raise RuntimeError("SINGLE 触发超时 — 检查接线/触发电平/方波输出")

    t, v = data[scope_channel]
    f0, tau_d, q, t_p, v_p = analyze_ringdown(t, v, f0_guess)
    return RingdownResult(time=t, voltage=v, f0=f0, tau_d=tau_d, q=q,
                          peak_times=t_p, peak_volts=v_p)


def plot_result(r: RingdownResult, path: str) -> None:
    """画衰减振荡 (+包络/峰) + 峰值对数图, 存 PNG。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    ax1.plot(r.time * 1e6, r.voltage, linewidth=0.8)
    env = float(np.max(r.peak_volts)) * np.exp(-r.time / r.tau_d)
    ax1.plot(r.time * 1e6, env, "--", color="r", alpha=0.7, label="包络 e^(−t/τ_d)")
    ax1.plot(r.time * 1e6, -env, "--", color="r", alpha=0.7)
    ax1.scatter(r.peak_times * 1e6, r.peak_volts, color="g", s=12, zorder=5, label="峰")
    ax1.set_xlabel("时间 (µs)"); ax1.set_ylabel("电压 (V)")
    ax1.set_title("衰减振荡 f₀ = %.1f kHz" % (r.f0 / 1e3))
    ax1.legend(); ax1.grid(alpha=0.3)
    ax2.semilogy(r.peak_times * 1e6, r.peak_volts, "g-o", markersize=4)
    ax2.set_xlabel("峰时间 (µs)"); ax2.set_ylabel("峰电压 (V, 对数)")
    ax2.set_title("ln(V) 线性衰减 → τ_d = %.2f µs, Q = %.1f"
                  % (r.tau_d * 1e6, r.q))
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
