"""Creates the demo estate on APIM 4.7.0 through its REST APIs (stdlib only):
users (SCIM2), a low subscription tier, 4 APIs (deployed + published), 5 applications with
subscriptions and keys. Writes traffic/apps.json (client id/secret per app) for traffic.py.
Idempotent enough to re-run: existing objects are looked up by name.
Usage: python3 setup_apim.py   (APIM admin is admin/admin in the stock image; demo users get DEMO_USER_PASSWORD)
"""
import base64, json, os, ssl, sys, urllib.request, urllib.error, urllib.parse

HOST = os.environ.get("APIM", "https://localhost:9443")
ADMIN = ("admin", "admin")
USER_PW = os.environ.get("DEMO_USER_PASSWORD", "Demo@user123")
CTX = ssl._create_unverified_context()  # stock self-signed cert, local demo only
HERE = os.path.dirname(os.path.abspath(__file__))

APIS = [  # name, context, backend path, resources
    ("Orders", "/orders", "orders", [("GET", "/orders"), ("POST", "/orders"), ("GET", "/orders/{id}")]),
    ("Payments", "/payments", "payments", [("POST", "/charges"), ("GET", "/charges/{id}"), ("POST", "/refunds")]),
    ("Inventory", "/inventory", "inventory", [("GET", "/items"), ("GET", "/items/{sku}"), ("PUT", "/items/{sku}")]),
    ("Customers", "/customers", "customers", [("GET", "/customers/{id}"), ("POST", "/customers")]),
]
USERS = ["alice", "bob", "carol"]
APPS = [  # app, owner, tier for every subscription, APIs it uses
    ("MobileApp", "alice", "Unlimited", ["Orders", "Payments", "Inventory", "Customers"]),
    ("WebPortal", "alice", "Unlimited", ["Orders", "Customers", "Inventory"]),
    ("PartnerAcme", "bob", "Unlimited", ["Inventory", "Orders"]),
    ("BatchReporting", "bob", "Unlimited", ["Orders", "Payments"]),
    ("PartnerGlobex", "carol", "Demo30PerMin", ["Inventory", "Orders"]),
]


def call(method, path, token=None, body=None, basic=None, form=None, ok=(200, 201, 202, 204)):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(HOST + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=CTX) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")
        if e.code in ok:
            return {}
        raise SystemExit(f"{method} {path} -> {e.code}: {msg[:400]}")


def admin_token(scopes):
    dcr = call("POST", "/client-registration/v0.17/register", basic=ADMIN, body={
        "clientName": "demo_setup", "owner": "admin", "grantType": "password refresh_token", "saasApp": True})
    tok = call("POST", "/oauth2/token", basic=(dcr["clientId"], dcr["clientSecret"]),
               form={"grant_type": "password", "username": "admin", "password": "admin", "scope": scopes})
    return tok["access_token"]


def user_token(user, scopes):
    dcr = call("POST", "/client-registration/v0.17/register", basic=(user, USER_PW), body={
        "clientName": f"demo_{user}", "owner": user, "grantType": "password refresh_token", "saasApp": True})
    return call("POST", "/oauth2/token", basic=(dcr["clientId"], dcr["clientSecret"]),
                form={"grant_type": "password", "username": user, "password": USER_PW, "scope": scopes})["access_token"]


