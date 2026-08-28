#!/bin/zsh

set -u

IDCAI_PROJECT_DIR="${0:A:h}"
IDCAI_URL="http://127.0.0.1:8765/?ui=20260827-2#incidents"
IDCAI_LABEL="com.idcai.ops.local"

if ! /usr/bin/curl -fsS --max-time 2 "http://127.0.0.1:8765/api/health" >/dev/null 2>&1; then
  /bin/launchctl remove "$IDCAI_LABEL" >/dev/null 2>&1 || true
  /bin/launchctl submit \
    -l "$IDCAI_LABEL" \
    -o /tmp/idc-ai-ops.stdout.log \
    -e /tmp/idc-ai-ops.stderr.log \
    -- /usr/bin/python3 "$IDCAI_PROJECT_DIR/run.py"

  for IDCAI_ATTEMPT in {1..10}; do
    if /usr/bin/curl -fsS --max-time 2 "http://127.0.0.1:8765/api/health" >/dev/null 2>&1; then
      break
    fi
    /bin/sleep 1
  done
fi

if /usr/bin/curl -fsS --max-time 2 "http://127.0.0.1:8765/api/health" >/dev/null 2>&1; then
  /usr/bin/open "$IDCAI_URL"
else
  /usr/bin/osascript -e 'display alert "机鉴启动失败" message "请把 /tmp/idc-ai-ops.stderr.log 发给开发人员检查。" as critical'
  exit 1
fi
