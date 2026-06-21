---
name: instruments
description: 操作 UTG962 信号发生器 + SDS824X HD 示波器 + UT61E 万用表。当用户要控制信号发生器、示波器、万用表、测量波形/频率/幅度/低速读数、抓取波形、截图、或测量线圈/谐振回路的 Q 值时使用此 skill。
---

# 仪表控制 Skill (UTG962 + SDS824X HD + UT61E)

## 环境准备

```bash
cd /Users/zhuminglei/Projects/ai-instruments
source .venv/bin/activate
```

## 核心库

```python
from instruments import awg, scope, dmm      # 仪器驱动
from experiments.q_measure import measure_q   # Q 值实验
```

CLI 入口：
- `python -m instruments.cli awg|scope|dmm ...`（仪器控制）
- `python -m experiments.cli q-measure|q-sweep ...`（实验）

---

## 仪器关键事实

1. **两台都是 USBTMC**，pyvisa-py + libusb，无需 NI-VISA
2. **用精确资源串手动打开**（`list_resources()` 发现不到）：
   - UTG962: `USB0::0x6656::0x0834::1021472514::INSTR`
   - SDS824X: `USB0::0xF4EC::0x1017::SDS08A0C801504::INSTR`
3. **UTG962 只写不查**：固件 USBTMC 读取不稳定，写命令全部可靠
4. **SDS824X 用新版 SCPI 树**（`:CHANnel1:SCALe`），非老式 LeCroy
5. **示波器电压测量 SCPI 超时**，幅度一律从波形自算 (`waveform_stats`)
6. **波形换算**：`V = int8(code) × (vdiv / code_per_div_8bit) - offset`，PREAMBLE 读取 `code_per_div/adc_bits`
7. **读波形冻结采集**：`:WAVeform:DATA?` 使示波器 STOP，`get_waveform()` 已内置恢复
8. **UT61E 走串口而不是 USBTMC**：`19200 7O1`，`DTR=1`、`RTS=0`，默认从 `UT61E_PORT` 环境变量找端口

---

## 信号发生器 (AWG)

### 内嵌脚本

输出正弦波并打开输出：
```bash
python -c "
from instruments import awg
awg.configure(1, wave='sine', frequency=1e6, amplitude=2, load=50, output=True)
"
```

扫频输出（载波正弦，线性扫频）：
```bash
python -c "
from instruments import awg
awg.configure_sweep(1, f_start=535e3, f_stop=1605e3, time_s=5, amp=1, load=50)
# 5 秒内从 535kHz 线性扫到 1605kHz
"
```

关闭输出：
```bash
python -c "from instruments import awg; awg.output_off(1)"
```

### CLI

```bash
python -m instruments.cli awg sine --freq 1k --amp 2 --out
python -m instruments.cli awg square --freq 5k --amp 1 --duty 30 --out
python -m instruments.cli awg off
```

---

## 示波器

### 内嵌脚本

截图保存 PNG：
```bash
python -c "
from instruments import scope
with open('screen.png', 'wb') as f:
    f.write(scope.screenshot('PNG'))
"
```

抓波形统计（Vpp/Vrms/Vmax/Vmin）：
```bash
python -c "
from instruments import scope
st = scope.waveform_stats(1)
print(f'C1: Vpp={st[\"vpp\"]:.3f}V Vrms={st[\"vrms\"]:.3f}V Freq={scope.measure_freq(1):.1f}Hz')
"
```

测频率：
```bash
python -c "
from instruments import scope
print(f'C1 FREQ = {scope.measure_freq(1):.1f} Hz')
"
```

导出波形 CSV：
```bash
python -c "
from instruments import scope
t, v = scope.get_waveform(1)
import csv; csv.writer(open('wave.csv','w')).writerows(zip(t,v))
print(f'保存 {len(v)} 点')
"
```

### CLI

