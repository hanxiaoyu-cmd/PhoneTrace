# 指标与准确性

[返回首页](../README.md) · [快速上手](quick-start.md)

PhoneTrace 的原则是：**能说明来源的数据才记录，不能可靠读取的值保持为空。** 它提供基于 Android 系统接口的性能观测，不代替外部功耗仪或游戏引擎内部分析工具。

当前已验证程序启动、模拟采集、文件保存、统计和历史回看等软件流程，**尚未完成手机真机验证，也未与参考仪器做精度对照**。软件测试通过不等于传感器准确或所有机型可用。

## 指标、来源与单位

| 字段 | 来源 | 含义与单位 |
| --- | --- | --- |
| `fps` | `dumpsys SurfaceFlinger --latency` 的实际呈现时间戳 | 所选游戏图层的新观测帧间隔计算的 FPS |
| `frame_time_ms` | 同上 | 本次采样中新观测帧间隔的平均值，ms |
| `frame_p95_ms` | 同上 | 本次采样中新观测帧间隔的第 95 百分位，ms |
| `slow_frames` | 同上 | 本次采样中大于 50 ms 的观测间隔数量 |
| `current_ma` | `/sys/class/power_supply/battery/current_now` | 原始 µA 转为 mA，保留符号 |
| `voltage_v` | `/sys/class/power_supply/battery/voltage_now`，不可用时尝试电池服务电压 | 原始 µV 或电池服务 mV 转为 V |
| `power_w` | 有效电流与电压，加上供电状态校验 | 电池放电功率估计，W |
| `battery_pct` | Android 电池服务 | 电量百分比 |
| `battery_temp_c` | Android 电池服务 | 电池温度，℃；不是 SoC 温度 |
| `cpu_pct` | `/proc/stat` 相邻有效计数差 | 整机 CPU 使用率，% |
| `app_mem_mb` | `dumpsys meminfo` 的目标应用 PSS | 按 1024 换算为 MiB；字段名沿用 `app_mem_mb` |

应用 PSS 每 5 秒读取一次，其余行保持空值，避免把旧值伪装成新的测量。系统无法读取的 CPU、内存或其他指标同样留空。当前不提供 GPU 利用率。

原始采样还记录 `fps_source`、`power_source`、`layer`、`notes`、时间戳和已观测到的 `frame_intervals_ms`，方便追溯每个值的来源和警告。

## FPS 测到的是什么

PhoneTrace 读取与目标游戏对应的 SurfaceFlinger 图层，使用实际呈现时间戳计算相邻帧间隔。自动图层选择优先考虑游戏的 `SurfaceView` / `BLAST` 图层，也允许手动选择。

设本次新观测到的有效帧间隔为 `Δt₁ … Δtₙ`，单位毫秒：

```text
观测 FPS = 1000 × n / ΣΔt
平均帧时间 = ΣΔt / n
慢帧数 = Δt > 50 ms 的间隔数量
```

开始记录的首次读取用于建立基线，后续只统计新出现的有效帧间隔。时间戳去重，切换图层或长时间无新帧后重新建立基线，避免重复累计或把暂停时段误计为连续游戏帧。

这些数据描述所选图层在系统合成链路中的呈现节奏，不能保证等同于游戏引擎内部渲染帧率。开启插帧时，两者尤其可能不同。PhoneTrace 不以显示器刷新率或无关系统图层替代游戏 FPS。

SurfaceFlinger 环形缓冲区有容量限制。高帧率、ADB 链路较慢、采样间隔过大都可能使一部分帧在读取前被覆盖。软件会尽可能保留相关警告，仅统计实际观测到的间隔；不能承诺逐帧完整捕获。建议保持默认 0.5 秒采样。

P95 使用已观测帧间隔排序后的第 95 百分位。慢帧阈值固定为大于 50 ms，不能与其他软件使用不同定义的 Jank 计数直接对比。

## 功耗为什么叫估计

在同时满足“未连接外部电源”“系统确认正在放电”“电流和电压有效”时，计算：

```text
电池放电功率估计（W）= |电流（mA）| × 电压（V）/ 1000
```

它估计的是**整机电池端放电功率**，包含屏幕、网络、处理器及其他进程的影响，不能表示游戏单独功耗或 SoC 独占功耗。

USB 充电时，电池电流可能只是充入或放出的净电流，无法直接推导整机消耗；PhoneTrace 因此将功耗留空。系统供电状态不明确、数据被电池服务模拟或接口不可读时，也不输出功耗估计。建议使用无线调试并拔掉 USB 线。

不同厂商的电流符号约定、节点含义、传感器刷新频率和双电芯上报方式可能不同。软件保留电流原始符号，不猜测双电芯倍率，不用电量百分比下降速度冒充瞬时功率，也不通过模型填充缺失值。即使节点能够读取，仍需要对具体机型核对数据含义；未经参考仪器对照不能声明测量误差范围。

## 统计如何处理缺失和中断

- **平均 FPS**：根据整段测试实际观测的帧间隔总数与累计时长计算，避免直接平均不同覆盖长度的采样 FPS。
- **平均功耗**：只对相邻的有效功耗样本进行梯形积分，再除以这些区间的有效时长。缺失端点或超过 `max(2 秒, 3 × 配置采样间隔)` 的间断不纳入积分。
- **覆盖情况**：记录总时长、有效功耗时长、观测帧间隔累计时长及相应覆盖比例。覆盖率描述观测范围，不是测量精度认证。
- **空值**：界面显示 `—`，CSV 留空，JSON 保存 `null`；缺失数据不计作零。图表在缺失值和长间断处断开。
- **断连与中断**：采样持续写入 JSONL 和 CSV。连续三次读取失败会结束测试并保留已写入数据，历史读取可从已有 JSONL 恢复记录；不补造未采集的数据。

## 如何复核一次测试

1. 查看 `metadata.json`：确认设备、游戏包名、图层设置、采样间隔及是否为演示模式。
2. 查看 `samples.csv` 或 `samples.jsonl`：检查数据源、缺失值、供电状态和 `notes` 中的警告。
3. 使用 `frame_intervals.csv` 复算 FPS、帧时间或慢帧；只对实际观测范围作结论。
4. 查看 `summary.json` 和 `report.html`：同时关注测试总时长与有效采集时长，避免忽略低覆盖率。
5. 对比其他工具时，先统一游戏场景、时间区间、图层、帧率定义、功耗测量位置和设备状态。

演示模式使用生成的模拟数据，只验证软件体验和处理流程。它不会在真机读取失败时自动启用，演示记录不能用于性能结论。

## 官方参考

- [Android Debug Bridge（ADB）](https://developer.android.com/tools/adb)：设备连接、调试授权和无线调试。
- [AOSP SurfaceFlinger FrameTracker](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/surfaceflinger/FrameTracker.cpp)：呈现时间戳与帧历史实现。
- [Linux Power Supply Class](https://docs.kernel.org/power/power_supply_class.html)：电池电流、电压等属性的定义与单位。
- [Android 内存使用诊断](https://developer.android.com/tools/dumpsys#meminfo)：PSS 等内存统计口径。

具体实现位于 [`phonetrace/collector.py`](../phonetrace/collector.py) 和 [`phonetrace/recorder.py`](../phonetrace/recorder.py)。欢迎提交带原始记录和验证方法的适配反馈。
