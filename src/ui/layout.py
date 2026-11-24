"""Главное окно ZeTer, редактор очереди, параметры и инструменты."""

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from src.core.constants import (
    APP_VERSION, AUDIO_QUALITIES, LOG_TAB_ALL, LOG_TAB_DOWNLOAD_FAILED, LOG_TAB_ERRORS,
    MAX_CONCURRENT_DL, MAX_SPLIT_HOURS,
)
from src.ui.about import build_about_page
from src.ui.studio_widgets import (
    FOREST, INK, LIME, LINE, PAPER, WHITE,
    configure_studio, panel_title, rounded_panel, show_tool, tool_window,
)
from src.ui.video_list import PlaylistRows


class UILayoutMixin:
    def setup_ui(self) -> None:
        configure_studio(self.root)
        self.root.configure(background=PAPER)
        self.root.minsize(860, 580)
        brand = ttk.Frame(self.root, padding=(28, 9))
        brand.pack(fill="x")
        logo = tk.Canvas(brand, width=32, height=32, background=WHITE, highlightthickness=0)
        logo.pack(side="left")
        logo_tile = "#e1ebda"
        logo.create_polygon(
            6, 0, 26, 0, 32, 0, 32, 6, 32, 26, 32, 32, 26, 32, 6, 32, 0, 32, 0, 26, 0, 6, 0, 0,
            fill=logo_tile, smooth=True, splinesteps=20,
        )
        logo.create_polygon(
            9.5, 5, 17, 5, 17, 12, 24, 12, 24, 25, 22.5, 26.5, 9.5, 26.5, 8, 25, 8, 6.5,
            fill=FOREST,
        )
        logo.create_polygon(19, 5.5, 23.5, 10, 19, 10, fill=FOREST)
        logo.create_polygon(12.5, 14, 20, 18.25, 12.5, 22.5, fill=logo_tile)
        ttk.Label(brand, text="ZeTer", style="Brand.TLabel").pack(side="left", padx=8)
        ttk.Separator(brand, orient="vertical").pack(side="left", fill="y", pady=5)
        ttk.Label(brand, text="Video Downloader").pack(side="left", padx=12)
        self._studio_about_nav = ttk.Button(
            brand, text="О программе", style="Link.TButton", cursor="hand2", command=self._studio_toggle_about,
        )
        self._studio_about_nav.pack(side="left")
        ttk.Separator(self.root).pack(fill="x")

        page = ttk.Frame(self.root, style="Paper.TFrame", padding=(28, 14, 28, 17))
        self._studio_download_page = page
        self._studio_about_page = None
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=7, uniform="studio")
        page.columnconfigure(1, weight=3, uniform="studio")
        page.rowconfigure(1, weight=1)
        def fit_margins(event):
            gutter = min(44, max(18, (event.width - 680) // 17))
            page.configure(padding=(gutter, 12, gutter, 16))
            brand.configure(padding=(gutter, 9))
        page.bind("<Configure>", fit_margins)
        top = ttk.Frame(page, style="Paper.TFrame")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ttk.Label(top, text="Загрузки", style="Page.TLabel").pack(side="left")
        ttk.Button(top, text="Импорт плейлиста", style="Import.TButton",
                   command=self.load_from_html).pack(side="left", padx=(18, 8))
        ttk.Button(top, text="Добавить ссылки", style="Primary.TButton",
                   command=self.show_studio_links).pack(side="left")

        left = ttk.Frame(page, style="Paper.TFrame")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        self._studio_build_progress(left)

        queue, self.studio_queue_parent = rounded_panel(left, padx=14, pady=12)
        queue.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        self._studio_build_editor()

        self._studio_build_sidebar(page)
        self._studio_build_log()
        self._studio_build_playlist()
        self._studio_initialize()

    def _studio_toggle_about(self):
        if self._studio_about_page is None:
            self._studio_about_page = build_about_page(self.root)
        about = self._studio_about_page
        if about.winfo_manager():
            about.pack_forget()
            self._studio_download_page.pack(fill="both", expand=True)
            self._studio_about_nav.configure(text="О программе")
        else:
            self._studio_download_page.pack_forget()
            about.pack(fill="both", expand=True)
            self._studio_about_nav.configure(text="Загрузки")

    def _studio_build_progress(self, parent):
        self.main_progress_frame, hero = rounded_panel(parent, dark=True, padx=19, pady=13)
        self.main_progress_frame.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=1)
        heading = ttk.Frame(hero, style="Hero.TFrame")
        heading.grid(row=0, column=0, sticky="w")
        pulse = tk.Canvas(heading, width=18, height=18, bg=FOREST, highlightthickness=0)
        pulse.pack(side="left", padx=(0, 6))
        pulse.create_line(1, 10, 5, 10, 7, 4, 10, 14, 13, 8, 15, 10, 17, 10, fill=LIME, width=1.5)
        ttk.Label(heading, text="Общий прогресс", style="HeroTitle.TLabel").pack(side="left")
        actions = ttk.Frame(hero, style="Hero.TFrame")
        actions.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.cancel_btn = ttk.Button(actions, text="Отменить", style="Hero.TButton", command=self.cancel_download,
                                     state=tk.DISABLED)
        self.cancel_btn.pack(side="left", padx=(0, 6))
        self.download_btn = ttk.Button(actions, text="Скачать всё", style="Start.TButton",
                                       command=self.start_download)
        self.download_btn.pack(side="left")
        self.resume_queue_btn = ttk.Button(
            hero, text="Продолжить очередь", style="Hero.TButton", command=self.resume_paused_download,
        )
        self.resume_queue_btn.grid(row=4, column=0, columnspan=2, sticky="e", pady=(6, 0))
        self.resume_queue_btn.grid_remove()
        self.progress_percent = ttk.Label(hero, text="0%", style="Percent.TLabel")
        self.progress_percent.grid(row=1, column=0, sticky="w", pady=(1, 0))
        self.studio_speed_graph = tk.Canvas(hero, bg=FOREST, highlightthickness=0, width=1, height=35)
        self.studio_speed_graph.grid(row=1, column=1, sticky="sew", padx=(8, 0), pady=(0, 6))
        self.main_progress = ttk.Progressbar(hero, mode="determinate",
                                             style="Studio.Horizontal.TProgressbar")
        self.main_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 6))
        self.studio_session_status = tk.StringVar(self.root, value="Готово к загрузке")
        foot = ttk.Frame(hero, style="Hero.TFrame")
        foot.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.studio_speed_text = tk.StringVar(self.root, value="")
        ttk.Label(foot, textvariable=self.studio_speed_text, style="HeroStatus.TLabel").pack(side="right")
        ttk.Label(foot, textvariable=self.studio_session_status, style="HeroStatus.TLabel",
                  width=1).pack(side="left", fill="x", expand=True)

    def _studio_build_editor(self):
        win = self._studio_links_window = tool_window(self.root, "Добавить ссылки", 660, 370)
        win.minsize(460, 260)
        editor = ttk.Frame(win, padding=18)
        editor.pack(fill="both", expand=True)
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(2, weight=1)
        ttk.Label(editor, text="Добавить ссылки", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(editor, text="Вставить из буфера", command=self.paste_from_clipboard).grid(
            row=0, column=1, padx=(4, 6))
        more = ttk.Menubutton(editor, text="Ещё")
        more.grid(row=0, column=2)
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label="Восстановить ссылки", command=self.restore_unfinished_links)
        menu.add_command(label="Очистить список ссылок", command=self.clear_urls)
        menu.add_separator()
        menu.add_command(label="Открыть список плейлиста", command=self.show_studio_playlist)
        more.configure(menu=menu)
        ttk.Label(editor, text="По одной ссылке в строке", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.url_text = scrolledtext.ScrolledText(
            editor, width=1, height=2, wrap=tk.WORD, font=("Segoe UI", 10),
            background="#f8faf5", foreground=INK, relief="flat", padx=7, pady=4,
            highlightthickness=1, highlightbackground=LINE, highlightcolor="#729955",
        )
        self.url_text.grid(row=2, column=0, columnspan=3, sticky="nsew")
        ttk.Button(editor, text="Готово", style="Primary.TButton", command=win.withdraw).grid(
            row=3, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.url_text.tag_config("DUPLICATE", background="#fde6e1", foreground="#923b24")
        self.url_text.tag_config("NEW", background="#e3f4d3")
        self.context_menu = tk.Menu(self.url_text, tearoff=0)
        self.context_menu.add_command(label="Копировать", command=self.copy_text)
        self.context_menu.add_command(label="Вставить", command=self.paste_text)
        self.context_menu.add_command(label="Удалить", command=self.delete_text)
        self.url_text.bind("<Control-KeyPress>", self._on_url_clipboard_shortcut)
        self.url_text.bind("<Button-3>", self.show_context_menu)

    def show_studio_links(self):
        show_tool(self._studio_links_window)
        self.url_text.focus_set()

    def _studio_build_sidebar(self, page):
        sidebar = ttk.Frame(page, style="Paper.TFrame")
        sidebar.grid(row=1, column=1, sticky="nsew")
        # A regular child frame is placed at full width even before the first resize.
        # Keep its requested height so the parent can clip it in short windows.
        content = ttk.Frame(sidebar, style="Paper.TFrame")
        content.place(x=0, y=0, relwidth=1)
        content.columnconfigure(0, weight=1)
        offset = 0

        def sync(_event=None):
            nonlocal offset
            min_sidebar_width = content.winfo_reqwidth() + scrollbar.winfo_reqwidth()
            if page.columnconfigure(1, "minsize") != min_sidebar_width:
                page.columnconfigure(1, minsize=min_sidebar_width)
            visible = sidebar.winfo_height()
            total = content.winfo_reqheight()
            if visible > 1 and total > visible:
                offset = max(0, min(offset, total - visible))
                content.place_configure(y=-offset, width=-scrollbar.winfo_reqwidth())
                scrollbar.place(relx=1, rely=0, relheight=1, anchor="ne")
                scrollbar.set(offset / total, (offset + visible) / total)
            else:
                offset = 0
                content.place_configure(y=0, width=0)
                scrollbar.place_forget()
                scrollbar.set(0, 1)

        def move(action, value, unit=None):
            nonlocal offset
            if action == "moveto":
                offset = round(float(value) * content.winfo_reqheight())
            elif action == "scroll":
                step = max(1, sidebar.winfo_height() - 24) if unit == "pages" else 24
                offset += int(value) * step
            sync()

        scrollbar = ttk.Scrollbar(sidebar, orient="vertical", command=move)

        def reveal(event):
            nonlocal offset
            visible = sidebar.winfo_height()
            if content.winfo_reqheight() <= visible:
                return
            y = event.widget.winfo_rooty() - content.winfo_rooty()
            bottom = y + event.widget.winfo_height()
            if y < offset or bottom > offset + visible:
                offset = y if y < offset else bottom - visible
                sync()

        def wheel(event):
            if content.winfo_reqheight() > sidebar.winfo_height():
                move("scroll", -int(event.delta / 120) * 3, "units")
                return "break"

        def bind_controls(parent):
            for child in parent.winfo_children():
                child.bind("<FocusIn>", reveal, add="+")
                child.bind("<MouseWheel>", wheel, add="+")
                bind_controls(child)

        self._studio_build_events(content)
        self._studio_build_settings(content)
        sidebar.bind("<Configure>", sync)
        sidebar.bind("<MouseWheel>", wheel)
        content.bind("<Configure>", sync)
        content.bind("<MouseWheel>", wheel)
        bind_controls(content)

    def _studio_build_settings(self, side):
        card, box = rounded_panel(side, padx=14, pady=14)
        card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        panel_title(box, "Параметры", "settings").pack(anchor="w", pady=(0, 12))
        ttk.Label(box, text="Папка сохранения", style="SettingsHeading.TLabel").pack(anchor="w")
        path = ttk.Frame(box)
        path.pack(fill="x", pady=(5, 0))
        self.save_path = tk.StringVar(value=self.settings.get("save_path", str(Path.home() / "Downloads")))
        self.save_path_entry = ttk.Entry(path, textvariable=self.save_path, width=1, style="Settings.TEntry")
        self.save_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.browse_path_btn = ttk.Button(path, text="Обзор", command=self.browse_path, style="Settings.TButton")
        self.browse_path_btn.pack(side="right")

        def section(title):
            ttk.Frame(box, height=1, style="SettingsDivider.TFrame").pack(fill="x", pady=(10, 8))
            ttk.Label(box, text=title, style="SettingsHeading.TLabel").pack(anchor="w", pady=(0, 3))
            group = ttk.Frame(box)
            group.pack(fill="x")
            return group

        downloads = section("Загрузка")
        self.skip_downloaded = tk.BooleanVar(value=self.settings.get("skip_downloaded", True))
        ttk.Checkbutton(downloads, text="Пропускать скачанные видео", variable=self.skip_downloaded,
                        style="SettingsSwitch.TCheckbutton", command=self._studio_save_skip_downloaded).pack(fill="x")
        self.download_subtitles = tk.BooleanVar(value=self.settings.get("download_subtitles", False))
        ttk.Checkbutton(downloads, text="Скачивать субтитры", variable=self.download_subtitles,
                        style="SettingsSwitch.TCheckbutton", command=self.save_current_settings).pack(fill="x")
        streams = ttk.Frame(downloads)
        streams.pack(fill="x", pady=(3, 0))
        ttk.Label(streams, text="Параллельные загрузки").pack(side="left")
        self.concurrent_var = tk.IntVar(value=self.settings.get("concurrent_downloads", 2))
        ttk.Spinbox(streams, from_=1, to=MAX_CONCURRENT_DL, state="readonly", width=3,
                    style="Settings.TSpinbox", textvariable=self.concurrent_var,
                    command=self.save_current_settings).pack(side="right")

        audio = section("Аудио")
        audio.columnconfigure(0, weight=1)
        self.merge_audio = tk.BooleanVar(value=self.settings.get("merge_audio", False))
        ttk.Checkbutton(audio, text="Объединять в один файл", variable=self.merge_audio,
                        style="SettingsSwitch.TCheckbutton", command=self._studio_save_merge).grid(
            row=0, column=0, sticky="ew")
        self.split_frame = ttk.Frame(audio)
        quality = ttk.Frame(audio)
        quality.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        ttk.Label(quality, text="Качество MP3").pack(side="left")
        self.audio_quality = tk.StringVar(value=self.settings["audio_quality"])
        selector = ttk.Combobox(
            quality, textvariable=self.audio_quality, values=AUDIO_QUALITIES, state="readonly", width=16,
        )
        selector.pack(side="right")
        selector.bind("<<ComboboxSelected>>", lambda _event: self.save_current_settings())
        self.split_frame.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        self.split_frame.columnconfigure(0, weight=1)
        ttk.Label(self.split_frame, text="Часов на часть").grid(row=0, column=0, sticky="w")
        self.split_hours = tk.IntVar(value=self.settings.get("split_hours", MAX_SPLIT_HOURS))
        split = ttk.Spinbox(self.split_frame, from_=0, to=MAX_SPLIT_HOURS, width=3, style="Settings.TSpinbox",
                            textvariable=self.split_hours, command=self._studio_save_split)
        split.grid(row=0, column=1, sticky="e")
        split.bind("<KeyRelease>", lambda _e: self.check_split_warning())
        split.bind("<FocusOut>", lambda _e: self._studio_save_split())
        ttk.Label(self.split_frame, text="0 — без разбивки", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w")

        diagnostics = section("Диагностика")
        self.write_problem_logs = tk.BooleanVar(value=self.settings.get("write_problem_logs", True))
        ttk.Checkbutton(diagnostics, text="Писать логи проблем", variable=self.write_problem_logs,
                        style="SettingsSwitch.TCheckbutton", command=self.on_problem_logging_change).pack(fill="x")

    def _studio_save_skip_downloaded(self):
        self.settings["skip_downloaded"] = self.skip_downloaded.get()
        self.save_settings()
        self.refresh_download_dashboard()

    def _studio_save_merge(self):
        self.on_merge_change()
        self.save_current_settings()

    def _studio_save_split(self):
        self.check_split_warning()
        self.split_hours.set(max(0, self.split_hours.get()))
        if self.split_hours.get() != self.settings.get("split_hours"):
            self.save_current_settings()

    def _studio_confirm_clear_queue(self):
        if self.is_downloading:
            messagebox.showinfo("Очередь скачивания", "Дождитесь завершения загрузки.",
                                parent=self.root)
            return
        confirmed = messagebox.askyesno(
            "Очистить очередь?",
            "Будут очищены очередь и поле «Добавить ссылки». Ссылки сохранятся в архиве, "
            "история скачанных видео и файлы останутся на диске.",
            default=messagebox.NO, parent=self.root,
        )
        if not confirmed or self.is_downloading:
            return
        if not self.clear_queue_draft():
            messagebox.showerror("Очередь скачивания",
                                 "Не удалось очистить очередь. Проверьте доступ к папке истории скачиваний.",
                                 parent=self.root)
            return
        self._dashboard_session = None
        self.main_progress["value"] = 0
        self.progress_percent.configure(text="0%")
        self.refresh_download_dashboard()

    def _studio_build_events(self, side):
        card, box = rounded_panel(side, padx=14, pady=14)
        card.grid(row=0, column=0, sticky="new")
        box.columnconfigure(0, weight=1)
        panel_title(box, "Последние события", "history").grid(
            row=0, column=0, sticky="w")
        self.studio_recent_text = tk.StringVar(self.root, value="События появятся во время работы.")
        recent = ttk.Label(box, textvariable=self.studio_recent_text, style="Muted.TLabel",
                           width=1, anchor="nw", justify="left", wraplength=230)
        recent.grid(row=1, column=0, sticky="ew", pady=(9, 10))
        recent.bind("<Configure>", lambda e: recent.configure(wraplength=max(100, e.width - 2)))
        ttk.Separator(box).grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(box, text="Открыть журнал   ›", command=self.show_studio_log).grid(
            row=3, column=0, sticky="w")

    def _studio_build_log(self):
        win = self._studio_log_window = tool_window(self.root, "Журнал загрузок", 860, 510)
        win.minsize(460, 230)
        box = ttk.Frame(win, padding=12)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Журнал загрузок", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.log_context_menu = tk.Menu(box, tearoff=0)
        self.log_context_menu.add_command(label="Копировать", command=self._copy_selected_log_text)
        self._log_context_widget = None
        self.log_notebook = ttk.Notebook(box)
        self.log_notebook.pack(fill="both", expand=True)
        self.log_text_widgets = {}
        self._studio_log_tabs = {}
        for key, title in ((LOG_TAB_ALL, "Весь журнал"), (LOG_TAB_DOWNLOAD_FAILED, "Не скачалось"),
                           (LOG_TAB_ERRORS, "Ошибки")):
            page = ttk.Frame(self.log_notebook)
            self.log_notebook.add(page, text=title)
            self._studio_log_tabs[key] = page
            widget = scrolledtext.ScrolledText(page, width=1, height=1, wrap=tk.WORD,
                                               font=("Segoe UI", 10), relief="flat", padx=8, pady=6)
            widget.pack(fill="both", expand=True)
            self._configure_log_text_widget(widget)
            self.log_text_widgets[key] = widget
        self.log_text = self.log_text_widgets[LOG_TAB_ALL]

    def show_studio_log(self, tab=None):
        if tab in self._studio_log_tabs:
            self.log_notebook.select(self._studio_log_tabs[tab])
        show_tool(self._studio_log_window)

    def _studio_build_playlist(self):
        self._studio_playlist_source = self.video_list
        self._playlist_checked = set()
        self._playlist_visible = []
        win = self._studio_playlist_window = tool_window(self.root, "Плейлист YouTube", 850, 520)
        win.protocol("WM_DELETE_WINDOW", self._close_studio_playlist)
        win.bind("<Escape>", self._close_studio_playlist)
        win.bind("<Destroy>", self._destroy_playlist_titles, add="+")
        win.minsize(600, 290)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        self.video_list_frame = ttk.LabelFrame(win, text="Видео из HTML-плейлиста", padding=10)
        frame = self.video_list_frame
        frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        self.video_stats_label = ttk.Label(frame, text="Видео: 0", style="CardTitle.TLabel")
        self.video_stats_label.grid(row=0, column=0, sticky="w", pady=(0, 5))
        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="Добавить выбранные в очередь", style="Start.TButton",
                   command=self.add_selected_to_queue).pack(side="left")
        more = ttk.Menubutton(actions, text="Выбор")
        more.pack(side="left", padx=5)
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label="Выбрать все", command=self.select_all_videos)
        menu.add_command(label="Только новые", command=self.select_only_new)
        menu.add_command(label="Снять выбор", command=self.deselect_all_videos)
        menu.add_separator()
        menu.add_command(label="Удалить выбранные", command=self.delete_selected_videos)
        more.configure(menu=menu)
        ttk.Checkbutton(actions, text="Показать скачанные", variable=self.show_downloaded,
                        command=self.display_video_list).pack(side="right")
        self._playlist_rows = PlaylistRows(frame, self._set_playlist_checked, self._remove_playlist_video)
        self._playlist_rows.grid(row=2, column=0, sticky="nsew")
        self.video_canvas = self._playlist_rows.canvas
        # A toplevel participates in bindtags of its children, including labels and checkboxes.
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            win.bind(event, self._playlist_rows.scroll_wheel)
        ttk.Button(frame, text="Импортировать HTML…", command=self.load_from_html).grid(
            row=3, column=0, sticky="w", pady=(8, 0))

    def show_studio_playlist(self):
        show_tool(self._studio_playlist_window)

    def _close_studio_playlist(self, _event=None):
        self._cancel_playlist_title_lookup()
        self._studio_playlist_window.withdraw()
        return "break"

    def _studio_initialize(self):
        self.on_merge_change()

        self.log(
            "📂 История скачиваний: текущая очередь, очереди запусков, ссылки и логи",
            "DOWNLOAD_ARCHIVE_FOLDER_LINK",
        )
        optional_missing = getattr(self, "optional_missing_deps", [])
        if self.missing_deps or optional_missing:
            self.show_dependency_warning()
        else:
            if self.js_runtime_found:
                self.log(f"✅ JavaScript Runtime: {self.js_runtime_name}", "SUCCESS")
            self.log(
                f"✅ Все зависимости установлены. Готов к работе. Версия v{APP_VERSION} активна.",
                "SUCCESS",
            )
            self.log_retention_status()
            self.log(
                "📄 Ручная обработка: Обработать вручную — ссылки на несостоявшиеся загрузки "
                "и видео с ошибками конвертации",
                "MANUAL_FOLDER_LINK"
            )
            if self.should_write_problem_logs():
                self.log(
                    "📄 Диагностика проблем для ИИ: Логи проблем/Сессии/<запуск>/events.jsonl "
                    "+ incidents.jsonl + attachments; начинать с корневого latest_run.json "
                    "и README_FOR_CODEX.md",
                    "PROBLEM_FOLDER_LINK"
                )
            else:
                self.log(
                    "📄 Диагностика проблем для ИИ отключена галочкой «Писать логи проблем»",
                    "INFO"
                )
