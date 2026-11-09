"""Turns on full Agent Builder trace export in the default Kibana space and prints the result.

python3 tracing_settings.py [on|off]   (reads ../stack.secrets; never prints secrets)
The settings are Kibana Advanced Settings (Stack Management -> Advanced Settings), one set per space.
"""
import base64, json, sys, urllib.request
from pathlib import Path

KEYS = ["enabled", "includeUserPrompts", "includeLlmResponses", "includeToolDetails",
        "includeSystemPrompt", "includeRealNames", "includeRealIds"]
secrets = dict(l.split("=", 1) for l in Path(__file__).resolve().parent.parent.joinpath("stack.secrets").read_text().splitlines() if "=" in l)
auth = "Basic " + base64.b64encode(f"elastic:{secrets['ELASTIC_PASSWORD']}".encode()).decode()
value = (sys.argv[1] if len(sys.argv) > 1 else "on") == "on"

def call(method, body=None):
    req = urllib.request.Request("http://localhost:5601/internal/kibana/settings", method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": auth, "kbn-xsrf": "true", "x-elastic-internal-origin": "kibana",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

call("POST", {"changes": {f"agentBuilder:tracing:{k}": value for k in KEYS}})
current = call("GET")["settings"]
for k in KEYS:
    print(f"agentBuilder:tracing:{k} =", current.get(f"agentBuilder:tracing:{k}", {}).get("userValue"))
