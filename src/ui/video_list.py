"""Список видео плейлиста и выбор по идентификаторам."""

from tkinter import font, messagebox, ttk
import tkinter as tk


def playlist_video_key(video):
    return video.get("video_id") or video["url"]


class _PlaylistRow(ttk.Frame):
    def __init__(self, parent, check, remove):
        super().__init__(parent, padding=(3, 7))
        self.key = None
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.checked = tk.BooleanVar(self)
        self.checkbox = ttk.Checkbutton(self, variable=self.checked, takefocus=False,
                                        command=lambda: check(self.key, self.checked.get()))
        self.checkbox.grid(row=0, column=0, padx=(0, 6))
        info = ttk.Frame(self)
        info.grid(row=0, column=1, sticky="ew")
        self.title = ttk.Label(info, font=("Segoe UI", 10, "bold"), width=1, justify="left")
        self.title.pack(fill="x")
        self.url = ttk.Label(info, style="Muted.TLabel", width=1)
        self.url.pack(fill="x")
        info.bind("<Configure>", lambda event: self.title.configure(wraplength=event.width))
        self.status = ttk.Label(self, width=9, anchor="center")
        self.status.grid(row=0, column=2, padx=8)
        self.remove = ttk.Button(self, text="Убрать", takefocus=False,
                                 command=lambda: remove(self.key))
        self.remove.grid(row=0, column=3)
        ttk.Separator(self).grid(row=1, column=0, columnspan=4, sticky="ew")

    def update_video(self, number, video, checked):
        self.key = playlist_video_key(video)
        self.checked.set(checked)
        self.title.configure(text=f"{number}. {video['title']}")
        self.url.configure(text=video["url"])
        downloaded = video.get("is_downloaded", False)
        self.status.configure(text="Скачано" if downloaded else "Новое",
                              foreground="#627368" if downloaded else "#36662b")