```bash
python -m instruments.cli scope setup -c 1 --vdiv 500m --tdiv 1m --coupling DC
python -m instruments.cli scope stats -c 1
python -m instruments.cli scope freq -c 1
python -m instruments.cli scope screenshot --path output/screen.png
python -m instruments.cli scope waveform --path output/wave.csv -c 1
```

---

## UT61E 万用表

适合做低速/DC/基础元件辅助读数：电阻、直流电压、二极管压降、电容等。高速波形、相位、Q、TDR 仍由示波器完成。

```bash
export UT61E_PORT=/dev/tty.usbserial-xxxx
python -m instruments.cli dmm status
python -m instruments.cli dmm read
python -m instruments.cli dmm monitor --interval 1
```

Python:

```bash
python -c "
from instruments import dmm
r = dmm.read_once()
print(r.display)
"
```

---

## Q 值测量

### 接线

变压器耦合（推荐，测真实 Q）：
```
AWG CH1(50Ω) → 1匝耦合绕组 → [磁耦合] → 主线圈
示波器 CH1 → 主线圈 ∥ 可变电容C → 地
```

直接激励（高阻）：
```
AWG CH1 → 串联电阻(~1kΩ) → 线圈L ∥ 电容C → 地
                               └→ 示波器 CH1
```

### 内嵌脚本

中波全段自适应扫频：
```bash
python -c "
from experiments.q_measure import measure_q, plot_result
result = measure_q(f_start=535e3, f_stop=1605e3, fine_points=50,
                   amplitude_vpp=1, scope_vdiv=0.5, load_ohm=50, settle_s=0.2)
print(f'f0={result.f0/1e3:.1f} kHz, Q={result.q:.1f}, BW={result.bandwidth/1e3:.2f} kHz')
plot_result(result, save_path='output/q.png')
"
```

已知谐振点窄范围精确扫频：
```bash
python -c "
from experiments.q_measure import measure_q, plot_result
result = measure_q(f_start=840e3, f_stop=880e3, coarse_step_hz=2000,
                   fine_points=60, amplitude_vpp=1, scope_vdiv=0.5, load_ohm=50)
print(f'f0={result.f0/1e3:.1f} kHz, Q={result.q:.1f}')
plot_result(result, save_path='output/q.png')
"
```

指定电容值反推电感：
```bash
python -c "
from experiments.q_measure import measure_q
result = measure_q(f_start=535e3, f_stop=1605e3, fine_points=50,
                   amplitude_vpp=1, capacitance_f=150e-12, load_ohm=50)
if result.inductance_h:
    print(f'L = {result.inductance_h*1e6:.1f} µH')
"
```

### CLI

```bash
# 中波全段自适应扫频
python -m experiments.cli q-measure --f-start 535k --f-stop 1605k \
    --amp 1 --vdiv 500m --load 50 --fine 50 -o output/q.png

# 已知谐振点窄扫
python -m experiments.cli q-measure --f-start 840k --f-stop 880k \
    --coarse-step 2k --fine 60 --amp 1 --vdiv 500m --load 50

# AWG 内置扫频 (实验性)
python -m experiments.cli q-sweep --f-start 840k --f-stop 880k --sweep-time 1
```

---

## 故障排查

- **Access denied**：USB 被占用，重插 USB 线
- **VI_ERROR_TMO（AWG）**：AWG 不支持查询，正常现象
- **VI_ERROR_TMO（示波器电压测量）**：改用 `scope stats`（波形自算）
- **波形削顶 is_clipped**：信号超量程，增大 V/div
- **找不到仪器**：`ioreg -p IOUSB -l | grep -i "uni-trend\|siglent"` 检查

## 不要做的事

- ❌ 对 UTG962 发查询命令（包括 `*IDN?`；业务流程和 Web 状态页都只写不查）
- ❌ 用老式 LeCroy 命令（`C1:VDIV`、`C1:WF?`）控制 SDS
- ❌ 反复 open/close USB 会话（会 Access denied）
- ❌ 依赖示波器 VPP/VRMS SCPI 测量
- ❌ 连续 `:WAVeform:DATA?` 不恢复采集
