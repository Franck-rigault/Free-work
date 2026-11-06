"""Chercheur d'annonces - Romorantin-Lanthenay + 30 km.
Serveur local sans dependance (Python 3 seul). Lancer : python server.py
"""
import json, os, threading, time, urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "suivi.json")
PORT = 8765
COMMUNE_INSEE = "41194"          # Romorantin-Lanthenay
VILLE = "Romorantin-Lanthenay"
DISTANCE_KM = 30
lock = threading.Lock()
_ft_token = {"value": None, "exp": 0}


import re, unicodedata


def strip_acc(t):
    return unicodedata.normalize("NFD", t.lower()).encode("ascii", "ignore").decode()


BEGINNER_RE = re.compile(r"debutant|sans experience|sans diplome|sans qualification|aucune experience|formation assuree|formation en interne|nous vous formons")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def http(url, data=None, headers=None, timeout=25):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
        return r.status, (json.loads(body) if body.strip() else {})


# ---------- France Travail ----------
def ft_token(cfg):
    if _ft_token["value"] and time.time() < _ft_token["exp"] - 30:
        return _ft_token["value"]
    js = None
    for scope in ("api_offresdemploiv2 o2dsoffre", "api_offresdemploiv2"):
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scope": scope,
        }).encode()
        try:
            _, js = http("https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire",
                         data, {"Content-Type": "application/x-www-form-urlencoded"})
            break
        except urllib.error.HTTPError as e:
            if scope.endswith("o2dsoffre") and e.code == 400:
                continue
            raise
    _ft_token["value"] = js["access_token"]
    _ft_token["exp"] = time.time() + int(js.get("expires_in", 1500))
    return _ft_token["value"]


