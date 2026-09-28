"""Explicit demonstration data. Never used as a real-device fallback."""
from __future__ import annotations

import math
import random
import time
from datetime import datetime

from .models import Sample, SessionConfig


class DemoCollector:
    def __init__(self, config: SessionConfig):
        self.config = config
        self.started = time.monotonic()
        self.last = self.started
        self.rng = random.Random(7318)

    def sample(self) -> Sample:
        now = time.monotonic()
        elapsed = now - self.started
        span = now - self.last
        self.last = now
        fps = 59.4 - 2.0 * math.sin(elapsed / 9) ** 8
        frames: list[float] = []
        if span > 0.1:
            count = max(1, round(fps * span))
            frames = [max(8.0, 1000 / fps + self.rng.uniform(-0.7, 0.7)) for _ in range(count)]
            if int(elapsed) % 19 == 18:
                frames[-1] = 58.0
            fps = len(frames) * 1000 / sum(frames)
        power = 4.25 + 0.45 * math.sin(elapsed / 5) + self.rng.uniform(-0.06, 0.06)
        voltage = 4.12 - elapsed * 0.00001
        return Sample(
            timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            elapsed_s=round(elapsed, 3),
            fps=round(fps, 2) if frames else None,
            frame_time_ms=sum(frames) / len(frames) if frames else None,
            frame_p95_ms=sorted(frames)[min(len(frames) - 1, int(len(frames) * 0.95))] if frames else None,
            slow_frames=sum(value > 50 for value in frames) if frames else None,
            power_w=round(power, 3),
            current_ma=round(-power / voltage * 1000, 2),
            voltage_v=round(voltage, 3),
            battery_pct=max(0, round(80 - elapsed / 200, 1)),
            battery_temp_c=round(30 + 7 * (1 - math.exp(-elapsed / 120)), 1),
            cpu_pct=round(35 + 8 * math.sin(elapsed / 6), 1),
            app_mem_mb=round(2350 + 110 * math.sin(elapsed / 11), 1),
            plugged=False,
            fps_source="DEMO — 模拟数据",
            power_source="DEMO — 模拟数据",
            layer="DEMO / Synthetic SurfaceView",
            notes=["演示数据：仅用于体验界面和验证记录流程，不是手机实测。"],
            frame_intervals_ms=frames,
        )

    def close(self):
        pass
