"""Creates the Anthropic chat_completion inference endpoints Agent Builder reasons with.
Reads ELASTIC_PASSWORD and ANTHROPIC_API_KEY from ../stack.secrets; prints neither.
Usage: python3 inference.py
"""
import base64, json, os, urllib.error, urllib.request

IMPL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = dict(l.strip().split("=", 1) for l in open(os.path.join(IMPL, "stack.secrets")) if "=" in l)
ES_PW, KEY = SECRETS["ELASTIC_PASSWORD"], SECRETS["ANTHROPIC_API_KEY"]
AUTH = "Basic " + base64.b64encode(f"elastic:{ES_PW}".encode()).decode()
ENDPOINTS = {"anthropic-haiku-5-5": "claude-haiku-5-5", "anthropic-sonnet-5-5": "claude-sonnet-5-5"}

for eid, model in ENDPOINTS.items():
    body = {"service": "anthropic", "service_settings": {"api_key": KEY, "model_id": model},
            "task_settings": {"max_tokens": 4096}}
    req = urllib.request.Request(f"http://localhost:9200/_inference/chat_completion/{eid}", method="PUT",
                                 data=json.dumps(body).encode(), headers={"Authorization": AUTH, "Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req))
        print(eid, "OK", r.get("service_settings"), r.get("task_settings"))
    except urllib.error.HTTPError as e:
        print(eid, e.code, e.read().decode()[:600])
