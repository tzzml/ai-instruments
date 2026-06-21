"""
UNI-T UTG962 信号发生器控制 (基于 instruments._backend)。

SCPI 命令来自 UTG900E 编程手册，方言为标准 SCPI 树 (命令以 ':' 开头)。
命令支持缩写 (只写大写部分)，本模块统一用缩写形式以减少串口流量。

通道: UTG962 有 CH1/CH2，本模块用 1/2 整数表示。
"""
from __future__ import annotations

from typing import Optional, Union

from . import _backend as bk

KEY = "awg"

# 支持的基本波形
WAVES = {
    "sine": "SIN",
    "square": "SQU",
    "pulse": "PULS",
    "ramp": "RAMP",
    "noise": "NOIS",
    "dc": "DC",
    "arb": "ARB",
}

# 幅度单位
AMP_UNITS = {"vpp": "VPP", "vrms": "VRMS", "dbm": "DBM"}


def _ch(channel: int) -> str:
    """把通道整数变成 SCPI 用的 'CHANnel<n>'。"""
    if channel not in (1, 2):
        raise bk.InstrumentError("UTG962 只有 CH1/CH2，收到 channel=%r" % channel)
    return ":CHANnel%d" % channel


def _fmt_num(value: Union[int, float]) -> str:
    """把数字格式化成仪器接受的字符串。整数不带小数点，浮点保留必要精度。"""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return repr(float(value))


# ---------- 查询 ----------
def identity() -> str:
    return bk.idn(KEY)


def status(channel: int = 1) -> dict:
    """读取一个通道的当前设置概要 (容错)。

    UTG962 的 USBTMC 读取不稳定: 重启后初期可用，但连续查询累积后会卡死。
    本函数逐项查询，超时的项标记为 None 不影响整体。建议:
      - 业务流程只用写命令 (可靠)
      - status() 仅用于调试/一次性确认
      - 若整体卡死，重启仪器即可恢复
    """
    import time as _t
    c = _ch(channel)
    items = [
        ("wave", "%s:BASE:WAVe?" % c),
        ("frequency_hz", "%s:BASE:FREQuency?" % c),
        ("amplitude", "%s:BASE:AMPLitude?" % c),
        ("offset_v", "%s:BASE:OFFSet?" % c),
        ("phase_deg", "%s:BASE:PHAse?" % c),
        ("duty_pct", "%s:BASE:DUTY?" % c),
        ("amp_unit", "%s:AMPLitude:UNIT?" % c),
        ("output_on", "%s:OUTPut?" % c),
        ("mode", "%s:MODe?" % c),
        ("load_ohm", "%s:LOAD?" % c),
    ]
    out = {}
    for key, cmd in items:
        out[key] = try_query(cmd)
        _t.sleep(0.05)  # 查询间留短延迟，降低卡死概率
    return out

def read_setting(channel: int, param: str) -> str:
    """读取单个设置项 (比 status() 更轻量，卡死概率低)。

    param 用 SCPI 后缀，如 'BASE:FREQuency?'、'BASE:AMPLitude?'、
    'OUTPut?'、'LOAD?'、'MODe?'。返回字符串或 None。
    """
    return try_query("%s:%s" % (_ch(channel), param))


# ---------- 输出控制 ----------
def output(channel: int, on: bool) -> None:
    """打开/关闭通道输出。"""
    bk.write(KEY, "%s:OUTPut %s" % (_ch(channel), "ON" if on else "OFF"))


def output_on(channel: int = 1) -> None:
    output(channel, True)


def output_off(channel: int = 1) -> None:
    output(channel, False)


# ---------- 波形参数 ----------
def set_wave(channel: int, wave: str) -> None:
    """设置基本波形类型。wave ∈ sine/square/pulse/ramp/noise/dc/arb。"""
    w = WAVES.get(wave.lower())
    if w is None:
        raise bk.InstrumentError("未知波形 %r，可选: %s" % (wave, ", ".join(WAVES)))
    bk.write(KEY, "%s:BASE:WAVe %s" % (_ch(channel), w))


