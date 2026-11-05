"""Envio de avisos pelo Telegram.

Precisa de duas variáveis de ambiente (GitHub Secrets):
  TELEGRAM_BOT_TOKEN  — token do bot criado no @BotFather
  TELEGRAM_CHAT_ID    — seu chat id (veja o README)
Sem elas, as mensagens só são impressas no log.
"""
import html
import os

import requests


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[aviso — Telegram não configurado]\n" + text)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        print(f"[erro Telegram {r.status_code}] {r.text[:200]}\n{text}")
    return r.ok


if __name__ == "__main__":
    ok = send("✅ Monitor PS5: teste de aviso funcionando.")
    raise SystemExit(0 if ok else 1)
