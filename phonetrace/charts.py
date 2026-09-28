"""Small, dependency-free Qt charts which preserve missing measurements."""

from __future__ import annotations

import math
from bisect import bisect_left

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


def elapsed_label(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class TimeSeriesChart(QWidget):
    """Paint elapsed-time curves; missing values always break the line."""

    def __init__(self, title: str, unit: str, color: str, zero_base: bool = True, parent=None):
        super().__init__(parent)
        self.title, self.unit = title, unit
        self.color, self.zero_base = QColor(color), zero_base
        self.points: list[tuple[float, float | None]] = []
        self.full_history = False
        self.window_s = 180.0
        self.max_gap_s = 2.0
        self.hover_x: float | None = None
        self.setMinimumHeight(184)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def clear(self):
        self.points.clear()
        self.hover_x = None
        self.full_history = False
        self.update()

    def set_sampling_interval(self, interval_s: float):
        try:
            interval_s = float(interval_s)
        except (TypeError, ValueError):
            interval_s = 0.5
        if not math.isfinite(interval_s) or interval_s <= 0:
            interval_s = 0.5
        self.max_gap_s = max(2.0, 3.0 * interval_s)
        self.update()

    def _segments(self, points):
        """Return valid runs, breaking both missing data and sampling outages."""
        segments = []
        segment = []
        previous_t = None
        for elapsed, value in points:
            gap = previous_t is not None and (elapsed - previous_t > self.max_gap_s or elapsed < previous_t)
            if value is None or not math.isfinite(value) or gap:
                if segment:
                    segments.append(segment)
                    segment = []
            if value is not None and math.isfinite(value):
                segment.append((elapsed, value))
            previous_t = elapsed
        if segment:
            segments.append(segment)
        return segments

    def add_point(self, elapsed_s: float, value: float | None):
        try:
            elapsed_s = float(elapsed_s)
            value = float(value) if value is not None else None
        except (TypeError, ValueError):
            return
        if not math.isfinite(elapsed_s):
            return
        if value is not None and not math.isfinite(value):
            value = None
        self.points.append((elapsed_s, value))
        self.update()

    def set_points(self, points: list[tuple[float, float | None]], full_history=True):
        self.points = list(points)
        self.full_history = full_history
        self.hover_x = None
        self.update()

    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        self.update()

    def leaveEvent(self, event):
        self.hover_x = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor("#111e30"))
        painter.setPen(QPen(QColor("#25354b"), 1))
        painter.drawRoundedRect(bounds, 12, 12)

        font = QFont(self.font())
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e5efff"))
        painter.drawText(QRectF(17, 11, self.width() - 130, 25), Qt.AlignmentFlag.AlignVCenter, self.title)
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor("#7f96b2"))
        mode = "完整记录" if self.full_history else "最近 3 分钟"
        painter.drawText(QRectF(self.width() - 123, 11, 105, 25), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, mode)

        plot = QRectF(51, 51, max(1, self.width() - 71), max(1, self.height() - 80))
        end = max(30.0, self.points[-1][0] if self.points else 0)
        start = 0.0 if self.full_history else max(0.0, end - self.window_s)
        relevant = [p for p in self.points if start <= p[0] <= end]
        values = [v for _, v in relevant if v is not None and math.isfinite(v)]
        if values:
            lower = 0.0 if self.zero_base else min(values) - max(2.0, (max(values) - min(values)) * 0.15)
            upper = max(values) * 1.15 if self.zero_base else max(values) + max(2.0, (max(values) - min(values)) * 0.15)
            upper = max(upper, lower + 1)
        else:
            lower, upper = (0.0, 60.0) if self.zero_base else (20.0, 50.0)

        def point(t, v):
            return QPointF(plot.left() + (t - start) / max(1.0, end - start) * plot.width(), plot.bottom() - (v - lower) / (upper - lower) * plot.height())

        for ratio in (0.0, 0.5, 1.0):
            y = plot.bottom() - ratio * plot.height()
            painter.setPen(QPen(QColor("#26364b"), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor("#8da2bc"))
            number = lower + ratio * (upper - lower)
            label = f"{number:.0f}" if upper >= 15 else f"{number:.1f}"
            painter.drawText(QRectF(4, y - 9, 39, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        for ratio in (0.0, 0.5, 1.0):
            x = plot.left() + ratio * plot.width()
            label = elapsed_label(start + ratio * (end - start))
            painter.drawText(QRectF(x - 25, plot.bottom() + 8, 50, 17), Qt.AlignmentFlag.AlignCenter, label)

        if not values:
            painter.setPen(QColor("#7188a4"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "等待可用数据 · 缺失值不会记作 0")
            return

        # Each segment is separate so unavailable samples cannot produce a false line.
        segments = [[point(elapsed, value) for elapsed, value in segment] for segment in self._segments(relevant)]
        painter.save()
        painter.setClipRect(plot.adjusted(-2, -2, 2, 2))
        for segment in segments:
            path = QPainterPath(segment[0])
            for vertex in segment[1:]:
                path.lineTo(vertex)
            if len(segment) > 1:
                shade = QPainterPath(path)
                shade.lineTo(segment[-1].x(), plot.bottom())
                shade.lineTo(segment[0].x(), plot.bottom())
                shade.closeSubpath()
                gradient = QLinearGradient(plot.topLeft(), plot.bottomLeft())
                tint = QColor(self.color)
                tint.setAlpha(50)
                gradient.setColorAt(0, tint)
                tint.setAlpha(0)
                gradient.setColorAt(1, tint)
                painter.fillPath(shade, gradient)
                painter.setPen(QPen(self.color, 2.0))
                painter.drawPath(path)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.color)
                painter.drawEllipse(segment[0], 2.5, 2.5)
        painter.restore()

        latest = relevant[-1][1] if relevant else None
        caption = f"{latest:.1f} {self.unit}" if latest is not None else "—"
        if self.hover_x is not None and plot.left() <= self.hover_x <= plot.right():
            target_t = start + (self.hover_x - plot.left()) / plot.width() * (end - start)
            index = bisect_left([p[0] for p in relevant], target_t)
            candidates = relevant[max(0, index - 1):min(len(relevant), index + 1)]
            if candidates:
                selected_t, selected_v = min(candidates, key=lambda p: abs(p[0] - target_t))
                cursor = point(selected_t, selected_v if selected_v is not None else lower)
                painter.setPen(QPen(QColor("#a7b9cf"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(cursor.x(), plot.top()), QPointF(cursor.x(), plot.bottom()))
                caption = f"{elapsed_label(selected_t)}  ·  {selected_v:.1f} {self.unit}" if selected_v is not None else f"{elapsed_label(selected_t)}  ·  无数据"
        painter.setPen(self.color)
        painter.drawText(QRectF(plot.left(), 34, plot.width(), 17), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, caption)
