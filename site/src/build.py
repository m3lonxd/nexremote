#!/usr/bin/env python3
"""Siteyi tek şablondan iki dilde üretir: /index.html (EN) ve /tr/index.html (TR).

    python3 site/src/build.py

Sürüm çekirdekten, indirme boyutları site/download/ altındaki gerçek
dosyalardan okunur — sayfadaki her sayı ölçülmüş bir değerdir.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(SRC)
ROOT = os.path.dirname(SITE)


def version() -> str:
    text = open(os.path.join(ROOT, "core", "nexremote", "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def size(name: str) -> str:
    n = os.path.getsize(os.path.join(SITE, "download", name))
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


# Replay: komut ve çıktılar CLI'ın gerçek biçimindedir (dil bağımsız);
# yalnızca açıklamalar çevrilir.
REPLAY_CAPTIONS = {
    "en": [
        "The agent asks what is on screen: every window with its ref, every terminal with the program in it.",
        "It reads another agent's terminal. Konsole hands over the visible text; no screenshot, no focus change.",
        "It answers in that terminal as if typed on its keyboard. The window never had to come forward.",
        "It finds the switch by its name and presses it through accessibility. The mouse does not move.",
        "The player publishes no controls, so it looks: a screenshot of that window, then a click on the pixel it saw. Your pointer goes back where it was.",
    ],
    "tr": [
        "Ajan ekranda ne olduğunu sorar: ref'iyle her pencere, içinde çalışan programla her terminal.",
        "Başka bir ajanın terminalini okur. Konsole görünen metni verir; ekran görüntüsü yok, odak değişmez.",
        "O terminale, klavyesinden yazılmış gibi cevap verir. Pencerenin öne gelmesi gerekmedi.",
        "Anahtarı adıyla bulur ve erişilebilirlik üzerinden basar. Fare hiç oynamaz.",
        "Oynatıcı kontrollerini yayınlamıyor; ajan bakar: o pencerenin ekran görüntüsü, sonra gördüğü piksele tık. İmleciniz yerine döner.",
    ],
}

# Ajanın diğer terminale yazdığı cevap (replay 3. bölüm)
TERM_MSG = {"en": "Fix both, then rerun the suite",
            "tr": "İkisini düzelt, sonra testleri tekrar çalıştır"}

S = {
    "en": {
        "title": "NexRemote — let your agent use the desktop",
        "description": "Shell commands that let an AI agent see and use a Linux desktop: every window, a real mouse and keyboard, and the terminals already open on screen.",
        "skip": "Skip to content",
        "nav_label": "Sections", "nav_replay": "Replay", "nav_reach": "What it reaches",
        "nav_connect": "Connect", "nav_install": "Install",
        "theme_label": "Switch light / dark",
        "h1": "Your agent can run commands. Now it can use the screen.",
        "lede": "NexRemote turns a Linux desktop into shell commands: every window, a real mouse and keyboard, and the terminals you already have open — other agents included. No root, no SDK, and a vision model only when an app gives it nothing else to read.",
        "cta_download": "Download",
        "copy": "Copy", "copied": "Copied",
        "hero_meta": "MIT · KDE Plasma 6 on Wayland · Python 3.10+",
        "replay_h": "A session, replayed",
        "replay_note": "Commands and output are in the exact format the CLI prints. The desktop is drawn, so you can see where each command lands.",
        "replay_chapters": "Replay chapters",
        "ch0": "Look", "ch1": "Read a terminal", "ch2": "Answer it", "ch3": "Press by name", "ch4": "Click what it sees",
        "play": "Play", "pause": "Pause", "again": "Play again",
        "reach_h": "What it reaches",
        "reach_note": "Each surface uses the most direct channel the desktop offers, and says so in <code>nexremote caps</code> when one is missing.",
        "th_surface": "Surface", "th_cmd": "Commands", "th_how": "Through", "th_note": "Worth knowing",
        "r_win": "Windows", "r_win_n": "Every window on screen, including apps that publish no accessibility tree.",
        "r_mouse": "Mouse", "r_mouse_n": "Your pointer is put back where it was right after each click.",
        "r_keys": "Keyboard", "r_keys_how": "portal + clipboard",
        "r_keys_n": "Any language. Characters your layout lacks are pasted, then your clipboard is restored.",
        "r_names": "Controls by name", "r_names_n": "Presses buttons and toggles without moving the mouse at all.",
        "r_terms": "Open terminals", "r_terms_n": "Shows what runs in each one: claude, ssh, vim, a build.",
        "r_shell": "Its own shell", "r_shell_n": "Sessions survive between calls, so it can start something and check on it later.",
        "r_shot": "Screen", "r_shot_n": "Crop and scale are remembered, so <code>click X Y --from-shot</code> lands on the right pixel.",
        "watch_h": "Eight agents open? One of them can run the others.",
        "watch_p1": "Every Konsole tab and tmux pane is listed with the program in its foreground. An agent can read another agent's screen, answer its question, or stop it with <code>--key C-c</code>.",
        "watch_p2": "None of it needs window focus or your keyboard. You keep working while it does.",
        "watch_cap": "First command: the author's desktop, IP masked. The rest: from the test sandbox.",
        "pixels_h": "When an app has nothing to read, it looks.",
        "pixels_p": "Browsers, games and most Electron apps publish no controls. Take a screenshot cropped to one window and scaled for the model; it clicks on pixels in that image and NexRemote converts them back to the screen.",
        "diag_title": "A window on a three-monitor desktop is cropped, scaled to 800 by 900, and a point in the image maps back to screen coordinates.",
        "diag_cap": "The author's three monitors, to scale. One point, two coordinate systems.",
        "connect_h": "Plug it into your agent",
        "connect_note": "Same core, four ways in. Pick whatever your harness already speaks.",
        "t_shell": "Shell", "t_text": "Text-only model",
        "p_shell": "Any harness that can run a command: Claude Code, Codex, Aider, opencode, a cron job. Give the agent the skill text and it knows the rest.",
        "p_shell_c": "# paste into your agent's instructions",
        "p_mcp": "21 tools over stdio for Claude Code, Claude Desktop, Cursor and other MCP clients. Screenshots come back as images the model can look at.",
        "p_http": "For agents on another machine. Listens on localhost by default and refuses any other address without a token.",
        "p_text": "For local models without tool calling. The model writes <code>nexremote</code> lines; the loop runs them and feeds the output back until it writes <code>DONE</code>.",
        "p_text_goal": "Open the calculator, compute 7×8, read the result",
        "trust_h": "What it does to your machine",
        "f1_t": "Asks once", "f1_d": "KDE shows a remote-control dialog the first time. Tick <em>Allow restoring</em> and it won't ask again. Delete <code>~/.config/nexremote/portal.json</code> to take it back.",
        "f2_t": "Gives your pointer back", "f2_d": "Wayland has one pointer. A click borrows it and returns it to where it was.",
        "f3_t": "No root", "f3_d": "No <code>/dev/uinput</code>, no daemon running as another user.",
        "f4_t": "Closed to the network", "f4_d": "The HTTP adapter will not listen beyond localhost without a token.",
        "f5_t": "Tested where it can't hurt you", "f5_d": "Input tests run in a throwaway desktop with its own D-Bus, a virtual KWin, its own portal and PipeWire.",
        "limits_h": "Limits",
        "l1": "Tested on KDE Plasma 6 (Wayland). GNOME should work through the same portal, but the window list falls back to accessibility. wlroots compositors are untested.",
        "l2": "Konsole exposes the visible screen only. For scrollback, run the agent in tmux.",
        "l3": "Browsers and Electron apps are driven by screenshot and coordinates.",
        "install_h": "Install",
        "s1_t": "Download and unpack",
        "s2_t": "Install the commands",
        "s2_d": "Puts <code>nexremote</code>, <code>nexremote-mcp</code>, <code>nexremote-http</code> and <code>nexremote-agent</code> in <code>~/bin</code>, then lists anything missing.",
        "s3_t": "Grant access once",
        "s3_d": "Turns on the accessibility tree and opens KDE's remote-control dialog: tick <em>Allow restoring</em>, press <em>Share</em>.",
        "reqs": "Needs <code>python3-dbus</code> <code>python3-gi</code> <code>xdg-desktop-portal-kde</code> <code>pipewire</code> <code>wl-clipboard</code>. Optional: <code>tmux</code> <code>python3-pil</code> <code>python3-xlib</code>, <code>pip install mcp</code>.",
        "other_lang": "tr", "other_lang_label": "TR", "other_lang_name": "Türkçe", "other_lang_href": "/tr/",
        "home": "/", "path": "/",
    },
    "tr": {
        "title": "NexRemote — ajanın masaüstünü kullansın",
        "description": "Bir AI ajanının Linux masaüstünü görmesini ve kullanmasını sağlayan kabuk komutları: her pencere, gerçek fare ve klavye, ekranda açık terminaller.",
        "skip": "İçeriğe geç",
        "nav_label": "Bölümler", "nav_replay": "Tekrar", "nav_reach": "Neye uzanır",
        "nav_connect": "Bağla", "nav_install": "Kurulum",
        "theme_label": "Açık / koyu tema",
        "h1": "Ajanın komut çalıştırabiliyor. Artık ekranı da kullanabilir.",
        "lede": "NexRemote bir Linux masaüstünü kabuk komutlarına çevirir: her pencere, gerçek fare ve klavye, ve zaten açık olan terminallerin — diğer ajanlar dahil. Root yok, SDK yok; görsel model ancak uygulama okunacak başka bir şey vermediğinde gerekir.",
        "cta_download": "İndir",
        "copy": "Kopyala", "copied": "Kopyalandı",
        "hero_meta": "MIT · KDE Plasma 6, Wayland · Python 3.10+",
        "replay_h": "Bir oturumun tekrarı",
        "replay_note": "Komutlar ve çıktılar, CLI'ın yazdığı biçimin birebir aynısı. Masaüstü çizimdir; her komutun nereye dokunduğunu görebilesin diye.",
        "replay_chapters": "Tekrar bölümleri",
        "ch0": "Bak", "ch1": "Terminali oku", "ch2": "Cevap ver", "ch3": "Adıyla bas", "ch4": "Gördüğüne tıkla",
        "play": "Oynat", "pause": "Durdur", "again": "Tekrar oynat",
        "reach_h": "Neye uzanır",
        "reach_note": "Her yüzey, masaüstünün sunduğu en doğrudan kanalı kullanır; biri eksikse <code>nexremote caps</code> bunu söyler.",
        "th_surface": "Yüzey", "th_cmd": "Komutlar", "th_how": "Nereden", "th_note": "Bilmeye değer",
        "r_win": "Pencereler", "r_win_n": "Ekrandaki her pencere; erişilebilirlik ağacı yayınlamayan uygulamalar dahil.",
        "r_mouse": "Fare", "r_mouse_n": "Her tıktan hemen sonra imleciniz eski yerine konur.",
        "r_keys": "Klavye", "r_keys_how": "portal + pano",
        "r_keys_n": "Her dil. Klavye düzeninizde olmayan karakterler yapıştırılır, panonuz geri yüklenir.",
        "r_names": "Adıyla kontroller", "r_names_n": "Düğmelere ve anahtarlara, fareyi hiç oynatmadan basar.",
        "r_terms": "Açık terminaller", "r_terms_n": "Her birinde ne çalıştığını gösterir: claude, ssh, vim, bir build.",
        "r_shell": "Kendi kabuğu", "r_shell_n": "Oturumlar çağrılar arasında yaşar; bir şeyi başlatıp sonra dönüp bakabilir.",
        "r_shot": "Ekran", "r_shot_n": "Kırpma ve ölçek hatırlanır; <code>click X Y --from-shot</code> doğru piksele düşer.",
        "watch_h": "Sekiz ajan açık mı? Biri diğerlerini yönetebilir.",
        "watch_p1": "Her Konsole sekmesi ve tmux bölmesi, ön plandaki programıyla listelenir. Bir ajan başka bir ajanın ekranını okuyabilir, sorusuna cevap verebilir ya da <code>--key C-c</code> ile durdurabilir.",
        "watch_p2": "Bunların hiçbiri pencere odağı ya da klavyeniz gerektirmez. O çalışırken siz de çalışmaya devam edersiniz.",
        "watch_cap": "İlk komut: geliştiricinin masaüstü, IP maskelendi. Gerisi: test sandbox'ından.",
        "pixels_h": "Okunacak bir şey yoksa bakar.",
        "pixels_p": "Tarayıcılar, oyunlar ve çoğu Electron uygulaması kontrollerini yayınlamaz. Tek pencereye kırpılmış ve modele göre küçültülmüş bir ekran görüntüsü alın; model o görüntüdeki piksele tıklar, NexRemote onu ekrana geri çevirir.",
        "diag_title": "Üç monitörlü masaüstündeki bir pencere kırpılır, 800'e 900 boyutuna küçültülür ve görüntüdeki bir nokta ekran koordinatına geri çevrilir.",
        "diag_cap": "Geliştiricinin üç monitörü, ölçekli. Tek nokta, iki koordinat sistemi.",
        "connect_h": "Ajanına bağla",
        "connect_note": "Aynı çekirdek, dört giriş. Harness'ın hangisini konuşuyorsa onu seç.",
        "t_shell": "Kabuk", "t_text": "Yalnız metin modeli",
        "p_shell": "Komut çalıştırabilen her harness: Claude Code, Codex, Aider, opencode, bir cron işi. Ajana skill metnini ver, gerisini bilir.",
        "p_shell_c": "# ajanının talimatlarına yapıştır",
        "p_mcp": "Claude Code, Claude Desktop, Cursor ve diğer MCP istemcileri için stdio üzerinden 21 araç. Ekran görüntüleri, modelin bakabildiği görsel olarak döner.",
        "p_http": "Başka makinedeki ajanlar için. Varsayılan olarak yalnızca localhost'u dinler; başka bir adrese token olmadan açılmayı reddeder.",
        "p_text": "Tool calling'i olmayan yerel modeller için. Model <code>nexremote</code> satırları yazar; döngü onları çalıştırır ve çıktıyı geri besler, ta ki model <code>DONE</code> yazana dek.",
        "p_text_goal": "Hesap makinesini aç, 7×8 hesapla, sonucu oku",
        "trust_h": "Makinene ne yapar",
        "f1_t": "Bir kez sorar", "f1_d": "İlk seferde KDE bir uzaktan kontrol penceresi açar. <em>Allow restoring</em> kutusunu işaretleyin, bir daha sormaz. Geri almak için <code>~/.config/nexremote/portal.json</code> dosyasını silin.",
        "f2_t": "İmlecini geri verir", "f2_d": "Wayland'de tek imleç vardır. Tık onu ödünç alır ve eski yerine geri koyar.",
        "f3_t": "Root yok", "f3_d": "<code>/dev/uinput</code> yok, başka kullanıcı olarak çalışan servis yok.",
        "f4_t": "Ağa kapalı", "f4_d": "HTTP adaptörü token olmadan localhost dışını dinlemez.",
        "f5_t": "Zarar veremeyeceği yerde test edilir", "f5_d": "Giriş testleri, kendi D-Bus'ı, sanal KWin'i, kendi portalı ve PipeWire'ı olan atılabilir bir masaüstünde koşar.",
        "limits_h": "Sınırlar",
        "l1": "KDE Plasma 6 (Wayland) üzerinde test edildi. GNOME'da aynı portal üzerinden çalışmalı, ama pencere listesi erişilebilirliğe düşer. wlroots tabanlı ortamlar test edilmedi.",
        "l2": "Konsole yalnızca görünen ekranı verir. Geçmiş satırlar için ajanı tmux içinde çalıştırın.",
        "l3": "Tarayıcılar ve Electron uygulamaları ekran görüntüsü ve koordinatla kullanılır.",
        "install_h": "Kurulum",
        "s1_t": "İndir ve aç",
        "s2_t": "Komutları kur",
        "s2_d": "<code>nexremote</code>, <code>nexremote-mcp</code>, <code>nexremote-http</code> ve <code>nexremote-agent</code> komutlarını <code>~/bin</code> altına koyar, eksik paketleri listeler.",
        "s3_t": "Erişimi bir kez ver",
        "s3_d": "Erişilebilirlik ağacını açar ve KDE'nin uzaktan kontrol penceresini getirir: <em>Allow restoring</em> işaretle, <em>Share</em>'e bas.",
        "reqs": "Gerekli: <code>python3-dbus</code> <code>python3-gi</code> <code>xdg-desktop-portal-kde</code> <code>pipewire</code> <code>wl-clipboard</code>. İsteğe bağlı: <code>tmux</code> <code>python3-pil</code> <code>python3-xlib</code>, <code>pip install mcp</code>.",
        "other_lang": "en", "other_lang_label": "EN", "other_lang_name": "English", "other_lang_href": "/",
        "home": "/tr/", "path": "/tr/",
    },
}

# Değeri HTML içeren anahtarlar (geri kalanı kaçışlanır)
RAW = {"reach_note", "r_shot_n", "watch_p1", "p_text", "f1_d", "f3_d", "s2_d", "s3_d", "reqs"}
ATTR = {"title", "description"}


def render(lang: str) -> str:
    tpl = open(os.path.join(SRC, "page.html"), encoding="utf-8").read()
    vals = dict(S[lang])
    vals.update(lang=lang, version=version(), zip_size=size("nexremote.zip"),
                tgz_size=size("nexremote.tar.gz"))
    vals["replay_json"] = json.dumps({"captions": REPLAY_CAPTIONS[lang],
                                      "termMsg": TERM_MSG[lang]},
                                     ensure_ascii=False).replace("</", "<\\/")

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in vals:
            raise KeyError(f"[{lang}] şablonda tanımsız anahtar: {key}")
        v = str(vals[key])
        if key in RAW or key == "replay_json":
            return v
        return html.escape(v, quote=key in ATTR or True)

    out = re.sub(r"\{\{([a-z0-9_]+)\}\}", sub, tpl)
    unused = set(S[lang]) - set(re.findall(r"\{\{([a-z0-9_]+)\}\}", tpl))
    if unused:
        print(f"[{lang}] kullanılmayan anahtarlar: {sorted(unused)}", file=sys.stderr)
    return out


def main() -> int:
    missing = set(S["en"]) ^ set(S["tr"])
    if missing:
        raise SystemExit(f"diller arasında eksik anahtar: {sorted(missing)}")
    targets = {"en": os.path.join(SITE, "index.html"),
               "tr": os.path.join(SITE, "tr", "index.html")}
    for lang, path in targets.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(render(lang))
        print(f"yazıldı: {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
