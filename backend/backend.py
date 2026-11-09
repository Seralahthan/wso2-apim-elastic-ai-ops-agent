# Mock backend for the four demo APIs. Stdlib only.
# GET/POST any path under /orders, /payments, /inventory, /customers -> JSON.
# Faults are switched at runtime by the traffic generator:
#   POST /__faults {"payments": {"error_rate": 0.4}, "inventory": {"delay_ms": 1500}}
import json, random, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BASE_DELAY = {"orders": 40, "payments": 90, "inventory": 25, "customers": 30}
faults = {}  # service -> {"error_rate": float, "delay_ms": int}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        if self.path.startswith("/__faults"):
            if self.command == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                faults.clear()
                faults.update(json.loads(self.rfile.read(n) or b"{}"))
            return self._send(200, faults)
        svc = self.path.strip("/").split("/")[0].split("?")[0]
        if svc not in BASE_DELAY:
            return self._send(404, {"error": "unknown service"})
        f = faults.get(svc, {})
        # jitter: lognormal-ish spread around the base latency
        time.sleep((BASE_DELAY[svc] * random.lognormvariate(0, 0.5) + f.get("delay_ms", 0)) / 1000)
        if random.random() < f.get("error_rate", 0.0):
            return self._send(random.choice([500, 502, 503]), {"error": f"{svc} backend failure"})
        if random.random() < 0.02:  # background noise: occasional client errors
            return self._send(random.choice([400, 404]), {"error": "bad request"})
        return self._send(200, {"service": svc, "path": self.path, "ok": True})

    do_GET = do_POST = do_PUT = do_DELETE = _handle

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
