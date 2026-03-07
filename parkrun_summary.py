#!/usr/bin/env python3
import subprocess
import sys
import os

# ---------------------------------------------------------------------------
# Auto-install dependencies — ALL output suppressed so nothing leaks into
# the HTML file when stdout is redirected.
# ---------------------------------------------------------------------------
for pkg, import_name in [("beautifulsoup4", "bs4"), ("requests", "requests")]:
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg, "--break-system-packages",
             "--quiet", "--quiet"],   # two --quiet flags → suppresses all pip output
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

from bs4 import BeautifulSoup
import requests

import datetime
import re
from dataclasses import dataclass
from statistics import mean
from typing import List, Optional
from collections import Counter


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EventHistoryEntry:
    event_number: int
    date: datetime.date
    finishers: Optional[int]
    volunteers: Optional[int]
    male_name: str
    female_name: str
    male_time_sec: Optional[int]
    female_time_sec: Optional[int]


@dataclass
class LatestRunner:
    position: int
    name: str
    time_sec: Optional[int]
    club: str
    gender: Optional[str]       # 'M', 'F', or None
    run_count: Optional[int]    # total parkruns including this one
    is_pb: bool                 # True if parkrun marked this as a PB
    age_category: str           # e.g. 'VM40-44', 'SW25-29', '' if unknown
    is_first_here: bool         # True if first time at THIS event


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def parse_int(text: str) -> Optional[int]:
    t = text.replace(",", "").strip()
    return int(t) if t.isdigit() else None


def parse_mmss_to_sec(value: str) -> Optional[int]:
    """
    parkrun stores first-finisher times in event history as a plain integer
    in MMSS format, e.g. '2417' means 24 minutes 17 seconds = 1457 total seconds.
    This is different from the MM:SS colon-separated format in results pages.
    """
    v = value.strip()
    if not v.isdigit():
        return None
    n = int(v)
    minutes, seconds = divmod(n, 100)
    if seconds >= 60:
        return None   # invalid
    return minutes * 60 + seconds
    t = text.replace(",", "").strip()
    return int(t) if t.isdigit() else None


def parse_time_to_sec(text: str) -> Optional[int]:
    """Accept MM:SS or H:MM:SS."""
    t = text.strip()
    if not t:
        return None
    parts = t.split(":")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        m, s = map(int, parts)
        return m * 60 + s
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s
    return None


def extract_date_prefix(text: str) -> str:
    out = []
    for ch in text.strip():
        if ch.isdigit() or ch in "/-":
            out.append(ch)
        else:
            break
    return "".join(out)


def parse_date_from_mixed(text: str) -> Optional[datetime.date]:
    date_part = extract_date_prefix(text)
    if not date_part:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(date_part, fmt).date()
        except ValueError:
            continue
    return None


def format_sec_to_time(sec: int) -> str:
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def is_rep_digit_position(pos: int) -> bool:
    s = str(pos)
    return len(s) > 1 and all(ch == s[0] for ch in s)


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def clean_name(raw: str) -> str:
    """
    Strip junk parkrun appends to the runner name, e.g.
      'Ruth JONES42 parkruns|Female|VW35-39|32.95%'
    We want just: 'Ruth JONES'
    """
    s = raw.strip()
    s = s.split("|")[0].strip()
    s = re.sub(r'\s*\d+\s+parkruns?\s*$', '', s, flags=re.IGNORECASE).strip()
    s = re.sub(r'\s*\d+\s*$', '', s).strip()
    return s


# Milestone t-shirt run counts
MILESTONE_COUNTS = {25, 50, 100, 250, 500}

# Clubs section is generic — no hardcoded local clubs


def parse_name_cell(td) -> dict:
    """
    Extract all useful info from a parkrun name cell.
    parkrun renders something like:
      <td>
        <a href="...">Ruth JONES</a>
        <span class="Results-table--pb">PB</span>   ← if PB
        42 parkruns                                  ← run count in text
        | Female | VW35-39 | 32.95%                 ← sometimes pipes
      </td>
    Returns dict with keys: name, run_count, is_pb, age_category
    """
    full_text = td.get_text(" ", strip=True)

    # ── Name ──────────────────────────────────────────────────────────────
    a = td.find("a")
    raw_name = a.get_text(strip=True) if a else full_text
    name = clean_name(raw_name)

    # ── PB flag ────────────────────────────────────────────────────────────
    # PB is marked on the TIME cell (class Results-table-td--pb), not here.
    # We return False and let the row-level parser detect it instead.
    is_pb = False

    # ── Run count ──────────────────────────────────────────────────────────
    run_count = None
    m = re.search(r'(\d+)\s+parkruns?', full_text, re.IGNORECASE)
    if m:
        run_count = int(m.group(1))

    # ── Age category ──────────────────────────────────────────────────────
    # Formats: VM40-44  VW50-54  SM18-19  SW25-29  (also JM/JW for juniors)
    age_category = ""
    m = re.search(r'\b([SV][MWmw]\d{2}(?:-\d{2})?|[JjSsVv][MWmw]\d+)\b', full_text)
    if m:
        age_category = m.group(1).upper()

    return {"name": name, "run_count": run_count, "is_pb": is_pb,
            "age_category": age_category}


