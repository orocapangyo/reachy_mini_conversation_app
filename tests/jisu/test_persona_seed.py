"""Tests for the per-profile persona seed files."""

from __future__ import annotations
from pathlib import Path

import pytest

from jisu.persona.seed import SeedFormatError, load_seed, apply_seed
from jisu.persona.store import CategorizedMemory
from jisu.persona.constants import SEEDS_DIR, SEED_PROVENANCE
from jisu.persona.categories import CategorizedFact, get_category
from reachy_mini_conversation_app.memory import MemoryFact


class RecordingStore:
    """CategorizedMemory double that refuses texts listed in ``refuse``."""

    name = "recording"

    def __init__(self, refuse: set[str] | None = None, known: set[str] | None = None) -> None:
        """Initialize the test double."""
        self.refuse = refuse or set()
        self.known = known or set()
        self.saved: list[tuple[str, str, str | None]] = []

    def list_categorized(self) -> list[CategorizedFact]:
        """Test double for ``list_categorized``."""
        return []

    def add_categorized(self, text: str, category: str, *, provenance: str | None = None) -> CategorizedFact | None:
        """Test double for ``add_categorized``."""
        if text in self.refuse:
            return None
        fact = MemoryFact(id=str(len(self.saved)), text=text, created_at=0)
        if text in self.known:
            return CategorizedFact(fact, get_category(category), duplicate=True)
        self.saved.append((text, category, provenance))
        return CategorizedFact(fact, get_category(category))

    def forget_by_id(self, fact_id: str) -> MemoryFact | None:
        """Test double for ``forget_by_id``."""
        return None

    def search_categorized(self, query: str, limit: int) -> list[CategorizedFact]:
        """Test double for ``search_categorized``."""
        return []


def _write(directory: Path, body: str, profile: str = "p") -> None:
    (directory / f"{profile}.toml").write_text(body, encoding="utf-8")


def test_bundled_seeds_are_valid() -> None:
    """Every shipped seed file parses and uses known categories."""
    files = sorted(SEEDS_DIR.glob("*.toml"))
    assert files, "expected at least one bundled seed"
    for path in files:
        assert load_seed(path.stem), path.name
    assert {fact.category for fact in load_seed("desk_companion_ko")} <= {"persona_trait", "persona_style"}


def test_missing_seed_is_empty(tmp_path: Path) -> None:
    """A profile without a seed file seeds nothing."""
    assert load_seed("nobody", tmp_path) == []


@pytest.mark.parametrize(
    "body",
    [
        "schema_version = 2\n",
        'schema_version = 1\nfacts = "x"\n',
        'schema_version = 1\n[[facts]]\ncategory = "diary"\ntext = "x"\n',
        'schema_version = 1\n[[facts]]\ncategory = "persona_trait"\ntext = "  "\n',
        "not toml [",
    ],
)
def test_bad_seed_files_are_rejected(tmp_path: Path, body: str) -> None:
    """Malformed seeds raise SeedFormatError instead of half-seeding."""
    _write(tmp_path, body)
    with pytest.raises(SeedFormatError):
        load_seed("p", tmp_path)


def test_apply_seed_reports_saved_and_skipped(tmp_path: Path) -> None:
    """Refused facts (duplicates / withdrawn) are skipped; the seed provenance is used."""
    _write(
        tmp_path,
        'schema_version = 1\n[[facts]]\ncategory = "persona_trait"\ntext = "나는 A"\n'
        '[[facts]]\ncategory = "Promise"\ntext = "나는 B"\n'
        '[[facts]]\ncategory = "persona_style"\ntext = "나는 C"\n',
    )
    store = RecordingStore(refuse={"나는 B"}, known={"나는 C"})
    assert isinstance(store, CategorizedMemory)
    result = apply_seed(store, "p", tmp_path)
    assert [f.text for f in result.saved] == ["나는 A"]
    assert [f.text for f in result.existing] == ["나는 C"]
    assert [(f.text, f.category) for f in result.skipped] == [("나는 B", "promise")]
    assert result.summary() == "1 saved, 1 already there, 1 skipped"
    assert store.saved == [("나는 A", "persona_trait", SEED_PROVENANCE)]
