#!/usr/bin/env bash
# One-shot prerequisite check for this repo and the blog post.
# Run it the same way the Agent Manager installer runs:  bash check-prereqs.sh
# It only reads; it changes nothing. Exit code 0 = ready, 1 = something to fix.
# Written to run on macOS's bash 3.2 too, so it can tell you that bash is too old.

fails=0; warns=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; warns=$((warns + 1)); }

echo "Tools"
for t in docker k3d kubectl helm gh git curl openssl python3; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t found"; else bad "$t not found"; fi
done

echo "Versions"
if [ "${BASH_VERSINFO[0]}" -gt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -ge 4 ]; }; then
  ok "bash $BASH_VERSION (4.4+ needed by the Agent Manager installer)"
else
  bad "bash $BASH_VERSION is too old: the Agent Manager installer needs 4.4+ (on macOS: brew install bash, then open a new terminal)"
fi
if command -v python3 >/dev/null 2>&1; then
  pv=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then ok "python3 $pv (3.10+ needed)"; else bad "python3 $pv is too old (3.10+ needed)"; fi
fi

echo "Docker"
if mem=$(docker info --format '{{.MemTotal}}' 2>/dev/null); then
  gb=$(awk -v m="$mem" 'BEGIN { printf "%.1f", m / 1e9 }')
  ok "Docker engine is running"
  if awk -v m="$mem" 'BEGIN { exit !(m >= 15e9) }'; then ok "$gb GB memory for Docker (about 16 GB needed)"
  else warn "$gb GB memory for Docker; about 16 GB is needed for everything together"; fi
else
  bad "Docker engine is not reachable (is Docker running? is DOCKER_HOST pointing somewhere old?)"
fi

echo "Ports"
busy=0
for p in 9200 5601 9443 8243 8280 18080 6550 8080 8443 10082 11080 11082 11085 19080 19443; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    busy=$((busy + 1))
    bad "port $p is in use by $(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 { print $1 }')"
  fi
done
[ "$busy" -eq 0 ] && ok "all 15 ports are free"

echo "Leftovers from an earlier run"
left=0
if command -v k3d >/dev/null 2>&1 && k3d cluster list 2>/dev/null | awk 'NR>1 { print $1 }' | grep -qx amp-local; then
  left=1; warn "k3d cluster amp-local already exists; the installer will reuse it"
fi
if command -v docker >/dev/null 2>&1 && docker volume ls -q 2>/dev/null | grep -q '^apim-elastic_'; then
  left=1; warn "Docker volumes apim-elastic_* exist; docker compose will reuse their data"
fi
[ "$left" -eq 0 ] && ok "none found"

echo
if [ "$fails" -eq 0 ]; then
  printf '\033[32mReady.\033[0m %d warning(s).\n' "$warns"; exit 0
else
  printf '\033[31mNot ready:\033[0m %d problem(s), %d warning(s). Fix the ✗ lines and run it again.\n' "$fails" "$warns"; exit 1
fi
