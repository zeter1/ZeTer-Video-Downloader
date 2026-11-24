"""Палитра, стили ttk и окна инструментов ZeTer."""

import tkinter as tk
from tkinter import ttk


PAPER = "#f4f5ef"
WHITE = "#ffffff"
INK = "#202820"
MUTED = "#5b695d"
LINE = "#d6ddd1"
FOREST = "#202d24"
LIME = "#c5f56b"


def configure_studio(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    font = ("Segoe UI", 10)
    style.configure(".", background=WHITE, foreground=INK, font=font)
    style.configure("TFrame", background=WHITE)
    style.configure("Paper.TFrame", background=PAPER)
    style.configure("Hero.TFrame", background=FOREST)
    style.configure("TLabel", background=WHITE, foreground=INK, font=font)
    style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Brand.TLabel", font=("Segoe UI", 16, "bold"))
    style.configure("Page.TLabel", background=PAPER, font=("Segoe UI", 27, "bold"))
    style.configure("CardTitle.TLabel", font=("Segoe UI", 13, "bold"))
    style.configure("HeroTitle.TLabel", background=FOREST, foreground=WHITE,
                    font=("Segoe UI", 11, "bold"))
    style.configure("Percent.TLabel", background=FOREST, foreground=WHITE,
                    font=("Segoe UI", 37, "bold"))
    style.configure("HeroStatus.TLabel", background=FOREST, foreground=LIME,
                    font=("Segoe UI", 10))
    style.configure("TButton", padding=(10, 5), borderwidth=1, relief="flat",
                    background=WHITE, bordercolor=LINE, lightcolor=WHITE, darkcolor=WHITE)
    style.map("TButton", background=[("pressed", "#dce5d5"), ("active", "#edf2e8")],
              foreground=[("disabled", "#788378")])
    style.configure("Link.TButton", foreground="#365d35", padding=(2, 3), bordercolor=WHITE,
                    font=("Segoe UI", 10, "underline"))
    style.configure("Start.TButton", background=LIME, bordercolor=LIME,
                    lightcolor=LIME, darkcolor=LIME, font=("Segoe UI", 10, "bold"))
    style.map("Start.TButton", background=[("disabled", "#dce3d8"), ("active", "#b5e75e")])
    style.configure("Primary.TButton", background=FOREST, foreground=WHITE, bordercolor=FOREST,
                    lightcolor=FOREST, darkcolor=FOREST, padding=(15, 8), font=("Segoe UI", 10, "bold"))
    style.map("Primary.TButton", background=[("pressed", "#18201a"), ("active", "#344536")])
    style.configure("Import.TButton", padding=(14, 8))
    style.configure("Hero.TButton", background="#314036", foreground=WHITE, bordercolor="#516154",
                    darkcolor="#314036", lightcolor="#314036", padding=(9, 4))
    style.map("Hero.TButton", background=[("active", "#405245"), ("disabled", FOREST)],
              foreground=[("disabled", "#8d9b90")])
    style.configure("Field.TLabel", padding=(6, 5), background="#f5f7f1")
    style.configure("TMenubutton", background=WHITE, bordercolor=LINE,
                    lightcolor=WHITE, darkcolor=WHITE, padding=(8, 5))
    style.configure("TCheckbutton", background=WHITE)
    style.map("TCheckbutton", background=[("active", WHITE)])
    configure_settings_style(root, style)
    style.configure("TEntry", fieldbackground=WHITE, padding=5, bordercolor=LINE)
    style.configure("TCombobox", padding=4, bordercolor=LINE)
    style.map("TCombobox", fieldbackground=[("readonly", "#f7f9f4")])
    style.configure("TSeparator", background=LINE)
    style.configure("Studio.Horizontal.TProgressbar", background=LIME,
                    troughcolor="#435448", bordercolor=FOREST, lightcolor=LIME,
                    darkcolor=LIME, thickness=7)
    style.configure("TNotebook", background=WHITE, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(12, 5))
    style.map("TNotebook.Tab", background=[("selected", "#e3eddb")])
    style.configure("Treeview", fieldbackground=WHITE, background=WHITE,
                    bordercolor=LINE, rowheight=max(32, int(root.winfo_fpixels("22p"))))
    style.configure("Treeview.Heading", background="#edf2e9", foreground=MUTED,
                    padding=(7, 5), font=("Segoe UI", 9, "bold"))
    style.map("Treeview", background=[("selected", "#dbeacb")],
              foreground=[("selected", INK)])


def configure_settings_style(root, style):
    field = "#f6f8f3"
    edge = "#57734b"
    style.configure("SettingsHeading.TLabel", foreground="#38533d", font=("Segoe UI", 10, "bold"))
    style.configure("SettingsDivider.TFrame", background=LINE)
    style.configure("Settings.TEntry", fieldbackground=field)
    style.map("Settings.TEntry", bordercolor=[("focus", edge)])
    style.configure("Settings.TButton", background=field, padding=(9, 4))
    style.configure("Settings.TSpinbox", fieldbackground=field, background="#edf2e8",
                    bordercolor=LINE, padding=(6, 4), arrowsize=10)
    style.map("Settings.TSpinbox", bordercolor=[("focus", edge)],
              fieldbackground=[("readonly", field)])
    style.configure("SettingsSwitch.TCheckbutton", padding=(0, 4), focuscolor=edge)

    # Keep native ttk mouse and keyboard behavior for the track-and-thumb control.
    try:
        from PIL import Image, ImageDraw, ImageTk
    except ImportError:
        return
    height = max(16, round(root.winfo_fpixels("12p")))
    width = max(height + 13, round(root.winfo_fpixels("24p")))
    gap = max(6, round(root.winfo_fpixels("5p")))
    scale = 4
    w, h, gutter = width * scale, height * scale, gap * scale

    def indicator(selected):
        image = Image.new("RGBA", (w + gutter, h))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((gutter, 0, gutter + w - 1, h - 1), radius=h // 2,
                               fill="#52773c" if selected else "#748094")
        inset = 3 * scale
        x = gutter + (w - h if selected else 0) + inset
        draw.ellipse((x, inset, x + h - 2 * inset - 1, h - inset - 1), fill=WHITE)
        return ImageTk.PhotoImage(image.resize((width + gap, height), Image.Resampling.LANCZOS), master=root)

    off, on = indicator(False), indicator(True)
    root._studio_switch_images = (off, on)
    style.element_create("SettingsSwitch.indicator", "image", off, ("selected", on), sticky="")
    style.layout("SettingsSwitch.TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("SettingsSwitch.indicator", {"side": "right", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "w"}),
            ]}),
        ]}),
    ])


