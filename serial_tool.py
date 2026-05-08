import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:
    serial = None
    SerialException = Exception
    list_ports = None


ANSI_PATTERN = re.compile(r"\x1b\[([0-9;]*)m")
ANSI_COLOR_MAP = {
    30: "#4b5563",
    31: "#ef4444",
    32: "#22c55e",
    33: "#eab308",
    34: "#3b82f6",
    35: "#d946ef",
    36: "#06b6d4",
    37: "#e5e7eb",
    90: "#9ca3af",
    91: "#f87171",
    92: "#4ade80",
    93: "#fde047",
    94: "#60a5fa",
    95: "#e879f9",
    96: "#22d3ee",
    97: "#f9fafb",
}

WINDOW_BG = "#161b22"
PANEL_BG = "#0d1117"
SUBTLE_BG = "#111827"
TEXT_COLOR = "#c9d1d9"
MUTED_TEXT = "#8b949e"
BORDER_COLOR = "#30363d"
ACCENT_COLOR = "#238636"
ACCENT_ACTIVE = "#2ea043"
BUTTON_BG = "#21262d"


class SerialToolApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("串口调试工具")
        self.root.geometry("980x780")
        self.root.minsize(860, 700)
        self.root.configure(bg=WINDOW_BG)

        self.serial_port = None
        self.reader_thread = None
        self.reader_running = False
        self.receive_queue = queue.Queue()
        self.advanced_window = None

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.bytesize_var = tk.StringVar(value="8")
        self.parity_var = tk.StringVar(value="N")
        self.stopbits_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(value="未连接")
        self.connect_button_var = tk.StringVar(value="打开串口")
        self.hex_send_var = tk.BooleanVar(value=False)
        self.hex_display_var = tk.BooleanVar(value=False)
        self.autoscroll_var = tk.BooleanVar(value=True)
        self.hex_line_open = False

        self._build_ui()
        self.refresh_ports()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.process_incoming_data)

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", background=WINDOW_BG, foreground=TEXT_COLOR, font=("Microsoft YaHei UI", 10))
        style.configure("App.TFrame", background=WINDOW_BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure(
            "Panel.TLabelframe",
            background=PANEL_BG,
            bordercolor=BORDER_COLOR,
            lightcolor=BORDER_COLOR,
            darkcolor=BORDER_COLOR,
            relief="solid",
            borderwidth=1,
            padding=8,
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background=PANEL_BG,
            foreground=TEXT_COLOR,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure("Panel.TLabel", background=PANEL_BG, foreground=TEXT_COLOR)
        style.configure("Muted.TLabel", background=PANEL_BG, foreground=MUTED_TEXT)
        style.configure(
            "Accent.TButton",
            background=ACCENT_COLOR,
            foreground="white",
            bordercolor=ACCENT_COLOR,
            lightcolor=ACCENT_COLOR,
            darkcolor=ACCENT_COLOR,
            relief="flat",
            padding=(10, 6),
        )
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE), ("pressed", ACCENT_ACTIVE)])
        style.configure(
            "Tool.TButton",
            background=BUTTON_BG,
            foreground=TEXT_COLOR,
            bordercolor=BORDER_COLOR,
            lightcolor=BORDER_COLOR,
            darkcolor=BORDER_COLOR,
            relief="flat",
            padding=(10, 6),
        )
        style.map("Tool.TButton", background=[("active", SUBTLE_BG)])
        style.configure(
            "Tool.TCheckbutton",
            background=PANEL_BG,
            foreground=TEXT_COLOR,
            indicatorcolor=BUTTON_BG,
            indicatormargin=4,
        )
        style.configure(
            "Tool.TCombobox",
            fieldbackground=SUBTLE_BG,
            background=SUBTLE_BG,
            foreground=TEXT_COLOR,
            bordercolor=BORDER_COLOR,
            lightcolor=BORDER_COLOR,
            darkcolor=BORDER_COLOR,
            insertcolor=TEXT_COLOR,
            padding=4,
            arrowsize=14,
        )
        style.map(
            "Tool.TCombobox",
            fieldbackground=[("readonly", SUBTLE_BG)],
            background=[("readonly", SUBTLE_BG)],
            foreground=[("readonly", TEXT_COLOR)],
        )

        main = ttk.Frame(self.root, padding=10, style="App.TFrame")
        main.pack(fill=tk.BOTH, expand=True)

        bottom_frame = ttk.Frame(main, style="App.TFrame")
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))

        output_frame = ttk.LabelFrame(main, text="串口输出", style="Panel.TLabelframe", padding=10)
        output_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.output_text = ScrolledText(
            output_frame,
            wrap=tk.WORD,
            height=20,
            font=("Consolas", 10),
            bg=SUBTLE_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor="#58a6ff",
            padx=8,
            pady=8,
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)
        self.output_text.configure(state=tk.DISABLED)
        self._configure_output_tags()

        output_toolbar = ttk.Frame(output_frame, style="Panel.TFrame")
        output_toolbar.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(output_toolbar, text="状态", style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Label(output_toolbar, textvariable=self.status_var, style="Panel.TLabel").pack(
            side=tk.LEFT, padx=(6, 18)
        )
        ttk.Checkbutton(
            output_toolbar, text="HEX显示", variable=self.hex_display_var, style="Tool.TCheckbutton"
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(
            output_toolbar, text="自动滚动", variable=self.autoscroll_var, style="Tool.TCheckbutton"
        ).pack(side=tk.LEFT)
        ttk.Button(output_toolbar, text="清空输出", command=self.clear_output, style="Tool.TButton").pack(
            side=tk.RIGHT
        )

        settings_frame = ttk.LabelFrame(bottom_frame, text="串口设置", style="Panel.TLabelframe")
        settings_frame.pack(fill=tk.X)

        self.connect_button = ttk.Button(
            settings_frame,
            textvariable=self.connect_button_var,
            command=self.toggle_port,
            width=12,
            style="Accent.TButton",
        )
        self.connect_button.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(settings_frame, text="串口", style="Panel.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.port_combo = ttk.Combobox(
            settings_frame,
            textvariable=self.port_var,
            width=24,
            state="readonly",
            style="Tool.TCombobox",
        )
        self.port_combo.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(settings_frame, text="刷新", command=self.refresh_ports, width=8, style="Tool.TButton").pack(
            side=tk.LEFT, padx=(0, 10)
        )

        ttk.Label(settings_frame, text="波特率", style="Panel.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        self.baud_combo = ttk.Combobox(
            settings_frame,
            textvariable=self.baud_var,
            values=["9600", "19200", "38400", "57600", "115200", "230400"],
            width=10,
            style="Tool.TCombobox",
        )
        self.baud_combo.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(
            settings_frame, text="更多设置", command=self.toggle_advanced_settings, width=10, style="Tool.TButton"
        ).pack(side=tk.LEFT)

        input_frame = ttk.LabelFrame(bottom_frame, text="串口输入", style="Panel.TLabelframe", padding=10)
        input_frame.pack(fill=tk.X, pady=(8, 0))
        input_frame.configure(height=230)
        input_frame.pack_propagate(False)

        input_layout = tk.Frame(input_frame, bg=PANEL_BG)
        input_layout.pack(fill=tk.BOTH, expand=True)

        self.input_text = ScrolledText(
            input_layout,
            wrap=tk.WORD,
            height=5,
            font=("Consolas", 10),
            bg=SUBTLE_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor="#58a6ff",
            padx=8,
            pady=8,
        )
        self.input_text.pack(fill=tk.BOTH, expand=True)

        input_toolbar = tk.Frame(input_layout, bg=PANEL_BG, height=44)
        input_toolbar.pack(fill=tk.X, pady=(10, 0), side=tk.BOTTOM)
        input_toolbar.pack_propagate(False)
        input_toolbar.grid_columnconfigure(0, weight=1)
        input_toolbar.grid_columnconfigure(1, weight=0)

        self.hex_send_check = tk.Checkbutton(
            input_toolbar,
            text="HEX发送",
            variable=self.hex_send_var,
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            activebackground=PANEL_BG,
            activeforeground=TEXT_COLOR,
            selectcolor=SUBTLE_BG,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 10),
        )
        self.hex_send_check.grid(row=0, column=0, sticky="w")

        action_frame = tk.Frame(input_toolbar, bg=PANEL_BG)
        action_frame.grid(row=0, column=1, sticky="e")

        self.clear_input_button = tk.Button(
            action_frame,
            text="清空输入",
            command=self.clear_input,
            width=10,
            bg=BUTTON_BG,
            fg=TEXT_COLOR,
            activebackground=SUBTLE_BG,
            activeforeground=TEXT_COLOR,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=6,
            font=("Microsoft YaHei UI", 10),
        )
        self.clear_input_button.pack(side=tk.LEFT, padx=(0, 8))

        self.send_button = tk.Button(
            action_frame,
            text="发送",
            command=self.send_data,
            width=10,
            bg=ACCENT_COLOR,
            fg="white",
            activebackground=ACCENT_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=6,
            font=("Microsoft YaHei UI", 10),
        )
        self.send_button.pack(side=tk.LEFT)

    def refresh_ports(self) -> None:
        if list_ports is None:
            self.port_combo["values"] = []
            self.status_var.set("未安装 pyserial，无法识别串口")
            return

        ports = list(list_ports.comports())
        port_names = [f"{port.device} - {port.description}" for port in ports]
        self.port_combo["values"] = port_names

        if port_names:
            current = self.port_var.get()
            if current not in port_names:
                self.port_var.set(port_names[0])
        else:
            self.port_var.set("")
            self.status_var.set("未检测到串口")

    def get_selected_port_device(self) -> str:
        selected = self.port_var.get().strip()
        if not selected:
            return ""
        return selected.split(" - ", 1)[0].strip()

    def open_port(self) -> None:
        if serial is None:
            messagebox.showerror("缺少依赖", "未安装 pyserial，请先执行: pip install pyserial")
            return

        port_device = self.get_selected_port_device()
        if not port_device:
            messagebox.showwarning("未选择串口", "请先选择一个串口")
            return

        if self.serial_port and self.serial_port.is_open:
            messagebox.showinfo("提示", "串口已经打开")
            return

        try:
            self.serial_port = serial.Serial(
                port=port_device,
                baudrate=int(self.baud_var.get()),
                bytesize=self._parse_bytesize(),
                parity=self._parse_parity(),
                stopbits=self._parse_stopbits(),
                timeout=0.2,
            )
        except (ValueError, SerialException) as exc:
            messagebox.showerror("打开失败", f"无法打开串口: {exc}")
            return

        self.reader_running = True
        self.reader_thread = threading.Thread(target=self.read_loop, daemon=True)
        self.reader_thread.start()
        self.status_var.set(f"已连接: {port_device}")
        self.connect_button_var.set("关闭串口")

    def close_port(self) -> None:
        self.reader_running = False

        if self.serial_port is not None:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except SerialException:
                pass

        self.serial_port = None
        self.status_var.set("未连接")
        self.connect_button_var.set("打开串口")

    def toggle_port(self) -> None:
        if self.serial_port and self.serial_port.is_open:
            self.close_port()
        else:
            self.open_port()

    def read_loop(self) -> None:
        while self.reader_running and self.serial_port and self.serial_port.is_open:
            try:
                data = self.serial_port.read(self.serial_port.in_waiting or 1)
            except SerialException as exc:
                self.receive_queue.put(("error", f"读取失败: {exc}"))
                break

            if data:
                self.receive_queue.put(("data", data))

        self.reader_running = False

    def process_incoming_data(self) -> None:
        while not self.receive_queue.empty():
            message_type, payload = self.receive_queue.get()
            if message_type == "error":
                self.append_output(f"[错误] {payload}\n")
                self.close_port()
            else:
                self.append_output(self._format_bytes(payload))

        self.root.after(100, self.process_incoming_data)

    def send_data(self) -> None:
        if not self.serial_port or not self.serial_port.is_open:
            messagebox.showwarning("未连接", "请先打开串口")
            return

        raw_text = self.input_text.get("1.0", tk.END).strip()
        if not raw_text:
            messagebox.showwarning("无发送内容", "请输入要发送的数据")
            return

        try:
            payload = self._build_payload(raw_text)
            self.serial_port.write(payload)
        except (ValueError, SerialException) as exc:
            messagebox.showerror("发送失败", str(exc))
            return

        preview = payload.hex(" ").upper() if self.hex_send_var.get() else raw_text
        self.append_output(f"[发送] {preview}\n")

    def append_output(self, text: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self._insert_ansi_text(text)
        self.output_text.configure(state=tk.DISABLED)
        if self.autoscroll_var.get():
            self.output_text.see(tk.END)

    def clear_output(self) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.configure(state=tk.DISABLED)
        self.hex_line_open = False

    def clear_input(self) -> None:
        self.input_text.delete("1.0", tk.END)

    def toggle_advanced_settings(self) -> None:
        if self.advanced_window and self.advanced_window.winfo_exists():
            self.advanced_window.lift()
            self.advanced_window.focus_force()
            return

        self.advanced_window = tk.Toplevel(self.root)
        self.advanced_window.title("更多串口设置")
        self.advanced_window.resizable(False, False)
        self.advanced_window.transient(self.root)
        self.advanced_window.configure(bg=WINDOW_BG)

        style = ttk.Style(self.advanced_window)
        container = ttk.Frame(self.advanced_window, padding=12, style="App.TFrame")
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="数据位", style="Panel.TLabel").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Combobox(
            container,
            textvariable=self.bytesize_var,
            values=["5", "6", "7", "8"],
            width=8,
            state="readonly",
            style="Tool.TCombobox",
        ).grid(row=0, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(container, text="校验位", style="Panel.TLabel").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        ttk.Combobox(
            container,
            textvariable=self.parity_var,
            values=["N", "E", "O", "M", "S"],
            width=8,
            state="readonly",
            style="Tool.TCombobox",
        ).grid(row=1, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(container, text="停止位", style="Panel.TLabel").grid(row=2, column=0, padx=6, pady=6, sticky="w")
        ttk.Combobox(
            container,
            textvariable=self.stopbits_var,
            values=["1", "1.5", "2"],
            width=8,
            state="readonly",
            style="Tool.TCombobox",
        ).grid(row=2, column=1, padx=6, pady=6, sticky="w")

        ttk.Button(container, text="关闭", command=self.close_advanced_window, width=10, style="Tool.TButton").grid(
            row=3, column=1, padx=6, pady=(12, 0), sticky="e"
        )

        self.advanced_window.protocol("WM_DELETE_WINDOW", self.close_advanced_window)

    def close_advanced_window(self) -> None:
        if self.advanced_window and self.advanced_window.winfo_exists():
            self.advanced_window.destroy()
        self.advanced_window = None

    def _configure_output_tags(self) -> None:
        self.output_text.tag_configure("ansi_default", foreground=TEXT_COLOR)
        self.output_text.tag_configure("ansi_bold", font=("Consolas", 10, "bold"))
        for code, color in ANSI_COLOR_MAP.items():
            self.output_text.tag_configure(f"ansi_fg_{code}", foreground=color)

    def _insert_ansi_text(self, text: str) -> None:
        current_tags = ["ansi_default"]
        last_index = 0

        for match in ANSI_PATTERN.finditer(text):
            if match.start() > last_index:
                self.output_text.insert(tk.END, text[last_index:match.start()], tuple(current_tags))

            current_tags = self._apply_ansi_codes(match.group(1), current_tags)
            last_index = match.end()

        if last_index < len(text):
            self.output_text.insert(tk.END, text[last_index:], tuple(current_tags))

    def _apply_ansi_codes(self, code_string: str, current_tags: list[str]) -> list[str]:
        codes = [0] if code_string == "" else [int(part) for part in code_string.split(";") if part]

        tags = [tag for tag in current_tags if tag == "ansi_default" or tag == "ansi_bold"]

        for code in codes:
            if code == 0:
                tags = ["ansi_default"]
            elif code == 1:
                if "ansi_bold" not in tags:
                    tags.append("ansi_bold")
            elif code == 22:
                tags = [tag for tag in tags if tag != "ansi_bold"]
                if "ansi_default" not in tags:
                    tags.insert(0, "ansi_default")
            elif code == 39:
                tags = [tag for tag in tags if not tag.startswith("ansi_fg_")]
                if "ansi_default" not in tags:
                    tags.insert(0, "ansi_default")
            elif code in ANSI_COLOR_MAP:
                tags = [tag for tag in tags if not tag.startswith("ansi_fg_")]
                tags.append(f"ansi_fg_{code}")

        return tags or ["ansi_default"]

    def _format_bytes(self, data: bytes) -> str:
        if self.hex_display_var.get():
            hex_text = data.hex(" ").upper()
            if not hex_text:
                return ""

            prefix = "" if not self.hex_line_open else " "
            self.hex_line_open = True

            if data.endswith((b"\n", b"\r")):
                self.hex_line_open = False
                return prefix + hex_text + "\n"

            return prefix + hex_text

        self.hex_line_open = False

        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return repr(data) + "\n"

    def _build_payload(self, raw_text: str) -> bytes:
        if self.hex_send_var.get():
            hex_text = raw_text.replace("\n", " ").replace("\r", " ")
            return bytes.fromhex(hex_text)
        return raw_text.encode("utf-8")

    def _parse_bytesize(self) -> int:
        return int(self.bytesize_var.get())

    def _parse_parity(self) -> str:
        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
            "M": serial.PARITY_MARK,
            "S": serial.PARITY_SPACE,
        }
        return parity_map[self.parity_var.get()]

    def _parse_stopbits(self) -> float:
        stopbits_map = {
            "1": serial.STOPBITS_ONE,
            "1.5": serial.STOPBITS_ONE_POINT_FIVE,
            "2": serial.STOPBITS_TWO,
        }
        return stopbits_map[self.stopbits_var.get()]

    def on_close(self) -> None:
        self.close_advanced_window()
        self.close_port()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SerialToolApp(root)
    if serial is None:
        messagebox.showwarning(
            "依赖未安装",
            "当前环境未安装 pyserial。\n请先执行: pip install pyserial\n安装后重新启动程序。",
        )
    root.mainloop()


if __name__ == "__main__":
    main()
