#!/usr/bin/env python3
"""
PocketMeta — genera una web estática del meta de Pokémon TCG Pocket
a partir de los torneos públicos de Limitless TCG.

Uso:
    python scripts/update.py            # datos reales (API de Limitless)
    python scripts/update.py --demo     # datos de ejemplo, sin red

Variables de entorno (opcionales):
    SITE_URL        URL pública de la web (para sitemap y canonical)
    SITE_NAME       Nombre de la web
    LIMITLESS_KEY   Clave de API de Limitless, si te la piden
    LIMITLESS_GAME  ID del juego en Limitless (por defecto POCKET)
    DAYS            Días hacia atrás a analizar (por defecto 14)
    MIN_PLAYERS     Mínimo de jugadores por torneo (por defecto 16)
    ADSENSE_CLIENT  Tu ID de AdSense (ca-pub-XXXX); vacío = sin anuncios
    AMAZON_TAG      Tu tag de afiliado de Amazon (p. ej. tuweb-21)
"""
import html
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"
DATA = ROOT / "data"

API = "https://play.limitlesstcg.com/api"
GAME = os.getenv("LIMITLESS_GAME", "POCKET")
KEY = os.getenv("LIMITLESS_KEY", "").strip()
DAYS = int(os.getenv("DAYS", "14"))
MIN_PLAYERS = int(os.getenv("MIN_PLAYERS", "16"))
SITE_URL = os.getenv("SITE_URL", "https://example.github.io/pocketmeta").rstrip("/")
SITE_NAME = os.getenv("SITE_NAME", "PocketMeta")
ADSENSE = os.getenv("ADSENSE_CLIENT", "").strip()
AMAZON_TAG = os.getenv("AMAZON_TAG", "").strip()


# ---------------------------------------------------------------- datos
def api_get(path, **params):
    if KEY:
        params["key"] = KEY
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        req = urllib.request.Request(url, headers={"User-Agent": "PocketMeta/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:  # límite de peticiones: esperar y reintentar
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Demasiados reintentos: {url}")


def fetch_real():
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    tournaments = []
    for page in range(1, 30):
        batch = api_get("/tournaments", game=GAME, limit=50, page=page)
        if not batch:
            break
        for t in batch:
            d = datetime.fromisoformat(t["date"].replace("Z", "+00:00"))
            if d >= cutoff and (t.get("players") or 0) >= MIN_PLAYERS:
                tournaments.append(t)
        last = datetime.fromisoformat(batch[-1]["date"].replace("Z", "+00:00"))
        if last < cutoff:
            break
        time.sleep(0.5)

    rows = []
    for t in tournaments:
        try:
            standings = api_get(f"/tournaments/{t['id']}/standings")
        except Exception as e:  # un torneo roto no tumba la ejecución
            print(f"  ! {t['name']}: {e}", file=sys.stderr)
            continue
        for s in standings:
            deck = s.get("deck") or {}
            if not deck.get("id"):
                continue
            rec = s.get("record") or {}
            rows.append({
                "deck_id": deck["id"],
                "deck_name": deck.get("name") or deck["id"],
                "wins": rec.get("wins", 0),
                "losses": rec.get("losses", 0),
                "ties": rec.get("ties", 0),
                "placing": s.get("placing"),
                "player": s.get("name") or s.get("player"),
                "tournament": t["name"],
                "tournament_id": t["id"],
                "players": t.get("players", 0),
                "date": t["date"][:10],
            })
        time.sleep(0.5)
    return tournaments, rows


def fetch_demo():
    """Datos inventados para ver la web sin conexión. NO publicar."""
    rnd = random.Random(42)
    decks = ["Mega Lucario ex", "Mega Gardevoir ex", "Darkrai ex Giratina", "Mega Charizard Y ex",
             "Suicune ex Greninja", "Mega Altaria ex", "Pikachu ex Zeraora", "Dragonite Dialga",
             "Mew ex Mewtwo", "Hydreigon Mega Absol", "Gholdengo Melmetal", "Rampardos Lucario"]
    weights = [10, 8, 7, 6, 5, 4, 3.5, 3, 3, 2.5, 2, 1.5]
    tournaments, rows = [], []
    today = datetime.now(timezone.utc).date()
    for i in range(40):
        n = rnd.randint(16, 180)
        t = {"id": f"demo{i}", "name": f"Torneo semanal #{i + 1}", "players": n,
             "date": str(today - timedelta(days=rnd.randint(0, DAYS - 1)))}
        tournaments.append(t)
        for p in range(n):
            k = rnd.choices(range(len(decks)), weights)[0]
            edge = (k % 4 - 1.5) * 0.02
            rounds = rnd.randint(4, 8)
            w = sum(rnd.random() < 0.5 + edge for _ in range(rounds))
            rows.append({"deck_id": slug(decks[k]), "deck_name": decks[k], "wins": w,
                         "losses": rounds - w, "ties": 0, "placing": p + 1,
                         "player": f"Jugador{rnd.randint(100, 999)}", "tournament": t["name"],
                         "tournament_id": t["id"], "players": n, "date": t["date"]})
    return tournaments, rows


def aggregate(rows):
    agg = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "ties": 0,
                               "top8": 0, "results": []})
    for r in rows:
        a = agg[r["deck_id"]]
        a["name"] = r["deck_name"]
        a["count"] += 1
        a["wins"] += r["wins"]
        a["losses"] += r["losses"]
        a["ties"] += r["ties"]
        if r["placing"] and r["placing"] <= 8:
            a["top8"] += 1
            a["results"].append(r)
    total = sum(a["count"] for a in agg.values()) or 1
    decks = []
    for did, a in agg.items():
        games = a["wins"] + a["losses"] + a["ties"]
        if a["count"] < 10 or games == 0:  # muestra demasiado pequeña
            continue
        share = a["count"] / total
        wr = (a["wins"] + 0.5 * a["ties"]) / games
        decks.append({
            "id": did, "slug": slug(did), "name": a["name"], "count": a["count"],
            "share": share, "winrate": wr, "top8": a["top8"],
            "record": f'{a["wins"]}-{a["losses"]}-{a["ties"]}',
            "best": sorted(a["results"], key=lambda r: (r["placing"], -r["players"]))[:10],
        })
    for d in decks:
        d["tier"] = tier(d)
    order = {"S": 0, "A": 1, "B": 2, "C": 3}
    decks.sort(key=lambda d: (order[d["tier"]], -d["share"]))
    return decks


