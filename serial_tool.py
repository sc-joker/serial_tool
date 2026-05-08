import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
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
HIGHLIGHT_COLOR = "#58a6ff"
LEFT_PANE_MIN_WIDTH = 860
RIGHT_PANE_MIN_WIDTH = 280
APP_DIR_NAME = "SerialTool"
CONFIG_FILE_NAME = "serial_tool_config.json"
MAX_DISPLAY_LINES = 30000
MAX_TEXT_BUFFER_BYTES = 50 * 1024
TEXT_BUFFER_FLUSH_MS = 200


def get_preferred_config_dir() -> str:
    base_dir = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA") or os.getcwd()
    return os.path.join(base_dir, APP_DIR_NAME)


def get_config_file_path(create_dir: bool = False) -> str:
    config_dir = get_preferred_config_dir()
    if create_dir:
        try:
            os.makedirs(config_dir, exist_ok=True)
        except OSError:
            return os.path.join(os.getcwd(), CONFIG_FILE_NAME)
    return os.path.join(config_dir, CONFIG_FILE_NAME)


class SerialToolApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("串口调试工具")
        self.root.geometry("1220x780")
        self.root.minsize(1160, 700)
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
        self.quick_panel_button_var = tk.StringVar(value="显示快捷命令")
        self.realtime_log_button_var = tk.StringVar(value="实时保存")
        self.input_panel_button_var = tk.StringVar(value="▾")
        self.file_send_button_var = tk.StringVar(value="发送文件")
        self.hex_send_var = tk.BooleanVar(value=False)
        self.hex_display_var = tk.BooleanVar(value=False)
        self.autoscroll_var = tk.BooleanVar(value=True)
        self.show_sent_data_var = tk.BooleanVar(value=True)
        self.input_mode_var = tk.StringVar(value="command")
        self.file_send_mode_var = tk.StringVar(value="continuous")
        self.file_chunk_size_var = tk.StringVar(value="500")
        self.file_pause_ms_var = tk.StringVar(value="100")
        self.file_progress_var = tk.StringVar(value="未开始")
        self.selected_file_var = tk.StringVar(value="未选择文件")

        self.hex_line_open = False
        self.text_receive_buffer = b""
        self.text_receive_last_update = 0.0
        self.quick_panel_visible = False
        self.input_panel_visible = True
        self.quick_commands = []
        self.config_loaded = False
        self.output_history = []
        self.realtime_log_enabled = False
        self.realtime_log_path = ""
        self.file_send_in_progress = False
        self.file_send_total_bytes = 0
        self.selected_file_path = ""
        self.file_send_stop_requested = False

        self._build_ui()
        self.load_config()
        self.update_file_send_mode()
        self.update_input_mode()
        self._bind_config_traces()
        self.config_loaded = True
        self.refresh_ports()
        if not self.quick_commands:
            self.add_quick_command("AT", save=False)
            self.add_quick_command("41 54", save=False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.process_incoming_data)

    def _build_ui(self) -> None:
        self._configure_styles()

        main = ttk.Frame(self.root, padding=10, style="App.TFrame")
        main.pack(fill=tk.BOTH, expand=True)

        self.main_paned = tk.PanedWindow(
            main,
            orient=tk.HORIZONTAL,
            sashwidth=6,
            sashrelief=tk.FLAT,
            bg=WINDOW_BG,
            bd=0,
            opaqueresize=True,
            showhandle=False,
        )
        self.main_paned.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(self.main_paned, style="App.TFrame")
        left_frame.grid_rowconfigure(0, weight=1)
        left_frame.grid_rowconfigure(1, weight=0)
        left_frame.grid_columnconfigure(0, weight=1)
        self.main_paned.add(left_frame, minsize=LEFT_PANE_MIN_WIDTH, stretch="always")

        output_frame = ttk.LabelFrame(left_frame, text="串口输出", style="Panel.TLabelframe", padding=10)
        output_frame.grid(row=0, column=0, sticky="nsew")
        output_frame.grid_rowconfigure(0, weight=1)
        output_frame.grid_columnconfigure(0, weight=1)

        self.output_text = ScrolledText(
            output_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg=SUBTLE_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=HIGHLIGHT_COLOR,
            padx=8,
            pady=8,
        )
        self.output_text.grid(row=0, column=0, sticky="nsew")
        self.output_text.configure(state=tk.DISABLED)
        self._configure_output_tags()

        output_toolbar = ttk.Frame(output_frame, style="Panel.TFrame")
        output_toolbar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
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
        ttk.Checkbutton(
            output_toolbar, text="显示发送", variable=self.show_sent_data_var, style="Tool.TCheckbutton"
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(
            output_toolbar,
            textvariable=self.realtime_log_button_var,
            command=self.toggle_realtime_log,
            style="Tool.TButton",
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            output_toolbar, text="保存当前日志", command=self.save_current_log, style="Tool.TButton"
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(output_toolbar, text="清空输出", command=self.clear_output, style="Tool.TButton").pack(
            side=tk.RIGHT
        )

        bottom_frame = ttk.Frame(left_frame, style="App.TFrame")
        bottom_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bottom_frame.grid_columnconfigure(0, weight=1)

        settings_frame = ttk.LabelFrame(bottom_frame, text="串口设置", style="Panel.TLabelframe", padding=8)
        settings_frame.grid(row=0, column=0, sticky="ew")

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
            values=[
                "1200",
                "2400",
                "4800",
                "9600",
                "19200",
                "38400",
                "57600",
                "115200",
                "230400",
                "460800",
                "921600",
            ],
            width=10,
            style="Tool.TCombobox",
        )
        self.baud_combo.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(
            settings_frame,
            text="更多设置",
            command=self.toggle_advanced_settings,
            width=10,
            style="Tool.TButton",
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(
            settings_frame,
            textvariable=self.quick_panel_button_var,
            command=self.toggle_quick_panel,
            width=14,
            style="Tool.TButton",
        ).pack(side=tk.RIGHT)

        self.input_section = ttk.Frame(bottom_frame, style="App.TFrame")
        self.input_section.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.input_section.grid_columnconfigure(0, weight=1)

        input_header = tk.Frame(
            self.input_section,
            bg=PANEL_BG,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            padx=10,
            pady=6,
        )
        input_header.grid(row=0, column=0, sticky="ew")
        input_header.grid_columnconfigure(0, weight=1)

        tk.Label(
            input_header,
            text="串口输入",
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            anchor="w",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.input_toggle_button = tk.Button(
            input_header,
            textvariable=self.input_panel_button_var,
            command=self.toggle_input_panel,
            width=3,
            bg=BUTTON_BG,
            fg=TEXT_COLOR,
            activebackground=SUBTLE_BG,
            activeforeground=TEXT_COLOR,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=4,
            pady=2,
            font=("Microsoft YaHei UI", 9),
        )
        self.input_toggle_button.grid(row=0, column=1, sticky="e")

        self.input_frame = tk.Frame(
            self.input_section,
            bg=PANEL_BG,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            padx=10,
            pady=10,
            height=230,
        )
        self.input_frame.grid(row=1, column=0, sticky="ew")
        self.input_frame.grid_propagate(False)
        self.input_frame.grid_rowconfigure(0, weight=1)
        self.input_frame.grid_columnconfigure(0, weight=1)

        mode_toolbar = tk.Frame(self.input_frame, bg=PANEL_BG, height=36)
        mode_toolbar.grid(row=0, column=0, sticky="ew")
        mode_toolbar.grid_propagate(False)

        tk.Radiobutton(
            mode_toolbar,
            text="命令模式",
            variable=self.input_mode_var,
            value="command",
            command=self.update_input_mode,
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            activebackground=PANEL_BG,
            activeforeground=TEXT_COLOR,
            selectcolor=SUBTLE_BG,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(side=tk.LEFT)

        tk.Radiobutton(
            mode_toolbar,
            text="文件模式",
            variable=self.input_mode_var,
            value="file",
            command=self.update_input_mode,
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            activebackground=PANEL_BG,
            activeforeground=TEXT_COLOR,
            selectcolor=SUBTLE_BG,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.command_mode_frame = tk.Frame(self.input_frame, bg=PANEL_BG)
        self.command_mode_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.command_mode_frame.grid_rowconfigure(0, weight=1)
        self.command_mode_frame.grid_rowconfigure(1, weight=0)
        self.command_mode_frame.grid_columnconfigure(0, weight=1)

        self.input_text = ScrolledText(
            self.command_mode_frame,
            wrap=tk.WORD,
            height=5,
            font=("Consolas", 10),
            bg=SUBTLE_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=HIGHLIGHT_COLOR,
            padx=8,
            pady=8,
        )
        self.input_text.grid(row=0, column=0, sticky="nsew")

        input_toolbar = tk.Frame(self.command_mode_frame, bg=PANEL_BG, height=44)
        input_toolbar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        input_toolbar.grid_propagate(False)
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

        self.file_mode_frame = tk.Frame(self.input_frame, bg=PANEL_BG)
        self.file_mode_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.file_mode_frame.grid_columnconfigure(1, weight=1)

        top_file_row = tk.Frame(self.file_mode_frame, bg=PANEL_BG)
        top_file_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_file_row.grid_columnconfigure(1, weight=1)

        self.choose_file_button = tk.Button(
            top_file_row,
            text="选择文件",
            command=self.choose_binary_file,
            bg=BUTTON_BG,
            fg=TEXT_COLOR,
            activebackground=SUBTLE_BG,
            activeforeground=TEXT_COLOR,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=5,
            font=("Microsoft YaHei UI", 10),
        )
        self.choose_file_button.grid(row=0, column=0, sticky="w")

        tk.Label(
            top_file_row,
            textvariable=self.selected_file_var,
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            anchor="w",
            font=("Consolas", 9),
            width=42,
        ).grid(row=0, column=1, sticky="ew", padx=(10, 0))

        mode_row = tk.Frame(self.file_mode_frame, bg=PANEL_BG)
        mode_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        tk.Radiobutton(
            mode_row,
            text="连续发送",
            variable=self.file_send_mode_var,
            value="continuous",
            command=self.update_file_send_mode,
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            activebackground=PANEL_BG,
            activeforeground=TEXT_COLOR,
            selectcolor=SUBTLE_BG,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(side=tk.LEFT)

        tk.Radiobutton(
            mode_row,
            text="分块发送",
            variable=self.file_send_mode_var,
            value="chunked",
            command=self.update_file_send_mode,
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            activebackground=PANEL_BG,
            activeforeground=TEXT_COLOR,
            selectcolor=SUBTLE_BG,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.chunk_config_row = tk.Frame(mode_row, bg=PANEL_BG)
        self.chunk_config_row.pack(side=tk.LEFT, padx=(12, 0))

        tk.Label(
            self.chunk_config_row,
            text="每",
            bg=PANEL_BG,
            fg=MUTED_TEXT,
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.file_chunk_entry = tk.Entry(
            self.chunk_config_row,
            textvariable=self.file_chunk_size_var,
            width=8,
            bg=SUBTLE_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=HIGHLIGHT_COLOR,
            font=("Consolas", 10),
        )
        self.file_chunk_entry.pack(side=tk.LEFT)

        tk.Label(
            self.chunk_config_row,
            text="字节暂停",
            bg=PANEL_BG,
            fg=MUTED_TEXT,
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(6, 4))

        self.file_pause_entry = tk.Entry(
            self.chunk_config_row,
            textvariable=self.file_pause_ms_var,
            width=8,
            bg=SUBTLE_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=HIGHLIGHT_COLOR,
            font=("Consolas", 10),
        )
        self.file_pause_entry.pack(side=tk.LEFT)

        tk.Label(
            self.chunk_config_row,
            text="ms",
            bg=PANEL_BG,
            fg=MUTED_TEXT,
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(6, 0))

        progress_row = tk.Frame(self.file_mode_frame, bg=PANEL_BG)
        progress_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        tk.Label(
            progress_row,
            text="发送进度",
            bg=PANEL_BG,
            fg=MUTED_TEXT,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=0, sticky="w")

        progress_value_row = tk.Frame(progress_row, bg=PANEL_BG, width=860, height=24)
        progress_value_row.grid(row=0, column=1, sticky="w", padx=(10, 0))
        progress_value_row.grid_propagate(False)

        self.file_progress_bar = ttk.Progressbar(
            progress_value_row,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            length=520,
            style="FileSend.Horizontal.TProgressbar",
        )
        self.file_progress_bar.place(x=0, y=2, width=520, height=20)

        tk.Label(
            progress_value_row,
            textvariable=self.file_progress_var,
            bg=PANEL_BG,
            fg=TEXT_COLOR,
            anchor="w",
            font=("Consolas", 10),
            width=32,
        ).place(x=528, y=0, width=300, height=24)

        action_row = tk.Frame(self.file_mode_frame, bg=PANEL_BG)
        action_row.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))

        self.send_file_button = tk.Button(
            action_row,
            textvariable=self.file_send_button_var,
            command=self.toggle_binary_file_send,
            bg=ACCENT_COLOR,
            fg="white",
            activebackground=ACCENT_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=12,
            pady=6,
            font=("Microsoft YaHei UI", 10),
        )
        self.send_file_button.pack(side=tk.RIGHT)

        self.update_file_send_mode()
        self.update_input_mode()

        self.quick_panel = ttk.LabelFrame(self.main_paned, text="快捷命令", style="Panel.TLabelframe", padding=10)
        self.quick_panel.configure(width=320)
        self.quick_panel.grid_rowconfigure(1, weight=1)
        self.quick_panel.grid_columnconfigure(0, weight=1)

        quick_top = ttk.Frame(self.quick_panel, style="Panel.TFrame")
        quick_top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(quick_top, text="新增命令", command=self.add_quick_command, style="Tool.TButton").pack(
            side=tk.LEFT
        )
        ttk.Label(quick_top, text="命令内容支持逐条发送，每条可单独选择 HEX。", style="Muted.TLabel").pack(
            side=tk.RIGHT
        )

        quick_canvas_frame = tk.Frame(self.quick_panel, bg=PANEL_BG, highlightthickness=0)
        quick_canvas_frame.grid(row=1, column=0, sticky="nsew")
        quick_canvas_frame.grid_rowconfigure(0, weight=1)
        quick_canvas_frame.grid_columnconfigure(0, weight=1)

        self.quick_canvas = tk.Canvas(
            quick_canvas_frame,
            bg=PANEL_BG,
            highlightthickness=0,
            bd=0,
        )
        self.quick_canvas.grid(row=0, column=0, sticky="nsew")

        self.quick_scrollbar = ttk.Scrollbar(
            quick_canvas_frame, orient="vertical", command=self.quick_canvas.yview
        )
        self.quick_scrollbar.grid(row=0, column=1, sticky="ns")
        self.quick_canvas.configure(yscrollcommand=self.quick_scrollbar.set)

        self.quick_list_frame = tk.Frame(self.quick_canvas, bg=PANEL_BG)
        self.quick_canvas_window = self.quick_canvas.create_window(
            (0, 0), window=self.quick_list_frame, anchor="nw"
        )
        self.quick_list_frame.bind("<Configure>", self._update_quick_canvas_scrollregion)
        self.quick_canvas.bind("<Configure>", self._resize_quick_canvas_window)

    def _configure_styles(self) -> None:
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
        style.configure(
            "FileSend.Horizontal.TProgressbar",
            troughcolor=BUTTON_BG,
            background=ACCENT_ACTIVE,
            darkcolor=ACCENT_ACTIVE,
            lightcolor=ACCENT_ACTIVE,
            bordercolor=BORDER_COLOR,
        )

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
            current_device = current.split(" - ", 1)[0].strip() if current else ""
            matched_port = next(
                (name for name in port_names if name.split(" - ", 1)[0].strip() == current_device),
                None,
            )
            if matched_port:
                self.port_var.set(matched_port)
            elif current not in port_names:
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
        self.save_config()

    def close_port(self) -> None:
        self.reader_running = False
        if self.file_send_in_progress:
            self.file_send_stop_requested = True
        self._flush_text_receive_buffer()

        if self.serial_port is not None:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except SerialException:
                pass

        self.serial_port = None
        self.status_var.set("未连接")
        self.connect_button_var.set("打开串口")
        if self.realtime_log_enabled:
            self.stop_realtime_log(notify=True, reason="串口关闭，已停止实时保存")
        self.save_config()

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
                self._flush_text_receive_buffer()
                self.append_output(f"[错误] {payload}\n")
                self.close_port()
            else:
                self._handle_received_data(payload)

        if (
            not self.hex_display_var.get()
            and self.text_receive_buffer
            and self.text_receive_last_update
            and (time.monotonic() - self.text_receive_last_update) * 1000 >= TEXT_BUFFER_FLUSH_MS
        ):
            self._flush_text_receive_buffer()

        self.root.after(100, self.process_incoming_data)

    def send_data(self) -> None:
        raw_text = self.input_text.get("1.0", tk.END).strip()
        self._send_payload(raw_text, self.hex_send_var.get(), source_label="发送")

    def _send_payload(self, raw_text: str, hex_mode: bool, source_label: str) -> None:
        if not self.serial_port or not self.serial_port.is_open:
            messagebox.showwarning("未连接", "请先打开串口")
            return

        if not raw_text:
            messagebox.showwarning("无发送内容", "请输入要发送的数据")
            return

        try:
            payload = self._build_payload(raw_text, hex_mode)
            self.serial_port.write(payload)
        except (ValueError, SerialException) as exc:
            messagebox.showerror("发送失败", str(exc))
            return

        preview = payload.hex(" ").upper() if hex_mode else raw_text
        if self.show_sent_data_var.get():
            self.append_output(f"[{source_label}] {preview}\n")

    def append_output(self, text: str) -> None:
        self.output_history.append(text)
        self.output_text.configure(state=tk.NORMAL)
        self._insert_ansi_text(text)
        self._trim_output_lines()
        self.output_text.configure(state=tk.DISABLED)
        self._write_realtime_log(text)
        if self.autoscroll_var.get():
            self.output_text.see(tk.END)

    def clear_output(self) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.configure(state=tk.DISABLED)
        self.hex_line_open = False
        self.text_receive_buffer = b""
        self.text_receive_last_update = 0.0
        self.output_history.clear()

    def clear_input(self) -> None:
        self.input_text.delete("1.0", tk.END)

    def update_input_mode(self) -> None:
        mode = self.input_mode_var.get()
        if mode == "file":
            self.command_mode_frame.grid_remove()
            self.file_mode_frame.grid()
        else:
            self.file_mode_frame.grid_remove()
            self.command_mode_frame.grid()
        self.save_config()

    def update_file_send_mode(self) -> None:
        if self.file_send_mode_var.get() == "chunked":
            self.chunk_config_row.pack(side=tk.LEFT, padx=(12, 0))
        else:
            self.chunk_config_row.pack_forget()
        self.save_config()

    def choose_binary_file(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择要发送的二进制文件",
            filetypes=[("All Files", "*.*")],
        )
        if not file_path:
            return

        self.selected_file_path = file_path
        self.selected_file_var.set(self._format_file_path_for_display(file_path))
        self.file_send_total_bytes = 0
        self.file_progress_bar["value"] = 0
        self.file_progress_var.set("已选择，等待发送")
        self.save_config()

    def toggle_binary_file_send(self) -> None:
        if self.file_send_in_progress:
            self.stop_binary_file_send()
        else:
            self.send_binary_file()

    def send_binary_file(self) -> None:
        if self.file_send_in_progress:
            messagebox.showinfo("提示", "文件发送进行中，请等待当前任务完成")
            return

        if not self.serial_port or not self.serial_port.is_open:
            messagebox.showwarning("未连接", "请先打开串口")
            return

        if not self.selected_file_path:
            messagebox.showwarning("未选择文件", "请先选择要发送的文件")
            return

        chunk_size = 0
        pause_ms = 0
        if self.file_send_mode_var.get() == "chunked":
            try:
                chunk_size = int(self.file_chunk_size_var.get())
                pause_ms = int(self.file_pause_ms_var.get())
                if chunk_size <= 0 or pause_ms < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("参数错误", "请填写有效的分块参数：字节数需大于 0，暂停可为 0")
                return

        self.file_send_in_progress = True
        self.file_send_stop_requested = False
        self.file_send_total_bytes = 0
        self.choose_file_button.configure(state=tk.DISABLED)
        self.file_send_button_var.set("停止发送")
        self.send_file_button.configure(bg=BUTTON_BG, activebackground=SUBTLE_BG)
        self.file_progress_var.set("0 B / 计算中...")
        self.file_progress_bar["value"] = 0
        mode_text = "分块发送" if self.file_send_mode_var.get() == "chunked" else "连续发送"
        self.append_output(
            f"[系统] 开始发送文件: {self.selected_file_path}，方式: {mode_text}"
            + (f"，每 {chunk_size} 字节暂停 {pause_ms} ms" if self.file_send_mode_var.get() == "chunked" else "")
            + "\n"
        )
        threading.Thread(
            target=self._send_binary_file_worker,
            args=(self.selected_file_path, chunk_size, pause_ms, self.file_send_mode_var.get()),
            daemon=True,
        ).start()

    def _send_binary_file_worker(self, file_path: str, chunk_size: int, pause_ms: int, send_mode: str) -> None:
        sent_bytes = 0
        error_message = ""

        try:
            self.file_send_total_bytes = os.path.getsize(file_path)
            with open(file_path, "rb") as file:
                while True:
                    if self.file_send_stop_requested:
                        error_message = "用户手动停止发送"
                        break

                    read_size = chunk_size if send_mode == "chunked" else 4096
                    chunk = file.read(read_size)
                    if not chunk:
                        break

                    if not self.serial_port or not self.serial_port.is_open:
                        error_message = "串口已关闭，文件发送已中止"
                        break

                    self.serial_port.write(chunk)
                    sent_bytes += len(chunk)
                    self.root.after(
                        0,
                        lambda current=sent_bytes: self._update_file_progress(current, self.file_send_total_bytes),
                    )

                    if send_mode == "chunked" and pause_ms > 0:
                        time.sleep(pause_ms / 1000.0)
        except (OSError, SerialException) as exc:
            error_message = str(exc)

        self.root.after(0, lambda: self._finish_binary_file_send(file_path, sent_bytes, error_message))

    def _finish_binary_file_send(self, file_path: str, sent_bytes: int, error_message: str) -> None:
        self.file_send_in_progress = False
        self.file_send_stop_requested = False
        self.choose_file_button.configure(state=tk.NORMAL)
        self.file_send_button_var.set("发送文件")
        self.send_file_button.configure(state=tk.NORMAL, bg=ACCENT_COLOR, activebackground=ACCENT_ACTIVE)

        if error_message:
            if error_message == "用户手动停止发送":
                self.file_progress_var.set(f"已停止，已发送 {sent_bytes} B")
                self.append_output(f"[系统] 文件发送已停止: {file_path}，已发送 {sent_bytes} 字节\n")
            else:
                self.file_progress_var.set(f"失败，已发送 {sent_bytes} B")
                self.append_output(
                    f"[系统] 文件发送失败: {file_path}，已发送 {sent_bytes} 字节，原因: {error_message}\n"
                )
            return

        self.file_progress_bar["value"] = 100
        self.file_progress_var.set(f"完成 {sent_bytes} / {self.file_send_total_bytes} B")
        self.append_output(f"[系统] 文件发送完成: {file_path}，共发送 {sent_bytes} 字节\n")

    def _update_file_progress(self, sent_bytes: int, total_bytes: int) -> None:
        if total_bytes > 0:
            percent = sent_bytes * 100 / total_bytes
            self.file_progress_bar["value"] = percent
            self.file_progress_var.set(f"{sent_bytes} / {total_bytes} B  ({percent:.1f}%)")
        else:
            self.file_progress_bar["value"] = 0
            self.file_progress_var.set(f"{sent_bytes} B")

    def _format_file_path_for_display(self, file_path: str, max_length: int = 42) -> str:
        if not file_path:
            return "未选择文件"
        if len(file_path) <= max_length:
            return file_path
        keep = max_length - 3
        head = max(keep // 2, 8)
        tail = max(keep - head, 8)
        return f"{file_path[:head]}...{file_path[-tail:]}"

    def stop_binary_file_send(self) -> None:
        if not self.file_send_in_progress:
            return

        self.file_send_stop_requested = True
        self.file_send_button_var.set("正在停止...")
        self.send_file_button.configure(state=tk.DISABLED)
        self.file_progress_var.set("正在停止...")

    def save_current_log(self) -> None:
        log_path = filedialog.asksaveasfilename(
            title="保存当前日志",
            defaultextension=".log",
            filetypes=[("Log Files", "*.log"), ("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if not log_path:
            return

        try:
            with open(log_path, "w", encoding="utf-8") as file:
                file.write("".join(self.output_history))
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存日志: {exc}")
            return

        self.append_output(f"[系统] 当前日志已保存到: {log_path}\n")

    def toggle_realtime_log(self) -> None:
        if self.realtime_log_enabled:
            self.stop_realtime_log(notify=True)
            return

        log_path = filedialog.asksaveasfilename(
            title="选择实时日志保存文件",
            defaultextension=".log",
            filetypes=[("Log Files", "*.log"), ("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if not log_path:
            return

        try:
            with open(log_path, "w", encoding="utf-8") as file:
                file.write("".join(self.output_history))
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法启动实时保存: {exc}")
            return

        self.realtime_log_path = log_path
        self.realtime_log_enabled = True
        self.realtime_log_button_var.set("停止实时保存")
        self.append_output(f"[系统] 已启动实时保存: {log_path}\n")

    def _write_realtime_log(self, text: str) -> None:
        if not self.realtime_log_enabled or not self.realtime_log_path:
            return

        try:
            with open(self.realtime_log_path, "a", encoding="utf-8") as file:
                file.write(text)
        except OSError as exc:
            failed_path = self.realtime_log_path
            self.stop_realtime_log(notify=False)
            messagebox.showerror("实时保存失败", f"无法写入日志文件: {exc}")
            self.output_history.append(f"[系统] 实时保存已停止: {failed_path}\n")
            self.output_text.configure(state=tk.NORMAL)
            self._insert_ansi_text(f"[系统] 实时保存已停止: {failed_path}\n")
            self.output_text.configure(state=tk.DISABLED)

    def stop_realtime_log(self, notify: bool = False, reason: str | None = None) -> None:
        if not self.realtime_log_enabled and not self.realtime_log_path:
            self.realtime_log_button_var.set("实时保存")
            return

        stopped_path = self.realtime_log_path
        self.realtime_log_enabled = False
        self.realtime_log_path = ""
        self.realtime_log_button_var.set("实时保存")

        if notify:
            message = reason or f"已停止实时保存: {stopped_path}"
            self.append_output(f"[系统] {message}\n")

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

    def toggle_quick_panel(self) -> None:
        if self.quick_panel_visible:
            self.main_paned.forget(self.quick_panel)
            self.quick_panel_visible = False
            self.quick_panel_button_var.set("显示快捷命令")
        else:
            self.main_paned.add(self.quick_panel, minsize=RIGHT_PANE_MIN_WIDTH, stretch="never")
            self.quick_panel_visible = True
            self.quick_panel_button_var.set("隐藏快捷命令")
        self.save_config()

    def toggle_input_panel(self) -> None:
        if self.input_panel_visible:
            self.input_frame.grid_remove()
            self.input_panel_visible = False
            self.input_panel_button_var.set("▸")
        else:
            self.input_frame.grid()
            self.input_panel_visible = True
            self.input_panel_button_var.set("▾")
        self.save_config()

    def add_quick_command(self, initial_text: str = "", hex_mode: bool = False, save: bool = True) -> None:
        item = {
            "text_var": tk.StringVar(value=initial_text),
            "hex_var": tk.BooleanVar(value=hex_mode),
            "trace_bound": False,
        }
        self.quick_commands.append(item)
        self._render_quick_commands()
        if save:
            self.save_config()

    def remove_quick_command(self, index: int) -> None:
        if 0 <= index < len(self.quick_commands):
            self.quick_commands.pop(index)
            self._render_quick_commands()
            self.save_config()

    def send_quick_command(self, index: int) -> None:
        if not (0 <= index < len(self.quick_commands)):
            return

        item = self.quick_commands[index]
        raw_text = item["text_var"].get().strip()
        label = f"快捷{index + 1}"
        self._send_payload(raw_text, item["hex_var"].get(), source_label=label)

    def _render_quick_commands(self) -> None:
        for child in self.quick_list_frame.winfo_children():
            child.destroy()

        for index, item in enumerate(self.quick_commands):
            if not item["trace_bound"]:
                item["text_var"].trace_add("write", self._on_quick_command_changed)
                item["hex_var"].trace_add("write", self._on_quick_command_changed)
                item["trace_bound"] = True
            row = tk.Frame(
                self.quick_list_frame,
                bg=SUBTLE_BG,
                highlightthickness=1,
                highlightbackground=BORDER_COLOR,
                padx=8,
                pady=8,
            )
            row.pack(fill=tk.X, pady=(0, 8))
            row.grid_columnconfigure(1, weight=1)

            title = tk.Label(
                row,
                text=f"{index + 1}.",
                bg=SUBTLE_BG,
                fg=MUTED_TEXT,
                anchor="w",
                font=("Microsoft YaHei UI", 9),
                width=3,
            )
            title.grid(row=0, column=0, sticky="w", padx=(0, 6))

            entry = tk.Entry(
                row,
                textvariable=item["text_var"],
                bg=PANEL_BG,
                fg=TEXT_COLOR,
                insertbackground=TEXT_COLOR,
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=BORDER_COLOR,
                highlightcolor=HIGHLIGHT_COLOR,
                font=("Consolas", 10),
            )
            entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), ipady=6)

            hex_check = tk.Checkbutton(
                row,
                text="HEX",
                variable=item["hex_var"],
                bg=SUBTLE_BG,
                fg=TEXT_COLOR,
                activebackground=SUBTLE_BG,
                activeforeground=TEXT_COLOR,
                selectcolor=PANEL_BG,
                highlightthickness=0,
                bd=0,
                font=("Microsoft YaHei UI", 9),
            )
            hex_check.grid(row=0, column=2, sticky="w", padx=(0, 8))

            send_button = tk.Button(
                row,
                text="发送",
                command=lambda i=index: self.send_quick_command(i),
                bg=ACCENT_COLOR,
                fg="white",
                activebackground=ACCENT_ACTIVE,
                activeforeground="white",
                relief=tk.FLAT,
                bd=0,
                highlightthickness=0,
                padx=12,
                pady=5,
                font=("Microsoft YaHei UI", 9),
            )
            send_button.grid(row=0, column=3, sticky="e", padx=(0, 8))

            delete_button = tk.Button(
                row,
                text="删",
                command=lambda i=index: self.remove_quick_command(i),
                bg=BUTTON_BG,
                fg=TEXT_COLOR,
                activebackground=PANEL_BG,
                activeforeground=TEXT_COLOR,
                relief=tk.FLAT,
                bd=0,
                highlightthickness=0,
                padx=10,
                pady=5,
                font=("Microsoft YaHei UI", 9),
            )
            delete_button.grid(row=0, column=4, sticky="e")

        self._update_quick_canvas_scrollregion()

    def _update_quick_canvas_scrollregion(self, _event=None) -> None:
        self.quick_canvas.configure(scrollregion=self.quick_canvas.bbox("all"))

    def _resize_quick_canvas_window(self, event) -> None:
        self.quick_canvas.itemconfigure(self.quick_canvas_window, width=event.width)

    def _on_quick_command_changed(self, *_args) -> None:
        self.save_config()

    def _bind_config_traces(self) -> None:
        vars_to_watch = [
            self.port_var,
            self.baud_var,
            self.bytesize_var,
            self.parity_var,
            self.stopbits_var,
            self.hex_send_var,
            self.hex_display_var,
            self.autoscroll_var,
            self.show_sent_data_var,
            self.input_mode_var,
            self.file_send_mode_var,
            self.file_chunk_size_var,
            self.file_pause_ms_var,
        ]
        for var in vars_to_watch:
            var.trace_add("write", self._on_config_var_changed)

    def _on_config_var_changed(self, *_args) -> None:
        if self.hex_display_var.get():
            self._flush_text_receive_buffer()
        self.save_config()

    def _handle_received_data(self, data: bytes) -> None:
        if self.hex_display_var.get():
            self._flush_text_receive_buffer()
            self.append_output(self._format_bytes(data))
            return

        self.text_receive_buffer += data
        self.text_receive_last_update = time.monotonic()
        lines = self.text_receive_buffer.splitlines(keepends=True)
        pending = b""
        if lines and not lines[-1].endswith((b"\n", b"\r")):
            pending = lines.pop()

        for line in lines:
            formatted = self._format_bytes(line)
            if formatted:
                self.append_output(formatted)

        self.text_receive_buffer = pending
        if len(self.text_receive_buffer) >= MAX_TEXT_BUFFER_BYTES:
            self._flush_text_receive_buffer()

    def _flush_text_receive_buffer(self) -> None:
        if not self.text_receive_buffer:
            return

        buffered = self.text_receive_buffer
        self.text_receive_buffer = b""
        self.text_receive_last_update = 0.0
        formatted = self._decode_text_bytes(buffered)
        if formatted:
            self.append_output(formatted)

    def _configure_output_tags(self) -> None:
        self.output_text.tag_configure("ansi_default", foreground=TEXT_COLOR)
        self.output_text.tag_configure("ansi_bold", font=("Consolas", 10, "bold"))
        for code, color in ANSI_COLOR_MAP.items():
            self.output_text.tag_configure(f"ansi_fg_{code}", foreground=color)

    def _trim_output_lines(self) -> None:
        try:
            line_count = int(self.output_text.index("end-1c").split(".")[0])
        except (ValueError, tk.TclError):
            return

        excess_lines = line_count - MAX_DISPLAY_LINES
        if excess_lines > 0:
            self.output_text.delete("1.0", f"{excess_lines + 1}.0")

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
            return self._decode_text_bytes(data)
        except Exception:
            return repr(data) + "\n"

    def _decode_text_bytes(self, data: bytes) -> str:
        return data.decode("utf-8", errors="replace")

    def _build_payload(self, raw_text: str, hex_mode: bool) -> bytes:
        if hex_mode:
            hex_text = raw_text.replace("\n", " ").replace("\r", " ")
            return bytes.fromhex(hex_text)
        return raw_text.encode("utf-8")

    def load_config(self) -> None:
        config_path = get_config_file_path()
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                config = json.load(file)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            return

        self.port_var.set(config.get("port", ""))
        self.baud_var.set(str(config.get("baudrate", "115200")))
        self.bytesize_var.set(str(config.get("bytesize", "8")))
        self.parity_var.set(config.get("parity", "N"))
        self.stopbits_var.set(str(config.get("stopbits", "1")))
        self.hex_send_var.set(bool(config.get("hex_send", False)))
        self.hex_display_var.set(bool(config.get("hex_display", False)))
        self.autoscroll_var.set(bool(config.get("autoscroll", True)))
        self.show_sent_data_var.set(bool(config.get("show_sent_data", True)))
        self.input_mode_var.set(config.get("input_mode", "command"))
        self.file_send_mode_var.set(config.get("file_send_mode", "continuous"))
        self.file_chunk_size_var.set(str(config.get("file_chunk_size", "500")))
        self.file_pause_ms_var.set(str(config.get("file_pause_ms", "100")))
        self.selected_file_path = str(config.get("selected_file_path", ""))
        self.selected_file_var.set(self._format_file_path_for_display(self.selected_file_path))

        quick_panel_visible = bool(config.get("quick_panel_visible", False))
        input_panel_visible = bool(config.get("input_panel_visible", True))
        commands = config.get("quick_commands", [])
        if isinstance(commands, list):
            for command in commands:
                if isinstance(command, dict):
                    self.add_quick_command(
                        initial_text=str(command.get("text", "")),
                        hex_mode=bool(command.get("hex", False)),
                        save=False,
                    )

        if quick_panel_visible:
            self.toggle_quick_panel()
        if not input_panel_visible:
            self.toggle_input_panel()

    def save_config(self) -> None:
        if not self.config_loaded:
            return

        config_path = get_config_file_path(create_dir=True)

        config = {
            "port": self.port_var.get(),
            "baudrate": self.baud_var.get(),
            "bytesize": self.bytesize_var.get(),
            "parity": self.parity_var.get(),
            "stopbits": self.stopbits_var.get(),
            "hex_send": self.hex_send_var.get(),
            "hex_display": self.hex_display_var.get(),
            "autoscroll": self.autoscroll_var.get(),
            "show_sent_data": self.show_sent_data_var.get(),
            "input_mode": self.input_mode_var.get(),
            "file_send_mode": self.file_send_mode_var.get(),
            "file_chunk_size": self.file_chunk_size_var.get(),
            "file_pause_ms": self.file_pause_ms_var.get(),
            "selected_file_path": self.selected_file_path,
            "quick_panel_visible": self.quick_panel_visible,
            "input_panel_visible": self.input_panel_visible,
            "quick_commands": [
                {
                    "text": item["text_var"].get(),
                    "hex": item["hex_var"].get(),
                }
                for item in self.quick_commands
            ],
        }

        try:
            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(config, file, ensure_ascii=False, indent=2)
        except OSError:
            pass

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
        self.save_config()
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
