import os
import sys
import subprocess
import time
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton,
    QComboBox, QHBoxLayout, QVBoxLayout, QMessageBox, QCheckBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap

from camera_thread import CameraThread
from filters import apply_canny


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Recorder")
        self.setMinimumSize(800, 600)

        self._thread: CameraThread | None = None
        self._waiting = False
        self._countdown_count = 0
        self._rec_start = 0.0
        self._output_dir = ""

        # WAIT 카운트다운용 (1초 간격)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        # REC 경과 시간용 (200ms 간격)
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(200)
        self._rec_timer.timeout.connect(self._update_time)

        self._setup_ui()
        self._refresh_cameras()

    # ── UI 구성 ────────────────────────────────────────────────
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 카메라 선택 행
        cam_row = QHBoxLayout()
        cam_row.addWidget(QLabel("Camera:"))
        self.combo_cam = QComboBox()
        self.combo_cam.setMinimumWidth(160)
        self.combo_cam.currentIndexChanged.connect(self._on_camera_changed)
        cam_row.addWidget(self.combo_cam)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_cameras)
        cam_row.addWidget(btn_refresh)

        cam_row.addStretch()

        # 스타트 라이트 (우측)
        self._lights = []
        for _ in range(3):
            lbl = QLabel()
            lbl.setFixedSize(18, 18)
            lbl.setStyleSheet("background: #333; border-radius: 9px;")
            cam_row.addWidget(lbl)
            self._lights.append(lbl)
        cam_row.addSpacing(6)
        root.addLayout(cam_row)

        # 카메라 영상
        self.lbl_video = QLabel("카메라를 선택하세요")
        self.lbl_video.setAlignment(Qt.AlignCenter)
        self.lbl_video.setStyleSheet("background: #111; color: #aaa;")
        self.lbl_video.setMinimumHeight(400)
        root.addWidget(self.lbl_video, stretch=1)

        # 하단 컨트롤 행
        ctrl_row = QHBoxLayout()

        self.lbl_status = QLabel("⬤  PREVIEW")
        self.lbl_status.setStyleSheet("color: gray; font-size: 14px; font-weight: bold;")
        ctrl_row.addWidget(self.lbl_status)

        self.lbl_time = QLabel("00:00:00")
        self.lbl_time.setStyleSheet("font-size: 14px; font-family: 'Courier New';")
        ctrl_row.addWidget(self.lbl_time)

        ctrl_row.addStretch()

        self.btn_mirror = QPushButton("⟺  Mirror")
        self.btn_mirror.setFixedHeight(36)
        self.btn_mirror.setCheckable(True)
        self.btn_mirror.clicked.connect(self._toggle_mirror)
        ctrl_row.addWidget(self.btn_mirror)

        self.btn_canny = QPushButton("⬡  Edge")
        self.btn_canny.setFixedHeight(36)
        self.btn_canny.setCheckable(True)
        self.btn_canny.clicked.connect(self._toggle_canny)
        ctrl_row.addWidget(self.btn_canny)

        self.chk_canny_record = QCheckBox("녹화에도 적용")
        self.chk_canny_record.setEnabled(False)
        self.chk_canny_record.stateChanged.connect(self._toggle_canny_record)
        ctrl_row.addWidget(self.chk_canny_record)

        self.btn_start = QPushButton("▶  Start")
        self.btn_start.setFixedHeight(36)
        self.btn_start.clicked.connect(self._toggle_recording)
        ctrl_row.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setFixedHeight(36)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_recording)
        ctrl_row.addWidget(self.btn_stop)

        root.addLayout(ctrl_row)

    # ── 카메라 감지 ─────────────────────────────────────────────
    def _refresh_cameras(self):
        self.combo_cam.blockSignals(True)
        self.combo_cam.clear()
        cameras = CameraThread.detect_cameras()
        if not cameras:
            self.combo_cam.addItem("카메라 없음")
            self.combo_cam.blockSignals(False)
            self._show_permission_dialog()
            return
        for idx in cameras:
            self.combo_cam.addItem(f"Camera {idx}", idx)
        self.combo_cam.blockSignals(False)
        self._start_preview(cameras[0])

    def _on_camera_changed(self, index: int):
        cam_id = self.combo_cam.itemData(index)
        if cam_id is not None:
            self._stop_recording()
            self._start_preview(cam_id)

    def _show_permission_dialog(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("카메라 권한 필요")
        msg.setIcon(QMessageBox.Warning)
        msg.setText("카메라에 접근할 수 없습니다.\n카메라 권한이 꺼져 있을 수 있습니다.")

        if sys.platform == "darwin":
            msg.setInformativeText(
                "macOS 시스템 설정 → 개인 정보 보호 및 보안 → 카메라에서\n"
                "이 앱(Terminal 또는 Python)을 활성화한 후 Refresh를 눌러주세요."
            )
            btn_open = msg.addButton("시스템 설정 열기", QMessageBox.ActionRole)
        elif sys.platform == "win32":
            msg.setInformativeText(
                "Windows 설정 → 개인 정보 → 카메라에서\n"
                "카메라 액세스를 허용한 후 Refresh를 눌러주세요."
            )
            btn_open = msg.addButton("Windows 설정 열기", QMessageBox.ActionRole)
        else:
            msg.setInformativeText("카메라 권한을 확인한 후 Refresh를 눌러주세요.")
            btn_open = None

        msg.addButton("닫기", QMessageBox.RejectRole)
        msg.exec_()

        if btn_open and msg.clickedButton() == btn_open:
            if sys.platform == "darwin":
                subprocess.run([
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
                ])
            elif sys.platform == "win32":
                subprocess.run(["start", "ms-settings:privacy-webcam"], shell=True)

    def _start_preview(self, camera_index: int):
        self._stop_thread()
        self._thread = CameraThread(camera_index)
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.error.connect(self._on_error)
        self._thread.recording_started.connect(self._on_recording_started)
        self._thread.start()

    # ── 프레임 수신 ─────────────────────────────────────────────
    @pyqtSlot(np.ndarray)
    def _on_frame(self, frame: np.ndarray):
        display = frame.copy()

        if self.btn_canny.isChecked():
            display = apply_canny(display)

        if self._waiting:
            cv2.circle(display, (30, 30), 12, (0, 255, 0), -1)
            cv2.putText(display, str(self._countdown_count), (50, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        elif self._thread and self._thread.is_recording:
            cv2.circle(display, (30, 30), 12, (0, 0, 255), -1)
            cv2.putText(display, "REC", (50, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(
            self.lbl_video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.lbl_video.setPixmap(pix)

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        QMessageBox.critical(self, "오류", msg)

    # ── 녹화 제어 ───────────────────────────────────────────────
    def _toggle_recording(self):
        if self._thread is None:
            return
        if self._waiting:
            self._stop_recording()
            return
        if not self._thread.is_recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        if self._thread is None:
            return
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._output_dir = os.path.join(base, "output")

        self._waiting = True
        self._countdown_count = 3
        self.lbl_time.setText("00:00:00")
        self._set_ui_state("wait")
        self._countdown_timer.start()

    def _countdown_tick(self):
        self._lights[3 - self._countdown_count].setStyleSheet("background: #333; border-radius: 9px;")
        self._countdown_count -= 1

        if self._countdown_count == 0:
            self._countdown_timer.stop()
            if self._thread is not None:
                self._thread.start_recording(self._output_dir)
            else:
                self._stop_recording()

    @pyqtSlot()
    def _on_recording_started(self):
        self._waiting = False
        self._rec_start = time.perf_counter()
        self._rec_timer.start()
        self._set_ui_state("rec")

    def _update_time(self):
        elapsed = int(time.perf_counter() - self._rec_start)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        self.lbl_time.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _stop_recording(self):
        if self._thread is None:
            return
        self._waiting = False
        self._countdown_count = 0
        self._rec_start = 0.0
        self._countdown_timer.stop()
        self._rec_timer.stop()
        if self._thread.is_recording:
            self._thread.stop_recording()
        self._set_ui_state("preview")

    def _set_ui_state(self, state: str):
        is_preview = state == "preview"
        self.btn_start.setEnabled(is_preview)
        self.btn_stop.setEnabled(not is_preview)
        self.btn_mirror.setEnabled(is_preview)
        self.btn_canny.setEnabled(is_preview)
        self.chk_canny_record.setEnabled(is_preview and self.btn_canny.isChecked())

        if state == "wait":
            self.lbl_status.setText("⬤  WAIT")
            self.lbl_status.setStyleSheet("color: green; font-size: 14px; font-weight: bold;")
            for lbl in self._lights:
                lbl.setStyleSheet("background: #dd2200; border-radius: 9px;")
        elif state == "rec":
            self.lbl_status.setText("⬤  REC")
            self.lbl_status.setStyleSheet("color: red; font-size: 14px; font-weight: bold;")
        else:
            self.lbl_status.setText("⬤  PREVIEW")
            self.lbl_status.setStyleSheet("color: gray; font-size: 14px; font-weight: bold;")
            for lbl in self._lights:
                lbl.setStyleSheet("background: #333; border-radius: 9px;")

    def _toggle_mirror(self):
        if self._thread:
            self._thread.flip = self.btn_mirror.isChecked()

    def _toggle_canny(self):
        self.chk_canny_record.setEnabled(self.btn_canny.isChecked())
        if not self.btn_canny.isChecked():
            self.chk_canny_record.setChecked(False)

    def _toggle_canny_record(self):
        if self._thread:
            self._thread.canny_record = self.chk_canny_record.isChecked()

    # ── 키보드 단축키 ────────────────────────────────────────────
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._toggle_recording()
        elif event.key() == Qt.Key_Escape:
            self.close()

    # ── 종료 처리 ────────────────────────────────────────────────
    def closeEvent(self, event):
        self._stop_thread()
        event.accept()

    def _stop_thread(self):
        if self._thread is not None:
            self._countdown_timer.stop()
            self._rec_timer.stop()
            if self._thread.is_recording:
                self._thread.stop_recording()
            self._thread.stop()
            self._thread = None
