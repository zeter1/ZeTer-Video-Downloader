"""Small, optional YouTube previews. Workers return data, Tk owns images."""

from io import BytesIO
from queue import Empty, Full, Queue
from threading import Event, Thread
from urllib.request import HTTPRedirectHandler, build_opener
import re

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    Image = None


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class QueueThumbnails:
    def __init__(self):
        self.inbox = Queue(maxsize=20)
        self.results = Queue()
        self.closed = Event()
        self.images = {}
        self.pending = set()
        self.started = False

    def request(self, video_id):
        if Image is None or not video_id or video_id in self.pending or video_id in self.images:
            return
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return
        try:
            self.inbox.put_nowait(video_id)
        except Full:
            return
        self.pending.add(video_id)
        if not self.started:
            self.started = True
            Thread(target=self._load, name="queue-thumbnails", daemon=True).start()

    def _load(self):
        opener = build_opener(NoRedirects)
        while not self.closed.is_set():
            try:
                video_id = self.inbox.get(timeout=0.3)
            except Empty:
                continue
            preview = None
            try:
                url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
                with opener.open(url, timeout=3) as response:
                    payload = response.read(128 * 1024 + 1)
                if len(payload) <= 128 * 1024:
                    with Image.open(BytesIO(payload), formats=["JPEG"]) as photo:
                        if 120 <= photo.width <= 640 and 68 <= photo.height <= 480:
                            preview = ImageOps.fit(photo.convert("RGB"), (66, 40))
            except (OSError, ValueError, Image.DecompressionBombError):
                pass  # Missing previews leave the standard video tile visible.
            self.results.put((video_id, preview))

    def collect(self, root):
        changed = False
        while True:
            try:
                video_id, image = self.results.get_nowait()
            except Empty:
                return changed
            self.pending.discard(video_id)
            self.images[video_id] = ImageTk.PhotoImage(image, master=root) if image else None
            changed = True
            if len(self.images) > 100:
                self.images.pop(next(iter(self.images)))