def extract_time_from_cell(td) -> Optional[int]:
    """
    parkrun sometimes wraps the time in a <span> or <td class="time">.
    Try the direct text first; if that fails, look for any text matching
    a time pattern anywhere in the cell.
    """
    direct = parse_time_to_sec(td.get_text(strip=True))
    if direct is not None:
        return direct
    # Search for MM:SS or H:MM:SS patterns in the raw HTML
    cell_text = td.get_text(" ", strip=True)
    m = re.search(r'\b(\d{1,2}:\d{2}(?::\d{2})?)\b', cell_text)
    if m:
        return parse_time_to_sec(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Event history parser  (reads from the event-history HTML page)
# ---------------------------------------------------------------------------

def parse_event_history(path: str) -> List[EventHistoryEntry]:
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    table = soup.find("table", class_="Results-table")
    if not table:
        raise RuntimeError("Could not find event history table in file.")

    entries: List[EventHistoryEntry] = []
    tbody = table.find("tbody") or table

    for row in tbody.find_all("tr", class_="Results-table-row"):
        parkrun_id = row.get("data-parkrun")
        if not parkrun_id:
            continue
        try:
            event_num = int(parkrun_id)
        except ValueError:
            continue

        data_date = row.get("data-date")
        if not data_date:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            date = parse_date_from_mixed(cells[1].get_text(strip=True))
            if date is None:
                continue
        else:
            try:
                date = datetime.datetime.strptime(data_date, "%Y-%m-%d").date()
            except ValueError:
                date = parse_date_from_mixed(data_date)
                if date is None:
                    continue

        finishers       = parse_int(row.get("data-finishers")  or "")
        volunteers      = parse_int(row.get("data-volunteers") or "")
        male_name       = row.get("data-male")       or ""
        female_name     = row.get("data-female")     or ""
        male_time_sec   = parse_mmss_to_sec(row.get("data-maletime")   or "")
        female_time_sec = parse_mmss_to_sec(row.get("data-femaletime") or "")

        entries.append(EventHistoryEntry(
            event_number=event_num,
            date=date,
            finishers=finishers,
            volunteers=volunteers,
            male_name=male_name,
            female_name=female_name,
            male_time_sec=male_time_sec,
            female_time_sec=female_time_sec,
        ))

    return entries


# ---------------------------------------------------------------------------
# Latest results parser
# ---------------------------------------------------------------------------

def parse_latest_results(path: str) -> List[LatestRunner]:
    """
    Parse the latest results HTML.
    parkrun stores all useful data as data-* attributes on each <tr>:
      data-name, data-position, data-runs, data-agegroup, data-club,
      data-gender, data-achievement ("New PB!" if PB)
    Time still comes from the <td> cell as it's not in the row attributes.
    """
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    runners: List[LatestRunner] = []

    for row in soup.find_all("tr", class_="Results-table-row"):
        # ── Position ──────────────────────────────────────────────────────
        try:
            position = int(row.get("data-position", ""))
        except ValueError:
            continue

        # ── Name ──────────────────────────────────────────────────────────
        name = clean_name(row.get("data-name", ""))

        # ── Run count ─────────────────────────────────────────────────────
        try:
            run_count = int(row.get("data-runs", ""))
        except ValueError:
            run_count = None

        # ── Age category ──────────────────────────────────────────────────
        age_category = row.get("data-agegroup", "").strip()

        # ── Club ──────────────────────────────────────────────────────────
        club = row.get("data-club", "").strip()

        # ── Gender ────────────────────────────────────────────────────────
        g = row.get("data-gender", "").strip().upper()
        gender = "M" if g.startswith("M") else ("F" if g.startswith("F") else None)

        # ── PB / First here ───────────────────────────────────────────────
        achievement = row.get("data-achievement", "").lower()
        is_pb        = "new pb" in achievement
        is_first_here = "first" in achievement

        # ── Time — still needs to come from the time cell ─────────────────
        time_td = row.find("td", class_=lambda c: c and "Results-table-td--time" in c)
        time_sec = extract_time_from_cell(time_td) if time_td else None

        runners.append(LatestRunner(
            position=position, name=name,
            time_sec=time_sec, club=club, gender=gender,
            run_count=run_count, is_pb=is_pb, age_category=age_category,
            is_first_here=is_first_here,
        ))

    m_count = sum(1 for r in runners if r.gender == "M")
    f_count = sum(1 for r in runners if r.gender == "F")
    t_count = sum(1 for r in runners if r.time_sec is not None)
    print(f"[DEBUG] Parsed {len(runners)} runners: {m_count} M, {f_count} F, "
          f"{t_count} with times.", file=sys.stderr)

    return runners


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def parse_volunteers(path: str) -> List[str]:
    """
    Parse volunteer names from the latest results page.
    parkrun lists them in a <p> tag inside a <div class="paddedt left">:
      <h3>Thanks to the volunteers</h3>
      <p>We are very grateful...: <a href="/shrewsbury/parkrunner/123">Name</a>, ...</p>
    Falls back to searching all <p> tags if the div isn't found.
    """
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    def names_from_p(p) -> List[str]:
        return [clean_name(a.get_text(strip=True))
                for a in p.find_all("a")
                if a.get("href") and "/parkrunner/" in a["href"]]

    # Strategy 1: find the h3 "Thanks to the volunteers" and grab the next <p>
    for h3 in soup.find_all("h3"):
        if "volunteer" in h3.get_text(strip=True).lower():
            # walk siblings until we find a <p> with parkrunner links
            for sib in h3.next_siblings:
                if hasattr(sib, "find_all"):
                    names = names_from_p(sib)
                    if names:
                        print(f"[DEBUG] Found {len(names)} volunteers via h3 sibling.", file=sys.stderr)
                        return names

    # Strategy 2: any <p> containing "grateful to the volunteers"
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True).lower()
        if "grateful to the volunteers" in text or "thanks to the volunteers" in text:
            names = names_from_p(p)
            if names:
                print(f"[DEBUG] Found {len(names)} volunteers via p text match.", file=sys.stderr)
                return names

    # Strategy 3: any <p> with 5+ parkrunner links (the volunteer list is always long)
    for p in soup.find_all("p"):
        names = names_from_p(p)
        if len(names) >= 5:
            print(f"[DEBUG] Found {len(names)} volunteers via p link count.", file=sys.stderr)
            return names

    print("[DEBUG] Volunteer list not found — tried all strategies.", file=sys.stderr)
    return []


def safe_mean_finishers(events: List[EventHistoryEntry]) -> Optional[float]:
    vals = [e.finishers for e in events if e.finishers is not None]
    return mean(vals) if vals else None


def safe_mean_volunteers(events: List[EventHistoryEntry]) -> Optional[float]:
    vals = [e.volunteers for e in events if e.volunteers is not None]
    return mean(vals) if vals else None


def find_biggest_changes(history: List[EventHistoryEntry]) -> tuple:
    max_inc = max_inc_from = max_dec = max_dec_from = None
    for prev, cur in zip(history, history[1:]):
        if prev.finishers is None or cur.finishers is None:
            continue
        diff = cur.finishers - prev.finishers
        if diff > 0 and (max_inc is None or diff > max_inc):
            max_inc, max_inc_from = diff, prev.event_number
        elif diff < 0 and (max_dec is None or diff < max_dec):
            max_dec, max_dec_from = diff, prev.event_number
    return max_inc, max_inc_from, max_dec, max_dec_from


def find_min_max_finishers(history: List[EventHistoryEntry]) -> tuple:
    non_null = [e for e in history if e.finishers is not None]
    if not non_null:
        return None, None
    return (min(non_null, key=lambda e: e.finishers or 0),
            max(non_null, key=lambda e: e.finishers or 0))


