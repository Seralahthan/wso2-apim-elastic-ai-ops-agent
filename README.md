# Ask Your API Analytics: an AI ops agent over WSO2 API Manager analytics

Companion repo for the blog post **"Ask Your API Analytics: An AI Agent, Governed by WSO2"** (link to be added).

It builds an AI agent that answers operational questions about your APIs ("When did the Payments errors start and stop?") straight from the gateway's analytics, and exports a trace of every answer to WSO2 Agent Manager.

```
WSO2 API Manager 4.7.0 -> apim_metrics.log -> Filebeat -> Logstash -> Elasticsearch
                                                                          ^
                                    Kibana Agent Builder (agent + 7 ES|QL tools)
                                                                          |
                                         OTLP traces -> WSO2 Agent Manager (external agent)
```

## What's in here

```
docker-compose.yml        API Manager 4.7.0, Elastic Stack 9.5.6, mock backend
apim/deployment.toml      API Manager config with ELK analytics enabled
elk/                      Filebeat 9 (filestream), Logstash pipeline, index template, kibana.yml (trace export)
elk/patches/              optional Kibana 9.5.6 patch for elastic/kibana#299041
backend/backend.py        mock backend for the four APIs, faults switchable at runtime
traffic/setup_apim.py     creates users, a low subscription tier, 4 APIs and 5 apps through the REST APIs
traffic/traffic.py        real gateway traffic with four scripted incidents
elastic-agent/tools/      the seven ES|QL tools
elastic-agent/agent.json  the agent: instructions + tool list (no model named)
elastic-agent/*.py        create the inference endpoints, tools and agent; ask a question; turn on tracing
elastic-agent/testset.txt the ten questions used to check the agent
```

## Prerequisites

- Docker with around 8 GB of memory for this compose file (it used about 6.3 GB in testing; Agent Manager needs its own k3d cluster on top)
- Python 3.10+ (scripts use the standard library only)
- An Anthropic API key, or edit `elastic-agent/inference.py` for another Elastic inference provider
- For the tracing part: WSO2 Agent Manager 2026.11.0, which needs a WSO2 subscription to pull from `registry.wso2.com`, plus `k3d`, `kubectl`, `helm`, `gh` and bash 4.4 or newer (on macOS: `brew install bash`)
- Free host ports: 9200, 5601, 9443, 8243, 8280 and 18080 for this compose file; 6550, 8080, 8443, 10082, 11080, 11082, 11085, 19080 and 19443 for Agent Manager

## Step 1: Start the stack

```bash
cp stack.secrets.example stack.secrets
```

Fill in `stack.secrets` (leave `AMP_AGENT_API_KEY` empty for now), then:

```bash
docker compose --env-file stack.secrets up -d
```

Elasticsearch starts with a 30-day trial license, because Agent Builder needs Enterprise features on a self-managed cluster. The `setup` container sets the `kibana_system` password and installs the index template.

## Step 2: Create the APIs and generate traffic

Wait until API Manager is up (`https://localhost:9443/publisher` loads, about two minutes), then:

```bash
python3 traffic/setup_apim.py
```

A two-minute smoke run with the whole incident schedule compressed:

```bash
python3 traffic/traffic.py --minutes 2 --compress 45
```

The full 90-minute run (keep the laptop awake; on Linux use `systemd-inhibit` instead of `caffeinate`):

```bash
caffeinate -i python3 traffic/traffic.py
```

The real incident start and end times are written to `traffic/incidents.jsonl`, so you know the right answers before you ask the agent. Check that events are arriving:

```bash
curl -s -u elastic:<ELASTIC_PASSWORD> "http://localhost:9200/_cat/indices/apim_event*?v"
```

## Step 3: Create the model endpoint, tools and agent

```bash
python3 elastic-agent/inference.py
python3 elastic-agent/apply.py
```

Make Sonnet 5.5 the default model for Agent Builder:

```bash
curl -u elastic:<ELASTIC_PASSWORD> -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: kibana' -H 'Content-Type: application/json' \
  -X POST http://localhost:5601/internal/kibana/settings -d '{"changes":{"genAiSettings:defaultAIConnector":"anthropic-sonnet-5-5"}}'
```

