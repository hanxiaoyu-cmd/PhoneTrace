from pathlib import Path
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

root = Path(__file__).resolve().parent.parent
dest = root / "assets"
dest.mkdir(exist_ok=True)
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#0b1220"))
painter.drawRoundedRect(QRectF(4, 4, 248, 248), 52, 52)
painter.setPen(QPen(QColor("#213b51"), 4))
painter.setBrush(Qt.BrushStyle.NoBrush)
painter.drawRoundedRect(QRectF(53, 35, 150, 187), 18, 18)
path = QPainterPath(QPointF(28, 144))
for x, y in [(79, 144), (104, 92), (134, 176), (161, 121), (181, 144), (229, 144)]:
    path.lineTo(x, y)
painter.setPen(QPen(QColor("#39dcc6"), 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
painter.drawPath(path)
painter.setPen(QPen(QColor("#a8c3d3"), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
painter.drawLine(QPointF(110, 202), QPointF(146, 202))
painter.end()
assert image.save(str(dest / "phonetrace.png"))
assert image.save(str(dest / "phonetrace.ico"))
