"""
External health check for the scrapers, run on a GitHub-hosted runner so it
still alerts when the self-hosted workstation is down.

An open issue labelled `outage-alert` marks an ongoing incident: one email is
sent when it opens and one when it closes. Standard library only.

Env: SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, ALERT_TO, GITHUB_TOKEN,
     GITHUB_REPOSITORY, optional TEST_ALERT=true to simulate a stale table.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

STALE_MINUTES = 45
LABEL = "outage-alert"
TABLES = ["outage_snapshot", "luma_scrape_results"]
ALERT_FROM = "PR Grid Status Alerts <alerts@prgridstatus.com>"


def request(url, headers, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def github(path, data=None, method=None):
    return request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}",
        {
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        data,
        method,
    )


def send_email(subject, body):
    try:
        request(
            "https://api.resend.com/emails",
            {
                "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                "Content-Type": "application/json",
                "User-Agent": "luma-external-check/1.0",
            },
            {"from": ALERT_FROM, "to": [os.environ["ALERT_TO"]], "subject": subject, "text": body},
        )
        print(f"email sent: {subject}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Resend error {e.code}: {e.read().decode()}") from None


def find_problems(now):
    if os.environ.get("TEST_ALERT") == "true":
        return ["TEST: simulated stale data (workflow_dispatch test_alert=true)."]
    problems = []
    headers = {"apikey": os.environ["SUPABASE_KEY"], "Authorization": f"Bearer {os.environ['SUPABASE_KEY']}"}
    for table in TABLES:
        try:
            rows = request(
                f"{os.environ['SUPABASE_URL']}/rest/v1/{table}?select=timestamp&order=timestamp.desc&limit=1",
                headers,
            )
        except Exception as e:
            # Keep the message generic: issues on this repo are public.
            problems.append(f"`{table}`: Supabase query failed ({type(e).__name__}). Is the project paused?")
            continue
        ts = datetime.fromisoformat(rows[0]["timestamp"].replace("Z", "+00:00"))
        age = (now - ts).total_seconds() / 60
        print(f"{table}: newest row {ts:%Y-%m-%d %H:%M} UTC ({age:.0f} min old)")
        if age > STALE_MINUTES:
            problems.append(f"`{table}`: newest row is {age:.0f} min old ({ts:%Y-%m-%d %H:%M} UTC).")
    return problems


def main():
    now = datetime.now(timezone.utc)
    problems = find_problems(now)
    open_issues = github(f"issues?state=open&labels={LABEL}")

    if problems and not open_issues:
        body = (
            "The external health check found stale scraper data:\n\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\nLikely causes: the self-hosted workstation or runner is down, workflows were "
            "disabled, or Supabase is paused. This issue closes automatically when data is fresh again."
        )
        issue = github("issues", {"title": f"Scraper outage detected {now:%Y-%m-%d %H:%M} UTC", "body": body, "labels": [LABEL]})
        send_email("🔴 [external check] Scraper outage detected", body.replace("`", "") + f"\n\n{issue['html_url']}")
    elif not problems and open_issues:
        for issue in open_issues:
            github(f"issues/{issue['number']}/comments", {"body": f"Data is fresh again as of {now:%Y-%m-%d %H:%M} UTC. Closing."})
            github(f"issues/{issue['number']}", {"state": "closed"}, method="PATCH")
            send_email(
                "✅ [external check] Resolved: scraper outage",
                f"Scraper data is fresh again as of {now:%Y-%m-%d %H:%M} UTC.\n\n{issue['html_url']}",
            )
    else:
        print("ongoing incident, already alerted" if problems else "all fresh")


if __name__ == "__main__":
    main()
