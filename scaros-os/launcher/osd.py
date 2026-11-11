#!/usr/bin/env python3
"""Scaros OS — affichage par-dessus les applis (volume, notifications).

Le menu de Scaros est une page web : quand YouTube ou une autre appli est ouverte en plein
écran, elle le cache. Ce petit programme ouvre des fenêtres « toujours devant » (non gérées
par Openbox, elles ne prennent jamais le focus) pour montrer le volume et les notifications.

scaros.py le lance et lui envoie une ligne JSON par message sur l'entrée standard :
    {"t": "volume", "level": 45, "muted": false}
    {"t": "note", "icon": "gamepad", "text": "Manette connectée.", "ms": 3500}
    {"t": "hide"}
"""

import json
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Rsvg", "2.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo, Rsvg  # noqa: E402

FONT = "Inter, Noto Sans, Sans"
SHEET = (0.086, 0.106, 0.141)     # #161b24 : toujours sombre, par-dessus une vidéo
LINE = (1, 1, 1, 0.13)
TEXT = (0.953, 0.961, 0.976)

# mêmes dessins que les icônes du menu (24 × 24, trait)
ICONS = {
    "speaker": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    "mute": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    "gamepad": '<path d="M7 7h10a4 4 0 0 1 4 4l.6 4.6a2.6 2.6 0 0 1-4.6 2L15 15H9l-2 2.6a2.6 2.6 0 0 1-4.6-2L3 11a4 4 0 0 1 4-4z"/>'
               '<path d="M7.5 10v3M6 11.5h3"/><circle cx="15.5" cy="10.5" r=".9" fill="#fff"/><circle cx="17.5" cy="12.8" r=".9" fill="#fff"/>',
    "headphones": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="14" width="4.5" height="6.5" rx="1.5"/>'
                  '<rect x="16.5" y="14" width="4.5" height="6.5" rx="1.5"/>',
    "bluetooth": '<path d="M7 7l10 10-5 5V2l5 5L7 17"/>',
    "airplay": '<path d="M6 17H4.5A1.5 1.5 0 0 1 3 15.5v-10A1.5 1.5 0 0 1 4.5 4h15A1.5 1.5 0 0 1 21 5.5v10a1.5 1.5 0 0 1-1.5 1.5H18"/>'
               '<path d="M12 14l5 6H7z"/>',
    "wifi": '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.6 16a5 5 0 0 1 6.8 0"/><circle cx="12" cy="19.3" r="1.1" fill="#fff"/>',
    "alert": '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4.5M12 17.3v.2"/>',
    "mouse": '<rect x="6" y="3" width="12" height="18" rx="6"/><path d="M12 7v3.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "download": '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "play": '<path d="M7 5v14l11-7z"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
}


def icon_handle(name):
    body = ICONS.get(name, ICONS["alert"])
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" fill="none" '
           f'stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    return Rsvg.Handle.new_from_data(svg.encode())


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def rounded_region(w, h, r):
    """Forme de la fenêtre : coins arrondis (sans compositeur, c'est la seule façon)."""
    region = cairo.Region()
    for y in range(h):
        dy = max(0, r - y - 0.5, y + 0.5 - (h - r))
        inset = 0 if dy == 0 else int(round(r - (r * r - dy * dy) ** 0.5))
        region.union(cairo.RectangleInt(inset, y, w - 2 * inset, 1))
    return region


class Bubble(Gtk.Window):
    """Une fenêtre flottante, dessinée à la main, qui se cache toute seule."""

    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)  # fenêtre non gérée : jamais de focus
        self.set_app_paintable(True)
        self.set_accept_focus(False)
        self.connect("draw", self.on_draw)
        self.timer = None
        self.size = (1, 1)

    @staticmethod
    def screen_size():
        """Taille de l'écran utilisé (la télé ou l'écran du PC, selon ce que Scaros a choisi)."""
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        geo = mon.get_geometry()
        return geo.x, geo.y, geo.width, geo.height

    def unit(self):
        """Comme le menu : 1 rem = 1 % de la largeur (écran 16:9)."""
        _, _, w, h = self.screen_size()
        return min(w / 100, h * 1.7778 / 100)

    def place(self, w, h, x, y):
        w, h = int(w), int(h)
        if (w, h) != self.size:
            self.size = (w, h)
            self.resize(w, h)
            self.shape_combine_region(rounded_region(w, h, int(self.unit() * 1.3)))
        self.move(int(x), int(y))
        self.queue_draw()
        self.show()
        if self.get_window():
            self.get_window().raise_()

    def hide_later(self, ms):
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(ms, self._hide)

    def _hide(self):
        self.timer = None
        self.hide()
        return False

    def cancel(self):
        """Cache tout de suite (appli fermée, menu revenu devant)."""
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        self.hide()

    def background(self, cr, w, h):
        u = self.unit()
        cr.set_source_rgb(*SHEET)
        cr.paint()
        cr.set_source_rgba(*LINE)
        cr.set_line_width(max(1, u * 0.08))
        rounded(cr, 0.5, 0.5, w - 1, h - 1, u * 1.3)
        cr.stroke()

    def draw_icon(self, cr, name, x, y, size):
        cr.save()
        cr.translate(x, y)
        cr.scale(size / 24, size / 24)
        rect = Rsvg.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 0, 0, 24, 24
        icon_handle(name).render_document(cr, rect)
        cr.restore()


