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
  - Bir turda BİRDEN ÇOK satır yazılabilir: sırayla çalışır, ilk hatada durur
    (her eylem için ayrı model turu beklemek yavaştır).
  - Bitince satır:      DONE: <özet>   (aynı turdaki eylemler önce çalışır; hata
    olursa görev bitmiş sayılmaz)
  - Düşünce serbest metin; yalnızca `nexremote` ile başlayan satırlar çalışır.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

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
  nexremote tree --window REF -i     # eyleme açık öğeler; satırlar ref=@s…:eN ile biter
  nexremote click REF --after        # öğeye tıkla + ekranda neyin değiştiğini gör
  nexremote click-text "Kaydet"      # adıyla bul ve tıkla (tek adım)
  nexremote fill REF "metin" --enter # alanı doldur ve Enter
  nexremote wait --element "Tamam"   # görünene kadar bekle (uyuyup tekrar bakma)
  nexremote find "Ad" --window REF   # öğe ara -> ref döner
  nexremote launch PROGRAM           # Electron'u ağacı açık başlat (ağaç boş + ipucu çıkarsa)
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
- Her eylem bir satır: `nexremote ...`. Bildiğin adımları AYNI turda alt alta yaz;
  sırayla çalışırlar, ilk hatada durulur ve sonuçları birlikte görürsün.
- Doğrulama için ayrı tur harcama: son eyleme `--after` ekle (ne değiştiğini yazar).
- Ref'ler arayüz değişse de geçerli kalır; STALE_REF/AMBIGUOUS_TARGET gelirse yeni `tree -i`.
- Görev bitince `DONE: <kısa özet>` yaz.
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


def run_tool(line: str, timeout: float = 60.0) -> tuple[str, bool]:
    """nexremote satırını çalıştır: (çıktı, başarılı mı)."""
    import shlex
    try:
        argv = shlex.split(line.strip())
    except ValueError as e:
        return f"[araç satırı ayrıştırılamadı: {e}]", False
    if not argv or argv[0] != "nexremote":
        return "[geçersiz araç satırı]", False
    # `nexremote` PATH'te olmayabilir — CLI'yi bu Python ile doğrudan çağır.
    # Kabuk kullanılmaz: model çıktısı kabukta yorumlanmaz, Windows'ta da aynı çalışır.
    cmd = [sys.executable, os.path.join(CLI, "nexremote_cli.py"), *argv[1:]]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (CORE, CLI, env.get("PYTHONPATH", "")) if p)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        p = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                           capture_output=True, timeout=timeout, env=env)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        # DeprecationWarning gürültüsünü temizle
        err = "\n".join(l for l in err.splitlines() if "DeprecationWarning" not in l
                        and "get_action_name" not in l)
        text = (out + (("\n[stderr] " + err) if err else ""))[:4000] or "(çıktı yok)"
        if p.returncode != 0:
            text += f"\n[çıkış kodu {p.returncode}]"
        return text, p.returncode == 0
    except subprocess.TimeoutExpired:
        return "[araç zaman aşımı]", False
    except Exception as e:
        return f"[araç hatası: {e}]", False


def _trim(history: list[str], keep: int, head: int = 3, old_limit: int = 1500) -> list[str]:
    """Her turda bütün geçmişi yeniden göndermek modeli yavaşlatır: ilk `head`
    girdi (talimat + görev) ve son `keep` girdi tam kalır; aradakiler kısaltılır."""
    if len(history) <= head + keep:
        return history
    middle = history[head:-keep]
    short = [h if len(h) <= old_limit else h[:old_limit] + "\n… (kısaltıldı)" for h in middle]
    if len(short) > 2 * keep:
        dropped = len(short) - 2 * keep
        short = [f"(önceki {dropped} girdi atlandı)"] + short[-2 * keep:]
    return history[:head] + short + history[-keep:]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NexRemote metin-protokol ajan döngüsü")
    p.add_argument("--model-cmd", required=True,
                   help="Model komutu (stdin=prompt, stdout=metin)")
    p.add_argument("--goal", required=True, help="Yapılacak görev")
    p.add_argument("--max-steps", type=int, default=20)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--keep", type=int, default=8, help="tam tutulacak son geçmiş girdisi")
    a = p.parse_args(argv)

    history: list[str] = [SYSTEM,
                          f"\nGÖREV: {a.goal}\n",
                          "İlk adımın: `nexremote caps` çalıştır ve ne "
                          "yapabildiğini öğren.\n"]
    steps = 0
    idle = 0                       # art arda araçsız tur
    while steps < a.max_steps:
        steps += 1
        prompt = "\n".join(_trim(history, a.keep)) + "\n\nSıradaki hamlen:\n"
        if a.verbose:
            print(f"\n{'='*60}\n[tur {steps}] modele gönderiliyor...\n")
        raw = call_model(a.model_cmd, prompt, a.timeout)
        if a.verbose:
            print(f"--- model yanıtı ---\n{raw}\n")

        done = _DONE_RE.search(raw)
        tools = _TOOL_RE.findall(raw)
        if not tools:
            if done:
                print(f"\n✅ DONE: {done.group(1).strip() or '(özet yok)'}")
                print(f"   ({steps} turda tamamlandı)")
                return 0
            idle += 1
            # model araç çağırmadı; ona yol göster
            history.append(f"ASİSTAN: {raw}")
            history.append("SİSTEM: Geçerli bir `nexremote ...` satırı yaz ya da "
                           "`DONE: ...` ile bitir.")
            if idle >= 3:
                # üst üste üç turdur araç yok — çıkmazı önle
                print("⚠️  Model araç çağırmadı. Son yanıt:")
                print(raw[:800])
                return 1
            continue
        idle = 0

        # Turdaki tüm araç satırları sırayla; ilk hatada dur, sonuçlar birlikte döner.
        history.append(f"ASİSTAN: {raw}")
        failed = False
        for line in tools:
            result, ok = run_tool(line)
            print(f"[{steps}] $ {line}")
            print(f"      → {result[:300]}")
            history.append(f"ARAÇ SONUCU ({line}){'' if ok else ' [HATA]'}:\n{result}\n")
            if not ok:
                failed = True
                skipped = len(tools) - tools.index(line) - 1
                if skipped:
                    history.append(f"SİSTEM: hata nedeniyle sonraki {skipped} satır çalıştırılmadı.")
                break
        if done and not failed:
            print(f"\n✅ DONE: {done.group(1).strip() or '(özet yok)'}")
            print(f"   ({steps} turda tamamlandı)")
            return 0
        if done and failed:
            history.append("SİSTEM: DONE kabul edilmedi — bir adım başarısız oldu.")

    print(f"\n⚠️  {a.max_steps} adımda bitmedi.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
