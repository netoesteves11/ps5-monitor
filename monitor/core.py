"""Orquestra: lê lojas, filtra anúncios, grava histórico e decide os avisos."""
from __future__ import annotations

import csv
import json
import os
import random
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import notify, stores
from .stores import Blocked, Offer, ParseError

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "prices.csv"
STATE_PATH = ROOT / "data" / "state.json"
TZ = ZoneInfo("America/Sao_Paulo")
CSV_FIELDS = ["timestamp", "produto", "loja", "status", "preco_avista", "preco_parcelado",
              "parcelas", "vendedor", "titulo", "url", "obs"]


@dataclass
class Reading:
    product_id: str
    store: str
    status: str               # ok | indisponivel | sem_oferta_oficial | bloqueado | erro
    offer: Offer | None = None
    note: str = ""


# ---------------------------------------------------------------- filtros
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def seller_ok(cfg: dict, store: str, offer: Offer) -> bool:
    if not offer.verified:
        # Plano B (comparador): só sabemos a loja, não o vendedor final.
        return _norm(offer.seller) in {_norm(x) for x in cfg["vendedores_aceitos"].get(store, [])}
    accepted = {_norm(x) for lst in cfg["vendedores_aceitos"].values() if isinstance(lst, list) for x in lst}
    if store == "amazon" and offer.seller_id:
        return offer.seller_id in cfg["vendedores_aceitos"].get("amazon_seller_ids", [])
    if store == "magalu" and offer.seller_id:
        return offer.seller_id in ("magazineluiza", "kabum", "carrefour")
    return _norm(offer.seller) in accepted


def title_problem(cfg: dict, product: dict, title: str, check_required: bool = True) -> str | None:
    t = _norm(title)
    for pat in cfg["filtros_globais"]["excluir"] + product.get("excluir", []):
        if re.search(pat, t, re.I):
            return f"título contém '{pat}'"
    for pat in (product.get("exigir", []) if check_required else []):
        if not re.search(pat, t, re.I):
            return f"título não contém '{pat}'"
    return None


def pick_best(cfg, product, store, offers: list[Offer]) -> Reading:
    """Escolhe a menor oferta válida (vendedor oficial, novo, título correto, com preço)."""
    reasons = []
    valid = []
    any_official_unavailable = False
    for o in offers:
        # No Buscapé o título vem cortado; a própria página já é do modelo certo.
        prob = title_problem(cfg, product, o.title, check_required=o.verified)
        if prob:
            reasons.append(f"{o.title[:50]}…: {prob}")
            continue
        if not o.seller:
            reasons.append("vendedor não identificado na página")
            continue
        if not seller_ok(cfg, store, o):
            reasons.append(f"vendedor terceiro: {o.seller}")
            continue
        if o.condition != "novo":
            reasons.append(f"condição '{o.condition}'")
            continue
        if not o.available or not o.price_cash:
            any_official_unavailable = True
            continue
        valid.append(o)
    if valid:
        best = min(valid, key=lambda o: o.price_cash)
        note = "" if best.verified else "vendedor não confirmado (via Buscapé)"
        return Reading(product["id"], store, "ok", best, note=note)
    if any_official_unavailable:
        return Reading(product["id"], store, "indisponivel", note="anúncio oficial sem estoque")
    unknown = [r for r in reasons if "não identificado" in r]
    if unknown and len(unknown) == len(reasons):
        # Não conseguimos garantir o vendedor: tratar como falha de leitura.
        return Reading(product["id"], store, "erro", note="não foi possível confirmar o vendedor")
    return Reading(product["id"], store, "sem_oferta_oficial",
                   note="; ".join(dict.fromkeys(reasons))[:300] or "nenhuma oferta")


