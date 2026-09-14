import sys
import os
import mmap
import ctypes
from ctypes import wintypes
from collections import deque
from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, 
    QPushButton, QLineEdit, QLabel
)
from PyQt6.QtGui import QPainter, QColor, QPen

# --- Win32 API Bindings ---
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT = 0x00001000
MEM_RESERVE = 0x00002000
PAGE_READWRITE = 0x04

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

# --- Injection Engine ---
def inject_dll_to_pid(pid: int, dll_path: str) -> bool:
    if not os.path.exists(dll_path):
        return False
    h_proc = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not h_proc:
        return False

    dll_bytes = dll_path.encode('utf-8') + b'\0'
    arg_addr = kernel32.VirtualAllocEx(h_proc, None, len(dll_bytes), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not arg_addr:
        kernel32.CloseHandle(h_proc)
        return False

    kernel32.WriteProcessMemory(h_proc, arg_addr, dll_bytes, len(dll_bytes), None)
    h_k32 = kernel32.GetModuleHandleW("kernel32.dll")
    load_lib = kernel32.GetProcAddress(h_k32, b"LoadLibraryA")

    h_thread = kernel32.CreateRemoteThread(h_proc, None, 0, load_lib, arg_addr, 0, None)
    if not h_thread:
        kernel32.CloseHandle(h_proc)
        return False

    kernel32.WaitForSingleObject(h_thread, 1500)
    kernel32.CloseHandle(h_thread)
    kernel32.CloseHandle(h_proc)
    return True

# --- Vector Buttons ---
class IconButton(QPushButton):
    def __init__(self, mode="menu"):
        super().__init__()
        self.mode = mode
        self.setFixedSize(38, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QColor("#2b2b2b") if self.isChecked() else QColor("#222222")
        border = QColor("#555555") if self.isChecked() else QColor("#333333")
        p.setBrush(bg)
        p.setPen(QPen(border, 1.2))
        p.drawRoundedRect(QRectF(1, 1, 36, 36), 10, 10)

        p.setPen(QPen(QColor("#d8d8d8"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.mode == "menu":
            p.drawLine(12, 14, 26, 14)
            p.drawLine(12, 19, 26, 19)
            p.drawLine(12, 24, 26, 24)
        elif self.mode == "presets":
            p.setBrush(QColor("#d8d8d8"))
            p.drawRoundedRect(QRectF(12, 12, 5.5, 5.5), 1, 1)
            p.drawRoundedRect(QRectF(20.5, 12, 5.5, 5.5), 1, 1)
            p.drawRoundedRect(QRectF(12, 20.5, 5.5, 5.5), 1, 1)
            p.drawRoundedRect(QRectF(20.5, 20.5, 5.5, 5.5), 1, 1)
        elif self.mode == "wot":
            p.drawRoundedRect(QRectF(13, 13, 12, 12), 2, 2)
            if not self.isChecked():
                p.drawLine(10, 10, 28, 28)

# --- Real-Time Frametime Graph Canvas ---
class FrametimeGraph(QWidget):
    def __init__(self):
        super().__init__()
        self.history = deque([16.66] * 50, maxlen=50)
        self.setFixedHeight(105)

    def add_sample(self, ms):
        self.history.append(ms)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(self.width()), float(self.height())
        p.setBrush(QColor("#181818"))
        p.setPen(QPen(QColor("#262626"), 1.2))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 10, 10)

        p.setPen(QPen(QColor("#242424"), 1))
        for y in (h * 0.25, h * 0.5, h * 0.75):
            p.drawLine(1, int(y), int(w - 1), int(y))
        cols = 16
        for c in range(1, cols):
            x = int(c * (w / cols))
            p.drawLine(x, 1, x, int(h - 1))

        # 16.66ms Guide Line
        mid_y = int(h * 0.5)
        p.setPen(QPen(QColor("#404040"), 1.2))
        p.drawLine(1, mid_y, int(w - 1), mid_y)

        # Draw Frametime Plot
        p.setPen(QPen(QColor("#e2e2e2"), 1.6))
        step_x = (w - 10) / (len(self.history) - 1)
        for i in range(len(self.history) - 1):
            y1 = h - (self.history[i] / 33.3 * h)
            y2 = h - (self.history[i+1] / 33.3 * h)
            y1 = max(6.0, min(h - 6.0, y1))
            y2 = max(6.0, min(h - 6.0, y2))
            p.drawLine(int(5 + i * step_x), int(y1), int(5 + (i + 1) * step_x), int(y2))

# --- Main Window ---
class FramepacerUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(400, 245)
        self.drag_pos = QPoint()

        self.shm = None
        self.current_pid = 0
        self.injected_pids = set()
        
        # Locate hook.dll next to exe
        base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
        self.dll_path = os.path.abspath(os.path.join(base_path, "hook.dll"))

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
        """)

        self.init_ui()

        # Polling tick
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    def init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(6)

        # Titlebar
        titlebar = QHBoxLayout()
        titlebar.setContentsMargins(4, 0, 4, 4)
        ico_badge = QLabel("▢")
        ico_badge.setStyleSheet("color: #cfcfcf; font-size: 15px; font-weight: bold;")
        title_lbl = QLabel("framepacer")
        title_lbl.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: 600;")

        btn_min = QPushButton("─")
        btn_min.setFixedSize(28, 22)
        btn_min.setStyleSheet("border:none; color:#888; font-size:10px;")
        btn_min.clicked.connect(self.showMinimized)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 22)
        btn_close.setStyleSheet("border:none; color:#888; font-size:11px;")
        btn_close.clicked.connect(self.close)

        titlebar.addWidget(ico_badge)
        titlebar.addSpacing(6)
        titlebar.addWidget(title_lbl)
        titlebar.addStretch()
        titlebar.addWidget(btn_min)
        titlebar.addWidget(btn_close)
        root.addLayout(titlebar)

        # Controls
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

        # Status & Stats
        self.lbl_proc = QLabel("Waiting for focus...")
        self.lbl_proc.setStyleSheet("color: #7d7d7d; font-size: 13px; font-weight: 500;")
        root.addWidget(self.lbl_proc)

        self.lbl_stats = QLabel("FPS: 0 | Time: 0.00 ms")
        self.lbl_stats.setStyleSheet("color: #d1d1d1; font-size: 13px; font-weight: 500;")
        root.addWidget(self.lbl_stats)

        # Graph
        self.graph = FrametimeGraph()
        root.addWidget(self.graph)

    def toggle_wot(self, checked):
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
        except Exception:
            pass

    def get_active_process(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd == int(self.winId()):
            return None, None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        # Query process image name
        h_proc = kernel32.OpenProcess(0x1000, False, pid.value) # PROCESS_QUERY_LIMITED_INFORMATION
        if not h_proc:
            return pid.value, "unknown.exe"
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(h_proc)
        
        exe_name = os.path.basename(buf.value)
        return pid.value, exe_name

    def tick(self):
        pid, exe = self.get_active_process()
        
        # Blacklist non-game windows
        ignored = ["explorer.exe", "SearchHost.exe", "Taskmgr.exe", "ShellExperienceHost.exe", ""]
        if exe in ignored or not exe:
            if exe:
                self.lbl_proc.setText(f"Rejected: {exe}")
                self.lbl_proc.setStyleSheet("color: #7d7d7d; font-size: 13px;")
            return

        # New game focused
        if pid != self.current_pid:
            self.current_pid = pid
            if pid not in self.injected_pids:
                if inject_dll_to_pid(pid, self.dll_path):
                    self.injected_pids.add(pid)
                    self.lbl_proc.setText(f"Active: {exe}")
                    self.lbl_proc.setStyleSheet("color: #4ade80; font-size: 13px;")
                else:
                    self.lbl_proc.setText(f"Rejected: {exe}")
                    self.lbl_proc.setStyleSheet("color: #ef4444; font-size: 13px;")
            else:
                self.lbl_proc.setText(f"Active: {exe}")
                self.lbl_proc.setStyleSheet("color: #4ade80; font-size: 13px;")

        # Read Telemetry from Shared Memory
        try:
            if not self.shm:
                # Open existing handle from hook.dll without creating a dummy one
                self.shm = mmap.mmap(-1, ctypes.sizeof(SharedData), "Local\\FramepacerIPC", access=mmap.ACCESS_WRITE)
                self.apply_target()
            
            data = SharedData.from_buffer(self.shm)
            fps = data.current_fps
            ft = data.current_frametime_ms
            self.lbl_stats.setText(f"FPS: {int(fps)} | Time: {ft:.2f} ms")
            if ft > 0:
                self.graph.add_sample(ft)
        except Exception:
            self.shm = None

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
    win = FramepacerUI()
    win.show()
    sys.exit(app.exec())