def set_frequency(channel: int, hz: float) -> None:
    """设置频率 (Hz)。范围约 1e-6 ~ 波形允许上限。"""
    if hz <= 0:
        raise bk.InstrumentError("频率必须 > 0，收到 %r" % hz)
    bk.write(KEY, "%s:BASE:FREQuency %s" % (_ch(channel), _fmt_num(hz)))


def set_period(channel: int, seconds: float) -> None:
    """设置周期 (秒)。"""
    if seconds <= 0:
        raise bk.InstrumentError("周期必须 > 0，收到 %r" % seconds)
    bk.write(KEY, "%s:BASE:PERiod %s" % (_ch(channel), _fmt_num(seconds)))


def set_amplitude(channel: int, value: float, unit: str = "vpp") -> None:
    """设置幅度。unit ∈ vpp/vrms/dbm。"""
    u = AMP_UNITS.get(unit.lower())
    if u is None:
        raise bk.InstrumentError("未知幅度单位 %r" % unit)
    bk.write(KEY, "%s:AMPLitude:UNIT %s" % (_ch(channel), u))
    bk.write(KEY, "%s:BASE:AMPLitude %s" % (_ch(channel), _fmt_num(value)))


def set_offset(channel: int, volts: float) -> None:
    """设置直流偏置 (V)。"""
    bk.write(KEY, "%s:BASE:OFFSet %s" % (_ch(channel), _fmt_num(volts)))


def set_phase(channel: int, degrees: float) -> None:
    """设置相位 (度，-360~360)。"""
    bk.write(KEY, "%s:BASE:PHAse %s" % (_ch(channel), _fmt_num(degrees)))


def set_duty(channel: int, percent: float) -> None:
    """设置占空比 (%, 0~100)，对方波/脉冲有效。"""
    if not 0 <= percent <= 100:
        raise bk.InstrumentError("占空比应在 0~100，收到 %r" % percent)
    bk.write(KEY, "%s:BASE:DUTY %s" % (_ch(channel), _fmt_num(percent)))


def set_load(channel: int, ohms: float) -> None:
    """设置输出负载电阻 (Ω)，高阻用 10000。"""
    if not 1 <= ohms <= 10000:
        raise bk.InstrumentError("负载电阻应在 1~10000Ω，收到 %r" % ohms)
    bk.write(KEY, "%s:LOAD %s" % (_ch(channel), _fmt_num(ohms)))


# ---------- 高频便捷预设 ----------
def configure(
    channel: int = 1,
    *,
    wave: str = "sine",
    frequency: Optional[float] = None,
    amplitude: Optional[float] = None,
    amplitude_unit: str = "vpp",
    offset: Optional[float] = None,
    phase: Optional[float] = None,
    duty: Optional[float] = None,
    load: Optional[float] = 50,
    output: Optional[bool] = None,
) -> None:
    """一次性配置一个通道的常用参数 (None 的项跳过)。

    这是最高频的用法: 指定波形 + 频率 + 幅度，其余可选。
    """
    set_wave(channel, wave)
    if load is not None:
        set_load(channel, load)
    if frequency is not None:
        set_frequency(channel, frequency)
    if amplitude is not None:
        set_amplitude(channel, amplitude, amplitude_unit)
    if offset is not None:
        set_offset(channel, offset)
    if phase is not None:
        set_phase(channel, phase)
    if duty is not None:
        set_duty(channel, duty)
    if output is not None:
        (output_on if output else output_off)(channel)


def reset() -> None:
    """恢复出厂设置。"""
    bk.write(KEY, "*RST")


# ---------- 调制 / 载波控制 ----------
# UTG962 支持调制模式: CONTINUE(纯载波) / AM / FM / PM / FSK / SWEEP / PWM
# "载波"即 BASE:WAVe + BASE:FREQuency + BASE:AMPLitude 设的基本波，
# 调制是在载波上叠加调制信号。

