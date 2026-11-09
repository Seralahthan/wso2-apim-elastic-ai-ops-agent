"""Creates or updates the ES|QL tools (tools/*.json) and the agent (agent.json) in Kibana Agent Builder.
Usage: python3 apply.py      (Kibana at localhost:5601, user elastic; password from ../stack.secrets)
"""
import base64, glob, json, os, sys, urllib.error, urllib.request

KIBANA = "http://localhost:5601"
HERE = os.path.dirname(os.path.abspath(__file__))
ES_PW = dict(l.strip().split("=", 1) for l in open(os.path.join(HERE, "..", "stack.secrets")) if "=" in l)["ELASTIC_PASSWORD"]
AUTH = "Basic " + base64.b64encode(f"elastic:{ES_PW}".encode()).decode()


def call(method, path, body=None):
    req = urllib.request.Request(KIBANA + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": AUTH, "kbn-xsrf": "true", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def upsert(kind, obj):
    code, _ = call("GET", f"/api/agent_builder/{kind}/{obj['id']}")
    if code == 200:  # update: id (and type for tools) are immutable, so they go in the path only
        code, res = call("PUT", f"/api/agent_builder/{kind}/{obj['id']}", {k: v for k, v in obj.items() if k not in ("id", "type")})
    else:
        code, res = call("POST", f"/api/agent_builder/{kind}", obj)
    print(kind, obj["id"], code, "" if code < 300 else res)
    return code < 300


ok = all(upsert("tools", json.load(open(p))) for p in sorted(glob.glob(os.path.join(HERE, "tools", "*.json"))))
agent = os.path.join(HERE, "agent.json")
if os.path.exists(agent):
    ok = upsert("agents", json.load(open(agent))) and ok
sys.exit(0 if ok else 1)