def tier(d):
    # Regla simple y explicable: popularidad + rendimiento
    s, w = d["share"], d["winrate"]
    if s >= 0.05 and w >= 0.51:
        return "S"
    if (s >= 0.03 and w >= 0.50) or w >= 0.53:
        return "A"
    if w >= 0.48:
        return "B"
    return "C"


# ---------------------------------------------------------------- HTML
def slug(s):
    s = s.lower()
    for a, b in zip("áéíóúñü", "aeiounu"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def e(s):
    return html.escape(str(s))


def num(n):
    return f"{n:,}".replace(",", ".")


def pct(x):
    return f"{x * 100:.1f}%".replace(".", ",")


CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#16181d;--mute:#5d6470;--line:#e3e6ee;--acc:#e3350d;
--S:#e3350d;--A:#f08a24;--B:#2a7de1;--C:#8a93a3}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#eef0f4;--mute:#9aa3b2;--line:#262b35}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:inherit}.wrap{max-width:880px;margin:0 auto;padding:0 16px}
header{background:var(--card);border-bottom:1px solid var(--line)}
header .wrap{display:flex;align-items:center;justify-content:space-between;height:56px}
.logo{font-weight:800;text-decoration:none;letter-spacing:-.02em}.logo b{color:var(--acc)}
h1{font-size:1.6rem;line-height:1.2;margin:24px 0 4px}.sub{color:var(--mute);margin:0 0 20px}
.demo{background:#fff3cd;color:#664d03;padding:10px 14px;border-radius:10px;margin:16px 0;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:0 0 20px}
.row{display:grid;grid-template-columns:36px 1fr auto;gap:12px;align-items:center;padding:12px 14px;
border-top:1px solid var(--line);text-decoration:none}.row:first-child{border-top:0}
.row:hover{background:color-mix(in srgb,var(--line) 40%,transparent)}
.tier{width:32px;height:32px;border-radius:8px;display:grid;place-items:center;color:#fff;font-weight:800}
.tS{background:var(--S)}.tA{background:var(--A)}.tB{background:var(--B)}.tC{background:var(--C)}
.name{font-weight:650}.meta{color:var(--mute);font-size:.85rem}
.wr{text-align:right;font-variant-numeric:tabular-nums}.wr b{display:block}
.bar{height:4px;background:var(--line);border-radius:4px;margin-top:6px}.bar i{display:block;height:100%;
background:var(--acc);border-radius:4px}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin:0 0 20px}
@media(min-width:600px){.stats{grid-template-columns:repeat(4,1fr)}}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.stat b{display:block;font-size:1.3rem}.stat span{color:var(--mute);font-size:.8rem}
table{width:100%;border-collapse:collapse;font-size:.9rem}td,th{padding:10px 14px;text-align:left;
border-top:1px solid var(--line)}th{color:var(--mute);font-weight:600;border-top:0}
.ad{min-height:100px;margin:0 0 20px}.aff{padding:14px}.aff a{font-weight:650;color:var(--acc)}
footer{color:var(--mute);font-size:.8rem;padding:24px 0 40px}
"""


def page(title, desc, path, body, demo):
    ads = (f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={e(ADSENSE)}" '
           f'crossorigin="anonymous"></script>') if ADSENSE else ""
    banner = ('<div class="demo">⚠️ Datos de ejemplo (modo demo). Ejecuta sin <code>--demo</code> '
              'para usar torneos reales.</div>') if demo else ""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<link rel="canonical" href="{SITE_URL}/{path}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:type" content="website">{ads}<style>{CSS}</style></head><body>
<header><div class="wrap"><a class="logo" href="{rel(path)}index.html">Pocket<b>Meta</b></a>
<span class="meta">Meta de TCG Pocket</span></div></header>
<main class="wrap">{banner}{body}</main>
<footer class="wrap">Datos de torneos de <a href="https://play.limitlesstcg.com">Limitless TCG</a>.
Web independiente, sin relación con Nintendo, Creatures, GAME FREAK ni The Pokémon Company.
Pokémon y sus nombres son marcas de sus respectivos propietarios.</footer></body></html>"""


def rel(path):
    return "../" * path.count("/")


def ad_slot():
    if not ADSENSE:
        return ""
    return (f'<div class="ad"><ins class="adsbygoogle" style="display:block" data-ad-client="{e(ADSENSE)}" '
            'data-ad-format="auto" data-full-width-responsive="true"></ins>'
            '<script>(adsbygoogle=window.adsbygoogle||[]).push({});</script></div>')


def affiliate():
    q = urllib.parse.quote("pokemon tcg cartas")
    url = f"https://www.amazon.es/s?k={q}" + (f"&tag={e(AMAZON_TAG)}" if AMAZON_TAG else "")
    return (f'<div class="card aff">¿Te gustan las cartas también en físico? '
            f'<a href="{url}" rel="sponsored nofollow">Mira sobres y colecciones de Pokémon TCG →</a></div>')


def render(decks, tournaments, rows, demo):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "mazos").mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    fecha = now.strftime("%d/%m/%Y")
    top = decks[0]["name"] if decks else "—"

    lines = []
    for d in decks:
        lines.append(
            f'<a class="row" href="mazos/{d["slug"]}.html"><span class="tier t{d["tier"]}">{d["tier"]}</span>'
            f'<span><span class="name">{e(d["name"])}</span><br><span class="meta">{pct(d["share"])} del meta · '
            f'{d["top8"]} top 8</span><span class="bar"><i style="width:{min(100, d["share"] * 500):.0f}%"></i>'
            f'</span></span><span class="wr"><b>{pct(d["winrate"])}</b><span class="meta">victorias</span></span></a>')

    body = f"""<h1>Mejores mazos de Pokémon TCG Pocket</h1>
<p class="sub">Tier list actualizada el {fecha} con {len(tournaments)} torneos y {num(len(rows))} participaciones de los últimos {DAYS} días.</p>
<div class="stats"><div class="stat"><b>{len(tournaments)}</b><span>torneos</span></div>
<div class="stat"><b>{num(len(rows))}</b><span>jugadores</span></div>
<div class="stat"><b>{len(decks)}</b><span>mazos analizados</span></div>
<div class="stat"><b style="font-size:1rem">{e(top)}</b><span>mazo n.º 1</span></div></div>
{ad_slot()}<div class="card">{''.join(lines)}</div>{affiliate()}
<div class="card aff"><b>Cómo calculamos la tier list.</b> S: más del 5 % del meta y más del 51 % de victorias.
A: más del 3 % y por encima del 50 %, o más del 53 % de victorias. B: 48 % o más. C: el resto.
Solo cuentan mazos con al menos 10 apariciones.</div>"""
    (OUT / "index.html").write_text(page(
        f"Tier list Pokémon TCG Pocket ({now.strftime('%B %Y')}) — mejores mazos | {SITE_NAME}",
        f"Los mejores mazos de Pokémon TCG Pocket según {len(tournaments)} torneos recientes. "
        f"Mazo n.º 1: {top}. Actualizado a diario.", "index.html", body, demo), encoding="utf-8")

    for d in decks:
        res = "".join(
            f'<tr><td>{r["placing"]}.º</td><td>{e(r["player"])}</td><td>{e(r["tournament"])}'
            f'<br><span class="meta">{r["players"]} jugadores · {r["date"]}</span></td></tr>' for r in d["best"])
        body = f"""<p><a href="../index.html">← Tier list</a></p>
<h1>{e(d["name"])}: estadísticas y resultados</h1>
<p class="sub">Tier {d["tier"]} · actualizado el {fecha}</p>
<div class="stats"><div class="stat"><b>{pct(d["winrate"])}</b><span>victorias</span></div>
<div class="stat"><b>{pct(d["share"])}</b><span>del meta</span></div>
<div class="stat"><b>{d["count"]}</b><span>apariciones</span></div>
<div class="stat"><b>{d["top8"]}</b><span>top 8</span></div></div>{ad_slot()}
<div class="card"><table><tr><th>Puesto</th><th>Jugador</th><th>Torneo</th></tr>
{res or '<tr><td colspan="3">Sin top 8 en este periodo.</td></tr>'}</table></div>{affiliate()}"""
        (OUT / "mazos" / f'{d["slug"]}.html').write_text(page(
            f'{d["name"]} — mazo de Pokémon TCG Pocket: winrate y resultados | {SITE_NAME}',
            f'{d["name"]} en TCG Pocket: {pct(d["winrate"])} de victorias, {pct(d["share"])} del meta y '
            f'sus mejores resultados recientes.', f'mazos/{d["slug"]}.html', body, demo), encoding="utf-8")

    urls = ["index.html"] + [f'mazos/{d["slug"]}.html' for d in decks]
    sm = "".join(f"<url><loc>{SITE_URL}/{u}</loc><lastmod>{now.date()}</lastmod></url>" for u in urls)
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>',
        encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    if ADSENSE:
        pub = ADSENSE.replace("ca-", "")
        (OUT / "ads.txt").write_text(f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n", encoding="utf-8")


def main():
    demo = "--demo" in sys.argv
    print("Modo demo" if demo else f"Descargando torneos de {GAME} ({DAYS} días)…")
    tournaments, rows = fetch_demo() if demo else fetch_real()
    if not rows:
        sys.exit("No se encontraron datos. Revisa LIMITLESS_GAME o si hace falta LIMITLESS_KEY.")
    decks = aggregate(rows)
    DATA.mkdir(exist_ok=True)
    (DATA / "meta.json").write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(), "demo": demo,
        "tournaments": len(tournaments), "entries": len(rows),
        "decks": [{k: v for k, v in d.items() if k != "best"} for d in decks]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    render(decks, tournaments, rows, demo)
    print(f"OK: {len(tournaments)} torneos, {len(rows)} jugadores, {len(decks)} mazos → {OUT}")


if __name__ == "__main__":
    main()
