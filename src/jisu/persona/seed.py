"""Initial persona facts per profile, kept in ``seeds/<profile>.toml``.

File format::

    schema_version = 1

    [[facts]]
    category = "persona_trait"
    text = "나는 사용자가 지쳐 보이면 짧은 농담으로 분위기를 푼다."

Seeding is safe to repeat: GBrain refuses exact duplicates, and a fact the user
deleted (withdrawn in GBrain) is not re-added either.
"""

from __future__ import annotations
import logging
import tomllib
from pathlib import Path
from dataclasses import field, dataclass

from jisu.persona.store import CategorizedMemory
from jisu.persona.constants import SEEDS_DIR, SEED_PROVENANCE, SEED_SCHEMA_VERSION
from jisu.persona.categories import get_category


logger = logging.getLogger(__name__)


class SeedFormatError(ValueError):
    """Raised when a seed file cannot be used."""


@dataclass(frozen=True)
class SeedFact:
    """One fact to seed."""

    category: str
    text: str


@dataclass
class SeedResult:
    """What one seeding run did."""

    path: Path
    saved: list[SeedFact] = field(default_factory=list)
    # Already in the store (GBrain answered "duplicate"): nothing written.
    existing: list[SeedFact] = field(default_factory=list)
    # Refused or failed (e.g. a fact the user deleted is withdrawn and stays deleted).
    skipped: list[SeedFact] = field(default_factory=list)

    def summary(self) -> str:
        """One-line description for logs."""
        return f"{len(self.saved)} saved, {len(self.existing)} already there, {len(self.skipped)} skipped"


def seed_path(profile: str, directory: Path = SEEDS_DIR) -> Path:
    """Return the seed file used for ``profile``."""
    return directory / f"{profile.strip()}.toml"


def load_seed(profile: str, directory: Path = SEEDS_DIR) -> list[SeedFact]:
    """Read the seed facts for ``profile`` ([] when the profile has no seed file)."""
    path = seed_path(profile, directory)
    if not path.is_file():
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SeedFormatError(f"{path}: {exc}") from exc

    if raw.get("schema_version") != SEED_SCHEMA_VERSION:
        raise SeedFormatError(f"{path}: schema_version must be {SEED_SCHEMA_VERSION}")
    entries = raw.get("facts", [])
    if not isinstance(entries, list):
        raise SeedFormatError(f"{path}: 'facts' must be an array of tables")

    facts: list[SeedFact] = []
    for index, entry in enumerate(entries):
        where = f"{path}: facts[{index}]"
        if not isinstance(entry, dict):
            raise SeedFormatError(f"{where} must be a table")
        category, text = entry.get("category"), entry.get("text")
        if not isinstance(category, str) or not isinstance(text, str) or not text.strip():
            raise SeedFormatError(f"{where} needs string 'category' and non-empty 'text'")
        try:
            get_category(category)
        except KeyError as exc:
            raise SeedFormatError(f"{where}: {exc.args[0]}") from exc
        facts.append(SeedFact(category=category.strip().lower(), text=text.strip()))
    return facts


def apply_seed(store: CategorizedMemory, profile: str, directory: Path = SEEDS_DIR) -> SeedResult:
    """Write the profile's seed facts into ``store`` (raises :class:`SeedFormatError` for a bad file)."""
    result = SeedResult(path=seed_path(profile, directory))
    for fact in load_seed(profile, directory):
        stored = store.add_categorized(fact.text, fact.category, provenance=SEED_PROVENANCE)
        if stored is None:
            result.skipped.append(fact)
        elif stored.duplicate:
            result.existing.append(fact)
        else:
            result.saved.append(fact)
    logger.info("Persona seed %s: %s", result.path.name, result.summary())
    return result
