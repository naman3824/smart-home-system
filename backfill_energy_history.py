"""
One-time backfill: reconstructs energy_daily history from the audit log's
existing device_toggle records, instead of the new "week vs last week"
insight starting from zero on deploy day.

Why this exists: energy_daily is a brand-new table (see this update's
changes to db.py). It has no rows until the live tick-loop accumulator in
server.py starts writing to it. But if the site's already been running,
audit_log already has a real history of every on/off toggle — this script
replays that history to estimate what each day's usage looked like, so the
"this week vs last week" card has real data right away instead of an empty
state for the first 1-2 weeks after deploying.

WHAT IT DOES NOT DO: it can't recover wattage changes that happened without
a fresh on/off toggle (e.g. someone bumped a fan's speed while it was
already running) — it uses each device's base wattage rating for the whole
interval. It also only sees *manual* toggles logged as "device_toggle";
automation/routine-fired changes use a different audit log format and
aren't counted here (same limitation as the live phantom-load/device-health
features). Treat the result as a reasonable estimate, not an exact figure.

USAGE (run once, after deploying the updated server.py/db.py):
    python3 backfill_energy_history.py

Safe to re-run: it recomputes and overwrites, it doesn't add on top of
itself. Today's date is intentionally left alone if it already has data
from the live accumulator having run at least once, so you don't
overwrite a more accurate live-tracked number with a backfilled estimate —
delete that guard below if you actually want to force-overwrite today too.
"""
import sys
from datetime import datetime, timedelta
from collections import defaultdict

import db
import server  # reuses _parse_toggle_detail, devices (for wattage), calculate_dhbvn_bill


def backfill():
    entries = db.get_audit_log(limit=100000)
    entries = [e for e in entries if e.get("action") == "device_toggle"]
    entries.sort(key=lambda e: e["created_at"])  # oldest first

    if not entries:
        print("No device_toggle history found in audit_log — nothing to backfill.")
        return

    def base_watts(room, device):
        return server.devices.get(room, {}).get(device, {}).get("watts", 0)

    # Reconstruct on/off intervals per (room, device), then split each
    # interval across calendar-day boundaries so a device left on overnight
    # contributes to both days correctly.
    daily_kwh = defaultdict(float)  # date_str -> kwh
    open_since = {}
    oldest_ts = None
    newest_ts = None

    def add_interval(room, device, start, end):
        watts = base_watts(room, device)
        if watts <= 0 or end <= start:
            return
        cur = start
        while cur < end:
            day_end = datetime(cur.year, cur.month, cur.day) + timedelta(days=1)
            chunk_end = min(end, day_end)
            hours = (chunk_end - cur).total_seconds() / 3600
            daily_kwh[cur.strftime("%Y-%m-%d")] += watts * hours / 1000
            cur = chunk_end

    for e in entries:
        parsed = server._parse_toggle_detail(e.get("detail", ""))
        if not parsed:
            continue
        room, device, value = parsed
        if "on" not in value:
            continue
        try:
            ts = datetime.strptime(e["created_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
        newest_ts = ts if newest_ts is None else max(newest_ts, ts)
        key = (room, device)
        if value["on"]:
            open_since[key] = ts
        else:
            started = open_since.pop(key, None)
            if started:
                add_interval(room, device, started, ts)

    # Anything still "on" with no matching off — close it out at now.
    now = datetime.now()
    for (room, device), started in open_since.items():
        add_interval(room, device, started, now)

    today_str = now.strftime("%Y-%m-%d")
    existing_today = db.get_energy_daily_range(days=1)
    skip_today = bool(existing_today and existing_today[-1]["date"] == today_str)

    written = 0
    for date_str, kwh in sorted(daily_kwh.items()):
        if date_str == today_str and skip_today:
            continue  # leave the live accumulator's number alone
        cost = server.calculate_dhbvn_bill(round(kwh, 4))["total"]
        db.upsert_energy_daily(date_str, round(kwh, 4), cost)
        written += 1

    span = f"{oldest_ts.strftime('%Y-%m-%d')} to {newest_ts.strftime('%Y-%m-%d')}" if oldest_ts else "n/a"
    print(f"Backfilled {written} day(s) of energy history from audit log ({span}).")
    print("The Energy tab's 'this week vs last week' card should now show real data")
    print("(needs 7 complete days for a total, 14 for a full comparison).")


if __name__ == "__main__":
    backfill()
