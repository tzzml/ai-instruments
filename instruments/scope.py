"""
Siglent SDS824X HD 示波器控制 (基于 instruments._backend)。

固件 3.8.x 用 SDS Programming Guide E11F+ 版标准 SCPI 树 (非老式 LeCroy)。
波形换算从 PREAMBLE 读取权威常数: code_per_div 和 adc_bits。
公式: V = int8(code) * (vdiv / code_per_div_8bit) - offset (手册第419页)。
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

from . import _backend as bk

KEY = "scope"

# 兼容旧调用方的常量名；实际换算一律以 PREAMBLE 为准。
CODE_CENTER = 128.0
CODE_PER_DIV = 30.0


def identity() -> str:
    return bk.idn(KEY)


def reset() -> None:
    bk.write(KEY, "*RST")


def autoset() -> None:
    bk.write(KEY, ":AUToset")


def run() -> None:
    bk.write(KEY, ":TRIGger:RUN")


def stop() -> None:
    bk.write(KEY, ":TRIGger:STOP")


def _fmt(value: float) -> str:
    return "%.2E" % value


def _num(resp: str) -> float:
    m = re.search(r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?", resp)
    if not m:
        raise bk.InstrumentError("无法解析数值: %r" % resp)
    return float(m.group(0))


# ---------- 通道 ----------
def channel_on(channel: int, on: bool = True) -> None:
    bk.write(KEY, ":CHANnel%d:SWITch %s" % (channel, "ON" if on else "OFF"))


def set_scale(channel: int, volts_per_div: float) -> None:
    """垂直灵敏度 (V/div)，仪器侧显示值 (含探头系数)。"""
    bk.write(KEY, ":CHANnel%d:SCALe %s" % (channel, _fmt(volts_per_div)))


def set_offset(channel: int, volts: float) -> None:
    bk.write(KEY, ":CHANnel%d:OFFSet %s" % (channel, _fmt(volts)))


def set_coupling(channel: int, coupling: str) -> None:
    coupling = coupling.upper()
    if coupling not in ("AC", "DC", "GND"):
        raise bk.InstrumentError("耦合应为 AC/DC/GND")
    bk.write(KEY, ":CHANnel%d:COUPling %s" % (channel, coupling))


def set_probe(channel: int, ratio: float) -> None:
    """探头系数 (如 10 表示 10x)。"""
    bk.write(KEY, ":CHANnel%d:PROBe VALue,%s" % (channel, _fmt(ratio)))


def get_scale(channel: int) -> float:
    return _num(bk.query(KEY, ":CHANnel%d:SCALe?" % channel))


def get_offset(channel: int) -> float:
    return _num(bk.query(KEY, ":CHANnel%d:OFFSet?" % channel))


def get_probe(channel: int) -> float:
    return _num(bk.query(KEY, ":CHANnel%d:PROBe?" % channel))


def get_channel_on(channel: int) -> bool:
    resp = bk.query(KEY, ":CHANnel%d:SWITch?" % channel).strip().upper()
    return resp in ("1", "ON", "TRUE")


def get_coupling(channel: int) -> str:
    return bk.query(KEY, ":CHANnel%d:COUPling?" % channel).strip()


def get_timebase() -> float:
    return _num(bk.query(KEY, ":TIMebase:SCALe?"))


def get_trigger_status() -> str:
    return bk.query(KEY, ":TRIGger:STATus?").strip()


def panel_status() -> dict:
    """Read the actual SDS channel state for the browser control panel."""
    channels = []
    for ch in range(1, 5):
        item = {"channel": ch}
        for key, fn in (
            ("on", lambda c=ch: get_channel_on(c)),
            ("scale", lambda c=ch: get_scale(c)),
            ("offset", lambda c=ch: get_offset(c)),
            ("coupling", lambda c=ch: get_coupling(c)),
            ("probe", lambda c=ch: get_probe(c)),
        ):
            try:
                item[key] = fn()
            except Exception as e:
                item[key + "_error"] = str(e)[:80]
        channels.append(item)

    out = {"online": True, "id": identity(), "channels": channels}
    for key, fn in (
        ("timebase", get_timebase),
        ("acquire_mode", get_acquire_mode),
        ("trigger_status", get_trigger_status),
    ):
        try:
            out[key] = fn()
        except Exception as e:
            out[key + "_error"] = str(e)[:80]
    return out


def get_srate() -> float:
    return _num(bk.query(KEY, ":ACQuire:SRATe?"))


def set_srate(rate: float) -> None:
    """固定采样率模式下的采样率 (Sa/s)。

    SDS800X HD 最高 2 GSa/s；设大于上限会自动钳位。
    低采样率 + 大 MDEPth 可覆盖秒~小时级时窗 (1 kSa/s × 50 Mpts ≈ 14 h)。
    """
    bk.write(KEY, ":ACQuire:SRATe %s" % _fmt(rate))


# ---------- 时基/触发 ----------
def set_timebase(seconds_per_div: float) -> None:
    bk.write(KEY, ":TIMebase:SCALe %s" % _fmt(seconds_per_div))


def set_trigger_edge(channel: int, level: float, slope: str = "POS") -> None:
    bk.write(KEY, ":TRIGger:MODE EDGE")
    bk.write(KEY, ":TRIGger:EDGE:SOURce C%d" % channel)
    sl_map = {"POS": "RISing", "NEG": "FALLing", "RISING": "RISing", "FALLING": "FALLing"}
    bk.write(KEY, ":TRIGger:EDGE:SLOPe %s" % sl_map.get(slope.upper(), slope.upper()))
    bk.write(KEY, ":TRIGger:EDGE:LEVel %s" % _fmt(level))


def trigger_mode(mode: str) -> None:
    bk.write(KEY, ":TRIGger:MODE %s" % mode.upper())


def force_trigger() -> None:
    bk.write(KEY, ":TRIGger:FORCE")


def acquire_single(channel: int, level: float, slope: str = "POS",
                   timeout: float = 5.0) -> bool:
    """SINGLE 触发单次捕获, 阻塞到采集完成。

    用于瞬态捕获 (LC 阻尼振荡起振、开关瞬态、单次脉冲、放电波形)。
    设置边沿触发 + SINGLE 模式并 arm, 轮询 :TRIGger:STATus? 直到 Stop
    (已捕获一帧) 或超时。

    返回 True=已捕获可读, False=超时。捕获后示波器停在 STOP,
    用 get_waveform/get_waveforms 读出; 完成后调 run() 恢复实时采集。
    """
    import time as _t
    set_trigger_edge(channel, level, slope)
    trigger_mode("SINGle")
    inst = bk.session(KEY, 5000)
    inst.write(":TRIGger:RUN")  # arm: SINGLE 下等待一次满足条件的触发
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        st = inst.query(":TRIGger:STATus?").strip()
        if "Stop" in st:
            return True
        _t.sleep(0.02)
    return False


# ---------- 采集配置 (ACQuire) ----------
def set_acquire_mode(mode: str) -> None:
    """采集模式。

    - YT  : 幅度-时间, 常规模式 (默认)。
    - XY  : 通道 X 对通道 Y, 利萨如/伏安曲线显示。
    - ROLL: 滚动模式, 波形从右侧写入, 适合每秒几次的慢速事件
            (秒级时基的"带状图记录")。ROLL 会限制存储深度。
    """
    m = mode.upper()
    if m not in ("YT", "XY", "ROLL"):
        raise bk.InstrumentError("采集模式应为 YT/XY/ROLL, 收到 %r" % mode)
    bk.write(KEY, ":ACQuire:MODE %s" % m)


def get_acquire_mode() -> str:
    return bk.query(KEY, ":ACQuire:MODE?")


def set_mdepth(depth) -> None:
    """存储深度 (记录长度)。

    SDS800X HD: 单通道 {10k|100k|1M|10M|100M},
    双通道 {...|50M}, 四通道 {...|25M}。
    depth 传字符串 ('10M') 或整数均可。ROLL/平均/ERES 会限制可用深度。
    """
    bk.write(KEY, ":ACQuire:MDEPth %s" % depth)


def get_mdepth() -> float:
    return _num(bk.query(KEY, ":ACQuire:MDEPth?"))


# ---------- 自动测量 ----------
# 注意: 实测 SDS800X HD 固件下，电压类测量项 (VPP/VMAX/VRMS...) 的
# :MEASure:SIMPle:VALue? 查询会超时 (固件怪癖)；FREQ/PERIOD 正常。
# 需要幅度时优先从波形自算 (waveform_stats)，比测量更可靠。
def measure(channel: int, param: str) -> float:
    """:MEASure:SIMPle 测量。可靠项: FREQ, PERIOD。电压类可能超时。"""
    p = param.upper()
    bk.write(KEY, ":MEASure:SIMPle:ITEM %s,C%d" % (p, channel))
    resp = bk.query(KEY, ":MEASure:SIMPle:VALue? %s,C%d" % (p, channel))
    return _num(resp)


def measure_freq(channel: int, vlevel: float = 0.0) -> float:
    """测频率。

    测频依赖稳定触发。如果刚改过信号频率/幅度，示波器可能未稳定触发，
    导致读出错误频率或 '****'。本函数先确保 RUN + 等触发 Trig'd 再读。
    vlevel: 触发电平 (V)，默认 0；信号有偏置时需传入。
    """
    inst = bk.session(KEY, 5000)
    _ensure_live(inst)
    # 等一次稳定触发
    import time as _t
    deadline = _t.time() + 2.0
    while _t.time() < deadline:
        st = inst.query(":TRIGger:STATus?").strip()
        if "Trig" in st:
            break
        _t.sleep(0.05)
    resp = bk.query(KEY, ":MEASure:SIMPle:VALue? FREQ,C%d" % channel)
    val = _num(resp)
    return val


# ---------- 波形抓取 ----------
def _read_preamble() -> dict:
    """读 :WAVeform:PREamble? 并按官方 WAVEDESC 偏移表解析换算常量。

    SDS800X HD 的 PREAMBLE 是固定 346 字节二进制块 (LeCroy WAVEDESC 风格)，
    关键字段偏移见 SDS 编程指南表5-2:
      116-119 long:  波形点数
      156-159 float: 原始垂直档位 (不含探头)
      160-163 float: 原始垂直偏移 (不含探头)
      164-167 float: 码字值/div (16bit 满量程)
      172-173 short: ADC 位数
      176-179 float: 采样间隔 (s)
    """
    inst = bk.session(KEY, 15000)
    old_term = inst.read_termination
    inst.read_termination = None
    try:
        inst.write(":WAVeform:PREamble?")
        # 头部: '#' + 1位(长度位数,通常9) + 长度数字 + 数据
        assert inst.read_bytes(1) == b"#"
        nlen = int(inst.read_bytes(1))
        datalen = int(inst.read_bytes(nlen))
        pre = inst.read_bytes(datalen)
    finally:
        inst.read_termination = old_term

    import struct as _struct
    return {
        "n_points":     _struct.unpack_from("<l", pre, 116)[0],
        "vdiv_raw":     _struct.unpack_from("<f", pre, 156)[0],
        "voff_raw":     _struct.unpack_from("<f", pre, 160)[0],
        "code_per_div": _struct.unpack_from("<f", pre, 164)[0],
        "adc_bits":     _struct.unpack_from("<h", pre, 172)[0],
        "sample_int":   _struct.unpack_from("<f", pre, 176)[0],
    }



def _ensure_live(inst, max_wait: float = 2.0) -> None:
    """确保示波器在实时采集: 发 RUN, 等待触发就绪。

    SDS 读波形后会停在 STOP；调用此函数恢复采集，并等到至少一次新触发，
    保证后续 :WAVeform:DATA? 读到的是新鲜波形而非冻结帧。
    """
    import time as _t
    inst.write(":TRIGger:RUN")
    deadline = _t.time() + max_wait
    while _t.time() < deadline:
        try:
            st = inst.query(":TRIGger:STATus?").strip()
        except Exception:
            st = ""
        if "Trig" in st or "trig" in st:
            break
        _t.sleep(0.05)


def get_waveform(channel: int) -> tuple[np.ndarray, np.ndarray]:
    """抓取一个通道波形，返回 (时间轴 s, 电压 V)。

    用官方 PREAMBLE 的权威换算常量 (表5-2)，公式来自手册第419页:
        V = int8(码值) × (垂直档位 / 码字值_div_8bit) - 垂直偏移

    - 码值为有符号 8bit (int8)，SDS800X HD 默认 BYTE 传输
    - 垂直档位/偏移 = PREAMBLE 原始值 × 探头系数
    - 码字值_div_8bit = PREAMBLE code_per_div / 2^(adc_bits-8)
      (PREAMBLE 返回 16bit 满量程码字，BYTE 传输时压缩到 8bit)
    实测 (2Vpp 真值): Vrms 误差 <1%，Vpp 误差 ~3%。
    """
    pre = _read_preamble()
    probe = get_probe(channel)
    vdiv = pre["vdiv_raw"] * probe
    voff = pre["voff_raw"] * probe
    # 8bit 视角的码字值/div (PREAMBLE 是 adc_bits 满量程)
    code_per_div_8bit = pre["code_per_div"] / (2 ** (pre["adc_bits"] - 8))
    sample_int = pre["sample_int"]

    # 关键: :WAVeform:DATA? 读取会冻结采集 (示波器进入 STOP)。
    # 必须每次抓取前重新 RUN 并等待新触发，否则读到的是冻结的旧帧。
    inst = bk.session(KEY, 15000)
    _ensure_live(inst)
    inst.write(":WAVeform:SOURce C%d" % channel)
    inst.write(":WAVeform:DATA?")
    raw = _read_definite_block(inst)

    codes = np.frombuffer(raw, dtype=np.int8).astype(np.float64)
    volts = codes * (vdiv / code_per_div_8bit) - voff

    n = len(volts)
    times = np.arange(n) * sample_int - n * sample_int / 2.0
    return times, volts


def get_waveforms(channels) -> dict:
    """多通道同步读取, 返回 {channel: (time_s, voltage_v)}。

    保证所有通道来自同一帧 (同一触发), 时间轴天然对齐——
    适合伏安曲线 (V_CH1 vs I_CH2)、相位差、传输特性、电磁场双路探测。

    同步原理: 示波器一次触发时所有开启通道并行采样到各自内存。
    本函数依次切换 :WAVeform:SOURce 读各通道, 全程不重新触发
    (不调 _ensure_live), 各通道读到的都是同一冻结帧。

    典型调用:
      - 瞬态: acquire_single(...) 后直接调用 (已在 STOP)。
      - 稳态: run() + 等触发稳定后调用 (读到最新完整帧)。
    读完后示波器停在 STOP, 需 run() 恢复。

    channels: 通道号可迭代, 如 [1, 2]。
    """
    inst = bk.session(KEY, 15000)
    result = {}
    for ch in channels:
        inst.write(":WAVeform:SOURce C%d" % ch)
        pre = _read_preamble()
        probe = get_probe(ch)
        vdiv = pre["vdiv_raw"] * probe
        voff = pre["voff_raw"] * probe
        code_per_div_8bit = pre["code_per_div"] / (2 ** (pre["adc_bits"] - 8))
        sample_int = pre["sample_int"]

        inst.write(":WAVeform:DATA?")
        raw = _read_definite_block(inst)
        codes = np.frombuffer(raw, dtype=np.int8).astype(np.float64)
        volts = codes * (vdiv / code_per_div_8bit) - voff

        n = len(volts)
        times = np.arange(n) * sample_int - n * sample_int / 2.0
        result[ch] = (times, volts)
    return result


def _read_definite_block(inst) -> bytes:
    """读取 IEEE488.2 定长度二进制块 (#<n><len><data>)。"""
    buf = b""
    while True:
        b = inst.read_bytes(1)
        buf += b
        if b == b"#":
            break
        if len(buf) > 64:
            idx = buf.find(b"#")
            if idx < 0:
                raise bk.InstrumentError("无 '#' 块头: %r" % buf[:20])
            buf = buf[idx:]
            break
    nlen = int(inst.read_bytes(1))
    datalen = int(inst.read_bytes(nlen))
    return inst.read_bytes(datalen)


def waveform_stats(channel: int) -> dict:
    """抓波形返回统计量。Vpp/Vrms 从波形自算，不依赖测量子系统。"""
    t, v = get_waveform(channel)
    return {
        "time": t, "voltage": v,
        "vpp": float(v.max() - v.min()),
        "vrms": float(np.sqrt(np.mean(v ** 2))),
        "vmean": float(v.mean()),
        "vmax": float(v.max()),
        "vmin": float(v.min()),
        "n_points": int(len(v)),
    }


def is_clipped(channel: int, threshold: float = 0.02) -> bool:
    """检测波形是否削顶 (code 卡在 0 或 255 的比例 > threshold)。"""
    inst = bk.session(KEY, 15000)
    _ensure_live(inst)
    inst.write(":WAVeform:SOURce C%d" % channel)
    inst.write(":WAVeform:DATA?")
    raw = _read_definite_block(inst)
    codes = np.frombuffer(raw, dtype=np.int8)
    sat = ((codes <= -127) | (codes >= 127)).mean()
    return sat > threshold


# ---------- 截图 ----------
def screenshot(fmt: str = "PNG") -> bytes:
    """:PRINt? 返回屏幕图像字节 (PNG/BMP)。

    PRINt? 直接返回裸图像字节 (无 IEEE488.2 # 块头)。SDS 把图像分多个 USBTMC
    数据包传输，pyvisa-py 的 read_raw() 会在单包缓冲 (约20KB) 处提前返回，
    必须循环读取直到拿到图像结束标记 (PNG 的 IEND, BMP/JPG 用固定 magic)。
    """
    inst = bk.session(KEY, 30000)
    old_term = inst.read_termination
    inst.read_termination = None
    try:
        inst.write(":PRINt? %s" % fmt.upper())
        chunks = []
        # 结束标记: PNG 以 IEND chunk 结尾; 用 magic 兜底 (读不到新数据时停)
        end_marker = b"IEND\xaeB`\x82" if fmt.upper() == "PNG" else None
        while True:
            try:
                chunk = inst.read_raw()
            except Exception:
                break
            if not chunk:
                break
            chunks.append(chunk)
            data_so_far = b"".join(chunks)
            if end_marker and end_marker in data_so_far:
                break
            if len(data_so_far) > 10 * 1024 * 1024:  # 10MB 上限保护
                break
        return b"".join(chunks)
    finally:
        inst.read_termination = old_term
