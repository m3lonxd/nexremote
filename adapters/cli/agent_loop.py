#!/usr/bin/env python3
"""NexRemote metin-protokol ajan döngüsü — TOOL-CALLING OLMAYAN MODELLER İÇİN.

Fikir: Metin üretebilen HER model (proxy'lenmiş CLI, ollama, uzak API, düz
sohbet modeli) bir ajan olabilir. Model `nexremote ...` komutlarını satır
olarak yazar; bu wrapper onları yakalar, çalıştırır ve sonucu geri besler.
Böylece tool-calling desteği olmayan modeller bile masaüstünü/terminali
görüp kontrol eder.

Model arayüzü EVRENSEL: stdin'e prompt verilir, stdout'tan metin alınır.
Bu yüzden herhangi bir CLI/LLM ile çalışır:
  --model-cmd "ollama run qwen2.5"
  --model-cmd "llm -m gpt-4o-mini"        (simonw/llm)
  --model-cmd "my-proxy-cli chat"
  --model-cmd "aider --message-only"      (örnek)

Kullanım:
  nexremote-agent --model-cmd "ollama run qwen2.5" \
      --goal "Hesap makinesini aç, 7 çarpı 8 hesapla, sonucu ekrandan oku."

Protokol (modelin uyması gereken):
  - Eylem için satır:   nexremote <alt-komut> [args]      ya da  TOOL: nexremote ...
  - Bitince satır:      DONE: <özet>
  - Düşünce serbest metin; yalnızca `nexremote` ile başlayan satırlar çalışır.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(_HERE, "..", "..", "core"))
CLI = os.path.abspath(_HERE)
for p in (CORE, CLI):
    if p not in sys.path:
        sys.path.insert(0, p)

_TOOL_RE = re.compile(r"^\s*(?:TOOL:\s*)?(nexremote\s+.+?)\s*$", re.MULTILINE)
_DONE_RE = re.compile(r"^\s*DONE:\s*(.*)$", re.MULTILINE)

SYSTEM = """Sen bir masaüstü/terminal kontrol ajanısın. Görevi adım adım yaparsın.

ELİNDEKİ TEK ARAÇ: `nexremote` kabuk komutu. Kullanılabilir alt komutlar:
  nexremote observe                  # İLK BUNU YAP: pencereler (win:{..}) + açık terminaller
  nexremote find "Ad" --window REF   # öğe ara -> atspi ref döner
  nexremote click REF                # öğeye isimle tıkla
  nexremote tree --window REF        # pencerenin erişilebilirlik ağacı
  nexremote set-text REF "metin"     # alanın içeriğini değiştir
  nexremote window REF activate      # pencereyi öne getir (yazmadan önce)
  nexremote type "metin"             # odaktaki pencereye yaz
  nexremote key "ctrl+s"             # tuş kombinasyonu
  nexremote click X Y                # ekran koordinatına gerçek tık (--right, --double)
  nexremote scroll N --at X Y        # tekerlek (+aşağı / -yukarı)
  nexremote terms                    # masaüstündeki açık terminaller
  nexremote term-read REF            # terminalin ekranını oku
  nexremote term-send REF "metin" --enter    # terminale yaz (--key C-c)
  nexremote run "komut"              # kendi terminalinde komut çalıştır, çıktıyı al

KURALLAR:
- Bir eylem yapmak için ÇOK SATIRDA tam olarak bir `nexremote ...` satırı yaz.
- Birden çok eylem gerekiyorsa sırayla yaz; her turda sonuçları göreceksin.
- Sonucu doğrulamak için eylemden sonra `observe` veya `term-read` ile tekrar bak.
- Görev bitince `DONE: <kısa özet>` yaz.
- ASLA birden fazla `nexremote` satırını aynı turda çalıştırmayı bekleme; tek tek.
"""


def call_model(model_cmd: str, prompt: str, timeout: float = 120.0) -> str:
    """Evrensel model arayüzü: stdin -> prompt, stdout -> metin."""
    try:
        p = subprocess.run(model_cmd, shell=True, input=prompt, text=True,
                           capture_output=True, timeout=timeout)
        return (p.stdout or "") + (("\n[stderr] " + p.stderr) if p.stderr else "")
    except subprocess.TimeoutExpired:
        return "[model zaman aşımı]"
    except Exception as e:
        return f"[model hatası: {e}]"


def run_tool(line: str, timeout: float = 60.0) -> str:
    """nexremote satırını çalıştır, çıktıyı döndür."""
    cmd = line.strip()
    if not cmd.startswith("nexremote"):
        return "[geçersiz araç satırı]"
    # `nexremote` wrapper PATH'te olmayabilir — doğrudan CLI'yi çağır (kendi kendine yeterli)
    if not cmd.startswith("nexremote-agent"):  # alt-araç != ajan döngüsü
        cmd = "python3 " + os.path.join(CLI, "nexremote_cli.py") + cmd[len("nexremote"):]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{CORE}:{CLI}:" + env.get("PYTHONPATH", "")
    try:
        p = subprocess.run(cmd, shell=True, text=True, capture_output=True,
                           timeout=timeout, env=env)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        # DeprecationWarning gürültüsünü temizle
        err = "\n".join(l for l in err.splitlines() if "DeprecationWarning" not in l
                        and "get_action_name" not in l)
        return (out + (("\n[stderr] " + err) if err else ""))[:4000] or "(çıktı yok)"
    except subprocess.TimeoutExpired:
        return "[araç zaman aşımı]"
    except Exception as e:
        return f"[araç hatası: {e}]"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NexRemote metin-protokol ajan döngüsü")
    p.add_argument("--model-cmd", required=True,
                   help="Model komutu (stdin=prompt, stdout=metin)")
    p.add_argument("--goal", required=True, help="Yapılacak görev")
    p.add_argument("--max-steps", type=int, default=20)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)

    history: list[str] = [SYSTEM,
                          f"\nGÖREV: {a.goal}\n",
                          "İlk adımın: `nexremote caps` çalıştır ve ne "
                          "yapabildiğini öğren.\n"]
    steps = 0
    while steps < a.max_steps:
        steps += 1
        prompt = "\n".join(history) + "\n\nSıradaki hamlen (yalnızca bir adım):\n"
        if a.verbose:
            print(f"\n{'='*60}\n[tur {steps}] modele gönderiliyor...\n")
        raw = call_model(a.model_cmd, prompt, a.timeout)
        if a.verbose:
            print(f"--- model yanıtı ---\n{raw}\n")

        done = _DONE_RE.search(raw)
        if done:
            print(f"\n✅ DONE: {done.group(1).strip() or '(özet yok)'}")
            print(f"   ({steps} adımda tamamlandı)")
            return 0

        tools = _TOOL_RE.findall(raw)
        if not tools:
            # model araç çağırmadı; ona yol göster
            history.append(f"ASİSTAN: {raw}")
            history.append("SİSTEM: Geçerli bir `nexremote ...` satırı yaz ya da "
                           "`DONE: ...` ile bitir.")
            if steps >= 3:
                # birkaç turdur araç yok — çıkmazı önle
                print("⚠️  Model araç çağırmadı. Son yanıt:")
                print(raw[:800])
                return 1
            continue

        # tek tek çalıştır (ilkini), sonucu geri besle
        line = tools[0]
        result = run_tool(line)
        print(f"[{steps}] $ {line}")
        print(f"      → {result[:300]}")
        history.append(f"ASİSTAN: {raw}")
        history.append(f"ARAÇ SONUCU ({line}):\n{result}\n")

    print(f"\n⚠️  {a.max_steps} adımda bitmedi.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
