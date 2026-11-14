"""Result of one sync run (contract shared with tools and the CLI)."""

from dataclasses import field, dataclass


@dataclass
class SyncReport:
    """What one sync run did, per profile folder name."""

    profiles: list[str] = field(default_factory=list)
    skipped_profiles: list[str] = field(default_factory=list)  # present on disk but not allowed
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    archived: dict[str, int] = field(default_factory=dict)
    excluded: dict[str, int] = field(default_factory=dict)  # private folders etc. (never exposed to the model)
    mirror_commits: dict[str, int] = field(default_factory=dict)  # git commits per profile (only with git_mirror on)
    warnings: list[str] = field(default_factory=list)
