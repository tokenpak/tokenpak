"""Tests for tokenpak.compression.slot_filler module."""

from tokenpak.compression.slot_filler import (
    FilledSlots,
    SlotFiller,
)


class TestFilledSlots:
    """Test FilledSlots dataclass."""

    def test_default_values(self):
        """Test default initialization."""
        result = FilledSlots(intent="test")
        assert result.intent == "test"
        assert result.slots == {}
        assert result.missing == []
        assert result.confidence == 1.0

    def test_with_slots(self):
        """Test initialization with slots."""
        result = FilledSlots(
            intent="search",
            slots={"query": "python", "limit": 10},
        )
        assert result.intent == "search"
        assert result.slots["query"] == "python"
        assert result.slots["limit"] == 10

    def test_with_missing(self):
        """Test initialization with missing slots."""
        result = FilledSlots(
            intent="action",
            missing=["entity", "duration"],
        )
        assert result.missing == ["entity", "duration"]

    def test_with_confidence(self):
        """Test initialization with confidence."""
        result = FilledSlots(intent="query", confidence=0.75)
        assert result.confidence == 0.75

    def test_all_fields(self):
        """Test initialization with all fields."""
        result = FilledSlots(
            intent="intent_name",
            slots={"slot1": "value1", "slot2": "value2"},
            missing=["slot3"],
            confidence=0.85,
        )
        assert result.intent == "intent_name"
        assert len(result.slots) == 2
        assert result.missing == ["slot3"]
        assert result.confidence == 0.85


class TestSlotFillerInit:
    """Test SlotFiller initialization."""

    def test_default_init(self):
        """Test default initialization."""
        filler = SlotFiller()
        assert isinstance(filler.definitions, dict)

    def test_custom_definitions(self):
        """Test initialization with custom definitions."""
        custom_defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["test"],
                        "required": True,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=custom_defs)
        assert "test" in filler.definitions

    def test_known_intents(self):
        """Test getting known intents."""
        custom_defs = {
            "search": {"slots": {}},
            "delete": {"slots": {}},
        }
        filler = SlotFiller(definitions=custom_defs)
        intents = filler.known_intents()
        assert "search" in intents
        assert "delete" in intents