def find_streak_at_least(history: List[EventHistoryEntry], threshold: int) -> int:
    best = cur = 0
    for e in history:
        if e.finishers is not None and e.finishers >= threshold:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def find_fastest_firsts(history: List[EventHistoryEntry]) -> tuple:
    m_ev = [e for e in history if e.male_time_sec   is not None]
    f_ev = [e for e in history if e.female_time_sec is not None]
    return (min(m_ev, key=lambda e: e.male_time_sec)   if m_ev else None,
            min(f_ev, key=lambda e: e.female_time_sec) if f_ev else None)


def compute_time_trend(history: List[EventHistoryEntry],
                       runners: List[LatestRunner]) -> List[tuple]:
    last_52 = history[-52:] if len(history) >= 52 else history
    rows = []

    m_times = [e.male_time_sec   for e in last_52 if e.male_time_sec   is not None]
    f_times = [e.female_time_sec for e in last_52 if e.female_time_sec is not None]

    # Get this week's actual 1st male/female from parsed results — more reliable
    # than the history entry, which may have a slightly different value.
    males_with_time   = sorted([r for r in runners if r.gender == "M" and r.time_sec is not None],
                                key=lambda r: r.time_sec)
    females_with_time = sorted([r for r in runners if r.gender == "F" and r.time_sec is not None],
                                key=lambda r: r.time_sec)

    this_m = males_with_time[0].time_sec   if males_with_time   else None
    this_f = females_with_time[0].time_sec if females_with_time else None

    # Drop the most recent event from the history averages so we're comparing
    # this week against the preceding 51 events, not including itself.
    prior_m_times = [e.male_time_sec   for e in last_52[:-1] if e.male_time_sec   is not None]
    prior_f_times = [e.female_time_sec for e in last_52[:-1] if e.female_time_sec is not None]

    if prior_m_times and this_m is not None:
        avg  = mean(prior_m_times)
        diff = this_m - avg
        arrow = "▲ faster" if diff < 0 else "▼ slower"
        rows.append(("Male 1st finisher",
                     f"{format_sec_to_time(this_m)} &nbsp;({arrow} than 52-event avg "
                     f"{format_sec_to_time(int(avg))} by {format_sec_to_time(abs(int(diff)))})"))

    if prior_f_times and this_f is not None:
        avg  = mean(prior_f_times)
        diff = this_f - avg
        arrow = "▲ faster" if diff < 0 else "▼ slower"
        rows.append(("Female 1st finisher",
                     f"{format_sec_to_time(this_f)} &nbsp;({arrow} than 52-event avg "
                     f"{format_sec_to_time(int(avg))} by {format_sec_to_time(abs(int(diff)))})"))

    time_vals = [r.time_sec for r in runners if r.time_sec is not None]
    if time_vals:
        rows.append(("Field average", format_sec_to_time(int(mean(time_vals)))))
    return rows


def get_milestone_achievers(runners: List[LatestRunner]) -> List[LatestRunner]:
    """Runners who hit exactly a milestone count THIS run."""
    return [r for r in runners if r.run_count in MILESTONE_COUNTS]


def get_milestone_chasers(runners: List[LatestRunner]) -> List[LatestRunner]:
    """Runners who are ONE run away from a milestone (next run is the milestone)."""
    return [r for r in runners if r.run_count is not None
            and (r.run_count + 1) in MILESTONE_COUNTS]


def get_first_timers(runners: List[LatestRunner]) -> tuple:
    """
    Returns two lists:
      - first_ever: run_count == 1 (first parkrun anywhere)
      - first_here: first visit to this event but have run elsewhere before
    """
    first_ever = [r for r in runners if r.run_count == 1]
    first_here = [r for r in runners if r.is_first_here and r.run_count != 1]
    return first_ever, first_here


def get_pb_runners(runners: List[LatestRunner]) -> List[LatestRunner]:
    return [r for r in runners if r.is_pb]


def get_age_category_winners(runners: List[LatestRunner]) -> dict:
    """
    Return a dict of age_category -> first LatestRunner in that category
    (lowest position among runners with that category and a time).
    Only include recognised parkrun age categories.
    """
    winners: dict = {}
    for r in runners:
        if not r.age_category or r.time_sec is None:
            continue
        if r.age_category not in winners:
            winners[r.age_category] = r
        elif r.time_sec < winners[r.age_category].time_sec:
            winners[r.age_category] = r
    return winners


def get_clubs(runners: List[LatestRunner]) -> List[LatestRunner]:
    """Runners with a club affiliation."""
    return [r for r in runners if r.club]


def get_doublers(volunteers: List[str], runners: List[LatestRunner]) -> List[LatestRunner]:
    """Runners who also appear in the volunteer list."""
    def normalise(name: str) -> str:
        return re.sub(r'\s+', ' ', name.strip().upper())
    vol_set = {normalise(v) for v in volunteers}
    return [r for r in runners if normalise(r.name) in vol_set]


def get_volunteer_stats(history: List[EventHistoryEntry]) -> dict:
    """
    Compute volunteer statistics from event history.
    Returns a dict with keys: avg_4, avg_52, highest, lowest, trend_arrow
    """
    last_4  = history[-4:]
    last_52 = history[-52:] if len(history) >= 52 else history

    avg_4  = safe_mean_volunteers(last_4)
    avg_52 = safe_mean_volunteers(last_52)

    non_null = [e for e in history if e.volunteers is not None]
    highest  = max(non_null, key=lambda e: e.volunteers) if non_null else None
    lowest   = min(non_null, key=lambda e: e.volunteers) if non_null else None

    # Simple trend: compare latest vs 4-event avg
    latest_vol = history[-1].volunteers
    trend_arrow = ""
    if latest_vol is not None and avg_4 is not None:
        if latest_vol > avg_4 * 1.1:
            trend_arrow = "▲"
        elif latest_vol < avg_4 * 0.9:
            trend_arrow = "▼"
        else:
            trend_arrow = "→"

    return {
        "avg_4":        avg_4,
        "avg_52":       avg_52,
        "highest":      highest,
        "lowest":       lowest,
        "trend_arrow":  trend_arrow,
        "latest":       latest_vol,
    }


# ---------------------------------------------------------------------------
# Weather  (Open-Meteo – free, no API key)
# ---------------------------------------------------------------------------