MOD_MODES = {
    "continue": "CONTINUE",   # 纯载波输出 (无调制)
    "am": "AM",               # 幅度调制
    "fm": "FM",               # 频率调制
    "pm": "PM",               # 相位调制
    "fsk": "FSK",             # 频移键控
    "sweep": "Line",          # 扫频 (Line=线性, Log=对数)
    "sweep_log": "Log",
    "pwm": "CONTINUE",        # PWM 需脉冲载波
}

MOD_WAVES = {"sine": "SIN", "square": "SQU", "ramp_up": "UPR",
             "ramp_down": "DNR", "arb": "ARB", "noise": "NOIS"}


def set_mode(channel: int, mode: str) -> None:
    """设置通道工作模式 (载波类型)。

    mode ∈ continue/am/fm/pm/fsk/sweep/sweep_log。
    - continue: 纯载波 (最常用，正常输出设定的正弦/方波等)
    - am/fm/pm: 调制模式，需配合 set_mod_* 参数
    - sweep: 频率扫描
    """
    m = MOD_MODES.get(mode.lower())
    if m is None:
        raise bk.InstrumentError("未知模式 %r，可选: %s" % (mode, ", ".join(MOD_MODES)))
    bk.write(KEY, "%s:MODe %s" % (_ch(channel), m))


def set_mod_wave(channel: int, wave: str) -> None:
    """设置调制信号的波形。wave ∈ sine/square/ramp_up/ramp_down/arb/noise。"""
    w = MOD_WAVES.get(wave.lower())
    if w is None:
        raise bk.InstrumentError("未知调制波形 %r" % wave)
    bk.write(KEY, "%s:MODulate:WAVe %s" % (_ch(channel), w))


def set_mod_frequency(channel: int, hz: float) -> None:
    """设置调制信号频率 (Hz)。"""
    bk.write(KEY, "%s:MODulate:FREQuency %s" % (_ch(channel), _fmt_num(hz)))


def set_mod_depth(channel: int, percent: float) -> None:
    """设置调制深度 (AM) / 频偏相关 (%)。范围 0~100 (AM 可到 120)。"""
    bk.write(KEY, "%s:MODulate:DEPTh %s" % (_ch(channel), _fmt_num(percent)))


def set_fm_deviation(channel: int, hz: float) -> None:
    """设置 FM 频率偏移 (Hz)。"""
    bk.write(KEY, "%s:FM:FREQuency:DEV %s" % (_ch(channel), _fmt_num(hz)))


def set_pm_deviation(channel: int, degrees: float) -> None:
    """设置 PM 相位偏移 (度，0~360)。"""
    bk.write(KEY, "%s:PM:PHASe:DEV %s" % (_ch(channel), _fmt_num(degrees)))


def set_fsk_hop(channel: int, hz: float) -> None:
    """设置 FSK 跳变频率 (Hz)。"""
    bk.write(KEY, "%s:FSK:HOPPing:FREQuency %s" % (_ch(channel), _fmt_num(hz)))


def set_sweep_range(channel: int, f_start: float, f_stop: float, time_s: float) -> None:
    """设置扫频起止频率和扫描时间。需先 set_mode(channel, 'sweep')。"""
    bk.write(KEY, "%s:SWEep:FREQuency:STARt %s" % (_ch(channel), _fmt_num(f_start)))
    bk.write(KEY, "%s:SWEep:FREQuency:STOP %s" % (_ch(channel), _fmt_num(f_stop)))
    bk.write(KEY, "%s:SWEEP:TIMe %s" % (_ch(channel), _fmt_num(time_s)))


# ---------- 双通道 ----------
def set_phase_sync(channel: int, on: bool) -> None:
    """通道间相位同步。on=True 使该通道与另一通道初始相位同步。"""
    bk.write(KEY, ":SYSTem:PHASe:MODe %s" % ("SYNChronization" if on else "INDependent"))


def set_both_output(on: bool) -> None:
    """同时控制两个通道的输出。"""
    output(1, on)
    output(2, on)


