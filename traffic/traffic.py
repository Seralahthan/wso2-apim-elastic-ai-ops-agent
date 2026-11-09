"""Live traffic through the APIM gateway with a scripted incident schedule (stdlib only).
Every event in Elastic comes from a real gateway call; nothing is back-dated.
Ground truth (incident start/end, wall clock UTC) goes to traffic/incidents.jsonl.

python3 traffic.py                  # 90 min run
python3 traffic.py --minutes 2 --compress 45   # smoke: whole schedule squeezed into 2 min
"""
import argparse, base64, json, os, random, ssl, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

GW, KM, BACKEND = "https://localhost:8243", "https://localhost:9443", "http://localhost:18080"  # mock backend fault switch (host port from docker-compose.yml)
PW = os.environ.get("DEMO_USER_PASSWORD", "Demo@user123")
CTX = ssl._create_unverified_context()
HERE = os.path.dirname(os.path.abspath(__file__))

ROUTES = {
    "Orders": [("GET", "/orders/1.0.0/orders", 5), ("POST", "/orders/1.0.0/orders", 2), ("GET", "/orders/1.0.0/orders/{n}", 4)],
    "Payments": [("POST", "/payments/1.0.0/charges", 3), ("GET", "/payments/1.0.0/charges/{n}", 2), ("POST", "/payments/1.0.0/refunds", 1)],
    "Inventory": [("GET", "/inventory/1.0.0/items", 4), ("GET", "/inventory/1.0.0/items/{n}", 5), ("PUT", "/inventory/1.0.0/items/{n}", 1)],
    "Customers": [("GET", "/customers/1.0.0/customers/{n}", 4), ("POST", "/customers/1.0.0/customers", 1)],
}
RATE = {"MobileApp": 3.0, "WebPortal": 2.0, "PartnerAcme": 1.5, "BatchReporting": 0.7, "PartnerGlobex": 0.3}  # req/s
UA = {"MobileApp": "OrdersApp/5.2 (iOS 18)", "WebPortal": "Mozilla/5.0 (Macintosh)", "PartnerAcme": "acme-sync/2.1",
      "BatchReporting": "python-requests/2.32", "PartnerGlobex": "globex-integrator/0.9"}
# (start_min, end_min, name, detail) on the 90-minute timeline
SCHEDULE = [
    (20, 30, "payments_5xx", {"payments": {"error_rate": 0.35}}),
    (35, 40, "auth_storm", {}),
    (45, 55, "inventory_latency", {"inventory": {"delay_ms": 1500}}),
    (60, 75, "globex_throttled", {}),
]


def post(url, data, headers):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), context=CTX))


def token(app):
    a = APPS[app]
    basic = base64.b64encode(f"{a['consumerKey']}:{a['consumerSecret']}".encode()).decode()
    return post(KM + "/oauth2/token", urllib.parse.urlencode(
        {"grant_type": "password", "username": a["owner"], "password": PW}).encode(), {"Authorization": "Basic " + basic})["access_token"]


def hit(app, tok):
    api = random.choice(APPS[app]["apis"])
    method, path, _ = random.choices(ROUTES[api], weights=[w for *_, w in ROUTES[api]])[0]
    req = urllib.request.Request(GW + path.replace("{n}", str(random.randint(1, 5000))), method=method,
                                 data=b'{"demo":true}' if method in ("POST", "PUT") else None,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": UA.get(app, "curl/8")})
    try:
        urllib.request.urlopen(req, context=CTX, timeout=30).read()
    except Exception:
        pass  # errors are the point; the gateway records them


def active(name, minute):
    return any(s <= minute < e and n == name for s, e, n, _ in SCHEDULE)


def main():
    global APPS
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=90)
    ap.add_argument("--compress", type=float, default=1.0, help="schedule minutes per real minute")
    args = ap.parse_args()
    APPS = json.load(open(os.path.join(HERE, "apps.json")))
    toks, t0 = {a: token(a) for a in APPS}, time.monotonic()
    pool, log = ThreadPoolExecutor(max_workers=64), open(os.path.join(HERE, "incidents.jsonl"), "a")
    state, refreshed = set(), time.monotonic()
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.write(json.dumps({"event": "run_start", "at": now(), "minutes": args.minutes, "compress": args.compress}) + "\n")
    tick = 0.1
    while (elapsed := time.monotonic() - t0) < args.minutes * 60:
        minute = elapsed / 60 * args.compress
        # incident edges -> backend faults + ground-truth log
        cur = {n for s, e, n, _ in SCHEDULE if s <= minute < e}
        for n in cur ^ state:
            edge = "start" if n in cur else "end"
            log.write(json.dumps({"event": f"{n}_{edge}", "at": now()}) + "\n"); log.flush()
            print(now(), n, edge, flush=True)
        if cur != state:
            faults = {}
            for s, e, n, d in SCHEDULE:
                if n in cur:
                    faults.update(d)
            urllib.request.urlopen(urllib.request.Request(BACKEND + "/__faults", data=json.dumps(faults).encode(), method="POST"))
            state = cur
        if time.monotonic() - refreshed > 50 * 60:  # tokens live 60 min
            toks, refreshed = {a: token(a) for a in APPS}, time.monotonic()
        for app, rps in RATE.items():
            if app == "PartnerGlobex" and active("globex_throttled", minute):
                rps = 1.5  # 90/min against a 30/min tier
            if app == "BatchReporting" and int(minute) % 15 < 3:
                rps = 4.0  # batch job burst every 15 min
            if random.random() < rps * tick:
                pool.submit(hit, app, toks[app])
        bad_rps = 2.0 if active("auth_storm", minute) else 0.05
        if random.random() < bad_rps * tick:
            pool.submit(hit, random.choice(list(APPS)), "invalid-" + str(random.randint(1, 99)))
        time.sleep(tick)
    urllib.request.urlopen(urllib.request.Request(BACKEND + "/__faults", data=b"{}", method="POST"))
    log.write(json.dumps({"event": "run_end", "at": now()}) + "\n")
    pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
