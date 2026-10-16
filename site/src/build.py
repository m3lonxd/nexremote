#!/usr/bin/env python3
"""Siteyi tek şablondan iki dilde üretir: /index.html (EN) ve /tr/index.html (TR).

    python3 site/src/build.py

Sayfadaki her sayı ölçülmüş bir değerdir:
  - sürüm               core/nexremote/__init__.py
  - MCP aracı           adapters/mcp/server.py içindeki @mcp.tool() sayısı
  - CLI komutu          adapters/cli/nexremote_cli.py içindeki sub.add_parser( sayısı
  - giriş komutu        install.sh'ın ~/bin'e yazdığı sarmalayıcı sayısı
  - yüzey               README.md "Ne yapabilir" tablosunun satırları
  - harness             docs/HARNESSES.md ilk tablosunun satırları
  - test paketi         tests/run_all.sh içindeki run satırları (+ sandbox'takiler)
  - Python satırı       core/ ve adapters/ altındaki .py dosyalarının satır toplamı
  - indirme boyutları   site/download/ altındaki gerçek dosyalar
Bir sayı okunamazsa derleme durur; sayfaya tahmin yazılmaz.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from urllib.parse import quote

SRC = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(SRC)
ROOT = os.path.dirname(SITE)

# İletişim adresi: iki dil de bunu kullanır (string'lerde tekrar edilmez).
CONTACT_EMAIL = "omeralibayrakcii@gmail.com"


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', read("core/nexremote/__init__.py")).group(1)


def size(name: str) -> str:
    n = os.path.getsize(os.path.join(SITE, "download", name))
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def md_table_rows(text: str, after: str | None = None) -> list[list[str]]:
    """İlk markdown tablosunun gövde satırları (başlık ve ayraç hariç)."""
    if after:
        text = text[text.index(after):]
    rows, started = [], False
    for line in text.splitlines():
        if line.startswith("|"):
            started = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(cells)
        elif started:
            break
    return rows[2:]


def harness_names() -> list[str]:
    return [re.sub(r"\*\*(.+?)\*\*", r"\1", r[0]) for r in md_table_rows(read("docs/HARNESSES.md"))]


def stats() -> dict[str, int]:
    run_all = read("tests/run_all.sh")
    py_lines = 0
    for base in ("core", "adapters"):
        for d, dirs, files in os.walk(os.path.join(ROOT, base)):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for fn in files:
                if fn.endswith(".py"):
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        py_lines += sum(1 for _ in f)
    st = {
        "mcp_tools": len(re.findall(r"^@mcp\.tool\(\)", read("adapters/mcp/server.py"), re.M)),
        "cli_cmds": len(re.findall(r"\bsub\.add_parser\(", read("adapters/cli/nexremote_cli.py"))),
        "entry_cmds": len(re.findall(r'^cat > "\$BIN/', read("install.sh"), re.M)),
        "surfaces": len(md_table_rows(read("README.md"), after="## Ne yapabilir")),
        "harnesses": len(harness_names()),
        "suites": len(re.findall(r'^run "', run_all, re.M)),
        "sandbox_suites": len(re.findall(r'^run ".*tests/sandbox\.sh', run_all, re.M)),
        "py_lines": py_lines,
    }
    for k, v in st.items():
        if v <= 0:
            raise SystemExit(f"sayı okunamadı: {k} = {v}")
    return st


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

# HARNESSES.md'deki genel (ürün adı olmayan) satırların görünen adları.
# Bilinmeyen bir satır ürün adı sayılır ve " / " ile bölünür.
HARNESS_GENERIC = {
    "Özel / proxy'lenmiş CLI": {"en": "your own CLI", "tr": "kendi CLI'ın"},
    "HTTP-tabanlı uzak ajan": {"en": "remote agents over HTTP", "tr": "HTTP ile uzak ajanlar"},
    "Tool-calling'siz model": {"en": "models without tool calling", "tr": "tool calling'i olmayan modeller"},
    "Görsel model": {"en": "vision models", "tr": "görsel modeller"},
    "Başsız sunucu (GUI yok)": {"en": "headless servers", "tr": "ekransız sunucular"},
}

S = {
    "en": {
        "title": "NexRemote — let your AI agent use the Linux desktop",
        "description": "Shell commands that let an AI agent see and use a Linux desktop: every window, a real mouse and keyboard, controls by name, and the terminals already open on screen.",
        "skip": "Skip to content",
        "nav_label": "Sections", "nav_how": "How it works", "nav_replay": "Replay",
        "nav_compare": "Compare", "nav_roadmap": "Roadmap", "nav_install": "Install",
        "theme_label": "Switch light / dark",
        "eyebrow": "Desktop control for AI agents · Linux · Wayland",
        "h1a": "Your agent can run commands.",
        "h1b": "Now it can use the screen.",
        "lede": "NexRemote turns a Linux desktop into shell commands: every window, a real mouse and keyboard, buttons pressed by name, and the terminals you already have open, other agents included. No root, no SDK, and a vision model only when an app gives it nothing else to read.",
        "cta_download": "Download", "cta_see": "See it work",
        "copy": "Copy", "copied": "Copied",
        "hero_meta": "MIT · KDE Plasma 6 on Wayland · Python 3.10+",
        "hv_title": "Drawing of a Linux desktop with terminals, a settings window and a video player. The agent's green crosshair lists the windows, finds the Dark switch by name and presses it, reads a terminal, and clicks the player from a screenshot.",
        "hv1": "Window manager", "hv2": "Accessibility tree", "hv3": "Terminal text", "hv4": "Pixels, last",
        "mq_label": "Setups documented for",

        "prob_kicker": "The problem",
        "prob_h": "Agents live in the terminal. Your work lives on the screen.",
        "prob_p": "Coding agents edit files and run builds. The moment a task needs a settings dialog, a running app or another terminal, they stop and hand it back to you. The usual ways around that each break somewhere.",
        "breaks": "Where it breaks:",
        "pa1_t": "Screenshot every step",
        "pa1_d": "The model looks at the whole screen, guesses coordinates, clicks, then takes another screenshot to see what happened.",
        "pa1_b": "every step is a vision call, so it is slow and costly, and a few pixels off is a wrong click. Text already on screen is read back from an image.",
        "pa2_t": "An integration per app",
        "pa2_d": "APIs, SDKs and browser drivers are exact, but each one covers a single program and has to be built and maintained.",
        "pa2_b": "desktop apps without an API, and the terminals already open on screen, stay out of reach.",
        "pa3_t": "X11 automation scripts",
        "pa3_d": "xdotool and similar tools were made for X11, where any client could read and type into any window.",
        "pa3_b": "Wayland closed that door. The usual workaround, ydotool, writes to <code>/dev/uinput</code>, which needs root or a udev rule.",
        "prob_close": "NexRemote takes the most direct channel the desktop already offers, and falls back to pixels only when nothing else is there.",

        "how_kicker": "How it works",
        "how_h": "Structure first. Pixels last.",
        "how_p": "Before it looks at a single pixel, NexRemote asks the desktop what it already knows. Each step down is used only when the one above has nothing to give.",
        "lad1_t": "Ask the window manager",
        "lad1_d": "KWin lists every window with its title, app, position and focus. The agent can bring one forward, minimize or close it, including apps that publish nothing else.",
        "lad2_t": "Read the accessibility tree, act by name",
        "lad2_d": "Buttons, switches and fields come with names and roles. The agent presses “Dark” by its name. The mouse does not move, and nothing depends on where the control is drawn.",
        "lad3_t": "Read terminal text directly",
        "lad3_d": "Konsole tabs and tmux panes hand over their text, along with the program running in each. No screenshot, no OCR, no focus change.",
        "lad4_t": "Only then: screenshot and click pixels",
        "lad4_d": "For browsers, games and most Electron apps. The shot is cropped to one window and scaled for the model; a pixel in that image is mapped back to the screen and clicked through the RemoteDesktop portal.",
        "via": "Channel", "model_gets": "Model receives", "gets_text": "text", "gets_image": "one image",
        "lp_structured": "structured", "lp_pixels": "pixels",
        "st_notree": "no tree published", "st_lift": "plain text, no image",
        "arch_h": "One core between any agent and the desktop",
        "arch_p": "Adapters only know the core; the core only talks to channels the desktop already provides. If a backend is missing, the rest keep working and <code>nexremote caps</code> says what is off.",
        "arch_alt": "Architecture: your agent connects through one of four adapters (shell CLI, MCP, HTTP, text loop) to the NexRemote core, which reaches the desktop through KWin scripting, AT-SPI, the RemoteDesktop portal, Konsole D-Bus and tmux, its own tmux or PTY sessions, and spectacle or grim.",
        "arch_agent": "Your agent", "arch_agent_s": "any harness, any model",
        "arch_text": "Text loop", "arch_core": "Core", "arch_desk": "Your desktop",
        "a_win": "windows", "a_ctl": "controls by name", "a_in": "mouse + keyboard",
        "a_term": "open terminals", "a_sh": "its own shell", "a_shot": "screenshots",

        "replay_kicker": "Replay",
        "replay_h": "A session, replayed",
        "replay_note": "Commands and output are in the exact format the CLI prints. The desktop is drawn, so you can see where each command lands.",
        "replay_chapters": "Replay chapters",
        "ch0": "Look", "ch1": "Read a terminal", "ch2": "Answer it", "ch3": "Press by name", "ch4": "Click what it sees",
        "play": "Play", "pause": "Pause", "again": "Play again",

        "reach_kicker": "Reach",
        "reach_h": "What it reaches",
        "reach_note": "Each surface uses the most direct channel the desktop offers, and says so in <code>nexremote caps</code> when one is missing.",
        "th_cmd": "Commands", "th_how": "Through",
        "r_win": "Windows", "r_win_n": "Every window on screen, including apps that publish no accessibility tree.",
        "r_mouse": "Mouse", "r_mouse_n": "Your pointer is put back where it was right after each click.",
        "r_keys": "Keyboard", "r_keys_how": "portal + clipboard",
        "r_keys_n": "Any language. Characters your layout lacks are pasted, then your clipboard is restored.",
        "r_names": "Controls by name", "r_names_n": "Presses buttons and toggles without moving the mouse at all.",
        "r_terms": "Open terminals", "r_terms_n": "Shows what runs in each one: claude, ssh, vim, a build.",
        "r_shell": "Its own shell", "r_shell_n": "Sessions survive between calls, so it can start something and check on it later.",
        "r_shot": "Screen", "r_shot_n": "Crop and scale are remembered, so <code>click X Y --from-shot</code> lands on the right pixel.",
        "r_caps": "Something missing?", "r_caps_n": "One command reports which channels work on this machine and which packages the rest need.",

        "watch_kicker": "Terminals",
        "watch_h": "Eight agents open? One of them can run the others.",
        "watch_p1": "Every Konsole tab and tmux pane is listed with the program in its foreground. An agent can read another agent's screen, answer its question, or stop it with <code>--key C-c</code>.",
        "watch_p2": "None of it needs window focus or your keyboard. You keep working while it does.",
        "watch_cap": "Output in the exact CLI format; terminal names are examples. The last two commands ran in the test sandbox.",

        "pixels_kicker": "Last resort",
        "pixels_h": "When an app has nothing to read, it looks.",
        "pixels_p": "Browsers, games and most Electron apps publish no controls. Take a screenshot cropped to one window and scaled for the model; it clicks on pixels in that image and NexRemote converts them back to the screen.",
        "diag_title": "A window on a three-monitor desktop is cropped, scaled to 800 by 900, and a point in the image maps back to screen coordinates.",
        "diag_cap": "A three-monitor layout, to scale. One point, two coordinate systems.",

        "cmp_kicker": "Compare",
        "cmp_h": "Where it stands",
        "cmp_p": "Compared with the kinds of tools in use today, not with named products. Each approach has its place; this is how they do on the work NexRemote is built for.",
        "c_shot": "Screenshot-only computer-use agents",
        "c_rpa": "Classic RPA and xdotool scripts",
        "c_api": "Per-app APIs and SDKs",
        "yes": "Yes", "no": "No", "partly": "Partly",
        "cr1": "Presses controls by name, not by coordinates",
        "c1s": "guesses coordinates from the image",
        "c1r": "some RPA tools use selectors; xdotool clicks coordinates",
        "c_inapp": "inside that one app",
        "cr2": "Reads on-screen text without a vision model",
        "c2u": "accessibility tree and terminal text",
        "c2s": "every read is an image",
        "c2r": "depends on the tool",
        "cr3": "Works with apps that publish nothing to read",
        "c3u": "screenshot fallback",
        "c3s": "its main mode",
        "c3r": "blind coordinates",
        "c3a": "needs an API",
        "cr4": "Reads and answers terminals already open",
        "c4s": "from pixels, when the window is visible",
        "c4r": "can type, cannot read",
        "c4a": "out of scope",
        "cr5": "Runs on Wayland without root",
        "c5s": "depends on setup; often an X11 VM",
        "c5r": "X11 only, or root for /dev/uinput",
        "c5a": "does not touch input",
        "cr6": "Plugs into any agent through a shell command",
        "c6u": "also MCP, HTTP and a text loop",
        "c6s": "usually tied to one model's tool format",
        "c6r": "it is a command too",
        "c6a": "one integration per app",
        "cmp_note": "Categories, not products. “Yes” means the approach does it out of the box; “Partly” means it depends on the tool or the setup.",

        "num_kicker": "Numbers",
        "num_h": "By the numbers",
        "num_note": "Counted from the v{{version}} source every time this page is built. No estimates, no projections.",
        "n_mcp": "MCP tools", "n_cli": "CLI commands",
        "n_ways": "ways to plug in: shell, MCP, HTTP, text loop",
        "n_surf": "desktop surfaces it reaches",
        "n_harn": "harness setups documented",
        "n_tests": "test suites, {{sandbox_suites}} of them in a throwaway desktop",
        "n_loc": "lines of Python, core and adapters",
        "n_root": "root privileges needed",

        "why_kicker": "Why now",
        "why_h": "The pieces are in place.",
        "why_p": "No forecast needed. This is where the tools already are.",
        "why1_t": "Agents already work in terminals",
        "why1_d": "Coding agents such as Claude Code, Codex, Aider and Gemini CLI run in a shell. A shell command is the one interface they all share, so that is the interface NexRemote speaks.",
        "why2_t": "MCP became a common tool protocol",
        "why2_d": "Editors and agent clients load tools over MCP. One NexRemote server gives all of them the same desktop tools, with screenshots returned as images.",
        "why3_t": "Linux desktops moved to Wayland",
        "why3_d": "Wayland removed the X11 shortcuts that old automation relied on. The sanctioned way in is the xdg-desktop-portal RemoteDesktop interface, which asks the user once. NexRemote is built on it.",

        "rm_kicker": "Roadmap",
        "rm_h": "What exists, what comes next",
        "rm_p": "The left column ships in the download today. The right column is planned, not built.",
        "rm_now": "In v{{version}} today", "rm_next": "Planned",
        "sh1": "Every window through KWin scripting",
        "sh2": "Mouse and keyboard through the RemoteDesktop portal, approved once",
        "sh3": "Reading and typing into open Konsole tabs and tmux panes",
        "sh4": "{{mcp_tools}} MCP tools, {{cli_cmds}} CLI commands, an HTTP API and a text loop for models without tool calling",
        "sh5": "Input tests that run in an isolated sandbox desktop",
        "rm1_t": "Electron accessibility wrapper.",
        "rm1_d": "Start Electron apps with <code>--force-renderer-accessibility</code> automatically, so their buttons can be pressed by name instead of by pixel.",
        "rm2_t": "GNOME and wlroots validation.",
        "rm2_d": "Test the portal path on GNOME and on wlroots compositors such as Sway, and document what works.",
        "rm3_t": "Remote deployment.",
        "rm3_d": "Ship the HTTP adapter with a systemd unit for running on another machine.",
        "rm4_t": "Live window tree over MCP.",
        "rm4_d": "Serve the window list and accessibility tree as MCP resources, alongside the tools.",

        "connect_kicker": "Connect",
        "connect_h": "Plug it into your agent",
        "connect_note": "Same core, four ways in. Pick whatever your harness already speaks.",
        "t_shell": "Shell", "t_text": "Text-only model",
        "p_shell": "Any harness that can run a command: Claude Code, Codex, Aider, opencode, a cron job. Give the agent the skill text and it knows the rest.",
        "p_shell_c": "# paste into your agent's instructions",
        "p_mcp": "{{mcp_tools}} tools over stdio for Claude Code, Claude Desktop, Cursor and other MCP clients. Screenshots come back as images the model can look at.",
        "p_http": "For agents on another machine. Listens on localhost by default and refuses any other address without a token.",
        "p_text": "For local models without tool calling. The model writes <code>nexremote</code> lines; the loop runs them and feeds the output back until it writes <code>DONE</code>.",
        "p_text_goal": "Open the calculator, compute 7×8, read the result",

        "trust_kicker": "Safety",
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

        "install_kicker": "Install",
        "install_h": "Install in three steps",
        "s1_t": "Download and unpack",
        "s2_t": "Install the commands",
        "s2_d": "Puts <code>nexremote</code>, <code>nexremote-mcp</code>, <code>nexremote-http</code> and <code>nexremote-agent</code> in <code>~/bin</code>, then lists anything missing.",
        "s3_t": "Grant access once",
        "s3_d": "Turns on the accessibility tree and opens KDE's remote-control dialog: tick <em>Allow restoring</em>, press <em>Share</em>.",
        "reqs": "Needs <code>python3-dbus</code> <code>python3-gi</code> <code>xdg-desktop-portal-kde</code> <code>pipewire</code> <code>wl-clipboard</code>. Optional: <code>tmux</code> <code>python3-pil</code> <code>python3-xlib</code>, <code>pip install mcp</code>.",

        "cta_h": "Give your agent the desktop.",
        "cta_p": "Unpack, install, approve once. For questions about the project, a partnership or an investment, write to us.",
        "talk": "Talk to us", "mail_subject": "NexRemote", "foot_contact": "Contact",

        "other_lang": "tr", "other_lang_label": "TR", "other_lang_name": "Türkçe", "other_lang_href": "/tr/",
        "home": "/", "path": "/",
    },
    "tr": {
        "title": "NexRemote — AI ajanın Linux masaüstünü kullansın",
        "description": "Bir AI ajanının Linux masaüstünü görmesini ve kullanmasını sağlayan kabuk komutları: her pencere, gerçek fare ve klavye, adıyla kontroller ve ekranda açık terminaller.",
        "skip": "İçeriğe geç",
        "nav_label": "Bölümler", "nav_how": "Nasıl çalışır", "nav_replay": "Oturum",
        "nav_compare": "Karşılaştır", "nav_roadmap": "Yol haritası", "nav_install": "Kurulum",
        "theme_label": "Açık / koyu tema",
        "eyebrow": "AI ajanları için masaüstü kontrolü · Linux · Wayland",
        "h1a": "Ajanın komut çalıştırabiliyor.",
        "h1b": "Artık ekranı da kullanabilir.",
        "lede": "NexRemote bir Linux masaüstünü kabuk komutlarına çevirir: her pencere, gerçek fare ve klavye, adıyla basılan düğmeler ve zaten açık olan terminallerin, diğer ajanlar dahil. Root yok, SDK yok; görsel model ancak uygulama okunacak başka bir şey vermediğinde devreye girer.",
        "cta_download": "İndir", "cta_see": "Çalışırken gör",
        "copy": "Kopyala", "copied": "Kopyalandı",
        "hero_meta": "MIT · KDE Plasma 6, Wayland · Python 3.10+",
        "hv_title": "Terminallerin, bir ayarlar penceresinin ve bir video oynatıcının açık olduğu bir Linux masaüstü çizimi. Ajanın yeşil artı imleci pencereleri listeler, Koyu anahtarını adıyla bulup basar, bir terminali okur ve ekran görüntüsünden oynatıcıya tıklar.",
        "hv1": "Pencere yöneticisi", "hv2": "Erişilebilirlik ağacı", "hv3": "Terminal metni", "hv4": "En son piksel",
        "mq_label": "Kurulumu belgelenmiş ortamlar",

        "prob_kicker": "Sorun",
        "prob_h": "Ajanlar terminalde yaşıyor. İşiniz ise ekranda.",
        "prob_p": "Kodlama ajanları dosya düzenler, build çalıştırır. İş bir ayarlar penceresine, çalışan bir uygulamaya ya da başka bir terminale dokunmayı gerektirdiği anda durur ve işi size geri verir. Bilinen çözümlerin her biri bir yerde tıkanır.",
        "breaks": "Tıkandığı yer:",
        "pa1_t": "Her adımda ekran görüntüsü",
        "pa1_d": "Model bütün ekrana bakar, koordinat tahmin eder, tıklar, sonra ne olduğunu görmek için bir ekran görüntüsü daha alır.",
        "pa1_b": "her adım bir görsel model çağrısıdır; yavaş ve pahalıdır, birkaç piksellik sapma yanlış tıklama demektir. Ekranda zaten yazılı olan metin bile görüntüden geri okunur.",
        "pa2_t": "Her uygulamaya ayrı entegrasyon",
        "pa2_d": "API'ler, SDK'lar ve tarayıcı sürücüleri kesindir; ama her biri tek bir programı kapsar ve ayrıca yazılıp bakımı yapılmalıdır.",
        "pa2_b": "API'si olmayan masaüstü uygulamaları ve ekranda zaten açık olan terminaller erişim dışında kalır.",
        "pa3_t": "X11 otomasyon betikleri",
        "pa3_d": "xdotool ve benzerleri, her istemcinin her pencereyi okuyup ona yazabildiği X11 için yapıldı.",
        "pa3_b": "Wayland bu kapıyı kapattı. Bilinen geçici çözüm ydotool ise <code>/dev/uinput</code>'a yazar; bu da root ya da bir udev kuralı ister.",
        "prob_close": "NexRemote, masaüstünün zaten sunduğu en doğrudan kanalı kullanır; piksele ancak başka hiçbir şey kalmadığında başvurur.",

        "how_kicker": "Nasıl çalışır",
        "how_h": "Önce yapı. Piksel en son.",
        "how_p": "NexRemote tek bir piksele bakmadan önce masaüstüne zaten ne bildiğini sorar. Bir alt basamağa ancak üstteki basamak verecek bir şey bulamadığında iner.",
        "lad1_t": "Pencere yöneticisine sor",
        "lad1_d": "KWin her pencereyi başlığı, uygulaması, konumu ve odağıyla listeler. Ajan bir pencereyi öne getirebilir, küçültebilir ya da kapatabilir; başka hiçbir şey yayınlamayan uygulamalar dahil.",
        "lad2_t": "Erişilebilirlik ağacını oku, adıyla bas",
        "lad2_d": "Düğmeler, anahtarlar ve alanlar adları ve rolleriyle gelir. Ajan “Dark” anahtarına adıyla basar. Fare hiç oynamaz ve hiçbir şey kontrolün ekranda nerede çizildiğine bağlı değildir.",
        "lad3_t": "Terminal metnini doğrudan oku",
        "lad3_d": "Konsole sekmeleri ve tmux bölmeleri metinlerini, her birinde çalışan programla birlikte verir. Ekran görüntüsü yok, OCR yok, odak değişmez.",
        "lad4_t": "Ancak o zaman: ekran görüntüsü ve piksel",
        "lad4_d": "Tarayıcılar, oyunlar ve çoğu Electron uygulaması için. Görüntü tek pencereye kırpılır ve modele göre küçültülür; o görüntüdeki bir piksel ekrana geri çevrilir ve RemoteDesktop portalı üzerinden tıklanır.",
        "via": "Kanal", "model_gets": "Modele giden", "gets_text": "metin", "gets_image": "tek görüntü",
        "lp_structured": "yapısal", "lp_pixels": "piksel",
        "st_notree": "ağaç yayınlamıyor", "st_lift": "düz metin, görüntü yok",
        "arch_h": "Her ajanla masaüstü arasında tek çekirdek",
        "arch_p": "Adaptörler yalnızca çekirdeği bilir; çekirdek yalnızca masaüstünün zaten sunduğu kanallarla konuşur. Bir backend eksikse geri kalanı çalışmaya devam eder ve <code>nexremote caps</code> neyin kapalı olduğunu söyler.",
        "arch_alt": "Mimari: ajanın dört adaptörden biriyle (kabuk CLI, MCP, HTTP, metin döngüsü) NexRemote çekirdeğine bağlanır; çekirdek masaüstüne KWin betikleri, AT-SPI, RemoteDesktop portalı, Konsole D-Bus ve tmux, kendi tmux ya da PTY oturumları ve spectacle ya da grim üzerinden ulaşır.",
        "arch_agent": "Ajanın", "arch_agent_s": "her harness, her model",
        "arch_text": "Metin döngüsü", "arch_core": "Çekirdek", "arch_desk": "Masaüstün",
        "a_win": "pencereler", "a_ctl": "adıyla kontroller", "a_in": "fare + klavye",
        "a_term": "açık terminaller", "a_sh": "kendi kabuğu", "a_shot": "ekran görüntüleri",

        "replay_kicker": "Oturum",
        "replay_h": "Bir oturumun tekrarı",
        "replay_note": "Komutlar ve çıktılar, CLI'ın yazdığı biçimin birebir aynısı. Masaüstü çizimdir; her komutun nereye dokunduğunu görebilesin diye.",
        "replay_chapters": "Tekrar bölümleri",
        "ch0": "Bak", "ch1": "Terminali oku", "ch2": "Cevap ver", "ch3": "Adıyla bas", "ch4": "Gördüğüne tıkla",
        "play": "Oynat", "pause": "Durdur", "again": "Tekrar oynat",

        "reach_kicker": "Erişim",
        "reach_h": "Neye uzanır",
        "reach_note": "Her yüzey, masaüstünün sunduğu en doğrudan kanalı kullanır; biri eksikse <code>nexremote caps</code> bunu söyler.",
        "th_cmd": "Komutlar", "th_how": "Nereden",
        "r_win": "Pencereler", "r_win_n": "Ekrandaki her pencere; erişilebilirlik ağacı yayınlamayan uygulamalar dahil.",
        "r_mouse": "Fare", "r_mouse_n": "Her tıktan hemen sonra imleciniz eski yerine konur.",
        "r_keys": "Klavye", "r_keys_how": "portal + pano",
        "r_keys_n": "Her dil. Klavye düzeninizde olmayan karakterler yapıştırılır, panonuz geri yüklenir.",
        "r_names": "Adıyla kontroller", "r_names_n": "Düğmelere ve anahtarlara, fareyi hiç oynatmadan basar.",
        "r_terms": "Açık terminaller", "r_terms_n": "Her birinde ne çalıştığını gösterir: claude, ssh, vim, bir build.",
        "r_shell": "Kendi kabuğu", "r_shell_n": "Oturumlar çağrılar arasında yaşar; bir şeyi başlatıp sonra dönüp bakabilir.",
        "r_shot": "Ekran", "r_shot_n": "Kırpma ve ölçek hatırlanır; <code>click X Y --from-shot</code> doğru piksele düşer.",
        "r_caps": "Bir şey eksik mi?", "r_caps_n": "Tek komut, bu makinede hangi kanalların çalıştığını ve geri kalanı için hangi paketlerin gerektiğini söyler.",

        "watch_kicker": "Terminaller",
        "watch_h": "Sekiz ajan açık mı? Biri diğerlerini yönetebilir.",
        "watch_p1": "Her Konsole sekmesi ve tmux bölmesi, ön plandaki programıyla listelenir. Bir ajan başka bir ajanın ekranını okuyabilir, sorusuna cevap verebilir ya da <code>--key C-c</code> ile durdurabilir.",
        "watch_p2": "Bunların hiçbiri pencere odağı ya da klavyeniz gerektirmez. O çalışırken siz de çalışmaya devam edersiniz.",
        "watch_cap": "Çıktı CLI'ın birebir biçiminde; terminal adları örnektir. Son iki komut test sandbox'ında çalıştı.",

        "pixels_kicker": "Son çare",
        "pixels_h": "Okunacak bir şey yoksa bakar.",
        "pixels_p": "Tarayıcılar, oyunlar ve çoğu Electron uygulaması kontrollerini yayınlamaz. Tek pencereye kırpılmış ve modele göre küçültülmüş bir ekran görüntüsü alın; model o görüntüdeki piksele tıklar, NexRemote onu ekrana geri çevirir.",
        "diag_title": "Üç monitörlü masaüstündeki bir pencere kırpılır, 800'e 900 boyutuna küçültülür ve görüntüdeki bir nokta ekran koordinatına geri çevrilir.",
        "diag_cap": "Üç monitörlü bir düzen, ölçekli. Tek nokta, iki koordinat sistemi.",

        "cmp_kicker": "Karşılaştırma",
        "cmp_h": "Nerede duruyor",
        "cmp_p": "Ürünlerle değil, bugün kullanılan araç türleriyle karşılaştırma. Her yaklaşımın bir yeri var; tablo, NexRemote'un yapmak için tasarlandığı işte nerede durduklarını gösteriyor.",
        "c_shot": "Yalnızca ekran görüntüsüyle çalışan bilgisayar kullanım ajanları",
        "c_rpa": "Klasik RPA ve xdotool betikleri",
        "c_api": "Uygulamaya özel API ve SDK'lar",
        "yes": "Evet", "no": "Hayır", "partly": "Kısmen",
        "cr1": "Kontrollere koordinatla değil, adıyla basar",
        "c1s": "koordinatı görüntüden tahmin eder",
        "c1r": "bazı RPA araçları seçici kullanır; xdotool koordinata tıklar",
        "c_inapp": "yalnızca o uygulamada",
        "cr2": "Ekrandaki metni görsel model olmadan okur",
        "c2u": "erişilebilirlik ağacı ve terminal metni",
        "c2s": "her okuma bir görüntüdür",
        "c2r": "araca bağlı",
        "cr3": "Okunacak hiçbir şey yayınlamayan uygulamalarda çalışır",
        "c3u": "ekran görüntüsüne düşer",
        "c3s": "asıl çalışma biçimi",
        "c3r": "kör koordinatlar",
        "c3a": "API gerekir",
        "cr4": "Zaten açık terminalleri okur ve onlara cevap verir",
        "c4s": "pencere görünürse, pikselden",
        "c4r": "yazabilir, okuyamaz",
        "c4a": "kapsam dışı",
        "cr5": "Wayland'de root olmadan çalışır",
        "c5s": "kuruluma bağlı; çoğu zaman X11 sanal makinesi",
        "c5r": "yalnızca X11 ya da /dev/uinput için root",
        "c5a": "girdiye dokunmaz",
        "cr6": "Her ajana tek bir kabuk komutuyla takılır",
        "c6u": "ayrıca MCP, HTTP ve metin döngüsü",
        "c6s": "çoğunlukla tek bir modelin araç biçimine bağlı",
        "c6r": "o da bir komut",
        "c6a": "her uygulamaya ayrı entegrasyon",
        "cmp_note": "Ürünler değil, kategoriler. “Evet”, yaklaşımın bunu kutudan çıktığı gibi yaptığı anlamına gelir; “Kısmen”, araca ya da kuruluma bağlı demektir.",

        "num_kicker": "Rakamlar",
        "num_h": "Rakamlarla",
        "num_note": "Bu sayfa her üretildiğinde v{{version}} kaynak kodundan sayılır. Tahmin yok, öngörü yok.",
        "n_mcp": "MCP aracı", "n_cli": "CLI komutu",
        "n_ways": "bağlanma yolu: kabuk, MCP, HTTP, metin döngüsü",
        "n_surf": "ulaştığı masaüstü yüzeyi",
        "n_harn": "kurulumu belgelenmiş harness",
        "n_tests": "test paketi; {{sandbox_suites}} tanesi atılabilir bir masaüstünde",
        "n_loc": "satır Python, çekirdek ve adaptörler",
        "n_root": "gereken root yetkisi",

        "why_kicker": "Neden şimdi",
        "why_h": "Parçalar yerine oturdu.",
        "why_p": "Öngörüye gerek yok. Araçlar zaten bu noktada.",
        "why1_t": "Ajanlar zaten terminalde çalışıyor",
        "why1_d": "Claude Code, Codex, Aider ve Gemini CLI gibi kodlama ajanları bir kabukta çalışır. Hepsinin ortak tek arayüzü kabuk komutudur; NexRemote da bu arayüzü konuşur.",
        "why2_t": "MCP ortak bir araç protokolü oldu",
        "why2_d": "Editörler ve ajan istemcileri araçlarını MCP üzerinden yükler. Tek bir NexRemote sunucusu hepsine aynı masaüstü araçlarını verir; ekran görüntüleri görsel olarak döner.",
        "why3_t": "Linux masaüstleri Wayland'e geçti",
        "why3_d": "Wayland, eski otomasyonun dayandığı X11 kestirmelerini kaldırdı. Resmî giriş yolu, kullanıcıya bir kez soran xdg-desktop-portal RemoteDesktop arayüzüdür. NexRemote bunun üzerine kurulu.",

        "rm_kicker": "Yol haritası",
        "rm_h": "Bugün olan, sırada olan",
        "rm_p": "Sol sütun bugün indirdiğiniz pakette var. Sağ sütun planlandı, henüz yapılmadı.",
        "rm_now": "Bugün v{{version}} içinde", "rm_next": "Planlanan",
        "sh1": "KWin betikleriyle her pencere",
        "sh2": "Bir kez onaylanan RemoteDesktop portalı üzerinden fare ve klavye",
        "sh3": "Açık Konsole sekmelerini ve tmux bölmelerini okuma ve onlara yazma",
        "sh4": "{{mcp_tools}} MCP aracı, {{cli_cmds}} CLI komutu, bir HTTP API ve tool calling'i olmayan modeller için metin döngüsü",
        "sh5": "Yalıtılmış bir sandbox masaüstünde koşan girdi testleri",
        "rm1_t": "Electron erişilebilirlik sarmalayıcısı.",
        "rm1_d": "Electron uygulamalarını otomatik olarak <code>--force-renderer-accessibility</code> ile başlatmak; böylece düğmelerine pikselle değil adıyla basılabilir.",
        "rm2_t": "GNOME ve wlroots doğrulaması.",
        "rm2_d": "Portal yolunu GNOME'da ve Sway gibi wlroots tabanlı ortamlarda test etmek, neyin çalıştığını belgelemek.",
        "rm3_t": "Uzak kurulum.",
        "rm3_d": "HTTP adaptörünü başka bir makinede çalıştırmak için bir systemd birimiyle birlikte sunmak.",
        "rm4_t": "MCP üzerinden canlı pencere ağacı.",
        "rm4_d": "Pencere listesini ve erişilebilirlik ağacını, araçların yanında MCP kaynakları olarak sunmak.",

        "connect_kicker": "Bağlantı",
        "connect_h": "Ajanına bağla",
        "connect_note": "Aynı çekirdek, dört giriş. Harness'ın hangisini konuşuyorsa onu seç.",
        "t_shell": "Kabuk", "t_text": "Yalnız metin modeli",
        "p_shell": "Komut çalıştırabilen her harness: Claude Code, Codex, Aider, opencode, bir cron işi. Ajana skill metnini ver, gerisini bilir.",
        "p_shell_c": "# ajanının talimatlarına yapıştır",
        "p_mcp": "Claude Code, Claude Desktop, Cursor ve diğer MCP istemcileri için stdio üzerinden {{mcp_tools}} araç. Ekran görüntüleri, modelin bakabildiği görsel olarak döner.",
        "p_http": "Başka makinedeki ajanlar için. Varsayılan olarak yalnızca localhost'u dinler; başka bir adrese token olmadan açılmayı reddeder.",
        "p_text": "Tool calling'i olmayan yerel modeller için. Model <code>nexremote</code> satırları yazar; döngü onları çalıştırır ve çıktıyı geri besler, ta ki model <code>DONE</code> yazana dek.",
        "p_text_goal": "Hesap makinesini aç, 7×8 hesapla, sonucu oku",

        "trust_kicker": "Güvenlik",
        "trust_h": "Makinene ne yapar",
        "f1_t": "Bir kez sorar", "f1_d": "İlk seferde KDE bir uzaktan kontrol penceresi açar. <em>Allow restoring</em> kutusunu işaretleyin, bir daha sormaz. Geri almak için <code>~/.config/nexremote/portal.json</code> dosyasını silin.",
        "f2_t": "İmlecini geri verir", "f2_d": "Wayland'de tek imleç vardır. Tık onu ödünç alır ve eski yerine geri koyar.",
        "f3_t": "Root yok", "f3_d": "<code>/dev/uinput</code> yok, başka kullanıcı olarak çalışan servis yok.",
        "f4_t": "Ağa kapalı", "f4_d": "HTTP adaptörü token olmadan localhost dışını dinlemez.",
        "f5_t": "Zarar veremeyeceği yerde test edilir", "f5_d": "Girdi testleri, kendi D-Bus'ı, sanal KWin'i, kendi portalı ve PipeWire'ı olan atılabilir bir masaüstünde koşar.",
        "limits_h": "Sınırlar",
        "l1": "KDE Plasma 6 (Wayland) üzerinde test edildi. GNOME'da aynı portal üzerinden çalışmalı, ama pencere listesi erişilebilirliğe düşer. wlroots tabanlı ortamlar test edilmedi.",
        "l2": "Konsole yalnızca görünen ekranı verir. Geçmiş satırlar için ajanı tmux içinde çalıştırın.",
        "l3": "Tarayıcılar ve Electron uygulamaları ekran görüntüsü ve koordinatla kullanılır.",

        "install_kicker": "Kurulum",
        "install_h": "Üç adımda kurulum",
        "s1_t": "İndir ve aç",
        "s2_t": "Komutları kur",
        "s2_d": "<code>nexremote</code>, <code>nexremote-mcp</code>, <code>nexremote-http</code> ve <code>nexremote-agent</code> komutlarını <code>~/bin</code> altına koyar, eksik paketleri listeler.",
        "s3_t": "Erişimi bir kez ver",
        "s3_d": "Erişilebilirlik ağacını açar ve KDE'nin uzaktan kontrol penceresini getirir: <em>Allow restoring</em> işaretle, <em>Share</em>'e bas.",
        "reqs": "Gerekli: <code>python3-dbus</code> <code>python3-gi</code> <code>xdg-desktop-portal-kde</code> <code>pipewire</code> <code>wl-clipboard</code>. İsteğe bağlı: <code>tmux</code> <code>python3-pil</code> <code>python3-xlib</code>, <code>pip install mcp</code>.",

        "cta_h": "Ajanına masaüstünü ver.",
        "cta_p": "Aç, kur, bir kez onayla. Proje, iş birliği ya da yatırım hakkında sorularınız için bize yazın.",
        "talk": "Bizimle konuşun", "mail_subject": "NexRemote hakkında", "foot_contact": "İletişim",

        "other_lang": "en", "other_lang_label": "EN", "other_lang_name": "English", "other_lang_href": "/",
        "home": "/tr/", "path": "/tr/",
    },
}

# Değeri HTML içeren anahtarlar (geri kalanı kaçışlanır)
RAW = {"reach_note", "r_shot_n", "watch_p1", "p_text", "f1_d", "f3_d", "s2_d", "s3_d", "reqs",
       "pa3_b", "arch_p", "rm1_d"}
# Derlemede üretilen, HTML içeren değerler
GENERATED_RAW = {"replay_json", "h1_html", "marquee_html", "arch_svg_h", "arch_svg_v"}

esc = html.escape


# ---------------------------------------------------------------- üreticiler
def h1_html(t: dict) -> str:
    """Başlık kelimeleri maskeli satırlara bölünür; JS yoksa düz metin gibi görünür."""
    out, i = [], 0
    for cls, line in (("l1", t["h1a"]), ("l2", t["h1b"])):
        words = []
        for w in line.split():
            words.append(f'<span class="w"><span style="--wi:{i}">{esc(w)}</span></span>')
            i += 1
        out.append(f'<span class="l {cls}">{" ".join(words)}</span>')
    return " ".join(out)


def marquee_html(lang: str) -> str:
    items = []
    for name in harness_names():
        if name in HARNESS_GENERIC:
            items.append((HARNESS_GENERIC[name][lang], True))
        else:
            items.extend((p.strip(), False) for p in name.split(" / "))
    lis = "".join(f'<li class="{"gen" if g else ""}">{esc(n)}</li>' for n, g in items)
    return lis


def arch_svgs(t: dict) -> tuple[str, str]:
    """Mimari diyagramı iki yönde (yatay masaüstü, dikey mobil) üretir."""
    agent = (t["arch_agent"], t["arch_agent_s"])
    adapters = [("Shell CLI", "nexremote"), ("MCP", "nexremote-mcp"),
                ("HTTP", "nexremote-http"), (t["arch_text"], "nexremote-agent")]
    core = (t["arch_core"], "nexremote.py")
    channels = [("KWin scripting", t["a_win"]), ("AT-SPI", t["a_ctl"]),
                ("RemoteDesktop portal", t["a_in"]), ("Konsole D-Bus · tmux", t["a_term"]),
                ("tmux · PTY", t["a_sh"]), ("spectacle · grim", t["a_shot"])]
    desk = (t["arch_desk"], "KDE Plasma 6 · Wayland")

    def node(nid, x, y, w, h, title, sub, cls=""):
        cx = x + w / 2
        return (f'<g class="an {cls}" data-an="{nid}">'
                f'<rect class="an-box" x="{x}" y="{y}" width="{w}" height="{h}" rx="10"/>'
                f'<rect class="an-hit" x="{x}" y="{y}" width="{w}" height="{h}" rx="10"/>'
                f'<text class="an-t" x="{cx}" y="{y + h / 2 - 3}" text-anchor="middle">{esc(title)}</text>'
                f'<text class="an-s" x="{cx}" y="{y + h / 2 + 14}" text-anchor="middle">{esc(sub)}</text></g>')

    def path(pid, d, stage):
        return f'<path class="ap" data-ap="{pid}" data-stage="{stage}" d="{d}" pathLength="1"/>'

    def svg(vb, paths, nodes, cls):
        return (f'<svg class="arch-svg {cls}" viewBox="{vb}" role="img" aria-label="{esc(t["arch_alt"], quote=True)}">'
                f'<g class="ap-g">{"".join(paths)}</g><g class="pk-g"></g>{"".join(nodes)}</svg>')

    # ----- yatay: 1160 x 470
    H, cy = 470, 235
    A = (0, cy - 60, 180, 120)
    ad = [(270, cy + (k - 1.5) * 72 - 23, 170, 46) for k in range(4)]
    C = (530, cy - 60, 150, 120)
    ch = [(760, cy + (j - 2.5) * 66 - 24, 210, 48) for j in range(6)]
    D = (1010, cy - 70, 150, 140)

    def hcurve(x1, y1, x2, y2):
        m = (x1 + x2) / 2
        return f"M{x1} {y1} C{m} {y1} {m} {y2} {x2} {y2}"

    hp, hn = [], []
    for k, b in enumerate(ad):
        hp.append(path(f"a{k}", hcurve(A[0] + A[2], cy, b[0], b[1] + b[3] / 2), 0))
        hp.append(path(f"b{k}", hcurve(b[0] + b[2], b[1] + b[3] / 2, C[0], cy), 1))
    for j, b in enumerate(ch):
        hp.append(path(f"c{j}", hcurve(C[0] + C[2], cy, b[0], b[1] + b[3] / 2), 2))
        hp.append(path(f"d{j}", hcurve(b[0] + b[2], b[1] + b[3] / 2, D[0], cy), 3))
    hn.append(node("A", *A, *agent, "an-agent"))
    hn += [node(f"ad{k}", *b, *adapters[k]) for k, b in enumerate(ad)]
    hn.append(node("C", *C, *core, "an-core"))
    hn += [node(f"ch{j}", *b, *channels[j]) for j, b in enumerate(ch)]
    hn.append(node("D", *D, desk[0], "Plasma 6 · Wayland", "an-desk"))
    horiz = svg(f"0 0 1160 {H}", hp, hn, "arch-hz")

    # ----- dikey: 360 x 900 (gövde hattı x=180)
    T = 180
    Av = (30, 0, 300, 70)
    adv = [(6 + k * 88, 140, 84, 52) for k in range(4)]
    tw = t["arch_text"].split()
    adv_lbl = [("Shell", "CLI"), ("MCP", "stdio"), ("HTTP", "token"), (tw[0], " ".join(tw[1:]))]
    Cv = (30, 262, 300, 70)
    rows = [410, 480, 550]
    chv = []
    for j in range(6):
        col, row = j % 2, j // 2
        x = 4 if col == 0 else 192
        chv.append((x, rows[row] - 26, 164, 52))
    Dv = (30, 640, 300, 80)

    def vcurve(x1, y1, x2, y2):
        m = (y1 + y2) / 2
        return f"M{x1} {y1} C{x1} {m} {x2} {m} {x2} {y2}"

    vp, vn = [], []
    for k, b in enumerate(adv):
        bx = b[0] + b[2] / 2
        vp.append(path(f"a{k}", vcurve(T, Av[1] + Av[3], bx, b[1]), 0))
        vp.append(path(f"b{k}", vcurve(bx, b[1] + b[3], T, Cv[1]), 1))
    for j, b in enumerate(chv):
        y = b[1] + b[3] / 2
        edge = b[0] + b[2] if j % 2 == 0 else b[0]
        vp.append(path(f"c{j}", f"M{T} {Cv[1] + Cv[3]} V{y - 12} Q{T} {y} {T - 12 if j % 2 == 0 else T + 12} {y} H{edge}", 2))
        vp.append(path(f"d{j}", f"M{edge} {y} H{T - 12 if j % 2 == 0 else T + 12} Q{T} {y} {T} {y + 12} V{Dv[1]}", 3))
    vn.append(node("A", *Av, *agent, "an-agent"))
    vn += [node(f"ad{k}", *b, *adv_lbl[k], "an-sm") for k, b in enumerate(adv)]
    vn.append(node("C", *Cv, *core, "an-core"))
    vn += [node(f"ch{j}", *b, *channels[j], "an-sm") for j, b in enumerate(chv)]
    vn.append(node("D", *Dv, *desk, "an-desk"))
    vert = svg("0 0 360 724", vp, vn, "arch-vt")
    return horiz, vert


# -------------------------------------------------------------------- render
def render(lang: str, st: dict[str, int]) -> str:
    tpl = open(os.path.join(SRC, "page.html"), encoding="utf-8").read()
    ver = version()
    nums = dict(st)
    sep = "," if lang == "en" else "."
    nums["py_lines_fmt"] = f"{st['py_lines']:,}".replace(",", sep)
    inner = {k: str(v) for k, v in nums.items()}
    inner["version"] = ver

    def fill(text: str) -> str:
        return re.sub(r"\{\{([a-z0-9_]+)\}\}", lambda m: inner[m.group(1)], text)

    t = {k: fill(v) for k, v in S[lang].items()}
    vals = dict(t)
    vals.update({k: str(v) for k, v in nums.items()})
    vals.update(lang=lang, version=ver, zip_size=size("nexremote.zip"), tgz_size=size("nexremote.tar.gz"),
                email=CONTACT_EMAIL,
                mailto=f"mailto:{CONTACT_EMAIL}?subject={quote(t['mail_subject'])}",
                replay_static=REPLAY_CAPTIONS[lang][0])
    vals["replay_json"] = json.dumps({"captions": REPLAY_CAPTIONS[lang], "termMsg": TERM_MSG[lang]},
                                     ensure_ascii=False).replace("</", "<\\/")
    vals["h1_html"] = h1_html(t)
    vals["marquee_html"] = marquee_html(lang)
    vals["arch_svg_h"], vals["arch_svg_v"] = arch_svgs(t)

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in vals:
            raise KeyError(f"[{lang}] şablonda tanımsız anahtar: {key}")
        v = str(vals[key])
        return v if key in RAW or key in GENERATED_RAW else esc(v, quote=True)

    used = set(re.findall(r"\{\{([a-z0-9_]+)\}\}", tpl))
    out = re.sub(r"\{\{([a-z0-9_]+)\}\}", sub, tpl)
    # h1a/h1b, h1_html üzerinden; mail_subject, mailto üzerinden kullanılır
    unused = set(S[lang]) - used - {"h1a", "h1b", "mail_subject", "arch_agent", "arch_agent_s",
                                     "arch_text", "arch_core", "arch_desk", "arch_alt",
                                     "a_win", "a_ctl", "a_in", "a_term", "a_sh", "a_shot"}
    if unused:
        print(f"[{lang}] kullanılmayan anahtarlar: {sorted(unused)}", file=sys.stderr)
    # Yüzey kartları README tablosuyla aynı sayıda olmalı (sayfadaki sayı kartlarla tutarlı kalsın)
    cards = len(re.findall(r"\bdata-surface\b", tpl))
    if cards != st["surfaces"]:
        raise SystemExit(f"yüzey kartı {cards}, README tablosu {st['surfaces']} satır: eşitleyin")
    return out


def main() -> int:
    missing = set(S["en"]) ^ set(S["tr"])
    if missing:
        raise SystemExit(f"diller arasında eksik anahtar: {sorted(missing)}")
    st = stats()
    targets = {"en": os.path.join(SITE, "index.html"),
               "tr": os.path.join(SITE, "tr", "index.html")}
    for lang, path in targets.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(render(lang, st))
        print(f"yazıldı: {os.path.relpath(path, ROOT)}")
    print("sayılar: " + ", ".join(f"{k}={v}" for k, v in st.items()) + f", version={version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