def rounded_panel(parent, *, dark=False, padx=16, pady=14):
    """Let the inner frame negotiate size normally; draw only in its margins."""
    color = FOREST if dark else WHITE
    shell = ttk.Frame(parent, style="Paper.TFrame")
    background = tk.Canvas(shell, bg=PAPER, highlightthickness=0, borderwidth=0)
    background.place(relwidth=1, relheight=1)
    body = ttk.Frame(shell, style="Hero.TFrame" if dark else "TFrame")
    body.pack(fill="both", expand=True, padx=padx, pady=pady)

    def corners(event):
        w, h = event.width - 1, event.height - 1
        radius = min(13, w // 2, h // 2)
        points = (radius, 0, w-radius, 0, w, 0, w, radius, w, h-radius, w, h,
                  w-radius, h, radius, h, 0, h, 0, h-radius, 0, radius, 0, 0)
        background.delete("panel")
        background.create_polygon(points, smooth=True, splinesteps=20, fill=color,
                                  outline=color if dark else LINE, tags="panel")
    background.bind("<Configure>", corners)
    return shell, body


def panel_title(parent, title, icon):
    heading = ttk.Frame(parent)
    symbol = tk.Canvas(heading, width=26, height=26, bg="#eff3e9", highlightthickness=0)
    symbol.pack(side="left", padx=(0, 8))
    if icon == "settings":
        for y, x in ((9, 11), (17, 16)):
            symbol.create_line(5, y, 21, y, fill=MUTED, width=1.5)
            symbol.create_oval(x-2, y-2, x+2, y+2, fill=WHITE, outline=MUTED)
    else:
        symbol.create_oval(6, 6, 20, 20, outline=MUTED, width=1.5)
        symbol.create_line(13, 8, 13, 13, 17, 15, fill=MUTED, width=1.5)
    ttk.Label(heading, text=title, style="CardTitle.TLabel").pack(side="left")
    return heading


def tool_window(root, title, width, height):
    window = tk.Toplevel(root)
    window.withdraw()
    window.title(title)
    window.transient(root)
    window.configure(background=WHITE)
    window.geometry(f"{width}x{height}")
    window._studio_initial_size = (width, height)
    window.protocol("WM_DELETE_WINDOW", window.withdraw)
    window.bind("<Escape>", lambda _event: window.withdraw())
    return window


def show_tool(window):
    if window.state() == "withdrawn":
        if window._studio_initial_size:
            width, height = window._studio_initial_size
            root = window.master
            x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
            y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
            # With Tk geometry negative offsets anchor against the opposite screen edge.
            if x >= 0 and y >= 0:
                window.geometry(f"+{x}+{y}")
            window._studio_initial_size = None
        window.deiconify()
    window.lift()
    window.focus_set()
