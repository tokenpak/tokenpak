"""
TrackEdge Integration Test Suite — conftest.py
Fixtures for integration tests across all pipeline stages.
"""

import os

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
XML_DATA_DIR = os.path.join(REPO_ROOT, "data", "raw_xml")
SA_XML_PATH = os.path.join(XML_DATA_DIR, "sa20260227ppsXML.xml")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def xml_available():
    """Return True if the SA XML file is present on this machine."""
    return os.path.isfile(SA_XML_PATH)


def xml_parser_available():
    """Return True if the xml_parser module can be imported."""
    try:
        import trackedge.parser.xml_parser  # noqa: F401
        return True
    except ImportError:
        return False


def edge_engine_available():
    """Return True if the edge_engine module can be imported."""
    try:
        import trackedge.model.edge_engine  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Synthetic 9-Race Data
# (Used when real XML is unavailable — mirrors expected parser output)
# ---------------------------------------------------------------------------

def _make_horse(prog, name, morn_odds="4-1", speed_ratings=None, pace_style="EP",
                days_since=14, jwin=0.18, twin=0.20, jstarts=50, tstarts=100,
                avg_class=50000, workouts=None, starts=10, past_performances=None):
    if speed_ratings is None:
        speed_ratings = [85, 80, 75]
    if workouts is None:
        workouts = [{"days_ago": 7, "rank": 3}]
    if past_performances is None:
        past_performances = [
            {"racedate": "2026-01-01", "surface": "D", "speedfigur": 85,
             "lenback1": 2.0, "lenback2": 3.0, "position1": 2, "position2": 3,
             "pacefigure": 90, "purse": 50000}
        ]
    return {
        "program": prog,
        "name": name,
        "morn_odds": morn_odds,
        "speed_ratings": speed_ratings,
        "pace_style": pace_style,
        "avg_pace": 2.5,
        "avg_lenback": 3.0,
        "days_since_last_race": days_since,
        "recent_workouts": workouts,
        "jockey_win_rate": jwin,
        "trainer_win_rate": twin,
        "jockey_starts": jstarts,
        "trainer_starts": tstarts,
        "starts": starts,
        "avg_class_rating": avg_class,
        "past_performances": past_performances,
    }


def _make_race(number, horses=None, surface="D", purse=60000, class_rating=60000, type_="Normal"):
    if horses is None:
        horses = [
            _make_horse(1, "Alpha", morn_odds="2-1", speed_ratings=[90, 85, 80]),
            _make_horse(2, "Bravo", morn_odds="4-1", speed_ratings=[80, 78, 75]),
            _make_horse(3, "Charlie", morn_odds="6-1", speed_ratings=[75, 72, 70]),
            _make_horse(4, "Delta", morn_odds="10-1", speed_ratings=[70, 68, 65]),
            _make_horse(5, "Echo", morn_odds="15-1", speed_ratings=[65, 62, 60]),
        ]
    return {
        "number": number,
        "track": "SA",
        "date": "2026-02-27",
        "surface": surface,
        "purse": purse,
        "class_rating": class_rating,
        "type": type_,
        "horses": horses,
    }


@pytest.fixture(scope="session")
def synthetic_9_races():
    """9-race synthetic dataset mirroring expected parser output."""
    return [_make_race(n) for n in range(1, 10)]


@pytest.fixture(scope="session")
def sa_xml_path():
    """Path to the Santa Anita 2026-02-27 XML file. Skip if unavailable."""
    if not xml_available():
        pytest.skip(
            f"XML data file not available: {SA_XML_PATH}\n"
            "Note: File was downloaded to Windows. Sync to Linux to run XML-based tests."
        )
    return SA_XML_PATH


@pytest.fixture(scope="session")
def xml_parser():
    """Import xml_parser. Skip if module doesn't exist yet."""
    if not xml_parser_available():
        pytest.skip(
            "trackedge.parser.xml_parser not implemented.\n"
            "Required module path: trackedge/parser/xml_parser.py"
        )
    from trackedge.parser.xml_parser import parse_xml
    return parse_xml


@pytest.fixture(scope="session")
def edge_engine():
    """Import edge engine. Skip if module doesn't exist yet."""
    if not edge_engine_available():
        pytest.skip(
            "trackedge.model.edge_engine not implemented.\n"
            "Required: edge_calculation(), market_probability(), EdgeResult dataclass"
        )
    from trackedge.model.edge_engine import edge_calculation, market_probability
    return edge_calculation, market_probability


@pytest.fixture(scope="session")
def standard_bankroll():
    return 1000.0
