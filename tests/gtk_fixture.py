#!/usr/bin/env python3
"""Testler için küçük GTK uygulaması (AT-SPI ağacı yayınlar).

    Arttır      → etiket "sayaç N"
    Ekle        → kutuya (üste) "Tamam" düğmesi ekler
    Önüne ekle  → "Arttır"ın ÖNÜNE "Yeni" düğmesi ekler (ref yolları kayar)
    Çoğalt      → aynı kutuya ikinci bir "Arttır" ekler (ikiz öğe)
    Sil         → "Hedef" düğmesini kaldırır
    Gecikmeli   → 0.6 sn sonra "Geç geldi" düğmesi ekler
    Kapat düğmesi devre dışı başlar; "Etkinleştir" 0.4 sn sonra etkinleştirir.
    Giriş alanı: Enter'a basılınca etiket "girildi: <metin>" olur.

    python3 gtk_fixture.py [ek düğme sayısı]
"""
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

win = Gtk.Window(title="NexTest")
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
label = Gtk.Label(label="sayaç 0")
count = [0]


def inc(_b):
    count[0] += 1
    label.set_text(f"sayaç {count[0]}")


def add_button(text, index=2, cb=None):
    # varsayılan: üstte (kaydırma alanının görünür kısmında) — gizli düğmeler ağaçta yok
    b = Gtk.Button(label=text)
    if cb:
        b.connect("clicked", cb)
    box.add(b)
    if index is not None:
        box.reorder_child(b, index)
    b.show()
    return b


inc_btn = Gtk.Button(label="Arttır")
inc_btn.connect("clicked", inc)
target = Gtk.Button(label="Hedef")
closer = Gtk.Button(label="Kapat")
closer.set_sensitive(False)
entry = Gtk.Entry()
entry.connect("activate", lambda e: label.set_text(f"girildi: {e.get_text()}"))

controls = [
    ("Ekle", lambda _b: add_button("Tamam")),
    ("Önüne ekle", lambda _b: add_button("Yeni", index=1)),
    ("Çoğalt", lambda _b: add_button("Arttır", cb=inc)),
    ("Sil", lambda _b: target.destroy() if target.get_parent() else None),
    ("Gecikmeli", lambda _b: GLib.timeout_add(600, lambda: add_button("Geç geldi") and False)),
    ("Etkinleştir", lambda _b: GLib.timeout_add(400, lambda: closer.set_sensitive(True) or False)),
]

box.add(label)
box.add(inc_btn)
box.add(target)
box.add(closer)
box.add(entry)
for text, cb in controls:
    b = Gtk.Button(label=text)
    b.connect("clicked", cb)
    box.add(b)
for i in range(int(sys.argv[1]) if len(sys.argv) > 1 else 0):
    box.add(Gtk.Button(label=f"Dolgu {i}"))

scroller = Gtk.ScrolledWindow()
scroller.add(box)
win.add(scroller)
win.set_default_size(400, 600)
win.connect("destroy", Gtk.main_quit)
win.show_all()
print("hazır", flush=True)
Gtk.main()
