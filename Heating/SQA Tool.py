# SQA_heating_tool.py
# ------------------------------------------------------------
# 시리얼(Serial)로 Heating/Puff 장치를 제어하는 Tkinter GUI 툴
# - 모델별(ETTR, UP30) 상이한 명령을 버튼/직접입력으로 전송
# - 수신 스레드로 장치 응답을 비동기 수신하여 로그창에 출력
# - 포트 자동 재연결(Write 중 예외시) 등 안정화 처리 포함
# ------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import serial
from serial import SerialException
from serial.tools import list_ports
import threading
import time


class SerialManager:
    """
    시리얼 포트 IO를 전담하는 클래스.
    - GUI 스레드와 분리된 수신 스레드(_read_loop) 운용
    - 쓰기/읽기 동시 접근을 위한 Lock 사용
    - 전송 예외 발생 시 포트 자동 재연결 시도(안정화)
    """
    def __init__(self, on_rx_callback):
        # 현재 열린 serial.Serial 인스턴스 (없으면 None)
        self.ser = None
        # 수신 스레드 종료 신호 플래그
        self._stop_event = threading.Event()
        # 수신 스레드 핸들
        self._rx_thread = None
        # 바깥(App)에서 넘겨받은 수신 콜백 (bytes -> 처리)
        self.on_rx_callback = on_rx_callback
        # 동시 접근 제어(쓰기/읽기)용 Lock
        self._lock = threading.Lock()

    def open(self, port, baudrate=115200):
        """
        지정 포트를 열고 수신 스레드를 기동한다.
        이미 열려 있으면 예외를 던진다.
        """
        if self.is_open():
            raise SerialException("이미 포트가 열려 있습니다.")
        # timeout을 짧게 두어 read 루프가 응답성을 갖게 함
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=0.1)

        # 이전 버퍼 잔여 데이터/출력 버퍼 비우기
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        # 수신 스레드 시작
        self._stop_event.clear()
        self._rx_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._rx_thread.start()

    def close(self):
        """
        포트를 닫고 수신 스레드를 종료한다.
        """
        # 수신 스레드 종료 요청
        self._stop_event.set()
        # 스레드가 살아있다면 1초까지 대기
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        # 포트가 열려있다면 닫기
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def is_open(self):
        """
        포트가 열려있는지 여부 반환.
        """
        return self.ser is not None and self.ser.is_open

    def send(self, text: str, model: str = ""):
        """
        텍스트 명령을 시리얼로 전송한다.
        - 모델별로 후행 제어문자 처리(UP30은 \r 필요)
        - 전송 중 예외 발생 시 포트를 재연결 시도하여 안정화
        - 약간의 sleep을 두어 장치가 응답할 시간을 확보
        """
        if not self.is_open():
            raise SerialException("포트가 열려 있지 않습니다.")

        # 모델별 명령 포맷(후행 CR 등) 처리
        if model == "UP30":
            # UP30은 CR(Carriage Return) 필요
            if not text.endswith("\r"):
                text += "\r"

        # UTF-8 바이트로 인코딩
        payload = text.encode("utf-8")

        # 쓰기 작업은 Lock으로 보호 (동시 접근 방지)
        with self._lock:
            try:
                # 포트에 바이트 쓰기
                self.ser.write(payload)
                # OS 버퍼에 남은 데이터 즉시 전송
                self.ser.flush()

                # 모델 특성에 따라 응답 대기시간을 여유 있게 준다.
                # (ETTR은 빠르고, UP30은 상대적으로 느린 응답 경향)
                delay = 0.3 if model == "ETTR" else 0.6
                time.sleep(delay)

            except Exception as e:
                # 쓰기 중 오류(케이블 접촉 불량/일시적 드라이버 에러 등) 발생 시
                # 현재 포트명을 기억해두고 재연결을 시도
                try:
                    port_name = self.ser.port
                    self.close()
                    time.sleep(1.0)  # 재오픈 전 약간의 텀
                    self.open(port_name, baudrate=115200)
                    # 호출 측에서 알 수 있도록 예외 전달
                    raise SerialException(f"WriteFile 실패 (포트 재연결 시도): {e}")
                except Exception as e2:
                    # 재연결 자체가 실패한 경우
                    raise SerialException(f"포트 재연결 실패: {e2}")

    def _read_loop(self):
        """
        백그라운드 수신 스레드:
        - in_waiting 바이트가 있으면 읽어서 라인 단위로 분할
        - 개행('\n') 기준으로 메시지 라인화하여 on_rx_callback에 전달
        - 메인 스레드와의 충돌을 피하기 위해 읽기만 수행
        """
        buffer = b""
        while not self._stop_event.is_set():
            try:
                if self.ser and self.ser.in_waiting:
                    # 읽기 또한 Lock으로 보호(동시 쓰기와 꼬임 방지)
                    with self._lock:
                        data = self.ser.read(self.ser.in_waiting)
                    if data:
                        buffer += data
                        # 장치 대부분이 '\n' 로 라인 종료 → 라인 분리 처리
                        if b"\n" in buffer:
                            lines = buffer.split(b"\n")
                            # 마지막 조각은 미완 라인일 수 있으므로 남겨둠
                            for line in lines[:-1]:
                                # 콜백은 bytes를 그대로 전달(상위에서 디코딩)
                                self.on_rx_callback(line.strip())
                            buffer = lines[-1]
                else:
                    # 바이트 없으면 과도한 CPU 점유 방지
                    time.sleep(0.03)
            except SerialException:
                # 포트가 닫히거나 치명적 오류 → 루프 탈출
                break


