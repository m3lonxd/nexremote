# NexRemote

Bir AI ajanına bu Linux masaüstünü ve üzerinde açık terminalleri **görme ve
kullanma** yetkisi verir. Ajan kabuk komutu çalıştırabiliyorsa yeter: Claude
Code, Codex, Aider, opencode, kendi betiğin ya da tool-calling'i olmayan bir
yerel model.

```console
$ nexremote observe
Pencereler (6) — * etkin:
  * org.kde.konsole        1920x1080+1080+0     ~ : claude-bin — Konsole
    zen                    1920x1080+1080+0     YouTube — Zen Browser
    ...
Açık terminaller (4):
  konsole:7891:1       claude-bin     ~/nexwatch            ~ : claude-bin
  konsole:29769:1      ssh            ~                     (root) 45.136.6.110

$ nexremote term-read konsole:29769:1          # başka bir terminalin ekranı
$ nexremote term-send konsole:7891:1 "testleri çalıştır" --enter
$ nexremote screenshot --window win:{…} --max-width 1568
$ nexremote click 412 230 --from-shot          # görüntüde gördüğün yere tıkla
$ nexremote type "Merhaba dünya — ğüşıöç"
```

## Ne yapabilir

| | Nasıl |
|---|---|
| **Ekrandaki her pencere** — başlık, uygulama, konum; öne getir, küçült, kapat | KWin scripting |
| **Gerçek fare** — tık, sağ/çift tık, kaydırma, sürükleme; kullanıcının imleci tıklamadan sonra yerine döner | RemoteDesktop portalı |
| **Gerçek klavye** — kısayollar, her dilde metin | portal + pano (Latin-1 dışı karakterler) |
| **Butona adıyla basma** — fare hiç oynamaz | AT-SPI erişilebilirlik ağacı |
| **Açık terminaller** — başka bir CLI ajanının, ssh'ın, build'in ekranını oku; ona yaz | Konsole D-Bus, tmux |
| **Kendi terminali** — komut çalıştır, çıktıyı al, oturum çağrılar arasında yaşar | tmux / PTY |
| **Ekran görüntüsü** — pencereye kırp, küçült; görüntü koordinatıyla tıkla | spectacle / grim |

Root gerekmez. Fare ve klavye için KDE **bir kez** izin sorar; sonraki
oturumlar sormadan başlar.

## Kurulum

```bash
git clone … nexremote && cd nexremote
./install.sh          # ~/bin altına komutları koyar, eksik paketleri listeler
nexremote setup       # erişilebilirliği açar; KDE izin penceresinde
                      # "Allow restoring…" işaretle → Share
nexremote caps        # neyin çalıştığını gösterir
```

## Ajanına bağla

| Ajan | Bağlantı |
|---|---|
| Kabuk çalıştıran her harness | `nexremote skill` çıktısını sistem talimatına ekle |
| MCP istemcisi (Claude Code, Claude Desktop, Cursor) | `claude mcp add nexremote -- ~/bin/nexremote-mcp` — 21 araç, ekran görüntüsü görsel olarak döner |
| HTTP konuşan uzak ajan | `nexremote-http --port 8788 --token GİZLİ` |
| Tool-calling'i olmayan model | `nexremote-agent --model-cmd "ollama run qwen2.5" --goal "…"` |

Komutların tamamı: `nexremote --help`. Ajan talimatı: [`adapters/skill/nexremote.md`](adapters/skill/nexremote.md).

## Mimari

```
core/nexremote/
  nexremote.py      tek giriş noktası (facade); adaptörler yalnızca bunu bilir
  kwin.py           pencereler + imleç konumu (KWin betiği → callDBus ile yanıt)
  portal_input.py   fare/klavye daemon'ı (RemoteDesktop + ScreenCast oturumu)
  terminals.py      masaüstündeki Konsole sekmeleri ve tmux bölmeleri
  backends/gui.py   AT-SPI ağacı, isimle bulma/tıklama
  backends/tui.py   NexRemote'un kendi tmux/PTY oturumları
  xcursor.py        görünür ajan imleci (click-through, kullanıcıyı engellemez)
adapters/  cli · mcp · http · skill
```

## Testler

```bash
tests/run_all.sh
```

Girdi enjeksiyonu yapan testler, `tests/sandbox.sh` ile açılan **izole** bir
masaüstünde koşar: ayrı D-Bus, sanal KWin, ayrı portal ve PipeWire. Gerçek
ekrana, fareye ve izinlere dokunmaz. İzin diyaloğunu da NexRemote'un kendi
erişilebilirlik okuması onaylar.

## Sınırlar

- Hedef KDE Plasma 6 Wayland. GNOME'da portal yolu çalışmalı ama pencere
  listesi (KWin) yalnızca AT-SPI'ye düşer. wlroots tabanlı ortamlar test edilmedi.
- Konsole D-Bus yalnızca görünen ekranı verir; geçmiş satırlar için tmux gerekir.
- Tarayıcılar ve Electron uygulamaları çoğunlukla ağaç yayınlamaz; bunlarda
  ekran görüntüsü + koordinat tıklaması kullanılır.

MIT
