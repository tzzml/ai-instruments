"""
线圈 Q 值测量 (扫频带宽法)。

原理: 线圈 L 与电容 C 组成谐振回路，AWG 扫频激励，示波器测回路响应幅度。
在谐振频率 f0 处幅度最大；幅度降到峰值的 1/√2 (即 -3dB) 处对应 f1 < f0 < f2，
则品质因数 Q = f0 / (f2 - f1)。

接线 (并联谐振, 推荐):
  AWG CH1 ──串联小电阻 R(如 1kΩ)──┬──> 示波器 CH1 (激励参考, 可选)
                                   └── L∥C 谐振回路 ──> 示波器 CH1/CH2 (测回路电压)
  共地。

  R 用于让 AWG (50Ω 输出) 近似恒压源驱动高阻谐振回路。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from instruments import _backend as bk
from instruments import awg, scope


@dataclass
class SweepPoint:
    freq: float   # Hz
    vrms: float   # V


@dataclass
class QResult:
    f0: float                    # 谐振频率 Hz
    peak_vrms: float             # 谐振点幅度 V
    f1: float                    # 下 -3dB 频率 Hz
    f2: float                    # 上 -3dB 频率 Hz
    bandwidth: float             # f2 - f1 Hz
    q: float                     # 品质因数
    sweep: list                  # SweepPoint 列表
    inductance_h: Optional[float] = None  # 若给电容值，反推电感
    valid: bool = True
    warnings: list[str] = field(default_factory=list)


def _measure_vrms_at(freq_hz: float, scope_channel: int = 1,
                     settle_s: float = 0.3) -> float:
    """把 AWG 设到指定频率，等示波器稳定后抓波形，返回 RMS 电压。

    关键: 切频率后立刻 scope.run() 恢复采集，settle 期间示波器在 RUN 状态
    直播波形；只在 waveform_stats() 内部短暂冻结 (读波形数据时)。
    """
    awg.set_frequency(1, freq_hz)
    scope.run()  # 恢复 RUN — 上一步 waveform 读取后示波器在 STOP
    import time
    time.sleep(settle_s)  # LC 回路稳定 + 示波器自动触发，用户看到实时波形
    st = scope.waveform_stats(scope_channel)
    return st["vrms"]


def measure_q(
    f_start: float = 100e3,
    f_stop: float = 3e6,
    coarse_points: int = 60,
    coarse_step_hz: Optional[float] = None,
    q_min: float = 30,
    fine_points: int = 80,
    amplitude_vpp: float = 2.0,
    scope_channel: int = 1,
    scope_vdiv: Optional[float] = None,
    capacitance_f: Optional[float] = None,
    load_ohm: float = 10000,
    settle_s: float = 0.3,
    progress=None,
) -> QResult:
    """扫频测量线圈 Q 值。

    Args:
        f_start/f_stop: 粗扫频率范围 (Hz)。
        coarse_points: 粗扫点数 (coarse_step_hz 未设时用对数间隔)。
        coarse_step_hz: 粗扫固定步长 Hz (设了就忽略 coarse_points 和 q_min)。
        q_min: 预期最小 Q 值，自动算步长 = f_min/q_min (保证 BW 内 ≥1 点)。
        fine_points: 细扫点数 (在峰附近精测 -3dB 带宽)。
        amplitude_vpp: AWG 输出幅度 (Vpp, 高阻)。
        scope_channel: 示波器测量通道。
        scope_vdiv: 示波器垂直档位 (V/div)；None 时用 autoset 或固定值。
        capacitance_f: 谐振电容值 (F)；给定则反推电感量。
        progress: 回调 (done, total, freq) 用于显示进度。

    Returns:
        QResult。
    """
    # 配置 AWG: 正弦, 恒定幅度
    awg.configure(1, wave="sine", frequency=f_start, amplitude=amplitude_vpp,
                  load=load_ohm, output=True)

    # 配置示波器: AUTO 触发, 时基跟随中心频率 (显示约 10 个周期)
    scope.channel_on(scope_channel, True)
    scope.set_coupling(scope_channel, "AC")
    if scope_vdiv is not None:
        scope.set_scale(scope_channel, scope_vdiv)
    scope.set_offset(scope_channel, 0)
    center_freq = np.sqrt(f_start * f_stop)  # 几何中心
    scope.set_timebase(10.0 / center_freq)    # 一次设好，不反复改
    scope.trigger_mode("AUTO")

    def sweep(freqs):
        pts = []
        for i, f in enumerate(freqs):
            vrms = _measure_vrms_at(f, scope_channel, settle_s=settle_s)
            pts.append(SweepPoint(f, vrms))
            if progress is not None:
                progress(i + 1, len(freqs), f)
        return pts

    # 1) 粗扫找峰 — 步长根据 Q_min 自适应
    #    确保最低频率处 -3dB 带宽内 ≥1 个采样点，不漏峰
    scope.trigger_mode("AUTO")
    if coarse_step_hz is not None:
        step1 = coarse_step_hz
    else:
        bw_min = f_start / q_min
        decade = 10 ** np.floor(np.log10(bw_min))
        mantissa = bw_min / decade
        if mantissa <= 2:
            step1 = decade
        elif mantissa <= 5:
            step1 = 2 * decade
        else:
            step1 = 5 * decade
        step1 = max(step1, 100.0)
    coarse_freqs = np.arange(f_start, f_stop + step1 * 0.5, step1)
    coarse_freqs = coarse_freqs[coarse_freqs <= f_stop]
    coarse = sweep(list(coarse_freqs))

    peak_idx = int(np.argmax([p.vrms for p in coarse]))
    f_peak = coarse[peak_idx].freq
    peak_vrms = coarse[peak_idx].vrms

    # 2) 细扫 — 范围 ±3×步长 (Q≥q_min 时 -3dB 全覆盖)
    scope.trigger_mode("AUTO")
    span = max(step1 * 3, (f_stop - f_start) * 0.01)
    f_lo = max(f_start, f_peak - span)
    f_hi = min(f_stop, f_peak + span)
    fine_freqs = np.linspace(f_lo, f_hi, fine_points)
    fine = sweep(list(fine_freqs))

    # 合并、按频率排序、去重邻近
    all_pts = sorted(coarse + fine, key=lambda p: p.freq)
    freqs = np.array([p.freq for p in all_pts])
    vrms = np.array([p.vrms for p in all_pts])

    # 3) 精确定位 f0 (峰)
    f0_idx = int(np.argmax(vrms))
    f0 = freqs[f0_idx]
    peak_vrms = vrms[f0_idx]
    threshold = peak_vrms / np.sqrt(2)  # -3dB

    # 4) 找 -3dB 点 f1, f2
    # 下边带: f0 左侧 vrms 降到 threshold
    left = vrms[:f0_idx]
    f1 = _interp_crossing(freqs[:f0_idx], left, threshold)
    right = vrms[f0_idx + 1:]
    f2 = _interp_crossing(freqs[f0_idx + 1:], right, threshold)

    # 检测边界截断: -3dB 点贴近扫频端点说明范围不够
    warnings = []
    f_range = freqs[-1] - freqs[0]
    if f1 is None:
        warnings.append("未找到 -3dB 下边界，请向左扩大扫频范围")
    elif (f1 - freqs[0]) / f_range < 0.05:
        warnings.append("-3dB 下界贴近扫频下边界，建议向左扩范围")
    if f2 is None:
        warnings.append("未找到 -3dB 上边界，请向右扩大扫频范围")
    elif (freqs[-1] - f2) / f_range < 0.05:
        warnings.append("-3dB 上界贴近扫频上边界，建议向右扩范围")

    valid = f1 is not None and f2 is not None
    if valid:
        bandwidth = f2 - f1
        q = f0 / bandwidth if bandwidth > 0 else float("inf")
    else:
        f1 = float("nan")
        f2 = float("nan")
        bandwidth = float("nan")
        q = float("nan")

    # 反推电感 (若给电容): f0 = 1/(2π√LC)
    inductance = None
    if capacitance_f is not None and f0 > 0:
        inductance = 1.0 / ((2 * np.pi * f0) ** 2 * capacitance_f)

    # 扫频结束: STOP 冻结屏幕 (AUTO 会一直自动触发刷新, 停不下来)。
    # 如需继续手动操作, 按 RUN 或示波器前面板 Run/Stop 键。
    scope.stop()
    awg.output_off(1)
    return QResult(
        f0=f0, peak_vrms=peak_vrms, f1=f1, f2=f2,
        bandwidth=bandwidth, q=q, sweep=all_pts, inductance_h=inductance,
        valid=valid, warnings=warnings,
    )


# ---------- AWG 扫频模式 Q 值测量 (实验性, 不建议使用) ----------
# TODO: 长时基下示波器采样率不足导致包络检波失败。
# 需解决: 用 PEAK detect 采集模式 或 分段 Hilbert 变换。
# 当前建议用 measure_q() 逐点扫频 (q_min 参数可加速)。

def measure_q_sweep(
    f_start: float = 535e3,
    f_stop: float = 1605e3,
    sweep_time_s: float = 2.0,
    amplitude_vpp: float = 1.0,
    scope_channel: int = 1,
    scope_vdiv: float = 0.5,
    capacitance_f: Optional[float] = None,
    load_ohm: float = 50,
) -> QResult:
    """利用 AWG 内置扫频 + 示波器单次捕获，1~2 秒完成 Q 值测量。

    原理: AWG 线性扫频 (f_start→f_stop)，示波器 SINGLE 捕获全段包络，
    包络检波后从时间轴映射频率轴，直接算 Q。比逐点扫频快 20-30 倍。

    注意: 示波器设置为 SINGLE 触发，需确保 LC 回路信号能稳定触发。
    """
    import time as _t

    # 1) 配置 AWG 扫频 (不立即输出，等示波器就位)
    awg.arm_sweep(1, f_start, f_stop, sweep_time_s,
                  amplitude_vpp, load=load_ohm)

    # 2) 配置示波器: 长时基 + SINGLE 触发
    scope.channel_on(scope_channel, True)
    scope.set_coupling(scope_channel, "AC")
    scope.set_scale(scope_channel, scope_vdiv)
    scope.set_offset(scope_channel, 0)
    scope.set_timebase(sweep_time_s / 10.0)
    bk.write("scope", ":ACQuire:MDEPth 10M")
    scope.set_trigger_edge(scope_channel, amplitude_vpp * 0.05, "POS")
    scope.stop()
    _t.sleep(0.1)
    bk.write("scope", ":TRIGger:MODE SINGLE")
    _t.sleep(0.3)

    # 3) 启动 AWG 扫频, 等待 SINGLE 捕获完成
    awg.start_sweep(1)
    deadline = _t.time() + sweep_time_s + 8.0
    while _t.time() < deadline:
        try:
            st = bk.query("scope", ":TRIGger:STATus?").strip()
        except Exception:
            st = ""
        if "Stop" in st or "STOP" in st:
            break
        _t.sleep(0.3)
    else:
        awg.output_off(1)
        raise bk.InstrumentError(
            "示波器 SINGLE 捕获超时, 未触发 (%.0fs)" % (sweep_time_s + 8.0))

    awg.output_off(1)

    # 4) 读取冻结的波形 (示波器在 STOP，跳过 _ensure_live)
    scope_inst = bk.session("scope", 15000)
    old_term = scope_inst.read_termination
    scope_inst.read_termination = None  # 二进制读取必须关掉终止符
    try:
        pre = _read_scope_preamble(scope_inst)
        probe = scope.get_probe(scope_channel)
        vdiv = pre["vdiv_raw"] * probe
        voff = pre["voff_raw"] * probe
        code_per_div_8bit = pre["code_per_div"] / (2 ** (pre["adc_bits"] - 8))
        sample_int = pre["sample_int"]

        scope_inst.write(":WAVeform:SOURce C%d" % scope_channel)
        scope_inst.write(":WAVeform:DATA?")
        raw = scope._read_definite_block(scope_inst)
    finally:
        scope_inst.read_termination = old_term
    codes = np.frombuffer(raw, dtype=np.int8).astype(np.float64)
    volts = codes * (vdiv / code_per_div_8bit) - voff

    # 恢复示波器
    scope.trigger_mode("AUTO")
    scope.run()

    # 5) 包络检波
    sweep_rate = (f_stop - f_start) / sweep_time_s  # Hz/s
    # 滑动 RMS 提取包络 (窗口 ≈ 20 个载波周期)
    fc_mid = np.sqrt(f_start * f_stop)
    # 窗口 = 20 个载波周期, 但不超过总样本的 1/10
    win_periods = 20
    win_samples = int(win_periods / max(fc_mid * sample_int, 1e-12))
    win_samples = max(min(win_samples, len(volts) // 10), 3)
    # 整流
    abs_v = np.abs(volts)
    # 移动平均低通滤波
    kernel = np.ones(win_samples) / win_samples
    envelope = np.convolve(abs_v, kernel, mode="same")

    # 峰值附近的载波频率测量 (零交叉法)
    peak_idx = int(np.argmax(envelope))
    # 取峰值附近 1000 个样本做零交叉测频
    zc_start = max(0, peak_idx - 500)
    zc_end = min(len(volts), peak_idx + 500)
    zc_seg = volts[zc_start:zc_end]
    zero_crossings = np.where(np.diff(np.signbit(zc_seg)))[0]
    if len(zero_crossings) >= 6:
        periods = np.diff(zero_crossings[1:-1]) * sample_int
        f0_measured = 1.0 / np.median(periods)
    else:
        # 零交叉不够，用时间映射估算
        t_peak_est = (peak_idx + zc_start) * sample_int
        f0_measured = f_start + sweep_rate * t_peak_est

    # 6) 从包络找 -3dB 点
    peak_env = envelope[peak_idx]
    threshold = peak_env / np.sqrt(2)

    # 左侧找 -3dB
    t1 = None
    for i in range(peak_idx - 1, 0, -1):
        if envelope[i] <= threshold <= envelope[i + 1]:
            frac = (threshold - envelope[i]) / (envelope[i + 1] - envelope[i])
            t1 = (i + frac) * sample_int
            break

    # 右侧找 -3dB
    t2 = None
    for i in range(peak_idx, len(envelope) - 1):
        if envelope[i] >= threshold >= envelope[i + 1]:
            frac = (envelope[i] - threshold) / (envelope[i] - envelope[i + 1])
            t2 = (i + frac) * sample_int
            break

    if t1 is None or t2 is None:
        raise bk.InstrumentError("未找到完整 -3dB 边界，请扩大扫频范围或降低 sweep_time")

    bw_time = t2 - t1
    bw_freq = bw_time * sweep_rate
    q = f0_measured / bw_freq if bw_freq > 0 else float("inf")

    # 反推电感
    inductance = None
    if capacitance_f is not None and f0_measured > 0:
        inductance = 1.0 / ((2 * np.pi * f0_measured) ** 2 * capacitance_f)

    # 构造 SweepPoint 列表用于画图
    n_plot = min(len(envelope), 500)
    idx_plot = np.linspace(0, len(envelope) - 1, n_plot).astype(int)
    sweep_pts = [
        SweepPoint(f_start + sweep_rate * (i * sample_int), float(envelope[i]))
        for i in idx_plot
    ]

    return QResult(
        f0=f0_measured, peak_vrms=float(peak_env / np.sqrt(2)),  # envelope → approx Vrms
        f1=f_start + sweep_rate * t1,
        f2=f_start + sweep_rate * t2,
        bandwidth=bw_freq, q=q, sweep=sweep_pts,
        inductance_h=inductance,
    )


def _read_scope_preamble(inst) -> dict:
    """读取 SDS 波形 PREAMBLE (调用方确保 read_termination=None)。

    PREAMBLE 是 IEEE 488.2 定长度块: #<n><len><346-byte-data>
    """
    inst.write(":WAVeform:PREamble?")
    # 跳过前导非 '#' 字节 (可能含 '\n' 等)
    while inst.read_bytes(1) != b"#":
        pass
    nlen = int(inst.read_bytes(1))
    datalen = int(inst.read_bytes(nlen))
    raw = inst.read_bytes(datalen)
    import struct as _st
    return {
        "n_points": _st.unpack_from("<i", raw, 116)[0],
        "vdiv_raw": _st.unpack_from("<f", raw, 156)[0],
        "voff_raw": _st.unpack_from("<f", raw, 160)[0],
        "code_per_div": _st.unpack_from("<f", raw, 164)[0],
        "adc_bits": _st.unpack_from("<h", raw, 172)[0],
        "sample_int": _st.unpack_from("<f", raw, 176)[0],
    }


def _interp_crossing(freqs: np.ndarray, vrms: np.ndarray,
                     threshold: float) -> Optional[float]:
    """在 vrms 单调段线性插值找到等于 threshold 的频率。"""
    if len(freqs) == 0:
        return None
    # 找 vrms 跨越 threshold 的区间
    below = vrms < threshold
    for i in range(len(vrms) - 1):
        if (vrms[i] - threshold) * (vrms[i + 1] - threshold) <= 0 and vrms[i] != vrms[i + 1]:
            # 线性插值
            t = (threshold - vrms[i]) / (vrms[i + 1] - vrms[i])
            return freqs[i] + t * (freqs[i + 1] - freqs[i])
    return None


def plot_result(result: QResult, save_path: Optional[str] = None):
    """画幅度-频率曲线 + 标注 f0/Q。返回 matplotlib Figure。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    freqs = np.array([p.freq for p in result.sweep])
    vrms = np.array([p.vrms for p in result.sweep])

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(freqs / 1e3, vrms, "b.-", ms=3, lw=0.8, label="Response")
    ax.axvline(result.f0 / 1e3, color="r", ls="--", lw=1, label="f₀ = %.2f kHz" % (result.f0 / 1e3))
    ax.axhline(result.peak_vrms, color="g", ls=":", lw=0.8, alpha=0.6)
    ax.axhline(result.peak_vrms / np.sqrt(2), color="orange", ls=":", lw=0.8,
               label="-3dB level")
    if result.f1 > 0:
        ax.axvline(result.f1 / 1e3, color="orange", ls=":", lw=0.8)
        ax.axvline(result.f2 / 1e3, color="orange", ls=":", lw=0.8)

    title = "Coil Q = %.1f  |  f₀ = %.2f kHz  |  BW = %.2f kHz" % (
        result.q, result.f0 / 1e3, result.bandwidth / 1e3)
    if result.inductance_h is not None:
        title += "  |  L ≈ %.2f µH" % (result.inductance_h * 1e6)
    ax.set_title(title)
    ax.set_xlabel("Frequency (kHz)")
    ax.set_ylabel("Amplitude (Vrms)")
    ax.set_xscale("log")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="best")

    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=120)
    return fig