class App(tk.Tk):
    """
    Tkinter 기반 GUI 애플리케이션.
    - 포트 선택/연결, 모델 선택, 명령 버튼, 로그창 구성
    - SerialManager를 소유하고 UI 이벤트 → 전송 로직을 호출
    """
    def __init__(self):
        super().__init__()
        self.title("SQA Heating Device Test Tool")
        self.geometry("800x700")

        # 시리얼 매니저 생성 (수신 콜백: _on_serial_data)
        self.serial_mgr = SerialManager(on_rx_callback=self._on_serial_data)

        # UI에서 사용할 변수들
        self.var_port = tk.StringVar()                 # 선택 포트명
        self.var_status = tk.StringVar(value="Disconnected")  # 상태 표시
        self.var_input = tk.StringVar()                # 직접 입력 명령어
        self.var_model = tk.StringVar(value="ETTR")    # 선택 모델(기본 ETTR)

        # 모델별 명령어 사전
        # 동일 기능 키(heat_start 등)로 모델마다 실제 명령 문자열 매핑
        self.models = {
            "ETTR": {
                "heat_start":   "heat start",
                "heat_stop":    "heat stop",
                "puff_start":   "ph on",
                "puff_stop":    "ph off",
                "version_check":"version",
                "flash_info":   "flr",
            },
            "UP30": {
                "heat_start":   "h on",
                "heat_stop":    "h off",
                "puff_start":   "p o",
                "puff_stop":    "p x",
                "version_check":"flash print all",
                "flash_info":   "flash print all",
            },
        }

        # UI 구성 및 초기 상태 세팅
        self._build_ui()
        self._refresh_ports()
        self._enable_command_widgets(False)
        # 창 닫기(X) 처리 → 포트 정리 후 종료
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        """
        상단 포트/연결/모델 영역 + 명령 버튼 + 직접입력 + 로그창을 구성.
        """
        # 상단 바
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        # 포트 선택 콤보
        ttk.Label(top, text="Port:").grid(row=0, column=0, sticky="w")
        self.cb_port = ttk.Combobox(top, textvariable=self.var_port, width=25, state="readonly")
        self.cb_port.grid(row=0, column=1, padx=6)

        # 포트 새로고침 버튼
        ttk.Button(top, text="새로고침", command=self._refresh_ports).grid(row=0, column=2, padx=6)

        # 연결/해제 버튼 (토글)
        self.btn_connect = ttk.Button(top, text="연결", command=self._toggle_connection)
        self.btn_connect.grid(row=0, column=3, padx=6)

        # 상태 라벨
        ttk.Label(top, textvariable=self.var_status, foreground="green").grid(row=0, column=4, padx=10)

        # 고정 BAUD 안내
        ttk.Label(top, text="Baudrate: 115200 (자동 안정화)").grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # 모델 선택 콤보
        ttk.Label(top, text="모델 선택:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.cb_model = ttk.Combobox(top, textvariable=self.var_model, values=list(self.models.keys()), state="readonly")
        self.cb_model.grid(row=2, column=1, padx=6, pady=(6, 0))
        # 모델 변경 시 단순 로그 출력
        self.cb_model.bind("<<ComboboxSelected>>", lambda e: self._log(f"[INFO] 모델 변경됨 → {self.var_model.get()}"))

        # 구분선
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # 제어 버튼 그룹 (Heating / Puff)
        cmd_frame = ttk.LabelFrame(self, text="Heating / Puff 제어", padding=10)
        cmd_frame.pack(fill=tk.X, padx=10, pady=5)

        # 버튼 생성: 각 버튼은 공용 핸들러 _send_cmd 를 특정 키와 함께 호출
        self.btn_heat_start = ttk.Button(cmd_frame, text="Heating Start", command=lambda: self._send_cmd("heat_start"))
        self.btn_heat_stop  = ttk.Button(cmd_frame, text="Heating Stop",  command=lambda: self._send_cmd("heat_stop"))
        self.btn_puff_start = ttk.Button(cmd_frame, text="Puff Start",    command=lambda: self._send_cmd("puff_start"))
        self.btn_puff_stop  = ttk.Button(cmd_frame, text="Puff Stop",     command=lambda: self._send_cmd("puff_stop"))

        # 그리드 배치
        self.btn_heat_start.grid(row=0, column=0, padx=10, pady=5)
        self.btn_heat_stop.grid(row=0,  column=1, padx=10, pady=5)
        self.btn_puff_start.grid(row=0, column=2, padx=10, pady=5)
        self.btn_puff_stop.grid(row=0,  column=3, padx=10, pady=5)

        # 추가 명령 그룹 (버전/Flash)
        extra_cmd_frame = ttk.LabelFrame(self, text="추가 명령", padding=10)
        extra_cmd_frame.pack(fill=tk.X, padx=10, pady=5)

        self.btn_version_check = ttk.Button(extra_cmd_frame, text="버전체크",  command=lambda: self._send_cmd("version_check"))
        self.btn_flash_info    = ttk.Button(extra_cmd_frame, text="Flash Info", command=lambda: self._send_cmd("flash_info"))

        self.btn_version_check.grid(row=0, column=0, padx=10, pady=5)
        self.btn_flash_info.grid(row=0,    column=1, padx=10, pady=5)

        # 직접 명령 입력 영역
        input_frame = ttk.LabelFrame(self, text="직접 명령 입력", padding=10)
        input_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(input_frame, text="명령어:").grid(row=0, column=0, padx=5)
        self.entry_cmd = ttk.Entry(input_frame, textvariable=self.var_input, width=50)
        self.entry_cmd.grid(row=0, column=1, padx=5, pady=4)

        # 엔터 키로도 전송되도록 바인딩
        self.entry_cmd.bind("<Return>", lambda e: self._send_input_cmd())
        ttk.Button(input_frame, text="보내기", command=self._send_input_cmd).grid(row=0, column=2, padx=5)

        # 구분선
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # 로그 영역 (수신 메시지 표시)
        log_frame = ttk.Frame(self, padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)

        # 헤더(제목/지우기 버튼)
        header = ttk.Frame(log_frame)
        header.pack(fill=tk.X)
        ttk.Label(header, text="수신 로그:").pack(side=tk.LEFT)
        ttk.Button(header, text="로그 지우기", command=self._clear_log).pack(side=tk.RIGHT)

        # 스크롤 가능한 텍스트 박스 (읽기 전용)
        self.txt_log = ScrolledText(log_frame, height=20, state="disabled")
        self.txt_log.pack(fill=tk.BOTH, expand=True, pady=5)

    # ---------------- Serial ----------------
    def _refresh_ports(self):
        """
        현재 PC에 연결된 COM 포트 목록을 가져와 콤보박스에 반영.
        첫 번째 포트를 기본 선택으로 지정.
        """
        ports = [p.device for p in list_ports.comports()]
        self.cb_port["values"] = ports
        self.var_port.set(ports[0] if ports else "")
        self._log(f"[INFO] 사용 가능한 포트: {ports}")

    def _toggle_connection(self):
        """
        연결 버튼 토글 동작: 연결되어 있으면 해제, 아니면 연결.
        """
        if self.serial_mgr.is_open():
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        """
        선택된 포트로 연결을 시도하고, 성공 시 UI 상태를 갱신.
        """
        port = self.var_port.get().strip()
        if not port:
            messagebox.showwarning("경고", "포트를 선택하세요.")
            return
        try:
            self.serial_mgr.open(port, baudrate=115200)
        except Exception as e:
            messagebox.showerror("연결 실패", f"{e}")
            return

        # 상태 표시 및 버튼/입력 활성화
        self.var_status.set(f"Connected ({port})")
        self.btn_connect.config(text="해제")
        self._enable_command_widgets(True)
        self._log(f"[OK] 연결됨: {port} @115200bps")

    def _disconnect(self):
        """
        포트 연결을 해제하고 UI 상태를 갱신.
        """
        self.serial_mgr.close()
        self.var_status.set("Disconnected")
        self.btn_connect.config(text="연결")
        self._enable_command_widgets(False)
        self._log("[OK] 연결 해제")

    def _enable_command_widgets(self, enabled):
        """
        연결 여부에 따라 제어 버튼/입력창 활성/비활성 처리.
        """
        state = "normal" if enabled else "disabled"
        for b in [self.btn_heat_start, self.btn_heat_stop, self.btn_puff_start,
                  self.btn_puff_stop, self.btn_version_check, self.btn_flash_info]:
            b.config(state=state)
        self.entry_cmd.config(state=state)

    # ---------------- 전송 ----------------
    def _send_cmd(self, cmd_key):
        """
        공용 명령 전송 핸들러(버튼에서 호출):
        - 현재 선택된 모델에서 cmd_key에 매핑된 실제 문자열을 찾아 전송.
        """
        model = self.var_model.get()
        cmd = self.models.get(model, {}).get(cmd_key)
        if not cmd:
            messagebox.showerror("오류", f"{cmd_key} 명령이 정의되지 않았습니다.")
            return
        try:
            self.serial_mgr.send(cmd, model=model)
            self._log(f">>> {cmd} ({model})")
        except Exception as e:
            messagebox.showerror("전송 실패", f"{e}")

    def _send_input_cmd(self):
        """
        직접 입력 명령 전송:
        - 입력창의 문자열을 그대로 전송(모델 후행처리는 SerialManager에서 수행).
        - 전송 성공 시 입력창 비움.
        """
        cmd = self.var_input.get().strip()
        if not cmd:
            return
        model = self.var_model.get()
        try:
            self.serial_mgr.send(cmd, model=model)
            self._log(f">>> {cmd} (직접 입력, {model})")
            self.var_input.set("")
        except Exception as e:
            messagebox.showerror("전송 실패", f"{e}")

    # ---------------- 수신 처리 ----------------
    def _on_serial_data(self, data: bytes):
        """
        수신 스레드로부터 bytes를 받아 UTF-8로 디코딩하고 로그창에 출력.
        - GUI 위젯 조작은 메인 스레드에서 해야 하므로 after() 사용.
        """
        try:
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            # 혹시 디코딩 실패시 repr로 안전 출력
            text = repr(data)
        # 메인 스레드에서 _log 호출
        self.txt_log.after(0, lambda: self._log(f"<<< {text}"))

    def _log(self, msg):
        """
        로그창(ScrolledText)에 한 줄을 추가하고 자동 스크롤.
        """
        self.txt_log.config(state="normal")
        self.txt_log.insert(tk.END, f"{msg}\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")

    def _clear_log(self):
        """
        로그창 내용 전체 삭제.
        """
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state="disabled")

    def _on_close(self):
        """
        창 닫기(X) 이벤트: 포트/스레드 정리 후 종료.
        """
        try:
            self.serial_mgr.close()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    # 애플리케이션 진입점
    app = App()
    app.mainloop()
