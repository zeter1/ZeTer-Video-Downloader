"""A queue table that draws only the rows visible in its viewport."""

import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import NamedTuple
from urllib.parse import urlsplit
import webbrowser

from src.ui.studio_widgets import INK, LINE, MUTED, WHITE


class QueueRow(NamedTuple):
    url: str
    title: str
    status: str
    color: tuple[str, str]
    percent: float | None
    video_id: str | None
    primary: str | None
    detail: str = ""


def web_address(url):
    try:
        address = urlsplit(url)
        if (address.scheme in {"http", "https"} and address.hostname
                and not any(char.isspace() or ord(char) < 32 for char in url)):
            return address
    except ValueError:
        pass
    return None


def open_video_link(url):
    """Open the original web URL only in response to user input."""
    if web_address(url) is None:
        return False
    try:
        return webbrowser.open_new_tab(url)
    except (OSError, webbrowser.Error):
        return False


def source_label(url):
    address = web_address(url)
    if address is None:
        return ""
    host = address.hostname.removeprefix("www.")
    return {"youtube.com": "YouTube", "m.youtube.com": "YouTube",
            "youtu.be": "YouTube", "vimeo.com": "Vimeo"}.get(host, host)


def fitting_prefix(text, width, measure):
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(text[:middle]) <= width:
            low = middle
        else:
            high = middle - 1
    return low


def fit_lines(text, width, measure, max_lines):
    text = " ".join(text.split())
    if not text or width < measure("…"):
        return ()
    if measure(text) <= width:
        return (text,)
    if max_lines == 1:
        return (text[:fitting_prefix(text, width - measure("…"), measure)].rstrip() + "…",)
    end = fitting_prefix(text, width, measure)
    word_end = text.rfind(" ", 0, end + 1)
    if word_end > 0:
        end = word_end
    return (text[:end], *fit_lines(text[end:], width, measure, 1))