class PlaylistRows(ttk.Frame):
    """Canvas с переиспользуемыми нативными контролами для видимой части списка."""

    def __init__(self, parent, check, remove):
        super().__init__(parent)
        self._check, self._remove = check, remove
        self._rows = ()
        self._checked = set()
        self._visible = {}
        self._spare = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background="white", width=1, height=120, takefocus=True,
                                highlightthickness=1, highlightbackground="white",
                                highlightcolor="#60883f")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self._scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        title_font = font.Font(root=self, font=("Segoe UI", 10, "bold"))
        body_font = font.nametofont("TkDefaultFont", root=self)
        self._row_height = 3 * title_font.metrics("linespace") + body_font.metrics("linespace") + 16
        self.canvas.configure(yscrollincrement=1)
        self._bind_keys(self.canvas, None)

    def scroll_wheel(self, event):
        steps = -int(event.delta / 120) if event.delta else (-1 if event.num == 4 else 1)
        self.canvas.yview_scroll(steps * self._row_height, "units")
        return "break"

    def set_rows(self, rows, checked, *, reset_scroll=False):
        self._rows, self._checked = rows, checked
        self.canvas.configure(scrollregion=(0, 0, 0, len(rows) * self._row_height))
        if reset_scroll or len(rows) * self._row_height <= self.canvas.winfo_height():
            self.canvas.yview_moveto(0)
        self.redraw()

    def _scrolled(self, first, last):
        self._scroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self._scroll.grid_remove()
        else:
            self._scroll.grid()
        self.redraw()

    def redraw(self):
        canvas = self.canvas
        width, height = canvas.winfo_width() - 2, canvas.winfo_height()
        if width < 2 or height <= 1:
            return
        start = max(0, int(canvas.canvasy(0)) // self._row_height)
        stop = min(len(self._rows), start + height // self._row_height + 2)
        focus = canvas.focus_get()
        for index in tuple(self._visible):
            if not start <= index < stop:
                item, row = self._visible.pop(index)
                if focus in (row.checkbox, row.remove):
                    canvas.focus_set()
                canvas.itemconfigure(item, state="hidden")
                self._spare.append((item, row))
        for index in range(start, stop):
            if index not in self._visible:
                self._visible[index] = self._spare.pop() if self._spare else self._new_row()
            item, row = self._visible[index]
            number, video = self._rows[index]
            key = playlist_video_key(video)
            if row.key != key and focus in (row.checkbox, row.remove):
                canvas.focus_set()
            row.update_video(number, video, key in self._checked)
            canvas.coords(item, 1, index * self._row_height)
            canvas.itemconfigure(item, width=width, height=self._row_height, state="normal")

    def _new_row(self):
        row = _PlaylistRow(self.canvas, self._check, self._remove)
        item = self.canvas.create_window(0, 0, window=row, anchor="nw")
        for widget in (row.checkbox, row.remove):
            self._bind_keys(widget, row)
            widget.bind("<Button-1>", lambda event: event.widget.focus_set(), add="+")
        return item, row

    def _bind_keys(self, widget, row):
        for event, step in (("<Up>", -1), ("<Down>", 1), ("<Prior>", "page-up"),
                            ("<Next>", "page-down"), ("<Home>", "first"), ("<End>", "last")):
            widget.bind(event, lambda _event, step=step: self._move_focus(row, step))
        widget.bind("<Return>", lambda _event: self._activate(widget, row))
        if row is None:
            widget.bind("<space>", lambda _event: self._activate(widget, row))
        else:
            widget.bind("<Left>", lambda _event: self._focus_control(row.checkbox))
            widget.bind("<Right>", lambda _event: self._focus_control(row.remove))

    @staticmethod
    def _focus_control(widget):
        widget.focus_set()
        return "break"

    def _activate(self, widget, row):
        if row is None:
            self._move_focus(None, 0)
            widget = self.canvas.focus_get()
        if isinstance(widget, (ttk.Checkbutton, ttk.Button)):
            widget.invoke()
        return "break"

    def _move_focus(self, row, step):
        if not self._rows:
            return "break"
        top = max(0, int(self.canvas.canvasy(0)))
        height = self.canvas.winfo_height()
        first = top // self._row_height
        page = max(1, height // self._row_height)
        index = next((i for i, (_, visible) in self._visible.items() if visible is row), first)
        if step == "first":
            index = 0
        elif step == "last":
            index = len(self._rows) - 1
        elif step == "page-up":
            index -= page
        elif step == "page-down":
            index += page
        else:
            index += step if row is not None else 0
        index = max(0, min(index, len(self._rows) - 1))
        y = index * self._row_height
        if y < top or y + self._row_height > top + height:
            new_top = y if y < top else y + self._row_height - height + 2
            self.canvas.yview_moveto(new_top / (len(self._rows) * self._row_height))
            self.redraw()
        if index in self._visible:
            self._visible[index][1].checkbox.focus_set()
        return "break"


class VideoListMixin:
    def display_video_list(self) -> None:
        replaced = self.video_list is not self._studio_playlist_source
        self._playlist_videos_by_key = {playlist_video_key(video): video for video in self.video_list}
        if replaced:
            self._playlist_checked = {key for key, video in self._playlist_videos_by_key.items()
                                      if not video.get("is_downloaded", False)}
        else:
            self._playlist_checked.intersection_update(self._playlist_videos_by_key)

        show_downloaded = self.show_downloaded.get()
        self._playlist_visible = [
            (number, video) for number, video in enumerate(self.video_list, 1)
            if show_downloaded or not video.get("is_downloaded", False)
        ]
        self._playlist_rows.set_rows(self._playlist_visible, self._playlist_checked,
                                     reset_scroll=replaced)
        downloaded = sum(bool(video.get("is_downloaded", False)) for video in self.video_list)
        total = len(self.video_list)
        self.update_video_stats(total, total - downloaded, downloaded)
        if replaced:
            self._studio_playlist_source = self.video_list
            self.show_studio_playlist()

    def update_video_stats(self, total: int, new: int, downloaded: int) -> None:
        self.video_stats_label.config(text=f"Всего {total}   ·   Новых {new}   ·   Скачано {downloaded}")

    def _set_playlist_checked(self, key, checked):
        if checked:
            self._playlist_checked.add(key)
        else:
            self._playlist_checked.discard(key)

    def select_only_new(self) -> None:
        count = 0
        for _, video in self._playlist_visible:
            checked = not video.get("is_downloaded", False)
            self._set_playlist_checked(playlist_video_key(video), checked)
            count += checked
        self._playlist_rows.redraw()
        self.log(f"✅ Выбрано {count} новых видео", "SUCCESS")

    def _remove_playlist_video(self, key):
        for index, video in enumerate(self.video_list):
            if playlist_video_key(video) == key:
                self.delete_video(index)
                break

    def delete_video(self, index: int) -> None:
        if 0 <= index < len(self.video_list):
            del self.video_list[index]
            self.display_video_list()

    def _selected_playlist_videos(self):
        return [video for _, video in self._playlist_visible
                if playlist_video_key(video) in self._playlist_checked]

    def delete_selected_videos(self) -> None:
        selected = {playlist_video_key(video) for video in self._selected_playlist_videos()}
        if not selected:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного видео")
            return
        if messagebox.askyesno("Подтверждение", f"Удалить {len(selected)} видео из списка?"):
            self.video_list[:] = [video for video in self.video_list
                                  if playlist_video_key(video) not in selected]
            self.display_video_list()
            self.log(f"🗑️ Удалено {len(selected)} видео", "SUCCESS")

    def select_all_videos(self) -> None:
        self._playlist_checked.update(playlist_video_key(video) for _, video in self._playlist_visible)
        self._playlist_rows.redraw()

    def deselect_all_videos(self) -> None:
        self._playlist_checked.difference_update(playlist_video_key(video) for _, video in self._playlist_visible)
        self._playlist_rows.redraw()

    def add_selected_to_queue(self) -> None:
        selected = [video["url"] for video in self._selected_playlist_videos()]
        if not selected:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного видео")
            return
        added = self.add_urls_with_check(selected)
        if added > 0:
            messagebox.showinfo("Готово", f"✅ Добавлено {added} видео в очередь")