# ---------------------------------------------------------------- coleta
def collect(cfg: dict, only_store: str | None = None, skip: set[str] = frozenset()) -> list[Reading]:
    s = cfg["settings"]

    def pause():
        time.sleep(random.uniform(s["pausa_min"], s["pausa_max"]))

    jobs = []
    for product in cfg["produtos"]:
        for entry in product["ofertas"]:
            if only_store and entry["loja"] != only_store:
                continue
            if entry["loja"] in skip:
                continue
            jobs.append((product, entry))

    need_browser = any(e["loja"] in stores.NEEDS_BROWSER for _, e in jobs)
    pw = browser = context = None
    if need_browser:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=stores.UA, locale="pt-BR", timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 850},
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9"},
        )

    readings = []
    first = True
    try:
        for product, entry in jobs:
            store = entry["loja"]
            offers, errors, blocked = [], [], False
            targets = entry.get("urls", [])
            if entry.get("busca"):
                targets = targets + [("busca", entry["busca"])]
            for target in targets:
                if not first:
                    pause()
                first = False
                try:
                    if isinstance(target, tuple):
                        if store != "carrefour":
                            raise ParseError("busca só implementada para Carrefour")
                        offers += stores.carrefour_search(target[1], pause=pause, browser=context)
                    else:
                        offers += stores.FETCHERS[store](target, context)
                except Blocked as e:
                    blocked = True
                    errors.append(f"bloqueado: {e}")
                except Exception as e:  # noqa: BLE001 — qualquer falha vira registro
                    errors.append(f"{type(e).__name__}: {str(e)[:150]}")
            fb = entry.get("plano_b_buscape")
            if fb and not offers and blocked:
                pause()
                try:
                    offers = stores.buscape(fb, context, stores.BUSCAPE_LABEL[store])
                    errors = [f"{stores.STORE_NAMES[store]} bloqueou; usado plano B (Buscapé)"]
                    if not offers:
                        errors.append("Buscapé não listou oferta dessa loja")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"plano B falhou: {type(e).__name__}: {str(e)[:120]}")
            if offers:
                r = pick_best(cfg, product, store, offers)
                if errors:
                    r.note = (r.note + " | " if r.note else "") + "parcial: " + "; ".join(errors)
            else:
                r = Reading(product["id"], store, "bloqueado" if blocked else "erro", note="; ".join(errors))
            readings.append(r)
            print(f"  [{product['id']:8}] {stores.STORE_NAMES[store]:15} {r.status:18} "
                  f"{('R$ %.2f' % r.offer.price_cash) if r.offer else ''} {r.offer.seller if r.offer else ''} {r.note[:120]}")
    finally:
        if context:
            context.close()
        if browser:
            browser.close()
        if pw:
            pw.stop()
    return readings


# ---------------------------------------------------------------- estado e avisos
def fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def evaluate(cfg: dict, readings: list[Reading], state: dict, now: str) -> list[str]:
    """Atualiza o estado e devolve as mensagens de aviso (HTML do Telegram)."""
    products = {p["id"]: p for p in cfg["produtos"]}
    threshold = cfg["settings"]["falhas_para_avisar"]
    msgs = []
    for r in readings:
        key = f"{r.product_id}|{r.store}"
        st = state.setdefault(key, {"last_price": None, "min_price": None, "min_at": None,
                                    "failures": 0, "failure_alerted": False, "below_target": False})
        product = products[r.product_id]
        store_name = stores.STORE_NAMES[r.store]

        if r.status in ("bloqueado", "erro"):
            st["failures"] += 1
            st["last_error"] = r.note
            if st["failures"] >= threshold and not st["failure_alerted"]:
                st["failure_alerted"] = True
                msgs.append(
                    f"⚠️ <b>{store_name} falhando</b> ({st['failures']}x seguidas)\n"
                    f"Modelo: {product['nome']}\nMotivo: {notify.esc(r.note[:300])}\n"
                    f"Sugestão: acompanhar via {stores.FALLBACK_HINT[r.store]}.")
            continue

        if st["failure_alerted"]:
            msgs.append(f"✅ <b>{store_name}</b> voltou a responder ({product['nome']}).")
        st["failures"], st["failure_alerted"] = 0, False
        st["last_status"] = r.status
        if r.status != "ok":
            continue

        o = r.offer
        price = round(o.price_cash, 2)
        prev, prev_min = st["last_price"], st["min_price"]
        target = product.get("valor_alvo")
        reasons = []
        if prev is not None and price < prev:
            reasons.append(f"caiu {(prev - price) / prev * 100:.1f}%".replace(".", ",") + " desde a última leitura")
        if prev_min is not None and price < prev_min:
            reasons.append("🏆 novo menor preço já registrado nesta loja")
        elif prev_min is not None and price == prev_min and prev is not None and prev > price:
            reasons.append("voltou ao menor preço já registrado")
        is_below = target is not None and price <= target
        if is_below and (not st["below_target"] or (prev is not None and price < prev)):
            reasons.append(f"🎯 abaixo do seu alvo de {fmt(target)}")
        st["below_target"] = is_below

        if reasons:
            pct = f"{(prev - price) / prev * 100:.1f}%".replace(".", ",") if prev and price < prev else "—"
            parc = f"\nParcelado: {fmt(o.price_installment)} ({o.installments})" if o.price_installment else ""
            msgs.append(
                f"🎮 <b>{notify.esc(product['nome'])}</b>\n"
                f"Loja: <b>{store_name}</b> (vendido por {notify.esc(o.seller)})\n"
                f"Preço antigo: {fmt(prev)}\nPreço novo: <b>{fmt(price)}</b> à vista\n"
                f"Queda: {pct}{parc}\n"
                f"Motivo: {'; '.join(reasons)}\n"
                + ("⚠️ Lido via Buscapé: confira se o vendedor é a própria loja.\n" if not o.verified else "")
                + 
                f'<a href="{o.url}">Abrir anúncio</a>')

        st["last_price"] = price
        st["last_at"] = now
        st["url"] = o.url
        if prev_min is None or price < prev_min:
            st["min_price"], st["min_at"] = price, now
    return msgs


