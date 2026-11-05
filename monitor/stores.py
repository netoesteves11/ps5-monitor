"""Leitura de preço por loja.

Cada função recebe uma URL e devolve uma lista de `Offer` (todas as ofertas que a
página mostra, de qualquer vendedor). O filtro de vendedor/título é feito depois,
em `core.py`, para podermos registrar *por que* um anúncio foi descartado.

Kabum e Carrefour: requests simples (o HTML já traz os dados).
Amazon e Magalu: Playwright (bloqueiam requisições sem navegador).
Nenhuma função tenta contornar CAPTCHA: se aparecer, levanta `Blocked`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
}


class Blocked(Exception):
    """Loja bloqueou (403/429/CAPTCHA)."""


class ParseError(Exception):
    """Página carregou mas o formato mudou."""


@dataclass
class Offer:
    title: str
    seller: str
    price_cash: float | None          # à vista (Pix/boleto quando a loja mostra)
    price_installment: float | None = None  # total parcelado
    installments: str = ""            # ex.: "10x sem juros"
    available: bool = True
    condition: str = "novo"
    seller_id: str = ""
    url: str = ""
    verified: bool = True             # False = vendedor final não pôde ser confirmado
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- helpers
_session = requests.Session()
_session.headers.update(HEADERS)


def _get(url: str) -> str:
    r = _session.get(url, timeout=40)
    if r.status_code in (403, 429, 503):
        raise Blocked(f"HTTP {r.status_code}")
    if r.status_code == 404:
        raise ParseError("HTTP 404 (anúncio removido?)")
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
        r.encoding = "utf-8"
    low = r.text[:20000].lower()
    if "captcha" in low and ("robot" in low or "robô" in low or "validate" in low):
        raise Blocked("CAPTCHA")
    return r.text


def _next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ParseError("__NEXT_DATA__ não encontrado")
    return json.loads(m.group(1))


def brl(text: str) -> float | None:
    """'R$ 4.139,90' -> 4139.90"""
    m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", text or "")
    if not m:
        return None
    return float(m.group(1).replace(".", "").replace(",", "."))


# ----------------------------------------------------------------------- KABUM
def kabum(url: str, browser=None) -> list[Offer]:
    html = _get(url)
    try:
        p = _next_data(html)["props"]["pageProps"]["product"]
    except (KeyError, TypeError):
        raise ParseError("Kabum: JSON do produto não encontrado")
    prices = p.get("prices") or {}
    inst = p.get("installment") or {}
    n = inst.get("installment")
    return [Offer(
        title=p.get("title", ""),
        seller=p.get("sellerName") or (p.get("marketplace") or {}).get("sellerName", ""),
        price_cash=prices.get("priceWithDiscount") or None,
        price_installment=prices.get("price") or None,
        installments=f"{n}x {'com' if inst.get('hasFee') else 'sem'} juros" if n else "",
        available=bool(p.get("available")),
        url=url,
    )]


# ------------------------------------------------------------------- CARREFOUR
def _carrefour_product(url: str) -> Offer:
    html = _get(url)
    lds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    prod = None
    for raw in lds:
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            prod = d
            break
    if not prod:
        raise ParseError("Carrefour: dados do produto (ld+json) não encontrados")
    off = prod.get("offers") or {}
    if isinstance(off, list):
        off = off[0] if off else {}

    # Vendedor: "Vendido e entregue por <strong>Carrefour</strong>" (loja própria)
    # ou "Vendido e entregue por <a ...>Nome</a>" (marketplace).
    seller = ""
    m = re.search(r"Vendido e entregue por(?:<!-- -->)?\s*(<[^>]+>)?\s*([^<]+)<", html)
    if m:
        seller = unescape(m.group(2)).strip()
    else:
        m = re.search(r"Vendido e Entregue por:</td><td[^>]*>([^<]+)<", html)
        if m:
            seller = unescape(m.group(1)).strip()

    # Parcelado (cartões comuns): "Por: R$ 4.599,00 em até 10x de R$ 459,90 sem juros"
    inst_total, inst_txt = None, ""
    soup_txt = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    mi = re.search(r"em até (\d+) ?x de (R\$\s?[\d.]+,\d{2}) (sem|com) juros", soup_txt)
    if mi:
        n, parcela = int(mi.group(1)), brl(mi.group(2))
        inst_total, inst_txt = round(n * parcela, 2), f"{n}x {mi.group(3)} juros"

    cond = str(off.get("itemCondition", "")).lower()
    return Offer(
        title=unescape(prod.get("name", "")),
        seller=seller,
        price_cash=float(off["price"]) if off.get("price") not in (None, "") else None,
        price_installment=inst_total,
        installments=inst_txt,
        available="instock" in str(off.get("availability", "")).lower(),
        condition="novo" if (not cond or "new" in cond) else cond,
        url=url,
    )


def carrefour(url: str, browser=None) -> list[Offer]:
    return [_carrefour_product(url)]


def carrefour_search(query: str, max_candidates: int = 6, pause=None) -> list[Offer]:
    """Busca no Carrefour e lê os anúncios candidatos (filtragem fina vem depois)."""
    from urllib.parse import quote
    html = _get("https://www.carrefour.com.br/busca/" + quote(query))
    links = []
    for href in re.findall(r'href="(/produto/[^"?#]+)"', html):
        if "console" in href and "playstation" in href and href not in links:
            links.append(href)
    offers = []
    for href in links[:max_candidates]:
        if pause:
            pause()
        try:
            offers.append(_carrefour_product("https://www.carrefour.com.br" + href))
        except ParseError:
            continue
    return offers


# ---------------------------------------------------------------------- AMAZON
AMAZON_CAPTCHA = ("validateCaptcha", "Digite os caracteres", "Type the characters", "/errors/validateCaptcha")


def amazon(url: str, browser) -> list[Offer]:
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
    if not m:
        raise ParseError("ASIN não encontrado na URL")
    asin = m.group(1)
    page = browser.new_page()
    try:
        page.goto(f"https://www.amazon.com.br/dp/{asin}", wait_until="domcontentloaded", timeout=60000)
        html = page.content()
        if any(s in html for s in AMAZON_CAPTCHA):
            raise Blocked("CAPTCHA da Amazon")
        if "Algo deu errado" in (page.title() or ""):
            raise Blocked("Amazon devolveu página de erro (provável bloqueio)")
        title = (page.text_content("#productTitle") or "").strip()
        if not title:
            raise ParseError("Amazon: título do produto não encontrado")
        # Lista "Todas as ofertas" — mostra cada vendedor com preço e condição.
        aod = page.evaluate(
            """async (asin) => {
                const r = await fetch('/gp/product/ajax/aodAjaxMain/ref=dp_aod_ALL_mbc?asin=' + asin + '&pc=dp&experienceId=aodAjaxMain');
                return await r.text();
            }""",
            asin,
        )
    finally:
        page.close()

    if any(s in aod for s in AMAZON_CAPTCHA):
        raise Blocked("CAPTCHA da Amazon (lista de ofertas)")
    soup = BeautifulSoup(aod, "html.parser")
    blocks = soup.select("#aod-pinned-offer, #aod-offer")
    if not blocks:
        # Produto sem nenhuma oferta (indisponível) — não é erro de leitura.
        if "aod-" in aod:
            return [Offer(title=title, seller="", price_cash=None, available=False, url=url,
                          notes=["sem ofertas na Amazon"])]
        raise ParseError("Amazon: lista de ofertas em formato inesperado")

    offers = []
    for b in blocks:
        price_el = b.select_one(".apex-pricetopay-accessibility-label") or b.select_one(".a-price .a-offscreen")
        price = brl(price_el.get_text(" ", strip=True)) if price_el else None
        if price is None:
            whole = b.select_one(".a-price-whole")
            frac = b.select_one(".a-price-fraction")
            if whole:
                price = brl(whole.get_text("", strip=True).rstrip(",") + "," + (frac.get_text(strip=True) if frac else "00"))
        sold = b.select_one("#aod-offer-soldBy")
        seller, seller_id = "", ""
        if sold:
            el = sold.select_one(".a-col-right a, .a-col-right span.a-size-small")
            seller = (el.get_text(" ", strip=True) if el else "").strip()
            link = sold.select_one("a[href*='seller=']")
            if link:
                mm = re.search(r"seller=([A-Z0-9]+)", link["href"])
                seller_id = mm.group(1) if mm else ""
        ships = b.select_one("#aod-offer-shipsFrom .a-col-right")
        heading = b.select_one("#aod-offer-heading")
        cond = (heading.get_text(" ", strip=True) if heading else "").lower()
        notes = []
        if ships:
            notes.append("enviado por " + ships.get_text(" ", strip=True))
        offers.append(Offer(
            title=title, seller=seller, seller_id=seller_id, price_cash=price,
            condition="novo" if cond.startswith("novo") else (cond or "desconhecida"),
            url=url, notes=notes,
        ))
    return offers


# ---------------------------------------------------------------------- MAGALU
def magalu(url: str, browser) -> list[Offer]:
    page = browser.new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if resp is not None and resp.status in (403, 429):
            raise Blocked(f"HTTP {resp.status}")
        raw = page.evaluate("() => { const e = document.getElementById('__NEXT_DATA__'); return e ? e.textContent : null; }")
        if not raw:
            body = (page.content() or "")[:5000].lower()
            if "captcha" in body or "access denied" in body or "bloque" in body:
                raise Blocked("Magalu bloqueou o acesso")
            raise ParseError("Magalu: __NEXT_DATA__ não encontrado")
    finally:
        page.close()
    try:
        item = json.loads(raw)["props"]["pageProps"]["data"]["item"]
    except (KeyError, TypeError):
        raise ParseError("Magalu: estrutura do produto mudou")
    title = item.get("title", "")
    offers = []
    for o in item.get("offers") or []:
        s = o.get("seller") or {}
        best = o.get("bestPrice") or {}
        plan = o.get("bestInstallmentPlan") or {}
        n = plan.get("installment")
        offers.append(Offer(
            title=title,
            seller=s.get("name") or s.get("description") or "",
            seller_id=s.get("id", ""),
            price_cash=best.get("totalAmount") or o.get("price"),
            price_installment=plan.get("totalAmount") or o.get("price"),
            installments=f"{n}x {plan.get('paymentMethodDescription', '')}".strip() if n else "",
            available=bool(item.get("available", True)) and bool(s.get("available", True)),
            url=url,
        ))
    if not offers:
        offers.append(Offer(title=title, seller="", price_cash=None, available=False, url=url,
                            notes=["sem ofertas"]))
    return offers


# --------------------------------------------------------------------- BUSCAPÉ
def buscape(url: str, browser, store_label: str) -> list[Offer]:
    """Plano B: lê no Buscapé as ofertas de uma loja (ex.: 'Magazine Luiza').

    O Buscapé mostra a loja, mas não o vendedor final do marketplace, então a
    oferta volta marcada como `verified=False`.
    """
    page = browser.new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if resp is not None and resp.status in (403, 429):
            raise Blocked(f"Buscapé HTTP {resp.status}")
        try:
            page.wait_for_selector('[data-testid="offer-list-wrapper"] li', timeout=25000)
        except Exception:
            raise ParseError("Buscapé: lista de ofertas não apareceu")
        rows = page.evaluate("""() => [...document.querySelectorAll('[data-testid="offer-list-wrapper"] li')].map(li => ({
            text: li.innerText.replace(/\\s+/g, ' '),
            alts: [...li.querySelectorAll('img[alt]')].map(i => i.alt).filter(a => a && !/buscap/i.test(a)),
        }))""")
    finally:
        page.close()
    offers, seen = [], set()
    for r in rows:
        txt, alts = r["text"], r["alts"]
        if not alts or store_label.lower() not in (alts[-1] or "").lower():
            continue
        price = brl((re.search(r"R\$\s?[\d.]+,\d{2}\s*à vista", txt) or [""])[0])
        mi = re.search(r"(\d+)x de (R\$\s?[\d.]+,\d{2})", txt)
        key = (alts[0], price)
        if price is None or key in seen:
            continue
        seen.add(key)
        offers.append(Offer(
            title=alts[0], seller=store_label, price_cash=price,
            price_installment=round(int(mi.group(1)) * brl(mi.group(2)), 2) if mi else None,
            installments=f"{mi.group(1)}x" if mi else "", url=url, verified=False,
            notes=["lido via Buscapé — vendedor final não confirmado"],
        ))
    return offers


FETCHERS = {"kabum": kabum, "carrefour": carrefour, "amazon": amazon, "magalu": magalu}
NEEDS_BROWSER = {"amazon", "magalu"}
BUSCAPE_LABEL = {"magalu": "Magazine Luiza", "kabum": "KaBuM!", "amazon": "Amazon", "carrefour": "Carrefour"}
STORE_NAMES = {"kabum": "Kabum", "amazon": "Amazon", "magalu": "Magazine Luiza", "carrefour": "Carrefour"}
FALLBACK_HINT = {
    "kabum": "Zoom (zoom.com.br) ou Buscapé, que listam a oferta da KaBuM!",
    "amazon": "Zoom/Buscapé (filtrando 'Amazon') ou o histórico do Keepa",
    "magalu": "Zoom ou Buscapé, filtrando a loja 'Magazine Luiza'",
    "carrefour": "Zoom ou Buscapé, filtrando a loja 'Carrefour'",
}
