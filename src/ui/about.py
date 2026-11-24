"""The in-app About page and the author's public contacts."""

from tkinter import ttk
import webbrowser

from src.core.constants import APP_VERSION
from src.ui.studio_widgets import rounded_panel


CONTACTS = (
    ("Почта", "zeter11@gmail.com", "mailto:zeter11@gmail.com"),
    ("Telegram", "@zeter1", "https://t.me/zeter1"),
    ("Telegram-группа", "@zeterchat", "https://t.me/zeterchat"),
    ("Сайт", "dkoles.com", "https://dkoles.com/"),
)
SOCIAL_LINKS = (
    ("LinkedIn", "https://www.linkedin.com/in/zeter/"),
    ("Facebook", "https://www.facebook.com/zeter1"),
    ("ВКонтакте", "https://vk.ru/zeter1"),
    ("Instagram", "https://www.instagram.com/zeter1992/"),
    ("TenChat", "https://tenchat.ru/zeter"),
    ("X", "https://x.com/zeter1"),
    ("Threads", "https://www.threads.com/@zeter1992"),
    ("Одноклассники", "https://ok.ru/zeter1"),
)


def _link(parent, text, url):
    return ttk.Button(parent, text=text, width=0, style="About.Link.TButton", cursor="hand2",
                      command=lambda: webbrowser.open_new_tab(url))


def build_about_page(parent):
    page = ttk.Frame(parent, style="Paper.TFrame")
    outer_margin = round(page.winfo_fpixels("13.5p"))
    card_padding = round(page.winfo_fpixels("18p"))
    max_card_width = round(page.winfo_fpixels("630p"))
    side_by_side_width = round(page.winfo_fpixels("432p"))

    style = ttk.Style(page)
    style.configure("About.Link.TButton", anchor="w", padding=(0, "2.25p"))
    style.configure("About.Field.TLabel", padding=("7.5p", "3p"),
                    background=style.lookup("Link.TButton", "background", ("active",)),
                    foreground=style.lookup("Link.TButton", "foreground"))

    card, body = rounded_panel(page, padx=card_padding, pady="15p")
    card.pack(fill="x")
    headline = ttk.Frame(body)
    headline.pack(fill="x")
    ttk.Label(headline, text="ZeTer Video Downloader", style="Brand.TLabel").pack(side="left")
    ttk.Label(headline, text=f"Версия {APP_VERSION}", style="About.Field.TLabel").pack(side="right")
    description = ttk.Label(
        body, width=1, justify="left",
        text="Скачивает видео в MP4 до 1080p и создаёт MP3.\n"
             "Поддерживает очередь ссылок, импорт HTML-плейлистов и загрузку субтитров.",
    )
    description.pack(fill="x", pady=("6p", "9p"))
    description.bind("<Configure>", lambda event: description.configure(wraplength=event.width))
    developer = ttk.Frame(body)
    developer.pack(fill="x")
    ttk.Label(developer, text="Разработчик", style="Muted.TLabel").pack(side="left")
    _link(developer, "Дмитрий Колесниченко · GitHub", "https://github.com/zeter1").pack(
        side="left", padx=(9, 0))
    ttk.Separator(body).pack(fill="x", pady="10.5p")

    sections = ttk.Frame(body)
    sections.pack(fill="x")
    sections.columnconfigure(0, weight=11, uniform="about")
    sections.columnconfigure(2, weight=10, uniform="about")
    contacts_section = ttk.Frame(sections)
    ttk.Label(contacts_section, text="Контакты", style="CardTitle.TLabel").pack(anchor="w")
    contacts = ttk.Frame(contacts_section)
    contacts.pack(fill="x", pady=("4.5p", 0))
    contacts.columnconfigure((0, 1), weight=1, uniform="contacts")
    for index, (label, address, url) in enumerate(CONTACTS):
        item = ttk.Frame(contacts)
        item.grid(row=index // 2, column=index % 2, sticky="w", pady="2.25p")
        ttk.Label(item, text=label, style="Muted.TLabel").pack(anchor="w")
        _link(item, address, url).pack(anchor="w")

    divider = ttk.Separator(sections, orient="vertical")
    social_section = ttk.Frame(sections)
    ttk.Label(social_section, text="Социальные сети", style="CardTitle.TLabel").pack(anchor="w")
    social = ttk.Frame(social_section)
    social.pack(fill="x", pady=("4.5p", 0))
    social.columnconfigure((0, 1), weight=1, uniform="social")
    for index, (label, url) in enumerate(SOCIAL_LINKS):
        _link(social, label, url).grid(row=index // 2, column=index % 2, sticky="w", pady="1.5p")

    gutter = None
    wide_layout = None

    def fit_layout(width):
        nonlocal gutter, wide_layout
        next_gutter = max(outer_margin, (width - max_card_width + 1) // 2)
        if gutter != next_gutter:
            page.configure(padding=(next_gutter, "10.5p"))
            gutter = next_gutter

        wide = width - 2 * (gutter + card_padding) >= side_by_side_width
        if wide == wide_layout:
            return
        wide_layout = wide
        if wide:
            contacts_section.grid(row=0, column=0, columnspan=1, sticky="new")
            divider.grid(row=0, column=1, sticky="ns", padx="15p")
            social_section.grid(row=0, column=2, columnspan=1, sticky="new", pady=0)
        else:
            divider.grid_remove()
            contacts_section.grid(row=0, column=0, columnspan=3, sticky="ew")
            social_section.grid(row=1, column=0, columnspan=3, sticky="ew", pady=("9p", 0))

    fit_layout(parent.winfo_width())
    page.bind("<Configure>", lambda event: fit_layout(event.width))
    return page