def wmo_code_to_description(code: int) -> str:
    return {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Icy fog",
        51: "Light drizzle", 53: "Moderate drizzle", 55: "Heavy drizzle",
        61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
        71: "Light snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
        80: "Light showers", 81: "Moderate showers", 82: "Heavy showers",
        85: "Snow showers", 86: "Heavy snow showers",
        95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Thunderstorm + heavy hail",
    }.get(code, f"Conditions (code {code})")


def fetch_weather(event_date: datetime.date, lat: float = 52.7076, lon: float = -2.7521) -> dict:
    try:
        today    = datetime.date.today()
        date_str = event_date.strftime("%Y-%m-%d")
        if event_date < today:
            url = (
                f"https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={lat}&longitude={lon}"
                f"&start_date={date_str}&end_date={date_str}"
                f"&hourly=temperature_2m,precipitation,windspeed_10m,weathercode"
                f"&timezone=Europe%2FLondon"
            )
        else:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,"
                f"windspeed_10m_max,weathercode"
                f"&timezone=Europe%2FLondon"
                f"&start_date={date_str}&end_date={date_str}"
            )
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if "hourly" in data:
            h = data["hourly"]; idx = 9
            return {
                "condition": wmo_code_to_description(h["weathercode"][idx]),
                "temp":      f"{h['temperature_2m'][idx]:.1f}°C",
                "wind":      f"{h['windspeed_10m'][idx]:.0f} km/h",
                "rain":      f"{h['precipitation'][idx]:.1f} mm" if h["precipitation"][idx] > 0 else "Dry",
            }
        elif "daily" in data:
            d = data["daily"]
            temp = (d["temperature_2m_max"][0] + d["temperature_2m_min"][0]) / 2
            return {
                "condition": wmo_code_to_description(d["weathercode"][0]),
                "temp":      f"{temp:.1f}°C",
                "wind":      f"{d['windspeed_10m_max'][0]:.0f} km/h",
                "rain":      f"{d['precipitation_sum'][0]:.1f} mm" if d["precipitation_sum"][0] > 0 else "Dry",
            }
        return {"error": "Unexpected API response"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{event_name} parkrun – Event #{event_number}</title>
<style>
  :root {{
    --green:  #4CAF50;
    --dkgreen:#2e7d32;
    --light:  #f1f8e9;
    --mid:    #c8e6c9;
    --white:  #ffffff;
    --text:   #212121;
    --muted:  #555;
    --gold:   #f9a825;
    --silver: #90a4ae;
    --bronze: #a1887f;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    background: #e8f5e9;
    color: var(--text);
    padding: 16px;
  }}
  .page {{ max-width: 860px; margin: 0 auto; }}

  .header {{
    background: linear-gradient(135deg, var(--dkgreen), var(--green));
    color: white;
    border-radius: 12px;
    padding: 28px 32px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    position: relative;
    overflow: hidden;
  }}
  .header h1 {{ font-size: 1.8em; font-weight: 700; }}
  .header .subtitle {{ font-size: 1.1em; opacity: 0.9; margin-top: 4px; }}
  .header .event-bg {{ font-size: 6em; font-weight: 900; opacity: 0.12;
    position: absolute; right: 16px; top: -10px; line-height: 1; }}

  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
  @media (max-width: 600px) {{ .grid {{ grid-template-columns: 1fr; }} }}

  .card {{
    background: var(--white);
    border-radius: 10px;
    padding: 18px 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.07);
  }}
  .card.full {{ grid-column: 1 / -1; }}

  .card h2 {{
    font-size: 0.82em;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--dkgreen);
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .card .sub {{ font-size: 0.75em; color: var(--muted); margin-bottom: 8px; margin-top: -6px; }}

  .kv {{ display: flex; justify-content: space-between; padding: 5px 0;
    border-bottom: 1px solid #f0f0f0; font-size: 0.92em; }}
  .kv:last-child {{ border-bottom: none; }}
  .kv .label {{ color: var(--muted); }}
  .kv .value {{ font-weight: 600; text-align: right; max-width: 60%; }}
  .value.pos {{ color: #c62828; }}
  .value.neg {{ color: #2e7d32; }}

  .weather-strip {{ display: flex; gap: 20px; flex-wrap: wrap; }}
  .weather-item {{ display: flex; flex-direction: column; align-items: center; gap: 2px; }}
  .wi-icon  {{ font-size: 1.5em; }}
  .wi-value {{ font-weight: 700; font-size: 1em; }}
  .wi-label {{ font-size: 0.7em; color: var(--muted); text-transform: uppercase; }}

  table.results {{ width: 100%; border-collapse: collapse; font-size: 0.88em; }}
  table.results th {{
    background: var(--mid); color: var(--dkgreen);
    text-align: left; padding: 6px 8px; font-size: 0.78em;
    text-transform: uppercase; letter-spacing: 0.05em;
  }}
  table.results td {{ padding: 5px 8px; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }}
  table.results tr:last-child td {{ border-bottom: none; }}
  table.results tr:nth-child(even) td {{ background: #fafafa; }}
  .pos-cell {{ font-weight: 700; color: var(--dkgreen); white-space: nowrap; width: 52px; }}
  .time-cell {{ font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; width: 60px; }}
  .medal-1 {{ color: var(--gold); }}
  .medal-2 {{ color: var(--silver); }}
  .medal-3 {{ color: var(--bronze); }}

  .fun-list {{ list-style: none; font-size: 0.9em; }}
  .fun-list li {{ display: flex; align-items: center; padding: 4px 0;
    border-bottom: 1px solid #f5f5f5; gap: 8px; }}
  .fun-list li:last-child {{ border-bottom: none; }}
  .fl-pos  {{ color: var(--dkgreen); font-weight: 700; min-width: 48px; white-space: nowrap; }}
  .fl-name {{ flex: 1; }}
  .fl-time {{ font-variant-numeric: tabular-nums; color: var(--muted); white-space: nowrap; }}

  .empty {{ color: var(--muted); font-style: italic; font-size: 0.88em; }}

  .club-bar {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 0.9em; }}
  .club-bar:last-child {{ margin-bottom: 0; }}
  .club-name {{ min-width: 150px; }}
  .club-track {{ flex: 1; background: var(--mid); border-radius: 4px; height: 12px; overflow: hidden; }}
  .club-fill  {{ background: var(--green); height: 100%; border-radius: 4px; transition: width 0.3s; }}
  .club-count {{ min-width: 26px; text-align: right; font-weight: 700; color: var(--dkgreen); }}

  .record-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  @media (max-width: 500px) {{ .record-grid {{ grid-template-columns: 1fr; }} }}
  .record-item {{ background: var(--light); border-radius: 6px; padding: 10px 12px; }}
  .ri-label {{ font-size: 0.72em; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
  .ri-name  {{ font-weight: 700; font-size: 0.92em; margin: 2px 0; }}
  .ri-time  {{ font-size: 1.2em; font-weight: 800; color: var(--dkgreen); }}
  .ri-event {{ font-size: 0.75em; color: var(--muted); }}

  @media print {{
    .grid {{ display: block !important; }}
    .card {{ break-inside: avoid; margin-bottom: 12px; box-shadow: none; border: 1px solid #ddd; }}
    .card.full {{ display: block; }}
    .record-grid {{ display: block !important; }}
    .age-cat-grid {{ display: block !important; }}
    .weather-strip {{ display: block !important; }}
    .club-track {{ display: none; }}
    body {{ background: white !important; padding: 0; }}
    .page {{ max-width: 100%; }}
  }}

  /* Milestone / PB badges */
  .badge {{
    display: inline-block; font-size: 0.7em; font-weight: 700;
    padding: 1px 6px; border-radius: 10px; margin-left: 4px;
    vertical-align: middle; white-space: nowrap;
  }}
  .badge-gold   {{ background: #fff8e1; color: #e65100; border: 1px solid #ffcc80; }}
  .badge-pb     {{ background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; }}
  .badge-first  {{ background: #e3f2fd; color: #1565c0; border: 1px solid #90caf9; }}

  /* Age category table */
  .age-cat-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 6px;
  }}
  .age-cat-item {{
    background: var(--light); border-radius: 6px; padding: 7px 10px; font-size: 0.85em;
  }}
  .ac-cat   {{ font-size: 0.72em; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.05em; }}
  .ac-name  {{ font-weight: 700; margin: 1px 0; }}
  .ac-time  {{ color: var(--dkgreen); font-weight: 600; }}
</style>
</head>
<body>
<div class="page">

<div class="header">
  <div class="event-bg">#{event_number}</div>
  <h1>{event_name} parkrun</h1>
  <div class="subtitle">{date_long} &nbsp;·&nbsp; Event #{event_number}</div>
</div>

<div class="grid">
  <div class="card">
    <h2><span>🌤</span> Weather at ~9am</h2>
    <div class="weather-strip">{weather_html}</div>
  </div>
  <div class="card">
    <h2><span>👟</span> Attendance</h2>
    {attendance_html}
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2><span>⏱</span> Time Trend</h2>
    {trend_html}
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2><span>🙋</span> Volunteers</h2>
    {volunteers_html}
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2><span>🏃‍♂️</span> Top 10 Male Finishers</h2>
    {top10_male_html}
  </div>
  <div class="card">
    <h2><span>🏃‍♀️</span> Top 10 Female Finishers</h2>
    {top10_female_html}
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2><span>🕛</span> The Noughty Step</h2>
    <div class="sub">Finished on an exact minute</div>
    {noughty_html}
  </div>
  <div class="card">
    <h2><span>🔢</span> Multiples of 50</h2>
    <div class="sub">50th, 100th, 150th place…</div>
    {mult50_html}
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2><span>🔁</span> Rep-Digit Positions</h2>
    <div class="sub">11th, 22nd, 33rd, 111th…</div>
    {repdigit_html}
  </div>
  <div class="card">
    <h2><span>🏃</span> Clubs This Week</h2>
    {clubs_html}
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2><span>👥</span> Club Runners This Week</h2>
    <div class="sub">All affiliated runners grouped by club</div>
    {club_runners_html}
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2><span>🎽</span> Milestone Achievers This Week</h2>
    <div class="sub">Hit 25, 50, 100, 250 or 500 parkruns today — time for a t-shirt!</div>
    {milestone_achievers_html}
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2><span>🔜</span> Milestone Chasers</h2>
    <div class="sub">One run away from their next t-shirt</div>
    {milestone_chasers_html}
  </div>
  <div class="card">
    <h2><span>🆕</span> First Timers</h2>
    <div class="sub">Welcome to parkrun!</div>
    {first_timers_html}
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2><span>⚡</span> Personal Bests This Week</h2>
    {pb_html}
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2><span>📋</span> Age Category Winners</h2>
    {age_cat_html}
  </div>
</div>

<div class="grid">
  <div class="card full">
    <h2><span>📊</span> All-Time Records</h2>
    {alltime_html}
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2><span>🏆</span> Course Records (All Time)</h2>
    {cr_alltime_html}
  </div>
  <div class="card">
    <h2><span>🏆</span> Fastest First Finisher (Last 52)</h2>
    {cr_52_html}
  </div>
</div>

<footer>Generated {generated} &nbsp;·&nbsp; Data from parkrun.org.uk &nbsp;·&nbsp; Weather from Open-Meteo</footer>
</div>
</body>
</html>"""


def condition_icon(condition: str) -> str:
    c = condition.lower()
    if "thunder" in c:              return "⛈️"
    if "heavy rain" in c or "heavy shower" in c: return "🌧️"
    if "rain" in c or "shower" in c or "drizzle" in c: return "🌦️"
    if "snow" in c:                 return "❄️"
    if "fog" in c:                  return "🌫️"
    if "overcast" in c:             return "☁️"
    if "partly cloudy" in c:        return "⛅"
    if "mainly clear" in c or "clear" in c: return "☀️"
    return "🌤️"


def rain_icon(rain: str) -> str:
    return "🌧️" if rain != "Dry" else "✅"


def build_weather_html(w: dict) -> str:
    if "error" in w:
        return f'<span class="empty">Weather unavailable: {w["error"]}</span>'
    condition = w.get("condition", "—")
    rain      = w.get("rain", "—")
    items = [
        (condition_icon(condition), "Conditions", condition),
        ("🌡️",                      "Temp",       w.get("temp", "—")),
        ("💨",                      "Wind",       w.get("wind", "—")),
        (rain_icon(rain),           "Rain",       rain),
    ]
    return "\n".join(
        f'<div class="weather-item">'
        f'<span class="wi-icon">{icon}</span>'
        f'<span class="wi-value">{val}</span>'
        f'<span class="wi-label">{label}</span>'
        f'</div>'
        for icon, label, val in items
    )


def build_attendance_html(latest, prev_event) -> str:
    rows = []
    fin = latest.finishers
    rows.append(("Finishers", str(fin) if fin is not None else "N/A", ""))
    if latest.volunteers is not None:
        rows.append(("Volunteers", str(latest.volunteers), ""))
    if prev_event and prev_event.finishers is not None and fin is not None:
        diff = fin - prev_event.finishers
        sign = "+" if diff >= 0 else ""
        cls  = "neg" if diff > 0 else ("pos" if diff < 0 else "")
        rows.append((f"vs #{prev_event.event_number}", f"{sign}{diff}", cls))
    return "\n".join(
        f'<div class="kv"><span class="label">{k}</span>'
        f'<span class="value {cls}">{v}</span></div>'
        for k, v, cls in rows
    )


def build_trend_html(trend_rows: List[tuple]) -> str:
    if not trend_rows:
        return '<span class="empty">No trend data available.</span>'
    return "\n".join(
        f'<div class="kv"><span class="label">{label}</span>'
        f'<span class="value">{val}</span></div>'
        for label, val in trend_rows
    )


def build_top10_html(runners: List[LatestRunner]) -> str:
    if not runners:
        return '<span class="empty">No data available — check debug output.</span>'
    medal = {1: "medal-1", 2: "medal-2", 3: "medal-3"}
    rows = []
    for i, r in enumerate(runners, 1):
        mcls = medal.get(i, "")
        t    = format_sec_to_time(r.time_sec) if r.time_sec else "—"
        rows.append(
            f'<tr>'
            f'<td class="pos-cell {mcls}">{ordinal(r.position)}</td>'
            f'<td>{r.name}</td>'
            f'<td class="time-cell">{t}</td>'
            f'</tr>'
        )
    return (
        '<table class="results"><thead><tr>'
        '<th>Pos</th><th>Name</th><th>Time</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows)
        + "</tbody></table>"
    )


def build_fun_list_html(runners: List[LatestRunner], show_time: bool = True) -> str:
    if not runners:
        return '<span class="empty">None this week.</span>'
    items = []
    for r in runners:
        time_part = ""
        if show_time and r.time_sec is not None:
            time_part = f'<span class="fl-time">{format_sec_to_time(r.time_sec)}</span>'
        items.append(
            f'<li>'
            f'<span class="fl-pos">{ordinal(r.position)}</span>'
            f'<span class="fl-name">{r.name}</span>'
            f'{time_part}'
            f'</li>'
        )
    return '<ul class="fun-list">' + "\n".join(items) + "</ul>"


def build_clubs_html(club_counts: Counter) -> str:
    if not club_counts:
        return '<span class="empty">No club data.</span>'
    top   = club_counts.most_common()
    max_c = top[0][1] if top else 1
    return "\n".join(
        f'<div class="club-bar">'
        f'<span class="club-name">{name}</span>'
        f'<div class="club-track"><div class="club-fill" style="width:{int(count/max_c*100)}%"></div></div>'
        f'<span class="club-count">{count}</span>'
        f'</div>'
        for name, count in top
    )


def build_alltime_html(highest, lowest, max_inc, max_inc_from,
                       max_dec, max_dec_from, streak_500) -> str:
    rows = []
    if highest:
        rows.append(("Record attendance",
                     f"{highest.finishers} — event #{highest.event_number}, {highest.date:%d/%m/%Y}"))
    if lowest:
        rows.append(("Lowest attendance",
                     f"{lowest.finishers} — event #{lowest.event_number}, {lowest.date:%d/%m/%Y}"))
    if max_inc is not None and max_inc_from is not None:
        rows.append(("Biggest jump", f"+{max_inc} finishers (#{max_inc_from} → #{max_inc_from+1})"))
    if max_dec is not None and max_dec_from is not None:
        rows.append(("Biggest drop", f"{max_dec} finishers (#{max_dec_from} → #{max_dec_from+1})"))
    if streak_500 > 1:
        rows.append(("500+ streak", f"{streak_500} events in a row"))
    if not rows:
        return '<span class="empty">No data.</span>'
    return "\n".join(
        f'<div class="kv"><span class="label">{k}</span>'
        f'<span class="value">{v}</span></div>'
        for k, v in rows
    )


def build_cr_html(fastest_male, fastest_female) -> str:
    items = []
    if fastest_male and fastest_male.male_time_sec is not None:
        items.append(("Male", fastest_male.male_name,
                      format_sec_to_time(fastest_male.male_time_sec),
                      f"Event #{fastest_male.event_number}, {fastest_male.date:%d/%m/%Y}"))
    if fastest_female and fastest_female.female_time_sec is not None:
        items.append(("Female", fastest_female.female_name,
                      format_sec_to_time(fastest_female.female_time_sec),
                      f"Event #{fastest_female.event_number}, {fastest_female.date:%d/%m/%Y}"))
    if not items:
        return '<span class="empty">No data.</span>'
    parts = [
        f'<div class="record-item">'
        f'<div class="ri-label">{g}</div>'
        f'<div class="ri-name">{name}</div>'
        f'<div class="ri-time">{time}</div>'
        f'<div class="ri-event">{event}</div>'
        f'</div>'
        for g, name, time, event in items
    ]
    return '<div class="record-grid">' + "\n".join(parts) + "</div>"


def build_milestone_achievers_html(runners: List[LatestRunner]) -> str:
    if not runners:
        return '<span class="empty">None this week.</span>'
    items = []
    for r in runners:
        t = format_sec_to_time(r.time_sec) if r.time_sec else "—"
        items.append(
            f'<li>'
            f'<span class="fl-pos">{ordinal(r.position)}</span>'
            f'<span class="fl-name">{r.name}'
            f'<span class="badge badge-gold">🎽 {r.run_count} parkruns!</span>'
            f'</span>'
            f'<span class="fl-time">{t}</span>'
            f'</li>'
        )
    return '<ul class="fun-list">' + "\n".join(items) + "</ul>"


def build_milestone_chasers_html(runners: List[LatestRunner]) -> str:
    if not runners:
        return '<span class="empty">None this week.</span>'
    items = []
    for r in runners:
        next_milestone = r.run_count + 1
        items.append(
            f'<li>'
            f'<span class="fl-pos">{ordinal(r.position)}</span>'
            f'<span class="fl-name">{r.name}'
            f'<span class="badge badge-gold">→ {next_milestone}</span>'
            f'</span>'
            f'<span class="fl-time">{r.run_count} done</span>'
            f'</li>'
        )
    return '<ul class="fun-list">' + "\n".join(items) + "</ul>"


def build_first_timers_html(first_ever: List[LatestRunner],
                            first_here: List[LatestRunner]) -> str:
    def runner_list(runners, badge_text, badge_class):
        if not runners:
            return '<span class="empty">None this week.</span>'
        items = []
        for r in runners:
            t = format_sec_to_time(r.time_sec) if r.time_sec else "—"
            items.append(
                f'<li>'
                f'<span class="fl-pos">{ordinal(r.position)}</span>'
                f'<span class="fl-name">{r.name}'
                f'<span class="badge {badge_class}">{badge_text}</span>'
                f'</span>'
                f'<span class="fl-time">{t}</span>'
                f'</li>'
            )
        return '<ul class="fun-list">' + "\n".join(items) + "</ul>"

    parts = []
    parts.append(
        '<div style="font-size:0.78em;font-weight:700;text-transform:uppercase;'
        'letter-spacing:0.07em;color:var(--dkgreen);margin-bottom:6px">'
        '🌍 First parkrun ever</div>'
        + runner_list(first_ever, "1st ever!", "badge-first")
    )
    parts.append(
        '<div style="font-size:0.78em;font-weight:700;text-transform:uppercase;'
        'letter-spacing:0.07em;color:var(--dkgreen);margin-top:12px;margin-bottom:6px">'
        '📍 First time at this event</div>'
        + runner_list(first_here, "1st here!", "badge-pb")
    )
    return "\n".join(parts)


def build_pb_html(runners: List[LatestRunner]) -> str:
    if not runners:
        return '<span class="empty">None recorded this week.</span>'
    items = []
    for r in runners:
        t = format_sec_to_time(r.time_sec) if r.time_sec else "—"
        items.append(
            f'<li>'
            f'<span class="fl-pos">{ordinal(r.position)}</span>'
            f'<span class="fl-name">{r.name}'
            f'<span class="badge badge-pb">PB</span>'
            f'</span>'
            f'<span class="fl-time">{t}</span>'
            f'</li>'
        )
    return '<ul class="fun-list">' + "\n".join(items) + "</ul>"


def build_age_cat_html(winners: dict) -> str:
    if not winners:
        return '<span class="empty">No age category data available.</span>'
    # Sort: females (SW/VW) first then males (SM/VM), then alphabetically within
    def cat_sort_key(cat: str):
        gender_order = 0 if cat[1].upper() == "W" else 1
        return (gender_order, cat)
    sorted_cats = sorted(winners.keys(), key=cat_sort_key)
    parts = []
    for cat in sorted_cats:
        r = winners[cat]
        t = format_sec_to_time(r.time_sec) if r.time_sec else "—"
        parts.append(
            f'<div class="age-cat-item">'
            f'<div class="ac-cat">{cat}</div>'
            f'<div class="ac-name">{r.name}</div>'
            f'<div class="ac-time">{t} <span style="color:var(--muted);font-size:0.85em">({ordinal(r.position)})</span></div>'
            f'</div>'
        )
    return '<div class="age-cat-grid">' + "\n".join(parts) + "</div>"


def build_locals_and_tourists_html(locals_: List[LatestRunner], tourists: List[LatestRunner]) -> str:
    def club_group_html(runners: List[LatestRunner], heading: str, heading_color: str) -> str:
        if not runners:
            return f'<div style="color:var(--muted);font-style:italic;font-size:0.88em;margin-bottom:12px">No {heading.lower()} this week.</div>'
        by_club: dict = {}
        for r in runners:
            by_club.setdefault(r.club, []).append(r)
        items = []
        for club, members in sorted(by_club.items(), key=lambda x: (-len(x[1]), x[0])):
            names = ", ".join(m.name for m in members)
            count_badge = f'<span class="badge badge-first">{len(members)}</span>' if len(members) > 1 else ""
            items.append(
                f'<li>'
                f'<span class="fl-pos" style="min-width:180px;color:{heading_color}">'
                f'{club}{count_badge}</span>'
                f'<span class="fl-name">{names}</span>'
                f'</li>'
            )
        return (
            f'<div style="font-size:0.78em;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.07em;color:{heading_color};margin-bottom:6px">{heading}</div>'
            f'<ul class="fun-list" style="margin-bottom:16px">' + "\n".join(items) + "</ul>"
        )

    return (
        club_group_html(locals_,  "Local clubs", "var(--dkgreen)") +
        club_group_html(tourists, "Visitors",    "#1565c0")
    )


def build_club_runners_html(affiliated: List[LatestRunner]) -> str:
    if not affiliated:
        return '<span class="empty">No club runners this week.</span>'
    by_club: dict = {}
    for r in affiliated:
        by_club.setdefault(r.club, []).append(r)
    items = []
    for club, members in sorted(by_club.items(), key=lambda x: (-len(x[1]), x[0])):
        names = ", ".join(m.name for m in members)
        count_badge = f'<span class="badge badge-first">{len(members)}</span>' if len(members) > 1 else ""
        items.append(
            f'<li>'
            f'<span class="fl-pos" style="min-width:180px">'
            f'{club}{count_badge}</span>'
            f'<span class="fl-name">{names}</span>'
            f'</li>'
        )
    return '<ul class="fun-list">' + "\n".join(items) + "</ul>"


def build_volunteer_html(volunteers: List[str], doublers: List[LatestRunner],
                         stats: dict) -> str:
    parts = []

    # ── Stats strip ───────────────────────────────────────────────────────────
    kv_rows = []
    if stats["latest"] is not None:
        arrow = stats["trend_arrow"]
        color = {"▲": "#2e7d32", "▼": "#c62828", "→": "#555"}.get(arrow, "#555")
        kv_rows.append(("Volunteers this week",
                         f'<span style="font-weight:700">{stats["latest"]}</span>'
                         f'&nbsp;<span style="color:{color}">{arrow}</span>'))
    if stats["avg_4"] is not None:
        kv_rows.append(("4-week avg",  f'{stats["avg_4"]:.1f}'))
    if stats["avg_52"] is not None:
        kv_rows.append(("52-week avg", f'{stats["avg_52"]:.1f}'))
    if stats["highest"]:
        h = stats["highest"]
        kv_rows.append(("Most ever",
                         f'{h.volunteers} — event #{h.event_number}, {h.date:%d/%m/%Y}'))
    if stats["lowest"]:
        l = stats["lowest"]
        kv_rows.append(("Fewest ever",
                         f'{l.volunteers} — event #{l.event_number}, {l.date:%d/%m/%Y}'))

    parts.append(
        '<div style="margin-bottom:14px">' +
        "\n".join(
            f'<div class="kv"><span class="label">{k}</span>'
            f'<span class="value">{v}</span></div>'
            for k, v in kv_rows
        ) + "</div>"
    )

    # ── Doublers ──────────────────────────────────────────────────────────────
    if doublers:
        d_parts = []
        for r in doublers:
            if r.time_sec:
                time_badge = f"<span class='badge badge-pb'>{format_sec_to_time(r.time_sec)}</span>"
            else:
                time_badge = ""
            d_parts.append(f'<strong>{r.name}</strong>&nbsp;{time_badge}')
        d_names = ", ".join(d_parts)
        parts.append(
            f'<div style="margin-bottom:14px">'
            f'<div style="font-size:0.78em;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.07em;color:var(--dkgreen);margin-bottom:6px">'
            f'🤸 Doublers — ran &amp; volunteered ({len(doublers)})</div>'
            f'<div style="font-size:0.9em">{d_names}</div>'
            f'</div>'
        )

    # ── Full volunteer list ───────────────────────────────────────────────────
    if volunteers:
        # Render as a flowing wrapped list of names
        name_spans = " &nbsp;·&nbsp; ".join(
            f'<span style="white-space:nowrap">{v}</span>'
            for v in sorted(volunteers)
        )
        parts.append(
            f'<div>'
            f'<div style="font-size:0.78em;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.07em;color:var(--dkgreen);margin-bottom:6px">'
            f'🙌 This week\'s volunteers ({len(volunteers)})</div>'
            f'<div style="font-size:0.85em;line-height:1.8;color:#333">{name_spans}</div>'
            f'</div>'
        )
    else:
        parts.append('<span class="empty">Volunteer list not found in results page.</span>')

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Master builder
# ---------------------------------------------------------------------------

def build_weekly_summary_html(history_path: str, latest_path: str, event_name: str = "parkrun", lat: float = 52.7076, lon: float = -2.7521) -> str:
    history = parse_event_history(history_path)
    if not history:
        raise RuntimeError("No event history parsed.")

    history.sort(key=lambda e: e.date)
    latest     = history[-1]
    today      = latest.date
    last_4     = history[-4:]
    last_52    = history[-52:] if len(history) >= 52 else history
    prev_event = history[-2] if len(history) >= 2 else None

    max_inc, max_inc_from, max_dec, max_dec_from = find_biggest_changes(history)
    lowest, highest  = find_min_max_finishers(history)
    streak_500       = find_streak_at_least(history, 500)
    fastest_male, fastest_female = find_fastest_firsts(history)
    fm_52, ff_52     = find_fastest_firsts(last_52)

    latest_runners = parse_latest_results(latest_path)
    volunteers     = parse_volunteers(latest_path)
    vol_stats      = get_volunteer_stats(history)
    doublers       = get_doublers(volunteers, latest_runners)

    noughty   = [r for r in latest_runners if r.time_sec is not None and r.time_sec % 60 == 0]
    mult_50   = [r for r in latest_runners if r.position % 50 == 0]
    rep_digit = [r for r in latest_runners if is_rep_digit_position(r.position)]

    males   = sorted([r for r in latest_runners if r.gender == "M" and r.time_sec is not None],
                     key=lambda r: r.time_sec)
    females = sorted([r for r in latest_runners if r.gender == "F" and r.time_sec is not None],
                     key=lambda r: r.time_sec)

    milestone_achievers = get_milestone_achievers(latest_runners)
    milestone_chasers   = get_milestone_chasers(latest_runners)
    first_ever, first_here = get_first_timers(latest_runners)
    pb_runners          = get_pb_runners(latest_runners)
    age_cat_winners     = get_age_category_winners(latest_runners)
    affiliated          = get_clubs(latest_runners)

    return HTML_TEMPLATE.format(
        event_number             = latest.event_number,
        event_name               = event_name,
        date_long                = today.strftime("%A, %d %B %Y"),
        generated                = datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        weather_html             = build_weather_html(fetch_weather(today, lat=lat, lon=lon)),
        attendance_html          = build_attendance_html(latest, prev_event),
        volunteers_html          = build_volunteer_html(volunteers, doublers, vol_stats),
        trend_html               = build_trend_html(compute_time_trend(history, latest_runners)),
        top10_male_html          = build_top10_html(males[:10]),
        top10_female_html        = build_top10_html(females[:10]),
        noughty_html             = build_fun_list_html(noughty),
        mult50_html              = build_fun_list_html(mult_50),
        repdigit_html            = build_fun_list_html(rep_digit),
        clubs_html               = build_clubs_html(Counter(r.club for r in affiliated)),
        club_runners_html        = build_club_runners_html(affiliated),
        milestone_achievers_html = build_milestone_achievers_html(milestone_achievers),
        milestone_chasers_html   = build_milestone_chasers_html(milestone_chasers),
        first_timers_html        = build_first_timers_html(first_ever, first_here),
        pb_html                  = build_pb_html(pb_runners),
        age_cat_html             = build_age_cat_html(age_cat_winners),
        alltime_html             = build_alltime_html(highest, lowest, max_inc, max_inc_from,
                                                      max_dec, max_dec_from, streak_500),
        cr_alltime_html          = build_cr_html(fastest_male, fastest_female),
        cr_52_html               = build_cr_html(fm_52, ff_52),
    )


# ---------------------------------------------------------------------------
# Entry point
# Output: writes directly to /data/summary-YYYY-MM-DD.html so the bash
# script's stdout redirect never captures HTML content.
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) not in (3, 4):
        print("Usage: parkrun_summary.py EVENTHISTORY_HTML LATEST_HTML [OUTPUT_HTML]",
              file=sys.stderr)
        sys.exit(1)

    history_path = sys.argv[1]
    latest_path  = sys.argv[2]

    # If a third arg is given use it; otherwise auto-generate alongside the data files
    if len(sys.argv) == 4:
        output_path = sys.argv[3]
    else:
        date_str    = datetime.date.today().strftime("%Y-%m-%d")
        data_dir    = os.path.dirname(os.path.abspath(latest_path))
        output_path = os.path.join(data_dir, f"summary-{date_str}.html")

    try:
        html = build_weekly_summary_html(history_path, latest_path)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        # Print ONLY the output path to stdout — this is what the bash script captures
        print(output_path)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