# ---------- 查询 (注意: UTG962 查询不稳, 仅首条可靠) ----------
def try_query(command: str) -> Optional[str]:
    """尝试查询 (UTG962 查询不稳定，可能超时)。成功返回值，失败返回 None。

    UTG962 的 USBTMC 读有缺陷：会话内连续查询会超时。
    如需可靠读取，每次调用前 reset_session。
    """
    try:
        return bk.query(KEY, command, timeout_ms=2500)
    except Exception:
        return None


# ---------- 其他波形参数 (手册补充) ----------
def set_invert(channel: int, on: bool) -> None:
    """设置通道信号反向输出。"""
    bk.write(KEY, "%s:INVersion %s" % (_ch(channel), "ON" if on else "OFF"))


def set_output_sync(channel: int, on: bool) -> None:
    """设置通道同步输出 (硬件 SYNC 接口)。

    注意: 设备只有一个 SYNC 接口，同时只能开一个通道的同步输出。
    """
    bk.write(KEY, "%s:OUTPut:SYNC %s" % (_ch(channel), "ON" if on else "OFF"))


def set_limit(channel: int, on: bool, lower_v: float = None, upper_v: float = None) -> None:
    """设置通道幅度限幅。

    on=True 开启限幅；可同时设上下限电压 (V)。
    限幅开启后，输出电压被钳位在 [lower, upper] 范围内。
    """
    bk.write(KEY, "%s:LIMit:ENABle %s" % (_ch(channel), "ON" if on else "OFF"))
    if on:
        if lower_v is not None:
            bk.write(KEY, "%s:LIMit:LOWer %s" % (_ch(channel), _fmt_num(lower_v)))
        if upper_v is not None:
            bk.write(KEY, "%s:LIMit:UPPer %s" % (_ch(channel), _fmt_num(upper_v)))


def set_ramp_symmetry(channel: int, percent: float) -> None:
    """设置斜波对称度 (%, 0~100)。对 RAMP 波形有效。"""
    bk.write(KEY, "%s:RAMP:SYMMetry %s" % (_ch(channel), _fmt_num(percent)))


def set_pulse_edges(channel: int, rise_s: float, fall_s: float) -> None:
    """设置脉冲波上升/下降沿脉宽 (秒)。对 PULSE 波形有效。"""
    bk.write(KEY, "%s:PULSe:RISe %s" % (_ch(channel), _fmt_num(rise_s)))
    bk.write(KEY, "%s:PULSe:FALL %s" % (_ch(channel), _fmt_num(fall_s)))


def set_mod_source(channel: int, source: str) -> None:
    """设置调制源。source ∈ internal/external。"""
    src = "INTernal" if source.lower().startswith("int") else "EXTernal"
    bk.write(KEY, "%s:MODulate:SOURce %s" % (_ch(channel), src))


# ---------- 便捷预设: 调制输出 ----------
def configure_am(channel: int, carrier_freq: float, carrier_amp: float,
                 mod_freq: float, mod_depth: float, *,
                 mod_wave: str = "sine", load: float = 10000) -> None:
    """快捷配置 AM (幅度调制) 输出。

    载波: 正弦, carrier_freq Hz, carrier_amp Vpp;
    调制: mod_freq Hz, 调制深度 mod_depth %。
    """
    set_mode(channel, "continue")
    set_wave(channel, "sine")
    set_load(channel, load)
    set_frequency(channel, carrier_freq)
    set_amplitude(channel, carrier_amp, "vpp")
    set_mode(channel, "am")
    set_mod_wave(channel, mod_wave)
    set_mod_frequency(channel, mod_freq)
    set_mod_depth(channel, mod_depth)
    output_on(channel)


