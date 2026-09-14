import sys
import mmap
import ctypes
from collections import deque
from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, 
    QPushButton, QLineEdit, QLabel
)
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath

# --- Win32 TOPMOST API for Reliable Borderless Pinning ---
user32 = ctypes.windll.user32
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

class SharedData(ctypes.Structure):
    _fields_ = [
        ("target_frametime_ms", ctypes.c_double),
        ("current_fps", ctypes.c_double),
        ("current_frametime_ms", ctypes.c_double),
        ("is_active", ctypes.c_int)
    ]

# --- Custom Icons Painted with Vector Math ---
class IconButton(QPushButton):
    def __init__(self, mode="menu"):
        super().__init__()
        self.mode = mode
        self.setFixedSize(38, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background state
        bg = QColor("#2b2b2b") if self.isDown() or self.isChecked() else QColor("#222222")
        border = QColor("#555555") if self.isChecked() else QColor("#333333")
        painter.setBrush(bg)
        painter.setPen(QPen(border, 1.2))
        painter.drawRoundedRect(QRectF(1, 1, 36, 36), 10, 10)

        # Vector Icons
        painter.setPen(QPen(QColor("#d8d8d8"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        
        if self.mode == "menu":
            painter.drawLine(12, 14, 26, 14)
            painter.drawLine(12, 19, 26, 19)
            painter.drawLine(12, 24, 26, 24)

        elif self.mode == "presets":
            # 2x2 grid
            painter.setBrush(QColor("#d8d8d8"))
            painter.drawRoundedRect(QRectF(12, 12, 5.5, 5.5), 1, 1)
            painter.drawRoundedRect(QRectF(20.5, 12, 5.5, 5.5), 1, 1)
            painter.drawRoundedRect(QRectF(12, 20.5, 5.5, 5.5), 1, 1)
            painter.drawRoundedRect(QRectF(20.5, 20.5, 5.5, 5.5), 1, 1)

        elif self.mode == "wot":
            # Window on Top Pin / Crossed-Pin
            painter.drawRoundedRect(QRectF(13, 13, 12, 12), 2, 2)
            if not self.isChecked():
                # Diagonal slash indicating pin is unhooked/crossed out
                painter.drawLine(10, 10, 28, 28)

# --- Frametime Graph Matching Reference Canvas ---
class FrametimeGraph(QWidget):
    def __init__(self):
        super().__init__()
        self.history = deque([16.66] * 50, maxlen=50)
        self.setFixedHeight(105)

    def add_sample(self, ms):
        self.history.append(ms)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Rounded Container Box
        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        painter.setBrush(QColor("#181818"))
        painter.setPen(QPen(QColor("#262626"), 1.2))
        painter.drawRoundedRect(rect, 10, 10)

        # Grid system
        painter.setPen(QPen(QColor("#242424"), 1))
        # Horizontal lines
        for y in (h * 0.25, h * 0.5, h * 0.75):
            painter.drawLine(1, int(y), int(w - 1), int(y))
        # Vertical columns
        cols = 16
        for c in range(1, cols):
            x = int(c * (w / cols))
            painter.drawLine(x, 1, x, int(h - 1))

        # Midline reference target (16.66ms)
        mid_y = int(h * 0.5)
        painter.setPen(QPen(QColor("#404040"), 1.2))
        painter.drawLine(1, mid_y, int(w - 1), mid_y)

        # Frametime Plot
        painter.setPen(QPen(QColor("#e2e2e2"), 1.6))
        step_x = (w - 10) / (len(self.history) - 1)
        for i in range(len(self.history) - 1):
            y1 = h - (self.history[i] / 33.3 * h)
            y2 = h - (self.history[i+1] / 33.3 * h)
            y1 = max(6.0, min(h - 6.0, y1))
            y2 = max(6.0, min(h - 6.0, y2))
            painter.drawLine(int(5 + i * step_x), int(y1), int(5 + (i + 1) * step_x), int(y2))

# --- Main Native Window ---
class FramepacerUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedSize(400, 245)
        self.drag_position = QPoint()

        self.shm = None

        self.setStyleSheet("""
            QWidget {
                background-color: #171717;
                color: #e6e6e6;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QLineEdit {
                background-color: #212121;
                border: 1px solid #303030;
                border-radius: 9px;
                color: #ffffff;
                font-size: 15px;
                font-weight: 600;
                padding: 4px;
            }
            QPushButton#apply_btn {
                background-color: #242424;
                border: 1px solid #363636;
                border-radius: 9px;
                padding: 7px 18px;
                font-size: 14px;
                font-weight: 600;
                color: #ffffff;
            }
            QPushButton#apply_btn:hover { background-color: #2d2d2d; }
            QPushButton#apply_btn:pressed { background-color: #1a1a1a; }
        """)

        self.init_ui()

        # Telemetry Polling Timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

    def init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(6)

        # Title Bar
        titlebar = QHBoxLayout()
        titlebar.setContentsMargins(4, 0, 4, 4)

        # Window icon badge
        self.ico_badge = QLabel("▢")
        self.ico_badge.setStyleSheet("color: #cfcfcf; font-size: 15px; font-weight: bold;")
        self.title_lbl = QLabel("framepacer")
        self.title_lbl.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: 600;")

        btn_min = QPushButton("─")
        btn_min.setFixedSize(28, 22)
        btn_min.setStyleSheet("border:none; color:#888; font-size:10px;")
        btn_min.clicked.connect(self.showMinimized)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 22)
        btn_close.setStyleSheet("border:none; color:#888; font-size:11px;")
        btn_close.clicked.connect(self.close)

        titlebar.addWidget(self.ico_badge)
        titlebar.addSpacing(6)
        titlebar.addWidget(self.title_lbl)
        titlebar.addStretch()
        titlebar.addWidget(btn_min)
        titlebar.addWidget(btn_close)
        root.addLayout(titlebar)

        # Controls Row
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.btn_menu = IconButton("menu")
        self.btn_grid = IconButton("presets")
        self.btn_wot = IconButton("wot")
        self.btn_wot.setCheckable(True)
        self.btn_wot.toggled.connect(self.toggle_wot)

        self.fps_box = QLineEdit("60")
        self.fps_box.setFixedSize(62, 38)
        self.fps_box.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setObjectName("apply_btn")
        self.btn_apply.setFixedHeight(38)
        self.btn_apply.clicked.connect(self.apply_target)

        controls.addWidget(self.btn_menu)
        controls.addWidget(self.btn_grid)
        controls.addWidget(self.btn_wot)
        controls.addStretch()
        controls.addWidget(self.fps_box)
        controls.addWidget(self.btn_apply)
        root.addLayout(controls)

        # Status lines
        self.lbl_proc = QLabel("Waiting for game...")
        self.lbl_proc.setStyleSheet("color: #7d7d7d; font-size: 13px; font-weight: 500; margin-left: 2px;")
        root.addWidget(self.lbl_proc)

        self.lbl_stats = QLabel("FPS: 0  |  Time: 0.00 ms")
        self.lbl_stats.setStyleSheet("color: #d1d1d1; font-size: 13px; font-weight: 500; margin-left: 2px;")
        root.addWidget(self.lbl_stats)

        # Frametime Plot
        self.graph = FrametimeGraph()
        root.addWidget(self.graph)

    def toggle_wot(self, checked):
        # Native Windows API call to toggle topmost floating window instantly
        hwnd = int(self.winId())
        flag = HWND_TOPMOST if checked else HWND_NOTOPMOST
        user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def apply_target(self):
        try:
            fps = float(self.fps_box.text())
            if fps > 0 and self.shm:
                data = SharedData.from_buffer(self.shm)
                data.target_frametime_ms = 1000.0 / fps
                data.is_active = 1
        except ValueError:
            pass

    def tick(self):
        if not self.shm:
            try:
                self.shm = mmap.mmap(0, ctypes.sizeof(SharedData), "Local\\FramepacerIPC")
                self.lbl_proc.setText("Direct3D Pacer Active")
                self.lbl_proc.setStyleSheet("color: #60a5fa; font-size: 13px;")
                self.apply_target()
            except Exception:
                return

        data = SharedData.from_buffer(self.shm)
        self.lbl_stats.setText(f"FPS: {int(data.current_fps)}  |  Time: {data.current_frametime_ms:.2f} ms")
        self.graph.add_sample(data.current_frametime_ms)

    # Window Dragging Logic for Frameless UI
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = FramepacerUI()
    win.show()
    sys.exit(app.exec())
