"""Asks apim-ops-agent one question through the Agent Builder converse API and saves the full response.
Usage: python3 converse.py <inference_id> "<question>" [out.json]
"""
import base64, json, os, sys, time, urllib.error, urllib.request

IMPL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ES_PW = dict(l.strip().split("=", 1) for l in open(os.path.join(IMPL, "stack.secrets")) if "=" in l)["ELASTIC_PASSWORD"]
AUTH = "Basic " + base64.b64encode(f"elastic:{ES_PW}".encode()).decode()

inference_id, question = sys.argv[1], sys.argv[2]
body = {"agent_id": "apim-ops-agent", "inference_id": inference_id, "input": question}
req = urllib.request.Request("http://localhost:5601/api/agent_builder/converse", data=json.dumps(body).encode(),
                             headers={"Authorization": AUTH, "kbn-xsrf": "true", "Content-Type": "application/json"})
t0 = time.time()
try:
    res = json.load(urllib.request.urlopen(req, timeout=300))
except urllib.error.HTTPError as e:
    sys.exit(f"HTTP {e.code}: {e.read().decode()[:1000]}")
res["_elapsed_s"] = round(time.time() - t0, 1)
if len(sys.argv) > 3:
    json.dump(res, open(sys.argv[3], "w"), indent=1)
for s in res.get("steps", []):
    if s.get("type") == "tool_call":
        print("TOOL", s.get("tool_id"), json.dumps(s.get("params")))
print("KEYS", sorted(res), "elapsed", res["_elapsed_s"], "s")
print(res.get("response", {}).get("message", res))
