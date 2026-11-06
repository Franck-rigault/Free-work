"""La Bonne Boite (France Travail) : entreprises proches qui embauchent souvent."""
import time, urllib.error, urllib.parse
import server

# codes ROME (metiers accessibles avec un bac animation / sans qualification, peu physiques)
ROMES = {
    "G1202": "Animation culturelle et ludique", "G1203": "Animation enfants / ados",
    "K1303": "Assistance aupres d'enfants (ATSEM, creche)", "K1302": "Assistance aupres d'adultes",
    "K1304": "Services domestiques", "D1106": "Vente", "D1505": "Caisse",
    "D1507": "Mise en rayon libre-service", "K2204": "Nettoyage de locaux", "G1803": "Service en restauration",
}
LAT, LON, DIST = 47.3586, 1.7437, 30
_tok = {"v": None, "exp": 0}


def token(cfg):
    if _tok["v"] and time.time() < _tok["exp"] - 30:
        return _tok["v"]
    js = None
    for scope in ("application_%s api_labonneboitev2 search" % cfg["client_id"],
                  "application_%s api_labonneboitev2" % cfg["client_id"]):
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"], "scope": scope}).encode()
        try:
            _, js = server.http("https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire",
                                data, {"Content-Type": "application/x-www-form-urlencoded"})
            break
        except urllib.error.HTTPError as e:
            if scope.endswith("search") and e.code == 400:
                continue
            raise
    _tok["v"], _tok["exp"] = js["access_token"], time.time() + int(js.get("expires_in", 1500))
    _tok["scope"] = js.get("scope", "?")
    return _tok["v"]


def dist_of(it):
    d = find(it, ("distance", "distance_km"))
    if isinstance(d, (int, float)):
        return d if d < 500 else d / 1000.0   # metres -> km
    lat = find(it, ("lat", "latitude"))
    lon = find(it, ("lon", "lng", "longitude"))
    try:
        return round(server.km(LAT, LON, float(lat), float(lon)), 1)
    except Exception:
        return None


def find(obj, names):
    """Cherche recursivement la premiere valeur non vide dont la cle est dans names."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in names and v not in (None, "", [], {}):
                return v
        for v in obj.values():
            r = find(v, names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find(v, names)
            if r is not None:
                return r
    return None


def search(cfg):
    errors, found = [], {}
    try:
        tk = token(cfg)
    except urllib.error.HTTPError as e:
        return [], ["La Bonne Boite : refus du token (HTTP %s) %s" % (e.code, e.read().decode("utf-8", "replace")[:200])]
    for code, label in ROMES.items():
        params = {"rome": code, "latitude": LAT, "longitude": LON, "distance": DIST, "page_size": 50}
        url = "https://api.francetravail.io/partenaire/labonneboite/v2/recherche?" + urllib.parse.urlencode(params)
        time.sleep(1.5)  # evite l'erreur 429 (trop de requetes)
        try:
            for attempt in range(4):
                try:
                    _, js = server.http(url, headers={"Authorization": "Bearer " + tk, "Accept": "application/json"})
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < 3:
                        time.sleep(4 * (attempt + 1))
                        continue
                    raise
        except urllib.error.HTTPError as e:
            msg = "La Bonne Boite : HTTP %s sur %s | %s | scopes du token : %s" % (
                e.code, url.split("?")[0], e.read().decode("utf-8", "replace")[:200], _tok.get("scope"))
            if msg not in errors:
                errors.append(msg)
            if e.code in (401, 403, 422):
                break  # inutile de continuer, meme refus pour tous les metiers
            continue
        except Exception as e:
            errors.append("La Bonne Boite : %s" % e)
            continue
        items = js.get("items") or js.get("companies") or js.get("results") or []
        for it in items:
            siret = str(it.get("siret") or it.get("id") or it.get("name"))
            pot = it.get("hiring_potential") or it.get("score") or 0
            c = found.setdefault(siret, {
                "id": "lbb-" + siret,
                "name": it.get("name") or it.get("company_name") or it.get("enseigne") or "",
                "address": it.get("address") or it.get("city") or "",
                "sector": it.get("naf_text") or it.get("naf_label") or "",
                "size": it.get("headcount_text") or "",
                "distance": dist_of(it),
                "potential": pot, "url": find(it, ("url", "website", "site_web", "link", "lien")) or "", "jobs": [],
            })
            c["potential"] = max(c["potential"] or 0, pot or 0)
            if label not in c["jobs"]:
                c["jobs"].append(label)
    # on retire ce qui depasse nettement le rayon quand la distance est connue
    for k in [k for k, c in found.items() if c["distance"] is not None and c["distance"] > DIST + 3]:
        del found[k]
    mx = max([c["potential"] or 0 for c in found.values()] + [1])
    for c in found.values():
        c["stars"] = max(1, min(5, int((c["potential"] or 0) / mx * 5 + 0.999)))
    out = sorted(found.values(), key=lambda c: (-(c["potential"] or 0), c["distance"] or 0))
    return out, errors
