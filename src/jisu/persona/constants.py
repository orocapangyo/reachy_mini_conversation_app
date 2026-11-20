"""Tuning constants for categorized memory and the GBrain-managed persona."""

from pathlib import Path


# --- subjects (GBrain ``entity`` slugs) ---------------------------------------
# Facts about the robot itself (persona, promises) vs. facts about the user.
REACHY_ENTITY = "reachy-mini"
USER_ENTITY = "user"

# --- listing / prompt limits ---------------------------------------------------
# How many facts one listing asks GBrain for (all categories together).
CATEGORIZED_LIST_LIMIT = 200
# Facts per prompt section (newest first); keeps the session prompt short.
PROMPT_MAX_PERSONA_FACTS = 20
PROMPT_MAX_PROMISE_FACTS = 10
PROMPT_MAX_USER_FACTS = 40

# --- seeds -----------------------------------------------------------------------
SEEDS_DIR = Path(__file__).parent / "seeds"
SEED_SCHEMA_VERSION = 1
SEED_PROVENANCE = "deskmate persona seed"

# --- update_persona tool ---------------------------------------------------------
PERSONA_TOOL_PROVENANCE = "reachy-mini conversation (update_persona tool)"
# Upper bound on persona writes per app process, so a chatty model cannot flood the brain.
PERSONA_TOOL_MAX_WRITES = 10
PERSONA_TEXT_MAX_CHARS = 200
