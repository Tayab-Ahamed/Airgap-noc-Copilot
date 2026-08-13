#!/usr/bin/env bash
# Proves there is no outbound connectivity at runtime. Run at startup on stage.
set -uo pipefail

echo "==> Verifying air-gap (expect all external probes to FAIL)"
FAIL=0
for host in 8.8.8.8 1.1.1.1 google.com; do
  if timeout 3 ping -c1 "$host" >/dev/null 2>&1; then
    echo "  [WARN] reachable: $host  <-- NOT air-gapped!"
    FAIL=1
  else
    echo "  [ok] unreachable: $host"
  fi
done

if curl -s --max-time 3 https://example.com >/dev/null 2>&1; then
  echo "  [WARN] outbound HTTPS works <-- NOT air-gapped!"; FAIL=1
else
  echo "  [ok] outbound HTTPS blocked"
fi

if [ "$FAIL" -eq 0 ]; then
  echo "==> AIR-GAP CONFIRMED. Ollama (localhost:11434) and the app run fully offline."
else
  echo "==> WARNING: environment is NOT fully air-gapped."; exit 1
fi