class QueueRows(ttk.Frame):
    def __init__(self, parent, row_actions, run_primary, thumbnail, request_preview):
        super().__init__(parent)
        self.row_actions, self.run_primary = row_actions, run_primary
        self.thumbnail, self.request_preview = thumbnail, request_preview
        self.rows = ()
        self.positions = {}
        self.selected = None
        self.title_font = tkfont.Font(self, family="Segoe UI", size=10, weight="bold")
        self.label_font = tkfont.Font(self, family="Segoe UI", size=9)
        self.status_font = tkfont.Font(self, family="Segoe UI", size=9, weight="bold")
        self.title_height = self.title_font.metrics("linespace")
        self.label_height = self.label_font.metrics("linespace")
        self.ROW_HEIGHT = max(54, 2 * self.title_height + self.label_height + 13)
        self.menu = tk.Menu(self, tearoff=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.heading = tk.Canvas(self, height=self.label_height + 13, bg="#eef3eb",
                                 highlightthickness=0, width=1)
        self.heading.grid(row=0, column=0, sticky="ew")
        self.canvas = tk.Canvas(self, background=WHITE, highlightthickness=1,
                                highlightbackground=LINE, highlightcolor="#60883f",
                                height=172, width=1, takefocus=True)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=lambda first, last: self._scrolled(scroll, first, last))
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Button-3>", self._context)
        self.canvas.bind("<MouseWheel>", lambda event: self._scroll(-event.delta / 120))
        self.canvas.bind("<Button-4>", lambda _event: self._scroll(-1))
        self.canvas.bind("<Button-5>", lambda _event: self._scroll(1))
        self.canvas.bind("<Up>", lambda _event: self._move(-1))
        self.canvas.bind("<Down>", lambda _event: self._move(1))
        self.canvas.bind("<Return>", self._selected_menu)
        self.canvas.bind("<Shift-F10>", self._selected_menu)
        self.canvas.bind("<Control-Return>", self._open_selected_link)

    def _scrolled(self, scrollbar, first, last):
        scrollbar.set(first, last)
        self.redraw()

    def _scroll(self, rows):
        self.canvas.yview_scroll(round(rows * self.ROW_HEIGHT), "units")
        return "break"

    def update_rows(self, rows, *, reset_scroll=False):
        canvas = self.canvas
        top = max(0, int(canvas.canvasy(0)))
        index, offset = divmod(top, self.ROW_HEIGHT)
        anchor = self.rows[index].url if index < len(self.rows) else None
        self.positions = {row.url: index for index, row in enumerate(rows)}
        if anchor not in self.positions:
            anchor = next((row.url for row in self.rows[index:] if row.url in self.positions), None)
            offset = 0
        self.rows = rows
        if self.selected not in self.positions:
            self.selected = None
        extent = max(1, len(rows) * self.ROW_HEIGHT)
        canvas.configure(scrollregion=(0, 0, 0, extent), yscrollincrement=1)
        next_top = (self.positions[anchor] * self.ROW_HEIGHT + offset
                    if anchor is not None and not reset_scroll else 0)
        if next_top != top or reset_scroll or not rows:
            canvas.yview_moveto(next_top / extent)
        self.redraw()

    def _columns(self, width):
        action = max(122, self.label_font.measure("Повторить MP3") + 58)
        status = max(146, self.status_font.measure("MP4 и MP3 готовы") + 27)
        compact = width < action + status + max(210, self.title_font.measure("Название видео") * 2)
        action_x = width - (45 if compact else action)
        return compact, action_x if compact else action_x - status, action_x

    def redraw(self):
        c = self.canvas
        width, height = c.winfo_width(), c.winfo_height()
        if width < 100 or height <= 1:
            return
        compact, status_x, action_x = self._columns(width)
        heading = self.heading
        heading.delete("all")
        hy = (self.label_height + 13) // 2
        heading.create_text(13, hy, text="Видео и статус" if compact else "Видео",
                            anchor="w", font=self.status_font, fill=MUTED)
        if not compact:
            heading.create_text(status_x, hy, text="Статус", anchor="w", font=self.status_font, fill=MUTED)
            heading.create_text(action_x, hy, text="Действия", anchor="w", font=self.status_font, fill=MUTED)
        c.delete("row")
        start = max(0, int(c.canvasy(0)) // self.ROW_HEIGHT)
        stop = start + height // self.ROW_HEIGHT + 2
        self._visible_images = []
        for index, row in enumerate(self.rows[start:stop], start):
            self._draw(row, index, width, compact, status_x, action_x)

    def _draw(self, row, index, width, compact, status_x, action_x):
        c = self.canvas
        y = index * self.ROW_HEIGHT
        middle = y + self.ROW_HEIGHT // 2
        background = "#e6efdf" if row.url == self.selected else ("#f8faf7" if index % 2 else WHITE)
        c.create_rectangle(1, y, width - 1, y + self.ROW_HEIGHT, outline="",
                           fill=background, tags="row")
        c.create_line(1, y + self.ROW_HEIGHT, width - 1, y + self.ROW_HEIGHT, fill="#e3e9df", tags="row")
        ink, accent = row.color
        if accent:
            c.create_rectangle(3, y + 7, 6, y + self.ROW_HEIGHT - 7,
                               fill=accent, outline="", tags="row")

        title_width = status_x - 25
        if row.video_id and not compact and title_width >= 320:
            preview = self.thumbnail(row.video_id)
            if preview:
                self._visible_images.append(preview)
                c.create_image(status_x - 12, middle, anchor="e", image=preview, tags="row")
                title_width -= preview.width() + 10
            else:
                self.request_preview(row.video_id)
        lines = fit_lines(row.title, title_width, self.title_font.measure, 2)
        subtitle = " · ".join(part for part in (source_label(row.url), row.detail) if part)
        status = row.status if row.percent is None else f"{row.status} {int(row.percent)}%"
        status_width = self.status_font.measure(status)
        content_y = y + 6
        for line in lines:
            c.create_text(13, content_y, text=line, anchor="nw", font=self.title_font, fill=INK, tags="row")
            content_y += self.title_height
        sub_y = y + self.ROW_HEIGHT - self.label_height - 5 if compact else content_y + 2
        sub_width = action_x - status_width - 35 if compact else title_width
        for value in fit_lines(subtitle, sub_width, self.label_font.measure, 1):
            c.create_text(13, sub_y, text=value, anchor="nw", font=self.label_font, fill=MUTED, tags="row")
        sx = max(13, action_x - status_width - 9) if compact else status_x
        sy = sub_y + self.label_height // 2 if compact else middle - (6 if row.percent is not None else 0)
        c.create_text(sx, sy, text=status, anchor="w", font=self.status_font, fill=ink, tags="row")
        if row.percent is not None and not compact:
            end = action_x - 14
            c.create_line(sx, middle + 10, end, middle + 10, width=3, fill="#dae4d5", tags="row")
            c.create_line(sx, middle + 10, sx + (end - sx) * row.percent / 100, middle + 10,
                          width=3, fill="#63974b", tags="row")
        self._action(width - 34, middle, width - 8, "⋯", f"menu:{index}")
        if row.primary and not compact:
            self._action(action_x, middle, width - 40, row.primary, f"primary:{index}")

    def _action(self, left, y, right, text, tag):
        self.canvas.create_rectangle(left, y - 13, right, y + 13, fill="#f1f5ed",
                                     outline=LINE, tags=("row", tag))
        self.canvas.create_text((left + right) // 2, y, text=text, fill=INK,
                                font=self.label_font, tags=("row", tag))

    def _select_at(self, event):
        y = self.canvas.canvasy(event.y)
        index = int(y) // self.ROW_HEIGHT
        if 0 <= index < len(self.rows):
            self.canvas.focus_set()
            self.selected = self.rows[index].url
            self.redraw()
            return index, y
        return None, y

    def _click(self, event):
        index, y = self._select_at(event)
        if index is None:
            return
        for action, invoke in (("primary", lambda: self.run_primary(self.selected)),
                               ("menu", self._selected_menu)):
            box = self.canvas.bbox(f"{action}:{index}")
            if box and box[0] <= event.x <= box[2] and box[1] <= y <= box[3]:
                invoke()
                return "break"

    def _context(self, event):
        index, _ = self._select_at(event)
        if index is not None:
            self._show_menu(event.x_root, event.y_root)
            return "break"

    def reveal(self, url):
        index = self.positions.get(url)
        if index is None:
            return False
        self.selected = url
        top = self.canvas.canvasy(0)
        y = index * self.ROW_HEIGHT
        if y < top or y + self.ROW_HEIGHT > top + self.canvas.winfo_height():
            self.canvas.yview_moveto(index / len(self.rows))
        self.redraw()
        return True

    def _move(self, delta):
        if self.rows:
            current = self.positions.get(self.selected)
            index = 0 if current is None else min(len(self.rows) - 1, max(0, current + delta))
            self.reveal(self.rows[index].url)
        return "break"

    def _selected_menu(self, _event=None):
        if self.selected is None:
            return "break"
        self.reveal(self.selected)
        index = self.positions[self.selected]
        y = int((index + 1) * self.ROW_HEIGHT - self.canvas.canvasy(0))
        top = self.canvas.winfo_rooty()
        self._show_menu(self.canvas.winfo_rootx() + self.canvas.winfo_width() - 34,
                        top + min(y, self.canvas.winfo_height()))
        return "break"

    def _show_menu(self, x, y):
        self.menu.delete(0, "end")
        for label, command in self.row_actions(self.selected):
            self.menu.add_command(label=label, command=command)
        if self.menu.index("end") is not None:
            try:
                self.menu.tk_popup(x, y)
            finally:
                self.menu.grab_release()

    def _open_selected_link(self, _event=None):
        if self.selected is not None:
            open_video_link(self.selected)
        return "break"
