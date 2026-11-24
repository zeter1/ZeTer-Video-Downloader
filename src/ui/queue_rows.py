"""A scrollable queue of video cards. It draws only lines in the viewport."""

import tkinter as tk
from tkinter import ttk
from urllib.parse import urlsplit
import webbrowser


def open_video_link(url):
    """Open the original web URL only in response to user input."""
    try:
        address = urlsplit(url)
        if (address.scheme in {"http", "https"} and address.hostname
                and not any(char.isspace() or ord(char) < 32 for char in url)):
            return webbrowser.open_new_tab(url)
    except (ValueError, OSError, webbrowser.Error):
        pass
    return False


class QueueRows(ttk.Frame):
    ROW_HEIGHT = 62

    def __init__(self, parent, on_action, thumbnail, request_preview):
        super().__init__(parent)
        self.on_action = on_action
        self.thumbnail, self.request_preview = thumbnail, request_preview
        self.rows = ()
        self.selected = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background="white", highlightthickness=1,
                                highlightbackground="#dce0d6", highlightcolor="#60883f",
                                height=220, width=1, takefocus=True)
        self.canvas.grid(sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=lambda first, last: self._scrolled(scroll, first, last))
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120) * 3, "units"))
        self.canvas.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Up>", lambda _event: self._move(-1))
        self.canvas.bind("<Down>", lambda _event: self._move(1))
        self.canvas.bind("<Return>", lambda _event: self._act())
        self.canvas.bind("<Control-Return>", self._open_selected_link)
        self.canvas.tag_bind("video-link", "<Enter>", lambda _event: self.canvas.configure(cursor="hand2"))
        self.canvas.tag_bind("video-link", "<Leave>", lambda _event: self.canvas.configure(cursor=""))

    def _scrolled(self, scrollbar, first, last):
        scrollbar.set(first, last)
        self.redraw()

    def update_rows(self, rows):
        self.rows = rows
        if all(row[0] != self.selected for row in rows):
            self.selected = None
        self.canvas.configure(scrollregion=(0, 0, 0, max(1, len(rows) * self.ROW_HEIGHT)),
                              yscrollincrement=max(1, self.ROW_HEIGHT // 3))
        self.redraw()

    def redraw(self):
        c = self.canvas
        width, height = c.winfo_width(), c.winfo_height()
        if width < 100 or height <= 1:
            return
        c.delete("row")
        if not self.rows:
            c.create_text(width // 2, min(height // 2, 64), text="Здесь появятся файлы очереди",
                          fill="#5b645b", font=("Segoe UI", 10), tags="row")
            return
        start = max(0, int(c.canvasy(0)) // self.ROW_HEIGHT)
        stop = start + height // self.ROW_HEIGHT + 2
        self._visible_images = []
        for index, row in enumerate(self.rows[start:stop], start):
            self._draw(row, index, width)

    def _draw(self, row, index, width):
        url, title, status, color, percent, video_id, action = row
        c = self.canvas
        y = index * self.ROW_HEIGHT
        middle = y + self.ROW_HEIGHT // 2
        c.create_rectangle(1, y, width - 1, y + self.ROW_HEIGHT, outline="",
                           fill="#eef4e7" if url == self.selected else "white", tags="row")
        c.create_line(1, y + self.ROW_HEIGHT, width - 1, y + self.ROW_HEIGHT,
                      fill="#e6e8e2", tags="row")
        preview = self.thumbnail(video_id)
        if preview:
            self._visible_images.append(preview)
            c.create_image(13, middle, anchor="w", image=preview, tags="row")
        else:
            self.request_preview(video_id)
            self._box(13, middle - 20, 79, middle + 20, "#e8eee3", "#e8eee3")
            c.create_polygon(42, middle - 7, 42, middle + 7, 54, middle,
                             fill="#5d7853", tags="row")
        badge_x = width - 199
        available = max(15, (badge_x - 105) // 8)
        c.create_text(94, middle - 8, text=self._short(title, available), anchor="w",
                      font=("Segoe UI", 10, "bold"), fill="#20251f", tags="row")
        c.create_text(94, middle + 12, text=self._short(url, available + 4), anchor="w",
                      font=("Segoe UI", 8, "underline"), fill="#365d35",
                      tags=("row", "video-link", f"video-link:{index}"))
        ink, background = color
        badge_y = middle - (12 if percent is not None else 0)
        self._box(badge_x, badge_y - 10, width - 59, badge_y + 11, background, background)
        c.create_text(badge_x + 7, badge_y, text=status, fill=ink, anchor="w",
                      font=("Segoe UI", 8, "bold"), tags="row")
        if percent is not None:
            c.create_line(badge_x + 1, middle + 9, badge_x + 71, middle + 9,
                          fill="#dce4d7", width=3, tags="row")
            c.create_line(badge_x + 1, middle + 9, badge_x + 1 + .7 * percent, middle + 9,
                          fill="#739d51", width=3, tags="row")
            c.create_text(badge_x + 80, middle + 9, anchor="w", text=f"{int(percent)}%",
                          fill="#566950", font=("Consolas", 8), tags="row")
        if action:
            self._box(width - 44, middle - 13, width - 16, middle + 13, "#fafbf8", "#dce0d6")
            c.create_text(width - 30, middle, text=action, fill=ink,
                          font=("Segoe UI Symbol", 11), tags="row")

    @staticmethod
    def _short(value, length):
        return value if len(value) <= length else value[:length - 1] + "…"

    def _box(self, x1, y1, x2, y2, fill, border):
        r = 5
        self.canvas.create_polygon(x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r,
                                   x2, y2, x2-r, y2, x1+r, y2, x1, y2, x1, y2-r,
                                   x1, y1+r, x1, y1, smooth=True, fill=fill, outline=border, tags="row")

    def _click(self, event):
        y = self.canvas.canvasy(event.y)
        index = int(y) // self.ROW_HEIGHT
        if 0 <= index < len(self.rows):
            link = self.canvas.bbox(f"video-link:{index}")
            self.canvas.focus_set()
            self.selected = self.rows[index][0]
            self.redraw()
            if link and link[0] <= event.x <= link[2] and link[1] <= y <= link[3]:
                self._open_selected_link()
            elif self.rows[index][-1] and event.x >= self.canvas.winfo_width() - 49:
                self._act()

    def _move(self, delta):
        if not self.rows:
            return "break"
        current = next((i for i, row in enumerate(self.rows) if row[0] == self.selected), None)
        index = 0 if current is None else min(len(self.rows) - 1, max(0, current + delta))
        self.selected = self.rows[index][0]
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        y = index * self.ROW_HEIGHT
        if y < top or y + self.ROW_HEIGHT > bottom:
            self.canvas.yview_moveto(index / len(self.rows))
        self.redraw()
        return "break"

    def _act(self):
        if self.selected is not None:
            self.on_action(self.selected)
        return "break"

    def _open_selected_link(self, _event=None):
        if self.selected is not None:
            open_video_link(self.selected)
        return "break"
