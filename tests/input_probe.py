#!/usr/bin/env python3
"""Sandbox içinde tam ekran test penceresi: gelen fare/klavye olaylarını
JSON satırları olarak kaydeder. Giriş enjeksiyonunu kanıtlamak için kullanılır.

    input_probe.py <log.jsonl>
"""
import json
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

log = open(sys.argv[1], "a", buffering=1)


def emit(**ev):
    log.write(json.dumps(ev, ensure_ascii=False) + "\n")


win = Gtk.Window(title="nexremote-probe")
win.set_default_size(1280, 800)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
entry = Gtk.Entry()
entry.set_name("probe-entry")
area = Gtk.EventBox()
area.set_size_request(1280, 760)
box.pack_start(entry, False, False, 0)
box.pack_start(area, True, True, 0)
win.add(box)


def on_button(_w, ev):
    if ev.type == Gdk.EventType.BUTTON_PRESS:
        emit(type="button", button=ev.button, x=round(ev.x_root), y=round(ev.y_root))
    elif ev.type == Gdk.EventType._2BUTTON_PRESS:
        emit(type="double")
    return False


def on_scroll(_w, ev):
    emit(type="scroll", direction=int(ev.direction), dy=ev.delta_y)
    return False


def on_key(_w, ev):
    emit(type="key", name=Gdk.keyval_name(ev.keyval),
         ctrl=bool(ev.state & Gdk.ModifierType.CONTROL_MASK),
         shift=bool(ev.state & Gdk.ModifierType.SHIFT_MASK))
    return False


def on_changed(e):
    emit(type="text", value=e.get_text())


area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.SCROLL_MASK
                | Gdk.EventMask.SMOOTH_SCROLL_MASK)
area.connect("button-press-event", on_button)
area.connect("scroll-event", on_scroll)
win.connect("key-press-event", on_key)
entry.connect("changed", on_changed)
win.connect("destroy", Gtk.main_quit)
win.fullscreen()
win.show_all()
entry.grab_focus()
emit(type="ready")
Gtk.main()