def append_csv(readings: list[Reading], now: str) -> None:
    new = not CSV_PATH.exists()
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        for r in readings:
            o = r.offer
            w.writerow({
                "timestamp": now, "produto": r.product_id, "loja": r.store, "status": r.status,
                "preco_avista": f"{o.price_cash:.2f}" if o and o.price_cash else "",
                "preco_parcelado": f"{o.price_installment:.2f}" if o and o.price_installment else "",
                "parcelas": o.installments if o else "",
                "vendedor": o.seller if o else "",
                "titulo": o.title if o else "",
                "url": o.url if o else "",
                "obs": "; ".join(([*o.notes] if o else []) + ([r.note] if r.note else [])),
            })


def summary(cfg: dict, readings: list[Reading]) -> str:
    lines = ["## Monitor PS5 — leitura de " + datetime.now(TZ).strftime("%d/%m/%Y %H:%M"), ""]
    for p in cfg["produtos"]:
        lines += [f"### {p['nome']} (alvo {fmt(p.get('valor_alvo'))})", "",
                  "| Loja | Status | À vista | Parcelado | Vendedor | Obs |", "|---|---|---|---|---|---|"]
        for r in readings:
            if r.product_id != p["id"]:
                continue
            o = r.offer
            lines.append(f"| {stores.STORE_NAMES[r.store]} | {r.status} | {fmt(o.price_cash) if o else '—'} | "
                         f"{fmt(o.price_installment) if o else '—'} | {o.seller if o else ''} | {r.note[:120]} |")
        lines.append("")
    warn = [r for r in readings if r.status in ("bloqueado", "erro")]
    if warn:
        lines.append("**Atenção:** " + ", ".join(f"{stores.STORE_NAMES[r.store]}/{r.product_id} ({r.status})" for r in warn))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv          # não grava nem envia nada
    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--loja=")), None)
    skip = {x for a in argv if a.startswith("--sem-loja=") for x in a.split("=", 1)[1].split(",")}
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    now = datetime.now(TZ).isoformat(timespec="seconds")
    print(f"Monitor PS5 — {now}")
    readings = collect(cfg, only, skip)
    state = load_state()
    msgs = evaluate(cfg, readings, state, now)
    text = summary(cfg, readings)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if dry:
        print(f"\n[dry-run] {len(msgs)} aviso(s) que seriam enviados:")
        for m in msgs:
            print("---\n" + m)
        return 0
    append_csv(readings, now)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    from .site import build
    build()
    for m in msgs:
        notify.send(m)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
