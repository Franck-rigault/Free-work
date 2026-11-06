"""Recupere les annonces (France Travail + Adzuna) et ecrit docs/offers.json.
Lance chaque jour par GitHub Actions, ou a la main : python fetch_offers.py
"""
import json, os, sys, datetime
import server, labonneboite

JOBS = ["animatrice", "animateur periscolaire", "ATSEM", "AESH", "auxiliaire de vie", "aide a domicile",
        "agent de service", "agent d'entretien", "employe polyvalent", "vendeuse", "caissiere",
        "equipier restauration", "serveuse", "preparateur de commandes", "agent de production", "creche"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "offers.json")

runs = [server.search_all("", 31), server.search_all(",".join(JOBS), 31)]
seen, offers, errors = set(), [], []
for r in runs:
    for e in r["errors"]:
        if e not in errors:
            errors.append(e)
    for o in r["offers"]:
        if o["id"] not in seen:
            seen.add(o["id"])
            offers.append(o)
offers.sort(key=lambda o: o["date"], reverse=True)

errors = [e for e in errors if "cles absentes" not in e]
companies = []
ft = server.get_cfg().get("france_travail", {})
if ft.get("client_id") and ft.get("client_secret"):
    companies, lbb_errors = labonneboite.search(ft)
    errors += lbb_errors

if not offers and not companies:
    print("Rien recupere :", errors)
    sys.exit(1)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "errors": errors,
               "offers": offers, "companies": companies}, f, ensure_ascii=False)
print(len(offers), "annonces,", len(companies), "entreprises | avertissements :", errors)
