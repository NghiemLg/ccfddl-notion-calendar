#!/usr/bin/env python3
"""CCFDDL YAML -> Notion data source + Google Calendar. Python >= 3.10."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
ROBOT_NAMES = {
    "ICRA", "IROS", "RSS", "CORL", "ROBIO", "ISRR", "ISER", "HRI", "CASE",
    "WAFR", "ICAR", "RO-MAN", "ROMAN", "ARSO", "IV", "ITSC",
}
ROBOT_RE = re.compile(r"\b(robot(?:ic)?s?|autonomous vehicles?|intelligent vehicles?|autonomous driving|robot learning)\b", re.I)
FIELDS = {"Name": "title", "Source ID": "rich_text", "Deadline": "date",
          "CORE": "rich_text", "CCF": "rich_text", "Field": "select",
          "Kind": "select", "URL": "url", "Progress": "select"}


def session_with_retry():
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], respect_retry_after_header=True)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    return s


def parse_deadline(value, tz_name):
    """Return timezone-aware timestamp; unknown timezone -> explicit error, never assume local time."""
    if value is None or str(value).strip().upper() in {"", "TBD", "TBA", "NONE"}:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    if len(raw) == 10:
        raw += " 23:59:59"  # Repository convention; not an officially specified clock time.
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is not None:
        return dt
    name = str(tz_name or "").strip()
    if name.upper() == "AOE":
        tz = timezone(timedelta(hours=-12))
    elif name.upper() in {"PT", "PST/PDT", "PACIFIC"}:
        tz = ZoneInfo("America/Los_Angeles")
    elif name.upper() in {"UTC", "GMT", "Z"}:
        tz = timezone.utc
    else:
        match = re.fullmatch(r"(?:UTC|GMT)\s*([+-])(\d{1,2})(?::?(\d{2}))?", name, re.I)
        if match:
            sign = 1 if match.group(1) == "+" else -1
            hh, mm = int(match.group(2)), int(match.group(3) or "0")
            if hh > 23 or mm > 59:
                raise ValueError(f"Invalid timezone: {name}")
            tz = timezone(sign * timedelta(hours=hh, minutes=mm))
        else:
            try:
                tz = ZoneInfo(name)
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Unknown timezone '{name}'") from exc
    return dt.replace(tzinfo=tz)


def classify(item):
    name = str(item.get("title", "")).strip().upper()
    desc = str(item.get("description", ""))
    category = str(item.get("sub", "")).strip().upper()
    if name in ROBOT_NAMES or ROBOT_RE.search(desc):
        return "Robotics"
    if category == "AI":
        return "AI"
    return None


@dataclass(frozen=True)
class Deadline:
    source_id: str
    name: str
    short_name: str
    when: datetime
    core: str
    ccf: str
    field: str
    kind: str
    url: str
    note: str


def normalize(entries, lookahead_days=550, include_ccf_a=False, now=None):
    """One stable row/event per conference edition + timeline slot + deadline kind."""
    if isinstance(entries, dict):
        # Some mirrors wrap the list in {conferences: [...]}.
        entries = entries.get("conferences", entries.get("data", []))
    if not isinstance(entries, list):
        raise ValueError("Expected YAML list of conferences")
    now = now or datetime.now(timezone.utc)
    cutoff = now + timedelta(days=lookahead_days)
    rows = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        rank = item.get("rank") or {}
        if not isinstance(rank, dict):
            continue
        core = str(rank.get("core") or "N").strip().upper().replace("A★", "A*")
        ccf = str(rank.get("ccf") or "N").strip().upper()
        if core not in {"A", "A*"} and not (include_ccf_a and ccf == "A"):
            continue
        field = classify(item)
        if not field:
            continue
        short = str(item.get("title", "")).strip()
        for edition in item.get("confs") or []:
            if not isinstance(edition, dict):
                continue
            year = edition.get("year", "?")
            eid = str(edition.get("id") or f"{short.lower()}{year}")
            tz = edition.get("timezone")
            url = str(edition.get("link") or "").strip()
            for idx, timeline in enumerate(edition.get("timeline") or []):
                if not isinstance(timeline, dict):
                    continue
                for key, kind in (("abstract_deadline", "Abstract"), ("deadline", "Paper")):
                    if key not in timeline:
                        continue
                    try:
                        dt = parse_deadline(timeline[key], tz)
                    except ValueError as exc:
                        print(f"SKIP {eid}/{idx}/{key}: {exc}", file=sys.stderr)
                        continue
                    if not dt or not now < dt.astimezone(timezone.utc) <= cutoff:
                        continue
                    sid = f"ccfddl:{eid}:{idx}:{kind.lower()}"
                    if sid in seen:
                        continue
                    seen.add(sid)
                    comment = str(timeline.get("comment") or "").strip()
                    name = f"{short} {year} - {kind} deadline"
                    if comment:
                        name += f" ({comment[:70]})"
                    rows.append(Deadline(sid, name[:200], short, dt, core, ccf,
                                         field, kind, url, comment))
    rows.sort(key=lambda r: (r.when, r.short_name, r.kind))
    return rows


def fetch_source(source):
    if source:
        raw = Path(source).read_text(encoding="utf-8")
    else:
        url = os.getenv("CCFDDL_YAML_URL", "https://ccfddl.github.io/conference/allconf.yml")
        r = session_with_retry().get(url, timeout=35)
        r.raise_for_status()
        # Let YAML decode UTF-8 instead of Requests' HTTP charset fallback.
        raw = r.content
    data = yaml.safe_load(raw)
    if not data:
        raise ValueError("YAML source is empty; aborting, no data will be deleted")
    return data


class Notion:
    def __init__(self, token, database_id):
        if not token or not database_id:
            raise ValueError("Missing NOTION_TOKEN or NOTION_DATABASE_ID")
        self.s = session_with_retry()
        self.s.headers.update({"Authorization": f"Bearer {token}",
                               "Notion-Version": NOTION_VERSION,
                               "Content-Type": "application/json"})
        self.database_id = database_id
        db = self.call("GET", f"/databases/{database_id}")
        sources = db.get("data_sources") or []
        if len(sources) != 1:
            raise ValueError(f"Database has {len(sources)} data sources; expected exactly 1. "
                             "Use a normal single-source Notion database.")
        self.source_id = sources[0]["id"]
        desc = self.call("GET", f"/data_sources/{self.source_id}")
        props = desc.get("properties") or {}
        wrong = [f"{n} ({t})" for n,t in FIELDS.items() if props.get(n, {}).get("type") != t]
        if wrong:
            raise ValueError("Notion database needs properties/types: " + ", ".join(wrong))

    def call(self, method, path, payload=None):
        time.sleep(0.36)  # Notion average ~3 req/s, leave capacity for others.
        res = self.s.request(method, NOTION_API + path, json=payload, timeout=35)
        if not res.ok:
            raise RuntimeError(f"Notion {res.status_code}: {res.text[:650]}")
        return res.json()

    def index_pages(self):
        result = {}
        cursor = None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            answer = self.call("POST", f"/data_sources/{self.source_id}/query", body)
            for page in answer.get("results", []):
                arr = page.get("properties", {}).get("Source ID", {}).get("rich_text") or []
                key = "".join(x.get("plain_text", "") for x in arr)
                if key:
                    result[key] = page
            if not answer.get("has_more"):
                break
            cursor = answer.get("next_cursor")
            if not cursor:
                raise RuntimeError("Notion indicated more pages but returned no next_cursor")
        return result

    @staticmethod
    def props(r, fresh=False):
        p = {
            "Name": {"title": [{"text": {"content": r.name}}]},
            "Source ID": {"rich_text": [{"text": {"content": r.source_id}}]},
            "Deadline": {"date": {"start": r.when.isoformat()}},
            "CORE": {"rich_text": [{"text": {"content": r.core}}]},
            "CCF": {"rich_text": [{"text": {"content": r.ccf}}]},
            "Field": {"select": {"name": r.field}},
            "Kind": {"select": {"name": r.kind}},
            "URL": {"url": r.url if r.url.startswith(("https://", "http://")) else None},
        }
        if fresh:
            p["Progress"] = {"select": {"name": "Watching"}}
        return p

    @staticmethod
    def changed(page, r):
        p = page["properties"]
        def txt(key, typ):
            return "".join(x.get("plain_text", "") for x in (p.get(key, {}).get(typ) or []))
        old = p.get("Deadline", {}).get("date") or {}
        try:
            time_changed = datetime.fromisoformat(old.get("start", "")).astimezone(timezone.utc) != r.when.astimezone(timezone.utc)
        except ValueError:
            time_changed = True
        return (txt("Name", "title") != r.name or time_changed or
                txt("CORE", "rich_text") != r.core or txt("CCF", "rich_text") != r.ccf or
                (p.get("Field", {}).get("select") or {}).get("name") != r.field or
                (p.get("Kind", {}).get("select") or {}).get("name") != r.kind or
                (p.get("URL", {}).get("url") or "") != r.url)

    def sync(self, rows):
        cache = self.index_pages()
        counters = {"created": 0, "updated": 0, "unchanged": 0, "errors": 0}
        for r in rows:
            try:
                old = cache.get(r.source_id)
                if old is None:
                    new = self.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": self.source_id},
                                                         "properties": self.props(r, fresh=True)})
                    cache[r.source_id] = new
                    counters["created"] += 1
                elif self.changed(old, r):
                    new = self.call("PATCH", f"/pages/{old['id']}", {"properties": self.props(r)})
                    cache[r.source_id] = new
                    counters["updated"] += 1
                else:
                    counters["unchanged"] += 1
            except Exception as exc:
                counters["errors"] += 1
                print(f"ERROR Notion {r.source_id}: {exc}", file=sys.stderr)
        return counters


def google_service(authorize=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    # Headless cloud authentication: Service Account JSON stored in GitHub Secrets.
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_json:
        from google.oauth2 import service_account
        info = json.loads(sa_json)
        if info.get("type") != "service_account":
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not a service_account key")
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Alternative: an existing authorized_user token kept in GitHub Secrets.
    # No interactive consent is attempted when an env-provided token is present.
    token_json = os.getenv("GOOGLE_TOKEN_JSON", "").strip()
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        if not creds.valid:
            if not creds.refresh_token:
                raise ValueError("GOOGLE_TOKEN_JSON has no refresh_token")
            creds.refresh(Request())
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    path = ROOT / "token.json"
    secret = ROOT / "credentials.json"
    creds = Credentials.from_authorized_user_file(str(path), SCOPES) if path.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secret.exists():
                raise FileNotFoundError("Missing credentials.json. Download Google OAuth Desktop JSON to project folder")
            flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
        path.write_text(creds.to_json(), encoding="utf-8")
        path.chmod(0o600)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def google_id(r):
    # Google event IDs allow base32hex chars (0-9 a-v); hex digest is a safe subset.
    return "ccf" + hashlib.sha256(r.source_id.encode()).hexdigest()[:36]


def google_body(r):
    title = f"[{r.core}][{r.field}] {r.name}"
    desc = (f"CCFDDL ID: {r.source_id}\nCORE: {r.core} | CCF: {r.ccf}\n"
            f"Field: {r.field} | Kind: {r.kind}\nRound: {r.note or '-'}\n"
            f"Official website: {r.url}\nSource: https://ccfddl.com/\n"
            "Verify the deadline on the official Call for Papers before submitting.")
    start = r.when.isoformat()
    end = (r.when + timedelta(minutes=15)).isoformat()
    return {
        "id": google_id(r), "summary": title, "description": desc,
        "start": {"dateTime": start}, "end": {"dateTime": end},
        "reminders": {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": 28 * 24 * 60},
            {"method": "popup", "minutes": 7 * 24 * 60},
            {"method": "popup", "minutes": 24 * 60}]},
        "extendedProperties": {"private": {"ccfddl_id": r.source_id}},
    }


def sync_calendar(rows, calendar_id, service):
    from googleapiclient.errors import HttpError
    out = {"created": 0, "updated": 0, "unchanged": 0, "errors": 0}
    for r in rows:
        new = google_body(r)
        event_id = new["id"]
        try:
            try:
                old = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            except HttpError as exc:
                if exc.resp.status != 404:
                    raise
                try:
                    service.events().insert(calendarId=calendar_id, body=new).execute()
                except HttpError as insert_error:
                    if insert_error.resp.status != 409:
                        raise
                    # Concurrent process created it; retry fetch rather than duplicate.
                    old = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
                else:
                    out["created"] += 1
                    continue
            keys = ("summary", "description")
            timed = lambda item, key: datetime.fromisoformat(item[key]["dateTime"].replace("Z", "+00:00")).astimezone(timezone.utc)
            changed = (any(old.get(k) != new[k] for k in keys) or
                       any(timed(old, key) != timed(new, key) for key in ("start", "end")))
            if changed:
                # Preserve user-added properties/reminders; edit only fields managed by this script.
                for k in ("summary", "description", "start", "end"):
                    old[k] = new[k]
                service.events().update(calendarId=calendar_id, eventId=event_id, body=old).execute()
                out["updated"] += 1
            else:
                out["unchanged"] += 1
        except Exception as exc:
            out["errors"] += 1
            print(f"ERROR Calendar {r.source_id}: {exc}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="Sync CCFDDL A/A* deadlines to Notion + Google Calendar")
    ap.add_argument("--source", metavar="FILE", help="Use local YAML file for offline testing")
    ap.add_argument("--dry-run", action="store_true", help="Preview filtered data; no APIs or credentials")
    ap.add_argument("--auth-google", action="store_true", help="Only do the initial Google OAuth flow")
    ap.add_argument("--notion-only", action="store_true")
    ap.add_argument("--calendar-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="Limit items, useful for first live test")
    args = ap.parse_args()
    if args.notion_only and args.calendar_only:
        ap.error("Use --notion-only OR --calendar-only")
    if args.auth_google:
        google_service(authorize=True)
        print("Google authorized; token.json saved")
        return
    raw = fetch_source(args.source)
    include_ccf_a = os.getenv("INCLUDE_CCF_A", "false").lower() in ("true", "yes", "1")
    rows = normalize(raw, lookahead_days=int(os.getenv("LOOKAHEAD_DAYS", "550")), include_ccf_a=include_ccf_a)
    if args.limit > 0:
        rows = rows[:args.limit]
    print(f"Matched {len(rows)} upcoming deadlines (CORE A/A*, AI/Robotics)")
    if args.dry_run:
        tz = ZoneInfo(os.getenv("DISPLAY_TZ", "Asia/Ho_Chi_Minh"))
        for r in rows:
            print(json.dumps({"id": r.source_id, "title": r.name,
                              "deadline_vn": r.when.astimezone(tz).isoformat(),
                              "core": r.core, "ccf": r.ccf, "field": r.field, "url": r.url},
                             ensure_ascii=False))
        return
    if not rows:
        print("No upcoming matches; no changes were made")
        return
    errors = 0
    if not args.calendar_only:
        notion = Notion(os.getenv("NOTION_TOKEN", ""), os.getenv("NOTION_DATABASE_ID", ""))
        result = notion.sync(rows)
        print("NOTION:", result)
        errors += result["errors"]
    if not args.notion_only:
        calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "")
        if not calendar_id:
            raise ValueError("Missing GOOGLE_CALENDAR_ID in .env")
        result = sync_calendar(rows, calendar_id, google_service())
        print("CALENDAR:", result)
        errors += result["errors"]
    if errors:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
