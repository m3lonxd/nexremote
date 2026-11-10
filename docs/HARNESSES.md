# NexRemote — Harness Kurulum Matrisi

Her harness bir yetenek seviyesine göre en uygun adaptörü kullanır.
Hiçbiri diğerine bağımlı değildir — biri çalışmıyorsa diğeri devrede.

| Harness / İstemci | Adaptör | Kurulum | Not |
|---|---|---|---|
| **Claude Desktop** | MCP | `claude_desktop_config.json`'a `mcpServers.nexremote` ekle | 28 araç + kaynaklar |
| **Claude Code** | Skill + CLI | bu repoyu `~/.claude/skills/`e bağla, `nexremote` PATH'te | `/nexremote` skill'i |
| **Cursor** | MCP | `.cursor/mcp.json` | stdio |
| **Continue / Roo** | MCP | MCP yapılandırması | stdio |
| **Aider** | CLI | `nexremote` PATH'te; ajan komutu çalıştırır | metin |
| **Codex CLI / Gemini CLI** | CLI | shell erişimi yeterli | metin |
| **Özel / proxy'lenmiş CLI** | CLI | `nexremote ...` komutlarını kabukta çalıştır | evrensel |
| **HTTP-tabanlı uzak ajan** | HTTP | `NEXREMOTE_TOKEN=X nexremote-http --port 8788` veya `deploy/install-service.sh` | stdlib |
| **Tool-calling'siz model** | Metin protokolü | `nexremote-agent --model-cmd "..." --goal "..."` | ajan döngüsü |
| **Görsel model** | CLI + screenshot | `nexremote screenshot /tmp/s.png` | piksel |
| **Başsız sunucu (GUI yok)** | CLI/HTTP (TUI) | sadece `run`/`screen`/`keys` | otomatik |

## MCP yapılandırma örnekleri

**Claude Desktop** (`~/.config/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "nexremote": {
      "command": "/home/KULLANICI/bin/nexremote-mcp"
    }
  }
}
```

**Cursor** (`.cursor/mcp.json` proje kökünde):
```json
{ "mcpServers": { "nexremote": { "command": "/home/KULLANICI/bin/nexremote-mcp" } } }
```
Yolu kendi sisteminden al: `command -v nexremote-mcp` (MCP istemcileri `~`'yi açmaz).

## Skill kurulumu (Claude Code / Claude Desktop skills)

```bash
mkdir -p ~/.claude/skills/nexremote
cp nexremote/adapters/skill/nexremote.md ~/.claude/skills/nexremote/SKILL.md
```
Ardından `nexremote` komutu PATH'te olsun (`install.sh` bunu kurar).
Skill, `nexremote` komutlarını ne zaman ve nasıl kullanacağını ajana anlatır.

## Metin protokolü (herhangi bir model)

Model arayüzü evrenseldir: **stdin → prompt, stdout → metin**. Yani:

```bash
# Yerel ollama
nexremote-agent --model-cmd "ollama run qwen2.5" --goal "Firefox'u aç, duckduckgo'ya git"

# simonw/llm
nexremote-agent --model-cmd "llm -m gpt-4o-mini" --goal "..."

# Proxy'lenmiş özel CLI
nexremote-agent --model-cmd "my-internal-llm chat --quiet" --goal "..."

# Sahte/test modeli
nexremote-agent --model-cmd "python3 my_mock.py" --goal "test"
```

Model, yanıtında `nexremote ...` satırları yazar (ister `TOOL:` önekiyle ister
düz); wrapper ilkini çalıştırır, sonucu modele geri besler, `DONE:` görünce
durur. Böylece **tool-calling desteği olmayan modeller bile ajan olur.**

## Ortam değişkenleri

| Değişken | Etki |
|---|---|
| `NEXREMOTE_BIN` | `install.sh` wrapper dizini (varsayılan `~/bin`) |
| `NEXREMOTE_TOKEN` / `NEXREMOTE_TOKEN_FILE` | HTTP adaptörü için Bearer token (veya onu içeren dosya) |
| `PYTHONPATH` | wrapper'lar otomatik ayarlar; elle kullanımda `core/` ekle |
| `NEXREMOTE_SERVICE` | `0`: CLI arka plan servisini kullanma; `1`: macOS/Windows'ta da aç (Linux'ta varsayılan açık) |
| `NEXREMOTE_SERVICE_IDLE` | servis bu kadar sn boşta kalınca kapanır (varsayılan 600) |
| `NEXREMOTE_STRICT` | `1`: ref hızlı yolunu kapat, her eylemde tüm ağacı oku (ikiz öğe denetimi) |
| `NEXREMOTE_TRACE` | `1`: her adımın süresi stderr'e |
| `NEXREMOTE_CURSOR_MS` | ajan imlecinin süzülme süresi (varsayılan 80; tık beklemez) |
| `NEXREMOTE_SETTLE` | `--after` öncesi arayüzün durulma beklemesi, sn (varsayılan 0.15) |
| `NEXREMOTE_TREE_NODES` / `NEXREMOTE_TREE_MS` | ağaç okuma bütçesi (varsayılan 5000 düğüm / 10000 ms) |
| `NEXREMOTE_KWIN_WATCH` | `0`: uzun ömürlü süreçte KWin kalıcı izleyicisini kapat |

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `nexremote caps` → GUI kapalı | `sudo apt install python3-gi gir1.2-atspi-2.0` |
| `type`/`key`/`click X Y` çalışmıyor | `nexremote setup` → KDE izninde Share; `nexremote caps` portal durumunu gösterir |
| Electron iç butonları görünmüyor | uygulamayı kapatıp `nexremote launch <program>` ile başlat |
| MCP'de araç yok | `pip install mcp`; wrapper `nexremote-mcp` kullan |
| `nexremote: command not found` | `export PATH="$HOME/bin:$PATH"` |
