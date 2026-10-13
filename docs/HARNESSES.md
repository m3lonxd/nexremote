# NexRemote — Harness Kurulum Matrisi

Her harness bir yetenek seviyesine göre en uygun adaptörü kullanır.
Hiçbiri diğerine bağımlı değildir — biri çalışmıyorsa diğeri devrede.

| Harness / İstemci | Adaptör | Kurulum | Not |
|---|---|---|---|
| **Claude Desktop** | MCP | `claude_desktop_config.json`'a `mcpServers.nexremote` ekle | 13 araç |
| **Claude Code** | Skill + CLI | bu repoyu `~/.claude/skills/`e bağla, `nexremote` PATH'te | `/nexremote` skill'i |
| **Cursor** | MCP | `.cursor/mcp.json` | stdio |
| **Continue / Roo** | MCP | MCP yapılandırması | stdio |
| **Aider** | CLI | `nexremote` PATH'te; ajan komutu çalıştırır | metin |
| **Codex CLI / Gemini CLI** | CLI | shell erişimi yeterli | metin |
| **Özel / proxy'lenmiş CLI** | CLI | `nexremote ...` komutlarını kabukta çalıştır | evrensel |
| **HTTP-tabanlı uzak ajan** | HTTP | `nexremote-http --port 8788 --token X` | stdlib |
| **Tool-calling'siz model** | Metin protokolü | `nexremote-agent --model-cmd "..." --goal "..."` | ajan döngüsü |
| **Görsel model** | CLI + screenshot | `nexremote screenshot /tmp/s.png` | piksel |
| **Başsız sunucu (GUI yok)** | CLI/HTTP (TUI) | sadece `run`/`screen`/`keys` | otomatik |

## MCP yapılandırma örnekleri

**Claude Desktop** (`~/.config/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "nexremote": {
      "command": "/home/omarchy/bin/nexremote-mcp"
    }
  }
}
```

**Cursor** (`.cursor/mcp.json` proje kökünde):
```json
{ "mcpServers": { "nexremote": { "command": "/home/omarchy/bin/nexremote-mcp" } } }
```

## Skill kurulumu (Claude Code / Claude Desktop skills)

```bash
mkdir -p ~/.claude/skills/nexremote
cp /home/omarchy/nexremote/adapters/skill/nexremote.md ~/.claude/skills/nexremote/SKILL.md
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
| `NEXREMOTE_TOKEN` | HTTP adaptörü için Bearer token |
| `PYTHONPATH` | wrapper'lar otomatik ayarlar; elle kullanımda `core/` ekle |

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `nexremote caps` → GUI kapalı | `sudo apt install python3-gi gir1.2-atspi-2.0` |
| `type`/`key` çalışmıyor | `sudo apt install wtype` |
| Electron iç butonları görünmüyor | uygulamayı `--force-renderer-accessibility` ile başlat |
| MCP'de araç yok | `pip install mcp`; wrapper `nexremote-mcp` kullan |
| `nexremote: command not found` | `export PATH="$HOME/bin:$PATH"` |