def configure_fm(channel: int, carrier_freq: float, carrier_amp: float,
                 mod_freq: float, deviation_hz: float, *,
                 load: float = 10000) -> None:
    """快捷配置 FM (频率调制) 输出。deviation_hz 为频率偏移。"""
    set_mode(channel, "continue")
    set_wave(channel, "sine")
    set_load(channel, load)
    set_frequency(channel, carrier_freq)
    set_amplitude(channel, carrier_amp, "vpp")
    set_mode(channel, "fm")
    set_mod_frequency(channel, mod_freq)
    set_fm_deviation(channel, deviation_hz)
    output_on(channel)


def configure_sweep(channel: int, f_start: float, f_stop: float,
                    time_s: float, amp: float = 2.0, *,
                    log: bool = False, load: float = 10000) -> None:
    """快捷配置线性/对数扫频输出 (立即开启)。"""
    set_mode(channel, "continue")
    set_wave(channel, "sine")
    set_load(channel, load)
    set_amplitude(channel, amp, "vpp")
    set_sweep_range(channel, f_start, f_stop, time_s)
    set_mode(channel, "sweep_log" if log else "sweep")
    output_on(channel)


def arm_sweep(channel: int, f_start: float, f_stop: float,
              time_s: float, amp: float, load: float = 50) -> None:
    """配置扫频但不立即输出。调用 start_sweep() 后开始扫频。

    用于示波器先 SINGLE 就位后再启动扫频。
    """
    set_mode(channel, "continue")
    set_wave(channel, "sine")
    set_load(channel, load)
    set_amplitude(channel, amp, "vpp")
    set_sweep_range(channel, f_start, f_stop, time_s)
    set_mode(channel, "sweep")


def start_sweep(channel: int = 1) -> None:
    """开启输出，启动已配置的扫频。"""
    output_on(channel)


def screenshot(autoflip: bool = True) -> bytes:
    """读取 UTG962 屏幕图像 (:DISPlay?)，返回 PNG 字节。

    手册第23页: DISPlay? 返回 IEEE 488.2 二进制块，内容为 BMP 图像
    (480x272, 24bit, 约 391680 bytes)。

    UTG962 输出的 BMP 像素行是**左右镜像**的 (实测)，autoflip=True 时
    自动左右翻转为正常方向，并转成 PNG 返回。

    注意: 走读取通道，UTG962 读取不稳定。建议在仪器重启后、
    未做大量其他查询时调用。失败(超时)需重启仪器恢复。
    """
    inst = bk.session(KEY, 8000)
    old_term = inst.read_termination
    inst.read_termination = None
    try:
        inst.write(":DISPlay?")
        # 读 IEEE488.2 块头: '#' + 1位(长度位数N) + N位数字(数据长度)
        assert inst.read_bytes(1) == b"#", "DISPlay? 无 '#' 块头"
        nlen = int(inst.read_bytes(1))
        datalen = int(inst.read_bytes(nlen))
        # 按声明长度读完整数据 (分包传输, read_bytes 内部会循环)
        data = inst.read_bytes(datalen)
        # UTG962 分包使 read_bytes 可能提前返回, 循环补齐到 datalen
        import time as _t
        retries = 0
        while len(data) < datalen and retries < 100:
            try:
                chunk = inst.read_raw()
                if not chunk:
                    retries += 1
                    _t.sleep(0.02)
                    continue
                data += chunk
            except Exception:
                retries += 1
                _t.sleep(0.02)
        # data 里前几字节可能是文本头, BMP 从 'BM' 开始
        bm_idx = data.find(b"BM")
        if bm_idx < 0:
            return data
        import struct
        bmp_size = struct.unpack_from("<I", data, bm_idx + 2)[0]
        bmp = data[bm_idx:bm_idx + bmp_size]
        if not autoflip:
            return bmp
        # UTG962 BMP 左右镜像, 翻正 + 转 PNG
        import io
        from PIL import Image, ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True  # UTG962 偶尔少几行, 容错解码
        img = Image.open(io.BytesIO(bmp))
        img.load()
        img = img.transpose(Image.FLIP_LEFT_RIGHT)  # 左右翻转修正
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    finally:
        inst.read_termination = old_term
