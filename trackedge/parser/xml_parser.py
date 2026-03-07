"""
trackedge/parser/xml_parser.py
-------------------------------
Parse Equibase-style PPS XML files into the TrackEdge race/horse dict format.

Expected XML schema (sa20260227ppsXML.xml and compatible fixtures):

  <ppsdata>
    <race number="1" track="SA" date="2026-02-27" surface="D"
          purse="60000" class_rating="60000" type="Allowance">
      <horse program="1" name="Iron Velocity" morn_odds="2-1"
             jockey="J. Rosario" trainer="B. Baffert"
             jockey_win_rate="0.20" trainer_win_rate="0.25"
             jockey_starts="60" trainer_starts="120"
             starts="12" avg_class_rating="60000" pace_style="EP">
        <pastperformances>
          <pp racedate="2026-01-15" surface="D" speedfigur="88"
              lenback1="1.5" lenback2="2.0"
              position1="1" position2="2"
              pacefigure="92" purse="60000"/>
        </pastperformances>
        <workouts>
          <workout date="2026-02-20" furlongs="4" time="0:47.2"
                   rank="2" total="8" days_ago="7"/>
        </workouts>
      </horse>
    </race>
  </ppsdata>

Public API:
  parse_xml(filepath: str) -> list[dict]

Each returned race dict contains:
  number, track, date, surface, purse, class_rating, type, horses

Each horse dict contains:
  program, name, morn_odds, speed_ratings, pace_style, avg_pace,
  avg_lenback, days_since_last_race, recent_workouts, jockey_win_rate,
  trainer_win_rate, jockey_starts, trainer_starts, starts,
  avg_class_rating, past_performances, workouts
"""

import xml.etree.ElementTree as ET
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_xml(filepath: str) -> list:
    """Parse a PPS XML file and return a list of race dicts.

    Args:
        filepath: Path to the XML file.

    Returns:
        List of race dicts, sorted by race number.

    Raises:
        FileNotFoundError: If filepath does not exist.
        ET.ParseError: If the XML is malformed.
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    # Handle both <ppsdata> root and root-as-race variants
    if root.tag == "race":
        race_elements = [root]
    else:
        race_elements = root.findall("race")

    races = [_parse_race(el) for el in race_elements]
    races.sort(key=lambda r: r["number"])
    return races


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_race(el: ET.Element) -> dict:
    """Parse a <race> element into a race dict."""
    purse = _int(el.get("purse", "0"))
    class_rating = _int(el.get("class_rating", str(purse)))
    return {
        "number": _int(el.get("number", "0")),
        "track": el.get("track", ""),
        "date": el.get("date", ""),
        "surface": el.get("surface", "D"),
        "purse": purse,
        "class_rating": class_rating,
        "type": el.get("type", "Normal"),
        "horses": [_parse_horse(h) for h in el.findall("horse")],
    }


def _parse_horse(el: ET.Element) -> dict:
    """Parse a <horse> element into a horse dict."""
    pps = _parse_pps(el.find("pastperformances"))
    workouts = _parse_workouts(el.find("workouts"))

    # Derive speed_ratings from last 3 speedfigur values
    speed_ratings = [pp["speedfigur"] for pp in pps if pp.get("speedfigur") is not None][:3]
    if not speed_ratings:
        speed_ratings = [0]

    # avg_pace from pacefigure; avg_lenback from lenback1
    avg_pace_vals = [pp["pacefigure"] for pp in pps if pp.get("pacefigure") is not None]
    avg_pace = sum(avg_pace_vals) / len(avg_pace_vals) if avg_pace_vals else 0.0

    avg_lb_vals = [pp["lenback1"] for pp in pps if pp.get("lenback1") is not None]
    avg_lenback = sum(avg_lb_vals) / len(avg_lb_vals) if avg_lb_vals else 0.0

    # days since last race — from most recent PP racedate
    days_since = _days_since_last(pps)

    # recent_workouts for scoring engine
    recent_workouts = [
        {"days_ago": w.get("days_ago", 0), "rank": w.get("rank", 5)}
        for w in workouts
    ]

    return {
        "program": _int(el.get("program", "0")),
        "name": el.get("name", ""),
        "morn_odds": el.get("morn_odds", "99-1"),
        "speed_ratings": speed_ratings,
        "pace_style": el.get("pace_style", "P"),
        "avg_pace": avg_pace,
        "avg_lenback": avg_lenback,
        "days_since_last_race": days_since,
        "recent_workouts": recent_workouts,
        "jockey_win_rate": _float(el.get("jockey_win_rate", "0.15")),
        "trainer_win_rate": _float(el.get("trainer_win_rate", "0.15")),
        "jockey_starts": _int(el.get("jockey_starts", "0")),
        "trainer_starts": _int(el.get("trainer_starts", "0")),
        "starts": _int(el.get("starts", "0")),
        "avg_class_rating": _int(el.get("avg_class_rating", "0")),
        "jockey": el.get("jockey", ""),
        "trainer": el.get("trainer", ""),
        "past_performances": pps,
        "workouts": workouts,
    }


def _parse_pps(container: ET.Element | None) -> list:
    if container is None:
        return []
    result = []
    for pp in container.findall("pp"):
        result.append({
            "racedate": pp.get("racedate", ""),
            "surface": pp.get("surface", "D"),
            "speedfigur": _int_or_none(pp.get("speedfigur")),
            "lenback1": _float_or_none(pp.get("lenback1")),
            "lenback2": _float_or_none(pp.get("lenback2")),
            "position1": _int_or_none(pp.get("position1")),
            "position2": _int_or_none(pp.get("position2")),
            "pacefigure": _int_or_none(pp.get("pacefigure")),
            "purse": _int_or_none(pp.get("purse")),
        })
    return result


def _parse_workouts(container: ET.Element | None) -> list:
    if container is None:
        return []
    result = []
    for wo in container.findall("workout"):
        result.append({
            "date": wo.get("date", ""),
            "furlongs": _float(wo.get("furlongs", "4")),
            "time": wo.get("time", ""),
            "rank": _int(wo.get("rank", "5")),
            "total": _int(wo.get("total", "10")),
            "days_ago": _int(wo.get("days_ago", "0")),
        })
    return result


def _days_since_last(pps: list) -> int:
    """Return days between today and the most recent PP racedate, or 30 if unknown."""
    if not pps:
        return 30
    try:
        most_recent = datetime.strptime(pps[0]["racedate"], "%Y-%m-%d").date()
        return (date.today() - most_recent).days
    except (ValueError, TypeError):
        return 30


# ---------------------------------------------------------------------------
# Type coercion helpers
# ---------------------------------------------------------------------------

def _int(s) -> int:
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return 0


def _float(s) -> float:
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return 0.0


def _int_or_none(s):
    if s is None:
        return None
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def _float_or_none(s):
    if s is None:
        return None
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return None