def main():
    # 1. users. APIM 4.7 has no /scim2 (404), so use the RemoteUserStoreManagerService SOAP admin service.
    for u in USERS:
        soap = lambda op, body: urllib.request.urlopen(urllib.request.Request(
            HOST + "/services/RemoteUserStoreManagerService.RemoteUserStoreManagerServiceHttpsSoap11Endpoint",
            data=('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
                  f'xmlns:ser="http://service.ws.um.carbon.wso2.org"><soapenv:Body><ser:{op}>{body}</ser:{op}>'
                  '</soapenv:Body></soapenv:Envelope>').encode(),
            headers={"Content-Type": "text/xml", "SOAPAction": f"urn:{op}",
                     "Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}), context=CTX).read().decode()
        if "<ns:return>true</ns:return>" not in soap("isExistingUser", f"<ser:userName>{u}</ser:userName>"):
            soap("addUser", f"<ser:userName>{u}</ser:userName><ser:credential>{USER_PW}</ser:credential>"
                            "<ser:roleList>Internal/subscriber</ser:roleList><ser:requirePasswordChange>false</ser:requirePasswordChange>")
    print("users ok")

    # 2. subscription tier that the noisy partner will hit
    adm = admin_token("apim:admin apim:tier_view apim:tier_manage")
    tiers = call("GET", "/api/am/admin/v4/throttling/policies/subscription", token=adm)["list"]
    if not any(t["policyName"] == "Demo30PerMin" for t in tiers):
        call("POST", "/api/am/admin/v4/throttling/policies/subscription", token=adm, body={
            "policyName": "Demo30PerMin", "displayName": "Demo30PerMin", "description": "30 requests per minute (demo)",
            "defaultLimit": {"type": "REQUESTCOUNTLIMIT", "requestCount": {"timeUnit": "min", "unitTime": 1, "requestCount": 30}},
            "stopOnQuotaReach": True, "billingPlan": "FREE"})
    print("tier ok")

    # 3. APIs: create, deploy a revision to the Default gateway, publish
    pub = admin_token("apim:api_view apim:api_create apim:api_publish apim:api_manage apim:subscription_view")
    existing = {a["name"]: a["id"] for a in call("GET", "/api/am/publisher/v4/apis?limit=100", token=pub)["list"]}
    for name, ctx, svc, res in APIS:
        if name not in existing:
            api = call("POST", "/api/am/publisher/v4/apis", token=pub, body={
                "name": name, "context": ctx, "version": "1.0.0", "policies": ["Unlimited", "Demo30PerMin"],
                "endpointConfig": {"endpoint_type": "http",
                                   "production_endpoints": {"url": f"http://backend:8080/{svc}"},
                                   "sandbox_endpoints": {"url": f"http://backend:8080/{svc}"}},
                "operations": [{"verb": v, "target": t, "authType": "Application & Application User", "throttlingPolicy": "Unlimited"} for v, t in res]})
            existing[name] = api["id"]
            rev = call("POST", f"/api/am/publisher/v4/apis/{api['id']}/revisions", token=pub, body={"description": "v1"})
            call("POST", f"/api/am/publisher/v4/apis/{api['id']}/deploy-revision?revisionId={rev['id']}", token=pub,
                 body=[{"name": "Default", "vhost": "localhost", "displayOnDevportal": True}])
            call("POST", f"/api/am/publisher/v4/apis/change-lifecycle?apiId={api['id']}&action=Publish", token=pub)
        print("api ok", name)

    # 4. applications per owner, subscriptions, keys
    out = {}
    for app, owner, tier, apis in APPS:
        dp = user_token(owner, "apim:subscribe apim:app_manage apim:sub_manage")
        found = call("GET", f"/api/am/devportal/v3/applications?query={app}", token=dp)["list"]
        a = found[0] if found else call("POST", "/api/am/devportal/v3/applications", token=dp, body={
            "name": app, "throttlingPolicy": "Unlimited", "description": f"{app} (demo)", "tokenType": "JWT"})
        subs = {s["apiId"] for s in call("GET", f"/api/am/devportal/v3/subscriptions?applicationId={a['applicationId']}", token=dp)["list"]}
        for n in apis:
            if existing[n] not in subs:
                call("POST", "/api/am/devportal/v3/subscriptions", token=dp, body={
                    "applicationId": a["applicationId"], "apiId": existing[n], "throttlingPolicy": tier})
        keys = call("GET", f"/api/am/devportal/v3/applications/{a['applicationId']}/oauth-keys", token=dp)["list"]
        k = next((k for k in keys if k["keyType"] == "PRODUCTION"), None) or call(
            "POST", f"/api/am/devportal/v3/applications/{a['applicationId']}/generate-keys", token=dp, body={
                "keyType": "PRODUCTION", "grantTypesToBeSupported": ["password", "client_credentials", "refresh_token"],
                "keyManager": "Resident Key Manager", "validityTime": 3600})
        out[app] = {"owner": owner, "apis": apis, "consumerKey": k["consumerKey"], "consumerSecret": k["consumerSecret"]}
        print("app ok", app, "->", ",".join(apis), tier)

    # client secrets are local demo creds; file is chmod 600 and not shared
    path = os.path.join(HERE, "apps.json")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(out, f, indent=2)
    print("wrote", path)


if __name__ == "__main__":
    sys.exit(main())
