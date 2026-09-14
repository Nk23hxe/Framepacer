import sys
import os
import time
import ctypes
from ctypes import wintypes
from collections import deque
from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint, QPointF
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, 
    QPushButton, QLineEdit, QLabel
)
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# --- Vector Buttons Matching Screenshot ---
class ActionButton(QPushButton):
    def __init__(self, mode="menu"):
        super().__init__()
        self.mode = mode
        self.setFixedSize(48, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(1, 1, w - 2, h - 2)

        # Background state
        bg = QColor("#262626") if self.isDown() else QColor("#1e1e1e")
        border = QColor("#383838")
        p.setBrush(bg)
        p.setPen(QPen(border, 1.2))
        p.drawRoundedRect(rect, 10, 10)

        p.setPen(Qt.PenStyle.NoPen)

        if self.mode == "menu":
            # Three rounded horizontal bars
            p.setBrush(QColor("#c5c5c5"))
            for y in [15, 23, 31]:
                p.drawRoundedRect(QRectF(14, y, 20, 3.2), 1.5, 1.5)

        elif self.mode == "presets":
            # 2x2 Grid with rounded rects
            p.setBrush(QColor("#c5c5c5"))
            p.drawRoundedRect(QRectF(15, 15, 7.5, 7.5), 1.5, 1.5)
            p.drawRoundedRect(QRectF(25.5, 15, 7.5, 7.5), 1.5, 1.5)
            p.drawRoundedRect(QRectF(15, 25.5, 7.5, 7.5), 1.5, 1.5)
            p.drawRoundedRect(QRectF(25.5, 25.5, 7.5, 7.5), 1.5, 1.5)

        elif self.mode == "pin":
            # Pushpin Vector
            active = self.isChecked()
            color = QColor("#ffffff") if active else QColor("#7a7a7a")
            p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(color if active else Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            path.moveTo(17, 18)
            path.lineTo(24, 15)
            path.lineTo(31, 22)
            path.lineTo(28, 29)
            path.lineTo(24, 25)
            path.lineTo(19, 30)
            path.lineTo(17, 18)
            p.drawPath(path)

            # Pin needle tip
            p.drawLine(19, 30, 13, 36)

            # Slashed line when unpinned
            if not active:
                p.setPen(QPen(QColor("#a8a8a8"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(12, 12, 36, 36)

# --- Real-Time Rounded Grid Frametime Graph ---
class FrametimeGraph(QWidget):
    def __init__(self):
        super().__init__()
        self.history = deque([16.66] * 50, maxlen=50)
        self.setFixedHeight(120)

    def add_sample(self, ms):
        self.history.append(ms)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(1, 1, w - 2, h - 2)

        # Outer rounded pill boundary
        p.setBrush(QColor("#1a1a1a"))
        p.setPen(QPen(QColor("#2d2d2d"), 1.2))
        p.drawRoundedRect(rect, 10, 10)

        # Subtle dark grid
        p.setPen(QPen(QColor("#272727"), 1))
        for row in [0.25, 0.5, 0.75]:
            y = int(h * row)
            p.drawLine(2, y, int(w - 2), y)

        cols = 16
        for c in range(1, cols):
            x = int(c * (w / cols))
            p.drawLine(x, 2, x, int(h - 2))

        # 60 FPS Target Baseline
        mid_y = int(h * 0.5)
        p.setPen(QPen(QColor("#4f4f4f"), 1.2))
        p.drawLine(2, mid_y, int(w - 2), mid_y)

        # Frametime History Line
        p.setPen(QPen(QColor("#cfcfcf"), 1.8))
        step_x = (w - 12) / (len(self.history) - 1)
        for i in range(len(self.history) - 1):
            y1 = h - (self.history[i] / 33.33 * h)
            y2 = h - (self.history[i+1] / 33.33 * h)
            y1 = max(6.0, min(h - 6.0, y1))
            y2 = max(6.0, min(h - 6.0, y2))
            p.drawLine(int(6 + i * step_x), int(y1), int(6 + (i + 1) * step_x), int(y2))

# --- Main Window ---
class FramepacerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(430, 260)
        self.drag_pos = QPoint()

        self.last_sample_time = time.perf_counter()
        self.current_fps = 0.0
        self.current_ms = 0.0

        self.setStyleSheet("""
            QWidget {
                background-color: #141414;
                color: #e6e6e6;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QLineEdit {
                background-color: #1e1e1e;
                border: 1px solid #333333;
                border-radius: 10px;
                color: #ffffff;
                font-size: 18px;
                font-weight: 500;
                padding: 4px;
            }
            QPushButton#apply_btn {
                background-color: #242424;
                border: 1px solid #383838;
                border-radius: 10px;
                padding: 8px 22px;
                font-size: 15px;
                font-weight: 600;
                color: #ffffff;
            }
            QPushButton#apply_btn:hover { background-color: #2e2e2e; }
            QPushButton#apply_btn:pressed { background-color: #181818; }
        """)

        self.init_ui()

        # Telemetry update loop
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(8)

        # Titlebar
        titlebar = QHBoxLayout()
        titlebar.setContentsMargins(2, 0, 2, 4)

        ico_badge = QLabel("▣")
        ico_badge.setStyleSheet("color: #ffffff; font-size: 16px;")
        title_lbl = QLabel("framepacer")
        title_lbl.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: 500;")

        btn_min = QPushButton("─")
        btn_min.setFixedSize(28, 22)
        btn_min.setStyleSheet("border:none; color:#777; font-size:11px;")
        btn_min.clicked.connect(self.showMinimized)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 22)
        btn_close.setStyleSheet("border:none; color:#777; font-size:12px;")
        btn_close.clicked.connect(self.close)

        titlebar.addWidget(ico_badge)
        titlebar.addSpacing(6)
        titlebar.addWidget(title_lbl)
        titlebar.addStretch()
        titlebar.addWidget(btn_min)
        titlebar.addWidget(btn_close)
        root.addLayout(titlebar)

        # Control Row
        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.btn_menu = ActionButton("menu")
        self.btn_presets = ActionButton("presets")
        self.btn_pin = ActionButton("pin")
        self.btn_pin.setCheckable(True)
        self.btn_pin.toggled.connect(self.toggle_wot)

        self.fps_box = QLineEdit("60")
        self.fps_box.setFixedSize(72, 48)
        self.fps_box.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setObjectName("apply_btn")
        self.btn_apply.setFixedSize(92, 48)

        controls.addWidget(self.btn_menu)
        controls.addWidget(self.btn_presets)
        controls.addWidget(self.btn_pin)
        controls.addStretch()
        controls.addWidget(self.fps_box)
        controls.addWidget(self.btn_apply)
        root.addLayout(controls)

        # Status Line
        self.lbl_status = QLabel("Status: Waiting")
        self.lbl_status.setStyleSheet("color: #8c8c8c; font-size: 15px; font-weight: 500;")
        root.addWidget(self.lbl_status)

        # Stats Line
        self.lbl_stats = QLabel("FPS: 0 | Time: 0.00 ms")
        self.lbl_stats.setStyleSheet("color: #cfcfcf; font-size: 15px; font-weight: 500;")
        root.addWidget(self.lbl_stats)

        # Frametime Canvas
        self.graph = FrametimeGraph()
        root.addWidget(self.graph)

    def toggle_wot(self, checked):
        # Apply topmost style via Qt WindowFlags + Native Windows SetWindowPos
        hwnd = int(self.winId())
        if checked:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        else:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self.show()
        self.btn_pin.update()

    def get_focused_window_title(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd == int(self.winId()):
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def tick(self):
        active_title = self.get_focused_window_title()

        if active_title:
            self.lbl_status.setText(f"Status: {active_title[:24]}")
            self.lbl_status.setStyleSheet("color: #ffffff; font-size: 15px; font-weight: 500;")

            # Calculate actual frame intervals
            now = time.perf_counter()
            delta = (now - self.last_sample_time) * 1000.0
            self.last_sample_time = now

            if 1.0 < delta < 100.0:
                self.current_ms = delta
                self.current_fps = 1000.0 / delta
                self.lbl_stats.setText(f"FPS: {int(self.current_fps)} | Time: {self.current_ms:.2f} ms")
                self.graph.add_sample(self.current_ms)
        else:
            self.lbl_status.setText("Status: Waiting")
            self.lbl_status.setStyleSheet("color: #8c8c8c; font-size: 15px; font-weight: 500;")

    # Window Dragging
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = FramepacerApp()
    win.show()
    sys.exit(app.exec())
