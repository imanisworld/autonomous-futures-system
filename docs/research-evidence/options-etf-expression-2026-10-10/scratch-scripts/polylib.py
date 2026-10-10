import json, os, sys, time, urllib.request, urllib.parse, hashlib
KEY = open(os.path.join(os.environ["TMPDIR"], "pk")).read().strip()
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache2"); os.makedirs(CACHE, exist_ok=True)
def get(path, params=None):
    params = dict(params or {}); q = urllib.parse.urlencode(params)
    ck = os.path.join(CACHE, hashlib.sha1((path + "?" + q).encode()).hexdigest() + ".json")
    if os.path.exists(ck):
        try: return json.load(open(ck))
        except ValueError: pass
    params["apiKey"] = KEY
    url = "https://api.polygon.io" + path + "?" + urllib.parse.urlencode(params); err = ""
    for i in range(6):
        try:
            with urllib.request.urlopen(url, timeout=60) as r: d = json.load(r)
            tmp = ck + f".{os.getpid()}.tmp"; json.dump(d, open(tmp, "w")); os.replace(tmp, ck); return d
        except Exception as e:
            err = str(e).replace(KEY, "***"); time.sleep(2 * (i + 1))
    print("FAIL", path, err, file=sys.stderr); return None
def minutes(tkr, d):
    r = get(f"/v2/aggs/ticker/{tkr}/range/1/minute/{d.isoformat()}/{d.isoformat()}", {"adjusted": "true", "sort": "asc", "limit": 50000})
    return (r or {}).get("results") or []
def px_at(bars, t_ms, side):
    if side == "entry":
        for b in bars:
            if b["t"] >= t_ms: return (b.get("vw") or b["o"]), b["t"]
    else:
        prev = None
        for b in bars:
            if b["t"] <= t_ms: prev = b
        if prev: return (prev.get("vw") or prev["c"]), prev["t"]
    return None, None