def search_ft(cfg, terms, days):
    token = ft_token(cfg)
    res = []
    for t in (terms or [""]):
        params = {"commune": COMMUNE_INSEE, "distance": DISTANCE_KM, "range": "0-99" if terms else "0-149", "sort": 1}
        if t:
            params["motsCles"] = t
        if days:
            params["publieeDepuis"] = days  # valeurs acceptees : 1,3,7,14,31
        url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search?" + urllib.parse.urlencode(params)
        try:
            _, js = http(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        except urllib.error.HTTPError as e:
            if e.code in (204, 206):
                continue
            raise
        res += js.get("resultats", [])
    out = []
    for o in res:
        sal = (o.get("salaire") or {}).get("libelle") or ""
        out.append({
            "id": "ft-" + o["id"],
            "source": "France Travail",
            "title": o.get("intitule", ""),
            "company": (o.get("entreprise") or {}).get("nom", ""),
            "location": (o.get("lieuTravail") or {}).get("libelle", ""),
            "contract": o.get("typeContratLibelle") or o.get("typeContrat", ""),
            "salary": sal,
            "date": (o.get("dateCreation") or "")[:10],
            "url": (o.get("origineOffre") or {}).get("urlOrigine")
                   or "https://candidat.francetravail.fr/offres/recherche/detail/" + o["id"],
            "description": (o.get("description") or "")[:3000],
            "lat": (o.get("lieuTravail") or {}).get("latitude"),
            "lon": (o.get("lieuTravail") or {}).get("longitude"),
            "facts": [x for x in (o.get("dureeTravailLibelle"), o.get("experienceLibelle"), o.get("qualificationLibelle"), o.get("complementExercice")) if x],
            "beginner": o.get("experienceExige") == "D" or "debutant" in strip_acc(o.get("experienceLibelle") or ""),
        })
    return out


# ---------- Adzuna ----------
def search_adzuna(cfg, terms, days):
    out = []
    for page in (1, 2):
        params = {"app_id": cfg["app_id"], "app_key": cfg["app_key"], "where": VILLE,
                  "distance": DISTANCE_KM, "results_per_page": 50, "sort_by": "date",
                  "content-type": "application/json"}
        if terms:
            params["what_or"] = " ".join(terms)
        if days:
            params["max_days_old"] = days
        url = "https://api.adzuna.com/v1/api/jobs/fr/search/%d?%s" % (page, urllib.parse.urlencode(params))
        _, js = http(url)
        res = js.get("results", [])
        for o in res:
            lo, hi = o.get("salary_min"), o.get("salary_max")
            sal = ""
            if lo and hi:
                sal = "%d - %d EUR / an" % (lo, hi)
            elif lo:
                sal = "%d EUR / an" % lo
            ctype = " ".join(x for x in (o.get("contract_type"), o.get("contract_time")) if x)
            ctype = {"permanent": "CDI", "contract": "CDD / mission", "full_time": "Temps plein",
                     "part_time": "Temps partiel"}.get(ctype, ctype)
            out.append({
                "id": "az-" + str(o["id"]),
                "source": "Adzuna",
                "title": (o.get("title") or "").replace("<strong>", "").replace("</strong>", ""),
                "company": (o.get("company") or {}).get("display_name", ""),
                "location": (o.get("location") or {}).get("display_name", ""),
                "contract": ctype,
                "salary": sal,
                "date": (o.get("created") or "")[:10],
                "url": o.get("redirect_url", ""),
                "description": (o.get("description") or "")[:3000],
                "lat": o.get("latitude"),
                "lon": o.get("longitude"),
                "facts": [],
                "beginner": False,
            })
        if len(res) < 50:
            break
    return out

import math

HEAVY_RE = re.compile(r"charges? lourdes?|port de charges?|port de colis|porter des charges|manutention|soulev|\bpalettes?\b|cariste|travaux physiques|efforts? physiques?|bucheron|macon|\bbtp\b|ouvrier agricole")
NOT_HEAVY_RE = re.compile(r"\b(pas|sans|aucun\w*|ni)\s+(de\s+|d')?(port\s+de\s+charges?|manutention|charges? lourdes?|effort\w* physique\w*)|poste assis|travail de bureau")
CHECK_RE = re.compile(r"preparateur|preparatrice|commande|agent de production|operateur|operatrice|conditionn|magasinier|employe polyvalent|mise en rayon|ouvrier|ripeur|livreu|logisti|agent de quai|equipier de production|emballage|agroalimentaire")
HOURS_RE = re.compile(r"(\d{1,2})\s?h(?:eures?)?\s?(?:/|par|hebdo)\s?(?:semaine|sem)|(\d{1,2})\s?h\s?(?:hebdo|par semaine)|temps (?:plein|partiel|complet)|horaires? (?:de jour|de journee|coupes?|variables?|decales?|de nuit|en equipe)|week-?end|nuit|mercredi|periscolaire|temps scolaire", re.I)
MISSION_KW = re.compile(r"mission|vous (?:serez|aurez|assurez|realisez|participez|accompagnez|encadrez)|en charge|consiste|poste|accueil|anim|encadr|accompagn|prepar|entretien|vente|service|vos taches", re.I)


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def summarize(o):
    txt = re.sub(r"\s+", " ", o["description"]).strip()
    facts = []
    for f in [o["contract"], o["salary"]] + list(o.get("facts", [])):
        if f and f not in facts:
            facts.append(f)
    seen = set()
    for m in HOURS_RE.finditer(txt):
        v = m.group(0).strip().lower()
        if v not in seen and len(seen) < 3:
            seen.add(v)
            facts.append(v)
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\s[-•*]\s", txt) if len(x.strip()) > 25]
    picks = [x for x in sents if MISSION_KW.search(x)][:2] or sents[:2]
    body = " ".join(p if len(p) <= 170 else p[:167].rsplit(" ", 1)[0] + "..." for p in picks)
    return (" | ".join(facts) + ("\n" if facts and body else "") + body).strip()


def analyze(o):
    t = strip_acc(o["title"] + " " + o["description"])
    if NOT_HEAVY_RE.search(t):
        o["load"] = "light"
    elif HEAVY_RE.search(t):
        o["load"] = "heavy"
    elif CHECK_RE.search(strip_acc(o["title"])):
        o["load"] = "check"      # metier souvent physique, rien de precise
    else:
        o["load"] = "unknown"
    o["heavy"] = o["load"] == "heavy"
    if o.get("lat") is not None and o.get("lon") is not None:
        try:
            o["km"] = round(km(47.3586, 1.7437, float(o["lat"]), float(o["lon"])))
        except Exception:
            pass
    o["summary"] = summarize(o)
    o["description"] = o["description"][:700]
    o.pop("facts", None)
    o.pop("lat", None)
    o.pop("lon", None)


def search_all(q, days):
    terms = [t.strip() for t in q.split(",") if t.strip()][:15]
    cfg = load_json(CONFIG_PATH, {})
    e = os.environ
    if e.get("FT_CLIENT_ID"):
        cfg["france_travail"] = {"client_id": e["FT_CLIENT_ID"], "client_secret": e.get("FT_CLIENT_SECRET", "")}
    if e.get("ADZUNA_APP_ID"):
        cfg["adzuna"] = {"app_id": e["ADZUNA_APP_ID"], "app_key": e.get("ADZUNA_APP_KEY", "")}
    offers, errors, jobs = [], [], []
    ft, az = cfg.get("france_travail", {}), cfg.get("adzuna", {})
    if ft.get("client_id") and ft.get("client_secret"):
        jobs.append(("France Travail", lambda: search_ft(ft, terms, days)))
    else:
        errors.append("France Travail : cles absentes dans config.json")
    if az.get("app_id") and az.get("app_key"):
        jobs.append(("Adzuna", lambda: search_adzuna(az, terms, days)))
    else:
        errors.append("Adzuna : cles absentes dans config.json")
    results = {}

    def run(name, fn):
        try:
            results[name] = fn()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                detail = ""
            results[name] = "%s : erreur HTTP %s sur %s | %s" % (name, e.code, e.url.split("?")[0], detail)
        except Exception as e:
            results[name] = "%s : %s" % (name, e)

    threads = [threading.Thread(target=run, args=j) for j in jobs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    for v in results.values():
        if isinstance(v, str):
            errors.append(v)
        else:
            offers += v
    for o in offers:
        if not o.get("beginner") and BEGINNER_RE.search(strip_acc(o["title"] + " " + o["description"])):
            o["beginner"] = True
    for o in offers:
        analyze(o)
    # dedoublonnage inter-sources (meme titre + meme entreprise)
    seen, uniq = set(), []
    for o in sorted(offers, key=lambda x: x["date"], reverse=True):
        k = (o["title"].lower().strip(), o["company"].lower().strip())
        if k in seen:
            continue
        seen.add(k)
        uniq.append(o)
    return {"offers": uniq, "errors": errors, "configured": bool(jobs)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "docs", "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/search":
            q = (qs.get("q", [""])[0]).strip()
            days = qs.get("days", [""])[0]
            return self.send(200, search_all(q, int(days) if days.isdigit() else 0))
        if u.path == "/api/state":
            with lock:
                return self.send(200, load_json(STATE_PATH, {}))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/state":
            n = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(n))
            except Exception:
                return self.send(400, {"error": "bad json"})
            with lock:
                with open(STATE_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"france_travail": {"client_id": "", "client_secret": ""},
                       "adzuna": {"app_id": "", "app_key": ""}}, f, indent=2)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Appli prete : http://localhost:%d  (Ctrl+C pour arreter)" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
