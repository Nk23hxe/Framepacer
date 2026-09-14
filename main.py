import sys
import os
import mmap
import ctypes
from ctypes import wintypes
from collections import deque
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, 
    QPushButton, QLineEdit, QLabel
)
from PyQt6.QtGui import QPainter, QColor, QPen

# --- Win32 Process Injection Logic ---
PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT = 0x00001000
MEM_RESERVE = 0x00002000
PAGE_READWRITE = 0x04

kernel32 = ctypes.windll.kernel32

def inject_dll(pid: int, dll_path: str) -> bool:
    h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not h_process:
        return False

    dll_bytes = dll_path.encode('ascii')
    arg_address = kernel32.VirtualAllocEx(h_process, None, len(dll_bytes) + 1, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not arg_address:
        kernel32.CloseHandle(h_process)
        return False

    kernel32.WriteProcessMemory(h_process, arg_address, dll_bytes, len(dll_bytes) + 1, None)
    h_kernel32 = kernel32.GetModuleHandleW("kernel32.dll")
    load_library_a = kernel32.GetProcAddress(h_kernel32, b"LoadLibraryA")

    h_thread = kernel32.CreateRemoteThread(h_process, None, 0, load_library_a, arg_address, 0, None)
    if not h_thread:
        kernel32.CloseHandle(h_process)
        return False

    kernel32.WaitForSingleObject(h_thread, 2000)
    kernel32.CloseHandle(h_thread)
    kernel32.CloseHandle(h_process)
    return True

# --- IPC Shared Memory ---
class SharedData(ctypes.Structure):
    _fields_ = [
        ("target_frametime_ms", ctypes.c_double),
        ("current_fps", ctypes.c_double),
        ("current_frametime_ms", ctypes.c_double),
        ("is_active", ctypes.c_int)
    ]

# --- Graph Canvas Matching Screenshot ---
class FrametimeGraph(QWidget):
    def __init__(self):
        super().__init__()
        self.history = deque([16.66] * 45, maxlen=45)
        self.setMinimumHeight(120)

    def add_sample(self, ms):
        self.history.append(ms)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, self.width(), self.height())
        painter.setBrush(QColor("#1e1e1e"))
        painter.setPen(QPen(QColor("#2d2d2d"), 1))
        painter.drawRoundedRect(rect, 8, 8)

        # Graph Grid
        painter.setPen(QPen(QColor("#292929"), 1))
        for y in range(15, self.height(), 15):
            painter.drawLine(0, y, self.width(), y)
        for x in range(0, self.width(), int(self.width() / 16)):
            painter.drawLine(x, 0, x, self.height())

        # Midline target reference
        mid_y = int(self.height() / 2)
        painter.setPen(QPen(QColor("#666666"), 1))
        painter.drawLine(0, mid_y, self.width(), mid_y)

        # Frametime line
        painter.setPen(QPen(QColor("#cccccc"), 1.8))
        step_x = self.width() / (len(self.history) - 1)
        for i in range(len(self.history) - 1):
            y1 = self.height() - (self.history[i] / 33.3 * self.height())
            y2 = self.height() - (self.history[i+1] / 33.3 * self.height())
            y1 = max(4, min(self.height() - 4, y1))
            y2 = max(4, min(self.height() - 4, y2))
            painter.drawLine(int(i * step_x), int(y1), int((i + 1) * step_x), int(y2))

# --- Main Window ---
class FramepacerUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("framepacer")
        self.setFixedSize(380, 235)
        self.shm = None
        self.attached = False

        self.setStyleSheet("""
            QWidget {
                background-color: #121212;
                color: #d1d1d1;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QPushButton {
                background-color: #242424;
                border: 1px solid #363636;
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #2e2e2e; }
            QPushButton:checked {
                background-color: #333333;
                border: 1px solid #5a5a5a;
                color: #ffffff;
            }
            QLineEdit {
                background-color: #1f1f1f;
                border: 1px solid #363636;
                border-radius: 8px;
                padding: 5px;
                font-size: 13px;
                font-weight: bold;
                color: #ffffff;
            }
        """)

        self.init_ui()

        # Update loop (30 Hz)
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        # Top Control Row
        top = QHBoxLayout()
        self.btn_menu = QPushButton("≡")
        self.btn_grid = QPushButton("⊞")
        self.btn_pacer = QPushButton("Pacer")
        self.btn_pacer.setCheckable(True)
        self.btn_pacer.setChecked(True)

        self.fps_box = QLineEdit("60")
        self.fps_box.setFixedWidth(52)
        self.fps_box.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.clicked.connect(self.apply_target)

        top.addWidget(self.btn_menu)
        top.addWidget(self.btn_grid)
        top.addWidget(self.btn_pacer)
        top.addStretch()
        top.addWidget(self.fps_box)
        top.addWidget(self.btn_apply)
        layout.addLayout(top)

        # Target Game / Process Status Line
        self.lbl_proc = QLabel("Waiting for game...")
        self.lbl_proc.setStyleSheet("color: #7a7a7a; font-size: 13px; margin-top: 2px;")
        layout.addWidget(self.lbl_proc)

        # Real-time Telemetry Line
        self.lbl_stats = QLabel("FPS: 0  |  Time: 0.00 ms")
        self.lbl_stats.setStyleSheet("color: #cccccc; font-size: 13px; font-weight: 500;")
        layout.addWidget(self.lbl_stats)

        # Live Graph
        self.graph = FrametimeGraph()
        layout.addWidget(self.graph)

        self.setLayout(layout)

    def apply_target(self):
        try:
            fps = float(self.fps_box.text())
            if fps > 0 and self.shm:
                data = SharedData.from_buffer(self.shm)
                data.target_frametime_ms = 1000.0 / fps
                data.is_active = 1 if self.btn_pacer.isChecked() else 0
        except ValueError:
            pass

    def tick(self):
        # Open IPC shared memory once hooked
        if not self.shm:
            try:
                self.shm = mmap.mmap(0, ctypes.sizeof(SharedData), "Local\\FramepacerIPC")
                self.lbl_proc.setText("Active: Hooked & Pacing")
                self.lbl_proc.setStyleSheet("color: #4ade80; font-size: 13px;")
                self.apply_target()
            except Exception:
                return

        data = SharedData.from_buffer(self.shm)
        fps = data.current_fps
        ft = data.current_frametime_ms
        self.lbl_stats.setText(f"FPS: {int(fps)}  |  Time: {ft:.2f} ms")
        self.graph.add_sample(ft)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = FramepacerUI()
    win.show()
    sys.exit(app.exec())
