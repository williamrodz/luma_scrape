#!/bin/bash
TOKEN=$(cat ~/.github_pat)
API=https://api.github.com/repos/williamrodz/luma_scrape/actions/workflows
STAMP=~/.luma-keepalive-stamp

# Re-enabling a workflow resets GitHub's 60-day inactivity timer for scheduled workflows.
enable_workflow() {
  curl -s -o /dev/null -w "enable $1: %{http_code}\n" -X PUT \
    -H "Authorization: token $TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "$API/$1/enable"
}

dispatch_workflow() {
  curl -s -X POST \
    -H "Authorization: token $TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "$API/$1/dispatches" \
    -d '{"ref":"main"}'
}

# Daily keepalive
if [ -z "$(find "$STAMP" -mmin -1440 2>/dev/null)" ]; then
  echo "$(date -u) keepalive"
  for wf in scrape-outage-status.yaml scrape.yml backup.yml; do
    enable_workflow "$wf"
  done
  touch "$STAMP"
fi

for wf in scrape-outage-status.yaml scrape.yml; do
  resp=$(dispatch_workflow "$wf")
  if echo "$resp" | grep -q "disabled workflow"; then
    echo "$(date -u) $wf was disabled; re-enabling"
    enable_workflow "$wf"
    resp=$(dispatch_workflow "$wf")
  fi
  if [ -n "$resp" ]; then echo "$resp"; fi
done