class Volume(Bubble):
    def __init__(self):
        super().__init__()
        self.level, self.muted = 0, False

    def show_level(self, level, muted):
        self.level, self.muted = max(0, min(100, int(level or 0))), bool(muted)
        u = self.unit()
        w, h = u * 34.6, u * 4.8
        sx, sy, sw, _ = self.screen_size()
        self.place(w, h, sx + (sw - w) / 2, sy + u * 1.6)
        self.hide_later(1700)

    def on_draw(self, _w, cr):
        u = self.unit()
        w, h = self.size
        self.background(cr, w, h)
        self.draw_icon(cr, "mute" if self.muted else "speaker", u * 1.5, (h - u * 2.4) / 2, u * 2.4)
        bx, bw, bh = u * 5.2, u * 22, u * 0.7
        by = (h - bh) / 2
        cr.set_source_rgba(1, 1, 1, 0.14)
        rounded(cr, bx, by, bw, bh, bh / 2)
        cr.fill()
        fill = 0 if self.muted else bw * self.level / 100
        if fill > 0:
            cr.set_source_rgb(*TEXT)
            rounded(cr, bx, by, max(fill, bh), bh, bh / 2)
            cr.fill()
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(f"{FONT} Bold {u * 1.8 * 0.75:.1f}"))
        layout.set_text("Muet" if self.muted else str(self.level), -1)
        tw, th = layout.get_pixel_size()
        cr.set_source_rgb(*TEXT)
        cr.move_to(w - u * 1.8 - tw, (h - th) / 2)
        PangoCairo.show_layout(cr, layout)
        return True


class Note(Bubble):
    def __init__(self):
        super().__init__()
        self.icon, self.text = "alert", ""

    def show_note(self, icon, text, ms):
        self.icon, self.text = icon, text
        u = self.unit()
        layout = self.make_layout(self.create_pango_context())
        tw, th = layout.get_pixel_size()
        w = u * 1.4 + u * 3.2 + u * 1.1 + tw + u * 1.6
        h = max(u * 3.2, th) + u * 2
        sx, sy, sw, _ = self.screen_size()
        self.place(w, h, sx + sw - w - u * 6, sy + u * 7)  # sous la ligne du volume
        self.hide_later(ms)

    def make_layout(self, ctx):
        u = self.unit()
        layout = Pango.Layout.new(ctx)
        layout.set_font_description(Pango.FontDescription(f"{FONT} Medium {u * 1.6 * 0.75:.1f}"))
        layout.set_width(int(u * 34 * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(self.text, -1)
        return layout

    def on_draw(self, _w, cr):
        u = self.unit()
        w, h = self.size
        self.background(cr, w, h)
        box = u * 3.2
        bx, by = u * 1.4, (h - box) / 2
        cr.set_source_rgba(1, 1, 1, 0.12)
        rounded(cr, bx, by, box, box, u * 0.8)
        cr.fill()
        self.draw_icon(cr, self.icon, bx + box * 0.22, by + box * 0.22, box * 0.56)
        layout = self.make_layout(PangoCairo.create_context(cr))
        tw, th = layout.get_pixel_size()
        cr.set_source_rgb(*TEXT)
        cr.move_to(bx + box + u * 1.1, (h - th) / 2)
        PangoCairo.show_layout(cr, layout)
        return True


def main():
    volume, note = Volume(), Note()
    buf = b""

    def on_input(source, condition):
        nonlocal buf
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            Gtk.main_quit()  # scaros.py s'est arrêté
            return False
        chunk = source.read(4096) if hasattr(source, "read") else b""
        if not chunk:
            Gtk.main_quit()
            return False
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            # un message abîmé est ignoré : une exception ici couperait l'affichage jusqu'au redémarrage
            try:
                handle(json.loads(line))
            except (ValueError, TypeError, AttributeError) as e:
                print(f"message ignoré : {e}", file=sys.stderr)
        return True

    def handle(msg):
        kind = msg.get("t")
        if kind == "volume":
            volume.show_level(msg.get("level"), msg.get("muted"))
        elif kind == "note":
            ms = max(800, min(20000, int(msg.get("ms") or 3500)))
            note.show_note(str(msg.get("icon", "alert")), str(msg.get("text", ""))[:160], ms)
        elif kind == "hide":
            volume.cancel()
            note.cancel()

    stdin = sys.stdin.buffer.raw
    GLib.io_add_watch(stdin, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_input)
    Gtk.main()


if __name__ == "__main__":
    main()
