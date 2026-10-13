# NexRemote

**AI ajanlarına uzaktan görme + kontrol yeteneği.** Masaüstünü (GUI) ve
terminal oturumlarını (TUI) bir ajana açar — her harness'a, her modele takılır.

> Tek tasarım ilkesi: **MCP lüks yol, kabuk komutu evrensel tabandır.**
> Bir ajan kabuk komutu çalıştırabiliyorsa, NexRemote'u kullanabilir.

---

## Neden?

Çoğu "computer use" çözümü tek bir ekosisteme kilitlidir: ya belirli bir
sağlayıcının tool-calling API'sini ister, ya MCP'yi destekleyen bir istemci,
ya da görsel yetenekli bir model şart koşar. NexRemote bunu tersine çevirir:

| Ortam | Nasıl takılır |
|---|---|
| **MCP'li istemci** (Claude Desktop, Cursor, Continue) | `adapters/mcp/server.py` — 13 araç |
| **Kabuk çalıştıran her harness** (Claude Code, Aider, Codex CLI, özel) | `nexremote` CLI komutları |
| **Proxy'lenmiş / özel CLI'lar** | CLI adaptörü — sadece `nexremote ...` |
| **HTTP konuşan uzak ajanlar** | `adapters/http/server.py` — sıfır bağımlılık |
| **Tool-calling'i OLMAYAN modeller** | `agent_loop.py` — metin protokolü |
| **Görsel modeller** | `nexremote screenshot` → PNG |

---

## Mimari

```
nexremote/
├── core/nexremote/
│   ├── models.py       Element / Window / Screenshot / CommandResult (ortak dil)
│   ├── base.py         Backend ABC (gui + tui polimorfizmi)
│   ├── nexremote.py    NexRemote facade — birleşik API
│   ├── input.py        Wayland giriş enjeksiyonu (wtype/ydotool/dotool)
│   └── backends/
│       ├── gui.py      AT-SPI: görme + isimle tıklama
│       └── tui.py      tmux/PTY: komut çalıştır + ekran oku
├── adapters/
│   ├── mcp/server.py   MCP sunucusu (13 araç)
│   ├── cli/            nexremote CLI + agent_loop (metin protokolü)
│   ├── http/server.py  HTTP API (stdlib)
│   └── skill/          LLM skill talimatı (nexremote.md)
└── docs/DEPENDENCIES.md
```

**Neden bu ayrım:** Backend'ler *nasıl* yapıldığını bilir (AT-SPI, tmux).
Adaptörler *nasıl sunulduğunu* bilir (MCP, CLI, HTTP). Hiçbiri diğerinin
detayını bilmez — yeni bir adaptör eklemek çekirdeğe dokunmaz.

---

## Nasıl çalışır

**GUI (AT-SPI).** Linux'un erişilebilirlik katmanı her butona, menüye, metin
alanına **isim + rol + eylem** verir. NexRemote bunu okur ve eylemi tetikler.
Piksel tahmini YOK — "kaydet" butonu varsa `nexremote find "Kaydet"` onu bulur,
`nexremote click REF` ona basar. KDE/GNOME/Qt/GTK uygulamalarında tam çalışır.

**TUI (tmux/PTY).** Bir terminal oturumunu açıp komut gönderir ve **ekranı
okur** (`capture-pane`). Böylece bir TUI ajanının (htop, vim, ncurses) ne
gösterdiğini görebilir ve ona tuş gönderebilirsin.

---

## Hızlı başlangıç

```bash
cd nexremote
./install.sh                    # wrapper'ları ~/bin'e kurar, bağımlılıkları kontrol eder
nexremote caps                  # ne yapabildiğini gör
nexremote observe               # mevcut durum
nexremote run "echo merhaba"    # terminal çalışıyor mu?
```

---

## Adaptörler

### 1. CLI (evrensel taban)
```bash
nexremote observe
nexremote find "Kaydet"
nexremote click atspi:9:0/0/12
nexremote run "ls -la" --json
```

### 2. MCP
```jsonc
// claude_desktop_config.json / .mcp.json
{ "mcpServers": { "nexremote": {
    "command": "python3",
    "args": ["/home/omarchy/nexremote/adapters/mcp/server.py"] } } }
```

### 3. Metin protokolü (tool-calling'siz modeller)
```bash
nexremote-agent --model-cmd "ollama run qwen2.5" \
  --goal "Hesap makinesini aç, 7x8 hesapla, sonucu oku"
```
Model `nexremote ...` satırları yazar; wrapper çalıştırır ve sonucu geri besler.

### 4. HTTP
```bash
python3 adapters/http/server.py --port 8788 --token GİZLİ
curl -s -X POST -d '{"command":"uptime"}' localhost:8788/run
```

---

## Detaylı kurulum

`docs/DEPENDENCIES.md` içinde sistem durumu ve kurulum komutları var.
Özet:
```bash
# Debian/Ubuntu
sudo apt install tmux wtype python3-gi gir1.2-atspi-2.0
pip install mcp                     # opsiyonel: MCP adaptörü için

# Arch
sudo pacman -S tmux wtype python-gobject at-spi2-core
```

---

## Sınırlar (dürüst)

- **Electron** uygulamaları (Discord, VS Code, Slack) iç butonlarını
  varsayılan olarak AT-SPI'ye sunmaz. Bayrakla açılır:
  `code --force-renderer-accessibility`.
- **Wayland yazma** için `wtype` (veya `ydotool`) gerekir. Yoksa `type`/`key`
  zarifçe başarısız olur (sessizce değil, `False`/hata ile).
- **Başsız sunucu**: GUI backend `available=False`; yalnızca `run`/`screen`/`keys`.
- Çekirdek ekran görüntüsü **piksel** getirir; görsel yorumu görsel yetenekli
  bir modele (veya `vision` ajanına) aittir — NexRemote yapısal ağacı tercih eder.

---

## Lisans
MIT
