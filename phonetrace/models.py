from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Device:
    serial: str
    state: str = "device"
    model: str = "Android"
    transport: str = "USB"


@dataclass
class Sample:
    timestamp: str
    elapsed_s: float
    fps: float | None = None
    frame_time_ms: float | None = None
    frame_p95_ms: float | None = None
    slow_frames: int | None = None
    power_w: float | None = None
    current_ma: float | None = None
    voltage_v: float | None = None
    battery_pct: float | None = None
    battery_temp_c: float | None = None
    cpu_pct: float | None = None
    app_mem_mb: float | None = None
    plugged: bool | None = None
    fps_source: str = "unavailable"
    power_source: str = "unavailable"
    layer: str = ""
    notes: list[str] = field(default_factory=list)
    frame_intervals_ms: list[float] = field(default_factory=list)
    current_source: str = "unavailable"
    voltage_source: str = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionConfig:
    serial: str
    package: str
    layer: str = ""
    interval_s: float = 0.5
    title: str = "游戏测试"
    demo: bool = False
    duration_s: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