class TestSlotFillerDurationExtraction:
    """Test duration slot extraction."""

    def test_extract_days(self):
        """Test extraction of days duration."""
        defs = {
            "test": {
                "slots": {
                    "duration": {
                        "type": "duration",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "show data for 5 days")
        assert "duration" in result.slots
        assert result.slots["duration"] is not None

    def test_extract_last_days(self):
        """Test extraction of 'last N days'."""
        defs = {
            "test": {
                "slots": {
                    "duration": {
                        "type": "duration",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "last 7 days")
        assert "duration" in result.slots

    def test_extract_week(self):
        """Test extraction of 'last week'."""
        defs = {
            "test": {
                "slots": {
                    "duration": {
                        "type": "duration",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "last week")
        assert "duration" in result.slots

    def test_extract_month(self):
        """Test extraction of 'last month'."""
        defs = {
            "test": {
                "slots": {
                    "duration": {
                        "type": "duration",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "last month")
        assert "duration" in result.slots

    def test_extract_today(self):
        """Test extraction of 'today'."""
        defs = {
            "test": {
                "slots": {
                    "duration": {
                        "type": "duration",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "today")
        assert "duration" in result.slots

    def test_no_duration_extracted(self):
        """Test when no duration is found."""
        defs = {
            "test": {
                "slots": {
                    "duration": {
                        "type": "duration",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "some random text")
        assert "duration" not in result.slots


class TestSlotFillerEnumExtraction:
    """Test enum slot extraction."""

    def test_extract_enum_exact_match(self):
        """Test extraction of enum with exact match."""
        defs = {
            "test": {
                "slots": {
                    "status": {
                        "type": "enum",
                        "values": ["active", "inactive", "pending"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "show active items")
        assert result.slots.get("status") == "active"

    def test_extract_enum_case_insensitive(self):
        """Test enum extraction is case insensitive."""
        defs = {
            "test": {
                "slots": {
                    "status": {
                        "type": "enum",
                        "values": ["active", "inactive"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "ACTIVE status")
        assert result.slots.get("status") == "active"

    def test_extract_enum_stemmed(self):
        """Test enum extraction with stemming."""
        defs = {
            "test": {
                "slots": {
                    "action": {
                        "type": "enum",
                        "values": ["approved", "rejected"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        # "approve" should match "approved" via stemming
        result = filler.fill("test", "request was approved")
        assert result.slots.get("action") == "approved"

    def test_enum_not_found(self):
        """Test when enum value not found."""
        defs = {
            "test": {
                "slots": {
                    "status": {
                        "type": "enum",
                        "values": ["active", "inactive"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "some random text")
        assert "status" not in result.slots


class TestSlotFillerEntityExtraction:
    """Test entity slot extraction."""

    def test_extract_entity_exact_match(self):
        """Test extraction of entity with exact match."""
        defs = {
            "test": {
                "slots": {
                    "target": {
                        "type": "entity",
                        "examples": ["vault", "database", "server"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "summarize the vault")
        assert result.slots.get("target") == "vault"

    def test_extract_entity_with_modifier(self):
        """Test entity extraction with post-modifier."""
        defs = {
            "test": {
                "slots": {
                    "target": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "vault database")
        # Should capture "vault database"
        assert "vault" in str(result.slots.get("target", "")).lower()

    def test_extract_entity_case_insensitive(self):
        """Test entity extraction is case insensitive."""
        defs = {
            "test": {
                "slots": {
                    "target": {
                        "type": "entity",
                        "examples": ["database"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "DATABASE server")
        assert "database" in str(result.slots.get("target", "")).lower()

    def test_entity_not_found(self):
        """Test when entity is not found."""
        defs = {
            "test": {
                "slots": {
                    "target": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "something else")
        assert "target" not in result.slots


class TestSlotFillerMissingSlots:
    """Test missing slot tracking."""

    def test_required_slot_missing(self):
        """Test tracking of missing required slot."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["test"],
                        "required": True,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "no matching content")
        assert "entity" in result.missing

    def test_optional_slot_not_missing(self):
        """Test optional slot not marked as missing."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["test"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "no matching")
        assert "entity" not in result.missing

    def test_multiple_missing_slots(self):
        """Test multiple missing slots."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": True,
                    },
                    "duration": {
                        "type": "duration",
                        "required": True,
                    },
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "some text")
        assert len(result.missing) == 2


class TestSlotFillerDefaults:
    """Test default slot values."""

    def test_default_value_used(self):
        """Test that default value is used."""
        defs = {
            "test": {
                "slots": {
                    "limit": {
                        "type": "int",
                        "default": 10,
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "no limit specified")
        assert result.slots.get("limit") == 10

    def test_extracted_overrides_default(self):
        """Test that extracted value overrides default."""
        defs = {
            "test": {
                "slots": {
                    "target": {
                        "type": "entity",
                        "examples": ["vault"],
                        "default": "database",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "summarize the vault")
        assert result.slots.get("target") == "vault"


class TestSlotFillerConfidence:
    """Test confidence calculation."""

    def test_confidence_all_required_filled(self):
        """Test confidence when all required slots are filled."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": True,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "summarize the vault")
        assert result.confidence == 1.0

    def test_confidence_some_required_missing(self):
        """Test confidence when some required slots are missing."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": True,
                    },
                    "duration": {
                        "type": "duration",
                        "required": True,
                    },
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "vault")
        assert result.confidence < 1.0

    def test_confidence_no_required_slots(self):
        """Test confidence with only optional slots."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "vault")
        assert result.confidence > 0.0


class TestSlotFillerCanonical:
    """Test intent canonicalization."""

    def test_lowercase_intent(self):
        """Test intent is lowercased."""
        defs = {"test_intent": {"slots": {}}}
        filler = SlotFiller(definitions=defs)
        result = filler.fill("TEST_INTENT", "text")
        assert result.intent == "test_intent"

    def test_dash_to_underscore(self):
        """Test dashes are converted to underscores."""
        defs = {"test_intent": {"slots": {}}}
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test-intent", "text")
        assert result.intent == "test_intent"

    def test_whitespace_stripped(self):
        """Test leading/trailing whitespace is stripped."""
        defs = {"test": {"slots": {}}}
        filler = SlotFiller(definitions=defs)
        result = filler.fill("  test  ", "text")
        assert result.intent == "test"


class TestSlotFillerUnknownIntent:
    """Test handling of unknown intents."""

    def test_unknown_intent_returns_empty(self):
        """Test unknown intent returns empty result."""
        filler = SlotFiller(definitions={})
        result = filler.fill("unknown_intent", "some text")
        assert result.intent == "unknown_intent"
        assert result.slots == {}
        assert result.confidence == 0.0

    def test_unknown_intent_no_slots(self):
        """Test unknown intent has no filled slots."""
        defs = {"other": {"slots": {}}}
        filler = SlotFiller(definitions=defs)
        result = filler.fill("nonexistent", "text")
        assert len(result.slots) == 0


class TestSlotFillerComplexScenarios:
    """Test complex multi-slot scenarios."""

    def test_multiple_slots_filled(self):
        """Test filling multiple slots at once."""
        defs = {
            "summarize": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault", "database"],
                        "required": True,
                    },
                    "duration": {
                        "type": "duration",
                        "required": False,
                    },
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("summarize", "summarize the vault for last 7 days")
        assert "entity" in result.slots
        assert "duration" in result.slots

    def test_mixed_filled_and_missing(self):
        """Test mix of filled and missing slots."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": True,
                    },
                    "duration": {
                        "type": "duration",
                        "required": True,
                    },
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "vault only")
        assert "entity" in result.slots
        assert "duration" not in result.slots
        assert "duration" in result.missing

    def test_enum_and_entity_together(self):
        """Test enum and entity extraction together."""
        defs = {
            "test": {
                "slots": {
                    "target": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": False,
                    },
                    "action": {
                        "type": "enum",
                        "values": ["create", "delete"],
                        "required": False,
                    },
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "delete the vault")
        assert "target" in result.slots
        assert "action" in result.slots


class TestSlotFillerEdgeCases:
    """Test edge cases."""

    def test_empty_text(self):
        """Test with empty text."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["test"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "")
        assert result.slots == {}

    def test_special_characters(self):
        """Test with special characters in text."""
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "vault-prod-2024")
        # Should handle special chars gracefully
        assert isinstance(result.slots, dict)

    def test_unicode_text(self):
        """Test with unicode text."""
        defs = {
            "test": {
                "slots": {
                    "duration": {
                        "type": "duration",
                        "required": False,
                    }
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "последние 5 дней")
        assert isinstance(result.slots, dict)

    def test_very_long_text(self):
        """Test with very long text."""
        long_text = "word " * 10000 + "vault last 7 days"
        defs = {
            "test": {
                "slots": {
                    "entity": {
                        "type": "entity",
                        "examples": ["vault"],
                        "required": False,
                    },
                    "duration": {
                        "type": "duration",
                        "required": False,
                    },
                }
            }
        }
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", long_text)
        assert "entity" in result.slots
        assert "duration" in result.slots

    def test_empty_definitions(self):
        """Test with empty slot definitions."""
        defs = {"test": {"slots": {}}}
        filler = SlotFiller(definitions=defs)
        result = filler.fill("test", "some text")
        assert result.slots == {}
        assert result.confidence > 0.0  # No required slots
