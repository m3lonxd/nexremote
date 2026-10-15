"""Ortak D-Bus oturum bağlantısı.

dbus-python paylaşılan bağlantıyı ilk oluşturulduğu andaki ana döngüyle
bağlar. Bir modül döngüsüz bağlantı açarsa, sonradan sinyal/geri çağrı bekleyen
modüller (portal yanıtı, KWin betiği) hiç tetiklenmez. Hepsi buradan geçer.
"""
from __future__ import annotations

_ready = False


def session_bus():
    global _ready
    import dbus
    if not _ready:
        from dbus.mainloop.glib import DBusGMainLoop
        DBusGMainLoop(set_as_default=True)
        _ready = True
    return dbus.SessionBus()
