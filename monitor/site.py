"""Gera docs/data.json (lido pela página docs/index.html no GitHub Pages)."""
import csv
import json
import tomllib
from datetime import datetime

from .core import CSV_PATH, ROOT, TZ
from .stores import STORE_NAMES

OUT = ROOT / "docs" / "data.json"


def build() -> None:
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    rows = []
    if CSV_PATH.exists():
        with CSV_PATH.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    products = []
    for p in cfg["produtos"]:
        series = {}
        latest = {}
        for r in rows:
            if r["produto"] != p["id"]:
                continue
            price = float(r["preco_avista"]) if r["status"] == "ok" and r["preco_avista"] else None
            series.setdefault(r["loja"], []).append({"t": r["timestamp"], "p": price})
            latest[r["loja"]] = {
                "t": r["timestamp"], "status": r["status"], "price": price,
                "installment": float(r["preco_parcelado"]) if r["preco_parcelado"] else None,
                "installments": r["parcelas"], "seller": r["vendedor"], "url": r["url"], "note": r["obs"],
            }
        stores = [e["loja"] for e in p["ofertas"]]
        products.append({
            "id": p["id"], "name": p["nome"], "target": p.get("valor_alvo"),
            "stores": [{"id": s, "name": STORE_NAMES[s], "points": series.get(s, []),
                        "latest": latest.get(s)} for s in stores],
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated": datetime.now(TZ).isoformat(timespec="seconds"),
        "products": products,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
