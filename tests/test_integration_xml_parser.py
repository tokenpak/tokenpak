"""
test_xml_parser.py — TrackEdge XML Parser Integration Tests

STATUS: BLOCKED — trackedge/parser/xml_parser.py not yet implemented.
XML data file (sa20260227ppsXML.xml) not synced to Linux.

All tests in this module will skip automatically. They document the
required behavior for whoever implements the parser.

To unblock:
  1. Implement trackedge/parser/xml_parser.py with parse_xml()
  2. Sync sa20260227ppsXML.xml to data/raw_xml/
  3. Rerun: pytest tests/test_xml_parser.py -v
"""

import pytest
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Module availability checks
# ---------------------------------------------------------------------------

XML_PARSER_AVAILABLE = False
XML_FILE_AVAILABLE = False
try:
    from trackedge.parser.xml_parser import parse_xml  # noqa: F401
    XML_PARSER_AVAILABLE = True
except ImportError:
    pass

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw_xml")
SA_XML = os.path.join(DATA_DIR, "sa20260227ppsXML.xml")
XML_FILE_AVAILABLE = os.path.isfile(SA_XML)

SKIP_PARSER = pytest.mark.skipif(
    not XML_PARSER_AVAILABLE,
    reason="MISSING: trackedge/parser/xml_parser.py — implement parse_xml() first",
)
SKIP_XML = pytest.mark.skipif(
    not XML_FILE_AVAILABLE,
    reason=(
        "MISSING: data/raw_xml/sa20260227ppsXML.xml\n"
        "File is on Windows; sync to Linux to enable these tests"
    ),
)


# ---------------------------------------------------------------------------
# 1. Parser Module Tests
# ---------------------------------------------------------------------------

class TestXmlParserModule:

    @SKIP_PARSER
    def test_parse_xml_function_exists(self):
        """parse_xml() must be importable from trackedge.parser.xml_parser"""
        from trackedge.parser.xml_parser import parse_xml
        assert callable(parse_xml)

    @SKIP_PARSER
    @SKIP_XML
    def test_parse_9_races(self):
        """Verify parser extracts all 9 races from SA XML."""
        races = parse_xml(SA_XML)
        assert len(races) == 9, f"Expected 9 races, got {len(races)}"

    @SKIP_PARSER
    @SKIP_XML
    def test_races_are_numbered_sequentially(self):
        """Race numbers must run 1–9."""
        races = parse_xml(SA_XML)
        numbers = [r.get("number") for r in races]
        assert numbers[0] == 1
        assert numbers[-1] == 9
        assert sorted(numbers) == list(range(1, 10))

    @SKIP_PARSER
    @SKIP_XML
    def test_each_race_has_horses(self):
        """Every race must have at least one horse."""
        races = parse_xml(SA_XML)
        for i, race in enumerate(races, 1):
            assert race.get("horses"), f"Race {i} has no horses"

    @SKIP_PARSER
    @SKIP_XML
    def test_horse_required_fields(self):
        """Every horse must have name, program, and morn_odds."""
        races = parse_xml(SA_XML)
        for race in races:
            for horse in race.get("horses", []):
                assert horse.get("name"), f"Horse missing name in race {race.get('number')}"
                assert horse.get("program", 0) > 0, f"Horse missing program in race {race.get('number')}"
                assert horse.get("morn_odds"), f"Horse missing morn_odds in race {race.get('number')}"

    @SKIP_PARSER
    @SKIP_XML
    def test_past_performances_structure(self):
        """past_performances key must exist; if populated, each PP must have racedate and surface."""
        races = parse_xml(SA_XML)
        pp_found = False
        for race in races:
            for horse in race.get("horses", []):
                assert "past_performances" in horse, f"Horse {horse.get('name')} missing past_performances"
                for pp in horse.get("past_performances", []):
                    pp_found = True
                    assert pp.get("racedate"), "PP missing racedate"
                    assert pp.get("surface"), "PP missing surface"
                    assert "speedfigur" in pp, "PP missing speedfigur (can be 0)"
        assert pp_found, "No past_performances found in any horse — parser may be broken"

    @SKIP_PARSER
    @SKIP_XML
    def test_workouts_key_present(self):
        """Every horse must have a workouts key (can be empty list)."""
        races = parse_xml(SA_XML)
        for race in races:
            for horse in race.get("horses", []):
                assert "workouts" in horse, f"Horse {horse.get('name')} missing workouts key"

    @SKIP_PARSER
    @SKIP_XML
    def test_race_metadata_fields(self):
        """Each race must have track, date, surface, purse."""
        races = parse_xml(SA_XML)
        for race in races:
            for field in ["track", "date", "surface", "purse"]:
                assert race.get(field) is not None, (
                    f"Race {race.get('number')} missing {field}"
                )

    @SKIP_PARSER
    @SKIP_XML
    def test_no_duplicate_programs(self):
        """Each race must not have duplicate program numbers."""
        races = parse_xml(SA_XML)
        for race in races:
            programs = [h.get("program") for h in race.get("horses", [])]
            assert len(programs) == len(set(programs)), (
                f"Duplicate programs in race {race.get('number')}: {programs}"
            )