Optionally, show times in UTC so they line up with `traffic/incidents.jsonl` (Kibana otherwise uses the browser's timezone):

```bash
curl -u elastic:<ELASTIC_PASSWORD> -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: kibana' -H 'Content-Type: application/json' \
  -X POST http://localhost:5601/internal/kibana/settings -d '{"changes":{"dateFormat:tz":"UTC"}}'
```

Open Kibana at http://localhost:5601, go to Agent Chat, pick "APIM Ops Agent" and ask one of the questions in `elastic-agent/testset.txt`. Or from the terminal:

```bash
python3 elastic-agent/converse.py anthropic-sonnet-5-5 "When did the Payments 5xx errors start and stop?"
```

## Step 4: Install WSO2 Agent Manager 2026.11.0

This can run while the traffic generator is running. Download the release assets and verify them:

```bash
mkdir -p ~/amp-install && cd ~/amp-install
gh release download amp/v2026.11.0 -R wso2/agent-manager
shasum -a 256 -c SHA256SUMS
```

Refresh Helm's repo cache first. The installer installs charts from Helm repos, and a stale cache for any repo already configured on your machine makes it fail with `no cached repo found`:

```bash
helm repo update
```

Run the installer with bash 4.4 or newer. macOS's built-in `/bin/bash` is 3.2 and stops at step 7 with `unbound variable`; after `brew install bash`, make sure `bash --version` shows 5.x:

```bash
AMP_REGISTRY_USERNAME=<your WSO2 email> bash k3d-install-subscription.sh --project wso2-agent-manager --version 2026.11.0
```

It prompts for your registry password (hidden), takes 15 to 20 minutes, and creates a k3d cluster called `amp-local`. If a step fails, fix the cause and run it again; it resumes where it stopped.

When it finishes, open the Console at http://console.amp.localhost:8080 and sign in as `admin`. The installer prints the admin password, so don't keep that terminal output. To read it again:

```bash
kubectl get secret amp-admin-credentials -n amp-thunder -o jsonpath='{.data.password}' | base64 -d
```

To remove Agent Manager later: `k3d cluster delete amp-local`.

## Step 5: Send the traces to WSO2 Agent Manager

1. In the Agent Manager Console: **Add Agent → Externally-Hosted Agent**, give it a name, and copy the API key from the Setup Agent panel (it is shown only once).
2. Put the key in `stack.secrets` as `AMP_AGENT_API_KEY`.
3. Check the exporter URL in `elk/kibana.yml` and the `extra_hosts` entry in `docker-compose.yml`. They point at Agent Manager's default local gateway (`default-default.gateway.localhost:19080`); change them if yours differs.
4. Recreate Kibana and turn on trace export:

```bash
docker compose --env-file stack.secrets up -d kibana
python3 elastic-agent/tracing_settings.py on
```

`tracing_settings.py on` turns on all seven `agentBuilder:tracing:*` settings, including prompts, model responses and tool results. That's right for a demo. For real data, decide per setting what may be copied into Agent Manager.

## Read-only access for people who ask

The role used in the post: people can chat with the agent but can't change it.

```bash
curl -u elastic:<ELASTIC_PASSWORD> -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
  -X PUT http://localhost:5601/api/security/role/apim_ops_reader -d '{
  "elasticsearch": {
    "cluster": ["monitor_inference"],
    "indices": [{ "names": ["apim_event_*"], "privileges": ["read", "view_index_metadata"] }]
  },
  "kibana": [{
    "base": [],
    "feature": { "agentBuilder": ["read"], "searchInferenceEndpoints": ["read"], "actions": ["read"] },
    "spaces": ["default"]
  }]
}'
```

## Optional: conversation titles on Kibana 9.5.6

With Claude Sonnet 5.5 or Opus 5.5, Kibana 9.5.6 can't generate conversation titles ([elastic/kibana#299041](https://github.com/elastic/kibana/issues/299041)). Answers are not affected. To patch it locally, copy the file out of the running container and apply the patch:

```bash
docker compose --env-file stack.secrets cp kibana:/usr/share/kibana/node_modules/@kbn/agent-builder-plugin/server/services/execution/utils/generate_title.js elk/patches/kibana-9.5.6/generate_title.js
patch elk/patches/kibana-9.5.6/generate_title.js < elk/patches/kibana-9.5.6/generate_title.patch
```

Then uncomment the `generate_title.js` mount in `docker-compose.yml` and recreate Kibana. Remove the mount after upgrading Kibana. Or simply use Claude Sonnet 5, which still accepts forced tool choice.

## Notes

- API Manager runs with the stock `admin`/`admin` account and demo users share `DEMO_USER_PASSWORD` (default in `setup_apim.py`). Local demo only.
- Nothing here is tuned for production: single-node Elasticsearch, HTTP inside the compose network, self-signed API Manager certificate.

## License

Apache License 2.0. Kibana, Elasticsearch and WSO2 products are under their own licenses; this repo contains configuration and scripts only.
