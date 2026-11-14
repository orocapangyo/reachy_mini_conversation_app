"""The five Bookmark Insight operations as plain functions (design doc §2.2, §3), independent of any host.

The robot app's ``tools/`` wrap these in ``Tool`` subclasses (same process);
``bookmark_insight.mcp_server`` (M9, optional) wraps the very same functions for
external clients. Both get identical result dicts, so the contract lives here:

    get_insight(days=None, since=None, until=None, profile=None) -> dict
    build_insight(days=None, since=None, until=None, profile=None) -> dict  (blocking, long)
    get_bookmark(id) -> dict
    list_bookmarks(days=None, since=None, until=None, profile=None, limit=None) -> dict
    finish_insight(conclusion, reason="explicit", transcript=None) -> dict

Rules shared with the tools: never raise (``{"error": ...}`` instead), every
success carries ``today`` and a Korean ``next_action`` for the voice model,
settings are loaded on every call (never at import time) so the environment
can change under a running host and tests can monkeypatch it.

M7: ``build_insight`` summarizes at most one batch per call and writes a
partial note when bookmarks are left (``status: partial`` with the counts);
while it runs, the profile/period is registered in this process so
``get_insight`` answers ``status: backfilling`` instead of touching the
locked repository. M8: the host may hand ``finish_insight`` a rendered
transcript (the robot app does; the MCP server has none).
"""

from __future__ import annotations
import time
import logging
import threading
from typing import Any, Mapping, Iterator
from pathlib import Path
from datetime import date
from contextlib import contextmanager
from dataclasses import dataclass

import bookmark_insight.sync as sync_mod
import bookmark_insight.insight as insight_mod
from bookmark_insight.llm import build_llm
from bookmark_insight.store import (
    note_path,
    read_note,
    find_bookmark,
    list_profiles,
    note_is_stale,
    bookmarks_in_period,
    append_conversation_section,
)
from bookmark_insight.models import SUMMARY_HEADING, InsightNote
from bookmark_insight.periods import Period, PeriodError, resolve_period
from bookmark_insight.session import SESSION_MANAGER, now_local_label
from bookmark_insight.settings import ENV_BOOKMARK_REPO, BookmarkInsightSettings, load_settings
from bookmark_insight.insight.builder import daily_cap_reached, pending_bookmarks
from bookmark_insight.summarize.runner import resolve_now


logger = logging.getLogger(__name__)

# --- tuning constants ---------------------------------------------------------
FALLBACK_PROFILE = "Default"
DEFAULT_DAYS = 7
DEFAULT_PERIOD_LABEL = "지난 일주일"
SYNC_MIN_INTERVAL_S = 30.0  # read operations re-sync Chrome at most this often per profile (0.1 s for 360 bookmarks)
DEFAULT_LIMIT = 20  # list_bookmarks
MAX_LIMIT = 200
ALLOWED_REASONS: tuple[str, ...] = ("explicit", "confirmed")  # finish_insight; "auto" is the host's
SETTINGS_URL = "http://127.0.0.1:7860/#/settings"  # the app's fixed UI port; the host must run with --ui
SETTINGS_SECTION = "Bookmark Insight"
SETUP_STEPS: tuple[str, ...] = ("프로필 탐색", "북마크 저장소 폴더 → 폴더 만들기·적용", ".env에 적용", "동기화 실행")

# --- next_action texts (§3.1 / §3.2, tuned per profile prompt later) ----------
NOT_CONFIGURED_NEXT_ACTION = (
    "북마크 기능이 아직 설정되지 않았다. 북마크 내용을 기억이나 추측으로 말하지 말고, 두 문장 안에 이렇게만 안내하라: "
    "브라우저에서 127.0.0.1 7860 설정 페이지를 열면 Bookmark Insight 칸에 버튼 네 개(프로필 탐색, 폴더 만들기·적용, "
    ".env에 적용, 동기화 실행)가 있으니 차례로 누르면 되고, 재시작은 필요 없으니 끝나면 다시 말해 달라고 하라. "
    "한국어로만 말한다."
)
UNKNOWN_PROFILE_NEXT_ACTION = "쓸 수 있는 프로필 이름을 말해 주라"
READY_NEXT_ACTION = (
    "노트가 준비됐다. 발화는 반드시 이 결과의 clusters를 근거로 하고, 기억이나 추측으로 북마크 내용을 "
    "말하지 마라. 한 번의 발화에 묶음 하나와 질문 하나만 꺼내고, 사용자가 반응한 뒤에 다음 묶음으로 "
    "넘어가라. 읽었는지·몇 개 모았는지·얼마나 묵혔는지 평가하지 말고, 왜 저장했는지도 묻지 마라. "
    "상관관계를 말할 때는 어느 북마크끼리인지 짧게 줄인 제목으로 짚고 긴 제목을 그대로 읽지 마라. "
    "unavailable에 있는 북마크는 '제목만 보고 하는 얘긴데'라고 밝혀라. 첫 발화는 짧은 문장 최대 두 개와 "
    "질문 하나로 끝낸다: 첫 묶음의 summary와 correlation을 한 문장씩 줄여 말하고, 반드시 그 묶음의 "
    "question으로 끝내라. 한국어로만 말한다."
)
GENERATING_NEXT_ACTION = (
    "이 기간의 인사이트 노트가 아직 없거나 낡았다. 먼저 반드시 사용자에게 '잠깐 훑어볼게' 한마디를 "
    "소리 내어 말한 다음에 {call}를 호출하라. 도구가 도는 동안 사용자가 말을 걸면 한국어로 짧게 "
    "'아직 훑는 중이야'라고 답하라. 결과가 돌아오면 그때 묶음 하나와 질문 하나로 이야기를 시작한다."
)
EMPTY_NEXT_ACTION = (
    "이 기간에는 북마크가 없다. 재촉이나 평가 없이 이렇게 말하고 대화를 이어라: "
    '"{label} 동안은 새로 저장한 게 없네. 요즘 관심 있는 건 뭐야?"'
)
BUILT_NEXT_ACTION = (
    "인사이트 생성이 끝났다. 묶음 하나와 질문 하나로 시작하라: 첫 묶음의 summary와 correlation을 짧게 줄인 "
    "제목으로 근거를 짚어 짧은 문장 최대 두 개로 말하고, 발화는 반드시 그 묶음의 question으로 끝내라. 나머지 "
    "묶음은 사용자가 반응한 뒤 하나씩 꺼내라. unavailable에 있는 북마크는 '제목만 보고 하는 얘긴데'라고만 "
    "밝히고, 읽었는지·개수·묵힌 기간으로 평가하지 마라. 노트에 없는 내용은 말하지 마라. 한국어로만 말한다."
)
DEGRADED_NEXT_ACTION = (
    "요약 서비스가 바빠서 새 인사이트를 못 만들었다. 대신 이전에 만든 인사이트(이 결과의 clusters)로 이야기한다. "
    "먼저 '요약 서비스가 바빠서 지난번 정리로 얘기할게'라고 한 문장으로 알리고, pending_titles에 있는 북마크 "
    "{count}건은 아직 못 봤다고 짧게 줄인 제목으로 말한 뒤, 첫 묶음의 요약 한 문장과 question으로 끝내라. "
    "한국어로만 말한다."
)
BUSY_NEXT_ACTION = (
    "요약 서비스가 바빠서 지금은 북마크를 못 봤다고 한 문장으로 짧게 사과하고, 조금 뒤에 다시 하자고 말하라. "
    "다른 설명은 덧붙이지 마라."
)
BOOKMARK_NEXT_ACTION = "요약을 바탕으로 짧게 답하라. 본문 미확보면 그렇다고 밝혀라"
LIST_NEXT_ACTION = (
    "목록은 보조 자료다. 사용자가 찾는 북마크를 짧게 줄인 제목으로 짚어 주고, 내용이 궁금하면 그 id로 "
    "get_bookmark를 호출하라. 개수나 읽었는지로 평가하지 마라."
)
FINISH_NEXT_ACTION = "짧게 마무리 인사를 하라"
NO_SESSION_NEXT_ACTION = (
    "인사이트 세션이 이미 닫혀 있다(자동 종료됐거나 아직 시작하지 않았다). "
    "사용자에게 짧게 알리고, 이어서 하려면 get_insight를 다시 호출하라."
)
# --- M7 partial / backfilling wording ------------------------------------------------
PARTIAL_NOTICE = "'{pending}건은 아직 안 봤어. 이어서 정리해 달라고 하면 계속 볼게'"
PARTIAL_NOTICE_DAILY_CAP = "'{pending}건은 오늘 요약 한도라 내일 이어서 볼게'"
PARTIAL_CONTINUE = "사용자가 '이어서 정리해 줘'라고 하면 먼저 '잠깐 훑어볼게'를 소리 내어 말한 다음 {call}를 호출하라."
PARTIAL_CONTINUE_DAILY_CAP = "오늘은 요약 한도에 걸려 더 못 보니 이어서 정리해 달라고 해도 내일 다시 하자고 말하라."
PARTIAL_NOTHING_SEEN = "아직 본 북마크가 없어 묶음이 없다. {notice}라고 한 문장으로만 말하고 다른 설명은 덧붙이지 마라. 한국어로만 말한다."
PARTIAL_NOTHING_SEEN_NOTICE = "'지금은 정리를 못 했어. 이어서 정리해 달라고 하면 다시 해 볼게'"
PARTIAL_NOTHING_SEEN_NOTICE_DAILY_CAP = "'오늘은 요약 한도라 아직 못 봤어. 내일 이어서 볼게'"
BACKFILLING_NEXT_ACTION = (
    "이 기간의 북마크를 지금 훑는 중이다(build_insight가 돌고 있다). 한국어로 짧게 '아직 훑는 중이야, 잠깐만'이라고만 "
    "답하고 build_insight를 다시 호출하지 마라. 끝나면 결과가 전달된다."
)


def _today() -> date:
    """Return the local date (patched in tests)."""
    return date.today()


def today_iso() -> str:
    """ISO ``today`` included in every successful result (§2.2)."""
    return _today().isoformat()


# --- Chrome sync before reading (a bookmark saved a second ago must be visible) ----------

_LAST_SYNC_AT: dict[str, float] = {}  # profile → time.monotonic() of the last sync by a read operation


def reset_sync_throttle() -> None:
    """Forget the last sync times (tests)."""
    _LAST_SYNC_AT.clear()


def sync_before_read(settings: BookmarkInsightSettings, profile: str) -> str | None:
    """Mirror Chrome into the repo for ``profile`` unless it was done within ``SYNC_MIN_INTERVAL_S``.

    Returns a short warning string (or ``None``); never raises, the callers still
    answer from whatever the repository holds.
    """
    now = time.monotonic()
    last = _LAST_SYNC_AT.get(profile)
    if last is not None and now - last < SYNC_MIN_INTERVAL_S:
        return None
    _LAST_SYNC_AT[profile] = now
    try:
        report = sync_mod.sync_profiles(settings, profiles=[profile])
    except Exception as exc:
        logger.warning("sync before read failed for %s (continuing with the repository): %s", profile, exc)
        return f"sync failed: {type(exc).__name__}: {exc}"
    warnings = [str(item) for item in (getattr(report, "warnings", None) or [])]
    if warnings:
        logger.warning("sync before read (%s): %s", profile, "; ".join(warnings))
    return "; ".join(warnings) or None


# --- running build_insight registry (M7: get_insight answers "backfilling" meanwhile) ----------

_BUILDING: set[tuple[str, str]] = set()  # (profile, period.key) of pipelines running in this process
_BUILDING_GUARD = threading.Lock()


def reset_building() -> None:
    """Forget every running pipeline (tests)."""
    with _BUILDING_GUARD:
        _BUILDING.clear()


def is_building(profile: str, period: Period) -> bool:
    """Whether a ``build_insight`` for ``profile``/``period`` is running in this process right now."""
    with _BUILDING_GUARD:
        return (profile, period.key) in _BUILDING


@contextmanager
def building(profile: str, period: Period) -> Iterator[None]:
    """Mark ``profile``/``period`` as being built for the duration of the block."""
    key = (profile, period.key)
    with _BUILDING_GUARD:
        _BUILDING.add(key)
    try:
        yield
    finally:
        with _BUILDING_GUARD:
            _BUILDING.discard(key)


# --- settings / profile ------------------------------------------------------------


def not_configured_error() -> dict[str, Any]:
    """Error returned by every operation when the bookmark repository is not set; points at the settings page."""
    return {
        "error": f"bookmark_insight not configured: set {ENV_BOOKMARK_REPO}",
        "settings_url": SETTINGS_URL,
        "settings_section": SETTINGS_SECTION,
        "setup_steps": list(SETUP_STEPS),
        "restart_required": False,  # settings are re-read on every call
        "next_action": NOT_CONFIGURED_NEXT_ACTION,
    }


def allowed_profiles(settings: BookmarkInsightSettings) -> list[str]:
    """Profiles an operation may target: allow list, else repo folders, else ``Default``."""
    if settings.chrome_profiles:
        return list(settings.chrome_profiles)
    if settings.configured:
        found = list_profiles(settings)
        if found:
            return found
    return [FALLBACK_PROFILE]


def default_profile(settings: BookmarkInsightSettings, allowed: list[str]) -> str:
    """Profile used when the caller omits the ``profile`` argument."""
    return settings.default_profile or allowed[0]


def profile_hint() -> str:
    """Short sentence appended to tool descriptions so the model never invents a profile name."""
    try:
        settings = load_settings()
    except Exception:  # defensive: a description must never break tool registration
        return "profile is optional (Chrome profile folder name)."
    allowed = allowed_profiles(settings)
    return f"profile is optional and must be one of: {', '.join(allowed)} (default: {default_profile(settings, allowed)})."


def resolve_profile(settings: BookmarkInsightSettings, requested: Any) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(profile, None)`` or ``(None, error_dict)`` for an unknown profile."""
    allowed = allowed_profiles(settings)
    if requested is None or str(requested).strip() == "":
        return default_profile(settings, allowed), None
    name = str(requested).strip()
    if name not in allowed:
        return None, {"error": "unknown profile", "available": allowed, "next_action": UNKNOWN_PROFILE_NEXT_ACTION}
    return name, None


# --- period --------------------------------------------------------------------


def _optional_int(value: Any, label: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PeriodError(f"{label} must be an integer, got {value!r}") from exc


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class PeriodArgs:
    """Normalized ``days`` / ``since`` / ``until`` arguments plus the resolved range."""

    days: int | None
    since: str | None
    until: str | None
    period: Period

    @property
    def explicit(self) -> bool:
        """Whether the caller gave since/until (changes the empty-period wording)."""
        return self.since is not None or self.until is not None

    def label(self) -> str:
        """Korean period wording for §3.2 ("지난 일주일" or the explicit range)."""
        if self.explicit:
            return f"{self.period.since.isoformat()}부터 {self.period.until.isoformat()}까지"
        if self.days is None or self.days == DEFAULT_DAYS:
            return DEFAULT_PERIOD_LABEL
        return f"지난 {self.days}일"

    def build_insight_call(self, profile: str) -> str:
        """Exact ``build_insight(...)`` call text the model should repeat."""
        return (
            f"build_insight(since='{self.period.since.isoformat()}', "
            f"until='{self.period.until.isoformat()}', profile='{profile}')"
        )


def resolve_period_args(days: Any = None, since: Any = None, until: Any = None) -> PeriodArgs:
    """Resolve raw arguments into a ``PeriodArgs`` (raises ``PeriodError``)."""
    day_count = _optional_int(days, "days")
    since_text = _optional_str(since)
    until_text = _optional_str(until)
    period = resolve_period(days=day_count, since=since_text, until=until_text, today=_today())
    return PeriodArgs(days=day_count, since=since_text, until=until_text, period=period)


# --- combined context ----------------------------------------------------------------


@dataclass(frozen=True)
class ToolContext:
    """Settings, profile and period a period-based operation works on."""

    settings: BookmarkInsightSettings
    profile: str
    args: PeriodArgs

    @property
    def period(self) -> Period:
        """Resolved inclusive date range."""
        return self.args.period


def prepare_context(kwargs: Mapping[str, Any]) -> ToolContext | dict[str, Any]:
    """Validate settings, profile and period from raw arguments; return an error dict on failure."""
    settings = load_settings()
    if not settings.configured:
        return not_configured_error()
    profile, error = resolve_profile(settings, kwargs.get("profile"))
    if error is not None or profile is None:
        return error or {"error": "unknown profile"}
    try:
        args = resolve_period_args(kwargs.get("days"), kwargs.get("since"), kwargs.get("until"))
    except PeriodError as exc:
        return {"error": str(exc)}
    return ToolContext(settings=settings, profile=profile, args=args)


# --- shared results ----------------------------------------------------------------


def empty_result(ctx: ToolContext) -> dict[str, Any]:
    """``status: empty`` with the §3.2 empty-period line (period wording adapted)."""
    return {
        "status": "empty",
        "today": today_iso(),
        "profile": ctx.profile,
        "period": ctx.period.to_dict(),
        "bookmark_count": 0,
        "next_action": EMPTY_NEXT_ACTION.format(label=ctx.args.label()),
    }


def ready_result(note: InsightNote, profile: str, next_action: str, status: str = "ready") -> dict[str, Any]:
    """``status: ready`` (or ``partial``) from a note; opens the insight session (§3.3)."""
    if note.path is not None:
        SESSION_MANAGER.open(profile, note.path)
    result: dict[str, Any] = {"status": status, "today": today_iso()}
    result.update(note.to_tool_result())
    result["profile"] = profile
    result["next_action"] = next_action
    return result


def partial_next_action(
    *, summarized_count: int, pending_count: int, daily_cap_reached: bool, has_clusters: bool, call: str, talk: str
) -> str:
    """Korean instruction for a partial note (M7): announce what is not seen yet, then talk per ``talk``."""
    if not has_clusters:
        notice = PARTIAL_NOTHING_SEEN_NOTICE_DAILY_CAP if daily_cap_reached else PARTIAL_NOTHING_SEEN_NOTICE
        return PARTIAL_NOTHING_SEEN.format(notice=notice)
    notice = (PARTIAL_NOTICE_DAILY_CAP if daily_cap_reached else PARTIAL_NOTICE).format(pending=pending_count)
    follow_up = PARTIAL_CONTINUE_DAILY_CAP if daily_cap_reached else PARTIAL_CONTINUE.format(call=call)
    return (
        f"이 기간의 노트는 아직 부분 노트다: {summarized_count}건까지 봤고 {pending_count}건은 아직 안 봤다. "
        f"있는 것으로 바로 이야기를 시작하되, 첫 발화 맨 앞에 {notice}라고 한 문장 알려라. "
        f"{follow_up} 그다음은 이렇게 한다: {talk}"
    )


def partial_result(note: InsightNote, ctx: ToolContext, talk: str) -> dict[str, Any]:
    """``status: partial`` from a note whose batch is not finished (M7); opens the session like ``ready``."""
    next_action = partial_next_action(
        summarized_count=note.summarized_count,
        pending_count=note.pending_count,
        daily_cap_reached=note.daily_cap_reached,
        has_clusters=bool(note.clusters),
        call=ctx.args.build_insight_call(ctx.profile),
        talk=talk,
    )
    result = ready_result(note, ctx.profile, next_action, status="partial")
    result["summarized_count"] = note.summarized_count
    result["pending_count"] = note.pending_count
    result["daily_cap_reached"] = note.daily_cap_reached
    return result


def backfilling_result(ctx: ToolContext) -> dict[str, Any]:
    """``status: backfilling``: a ``build_insight`` for this profile/period is running right now (no session)."""
    return {
        "status": "backfilling",
        "today": today_iso(),
        "profile": ctx.profile,
        "period": ctx.period.to_dict(),
        "next_action": BACKFILLING_NEXT_ACTION,
    }


def error_result(exc: BaseException) -> dict[str, Any]:
    """``{"error": "<Type>: <message>"}`` shape every operation uses for unexpected failures."""
    return {"error": f"{type(exc).__name__}: {exc}"}


# --- get_insight ---------------------------------------------------------------------


def get_insight(
    days: Any = None, since: Any = None, until: Any = None, profile: Any = None, **_: Any
) -> dict[str, Any]:
    """Return the insight note for a period (ready / partial / backfilling / generating / empty); never heavy."""
    try:
        ctx = prepare_context({"days": days, "since": since, "until": until, "profile": profile})
        if not isinstance(ctx, ToolContext):
            return ctx
        if is_building(ctx.profile, ctx.period):
            # The pipeline holds the repository lock; answer before touching sync or the note.
            return backfilling_result(ctx)
        sync_warning = sync_before_read(ctx.settings, ctx.profile)
        result = _read_insight(ctx, bookmarks_in_period(ctx.settings, ctx.profile, ctx.period))
        if sync_warning:
            result["sync_warning"] = sync_warning
        return result
    except Exception as exc:
        logger.exception("get_insight failed")
        return error_result(exc)


def _read_insight(ctx: ToolContext, bookmarks: list[Any]) -> dict[str, Any]:
    if not bookmarks:
        return empty_result(ctx)
    path = note_path(ctx.settings, ctx.profile, ctx.period)
    if path.is_file():
        try:
            note, _body = read_note(path)
        except ValueError as exc:
            logger.warning("Unreadable insight note %s: %s", path, exc)
        else:
            if not note_is_stale(note, bookmarks):
                if note.complete:
                    return ready_result(note, ctx.profile, READY_NEXT_ACTION)
                pending = pending_bookmarks(bookmarks)
                if pending:  # partial and current: talk with what is there, offer to continue
                    note.pending = [bookmark.guid for bookmark in pending]
                    note.daily_cap_reached = daily_cap_reached(ctx.settings, resolve_now(None))
                    return partial_result(note, ctx, READY_NEXT_ACTION)
                # Everything got summarized but the note was never finalized → one more build.
    return {
        "status": "generating",
        "today": today_iso(),
        "profile": ctx.profile,
        "period": ctx.period.to_dict(),
        "bookmark_count": len(bookmarks),
        "next_action": GENERATING_NEXT_ACTION.format(call=ctx.args.build_insight_call(ctx.profile)),
    }


# --- build_insight -------------------------------------------------------------------


def build_insight(
    days: Any = None, since: Any = None, until: Any = None, profile: Any = None, **_: Any
) -> dict[str, Any]:
    """Run the pipeline (sync → summarize one batch → cluster → note); ready / partial / empty / error. Blocking."""
    ctx = prepare_context({"days": days, "since": since, "until": until, "profile": profile})
    if not isinstance(ctx, ToolContext):
        return ctx
    return run_build_pipeline(ctx)


def run_build_pipeline(ctx: ToolContext) -> dict[str, Any]:
    """Blocking pipeline body for a validated context (hosts run it off their event loop).

    Registers the profile/period as building for the whole run (M7) so a
    concurrent ``get_insight`` answers ``backfilling``; the registry is cleared
    even when the pipeline fails.
    """
    with building(ctx.profile, ctx.period):
        try:
            warning = _sync_for_build(ctx.settings, ctx.profile)
            llm = build_llm(ctx.settings.gemini_api_key, ctx.settings.batch_model)
            note = insight_mod.build_insight(ctx.settings, llm, ctx.profile, ctx.period)
        except Exception as exc:
            logger.exception("build_insight failed")
            result = error_result(exc)
            result["profile"] = ctx.profile
            if looks_like_model_outage(exc):
                result["next_action"] = BUSY_NEXT_ACTION
            return result
    if note is None or getattr(note, "path", None) is None:
        result = empty_result(ctx)
    elif getattr(note, "degraded", False):
        pending = list(getattr(note, "pending_titles", []) or [])
        result = ready_result(note, ctx.profile, DEGRADED_NEXT_ACTION.format(count=len(pending)))
        result["degraded"] = True
        result["pending_titles"] = pending
    elif not getattr(note, "complete", True):
        result = partial_result(note, ctx, BUILT_NEXT_ACTION)
    else:
        result = ready_result(note, ctx.profile, BUILT_NEXT_ACTION)
    if warning:
        result["sync_warning"] = warning
    return result


def looks_like_model_outage(exc: BaseException) -> bool:
    """Whether the failure is the batch model being busy/unreachable (vs. a bug) → friendlier wording."""
    if isinstance(exc, insight_mod.ClusterUnavailableError):
        return True
    text = str(exc).lower()
    return any(
        marker in text for marker in ("503", "unavailable", "timeout", "timed out", "429", "resource_exhausted")
    )


def _sync_for_build(settings: BookmarkInsightSettings, profile: str) -> str | None:
    """Mirror Chrome bookmarks; return a warning string instead of failing the pipeline."""
    try:
        report = sync_mod.sync_profiles(settings, profiles=[profile])
    except NotImplementedError as exc:
        logger.warning("build_insight: sync unavailable, continuing with the repository as is: %s", exc)
        return f"sync skipped: {exc}"
    except Exception as exc:
        logger.warning("build_insight: sync failed, continuing with the repository as is: %s", exc)
        return f"sync failed: {type(exc).__name__}: {exc}"
    warnings = getattr(report, "warnings", None) or []
    return "; ".join(str(item) for item in warnings) or None


# --- get_bookmark --------------------------------------------------------------------


def extract_summary(body: str) -> str:
    """Return the text under ``## 요약`` (up to the next ``##`` heading), stripped."""
    lines = body.split("\n")
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == SUMMARY_HEADING), None)
    if start is None:
        return body.strip()
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def get_bookmark(id: Any = None, **_: Any) -> dict[str, Any]:  # noqa: A002 - the tool argument is named ``id``
    """Return one bookmark's front matter and stored summary by guid (ids are global, no profile)."""
    try:
        settings = load_settings()
        if not settings.configured:
            return not_configured_error()
        bookmark_id = str(id or "").strip()
        if not bookmark_id:
            return {"error": "id is required"}
        bookmark = find_bookmark(settings, bookmark_id)
        if bookmark is None:
            return {"error": f"bookmark not found: {bookmark_id}"}
        return {
            "id": bookmark.guid,
            "profile": bookmark.profile,
            "title": bookmark.title,
            "url": bookmark.url,
            "tags": list(bookmark.tags),
            "date": bookmark.date.isoformat(),
            "summary_status": bookmark.summary_status,
            "summary": extract_summary(bookmark.body),
            "today": today_iso(),
            "next_action": BOOKMARK_NEXT_ACTION,
        }
    except Exception as exc:
        logger.exception("get_bookmark failed")
        return error_result(exc)


# --- list_bookmarks ------------------------------------------------------------------


def list_bookmarks(
    days: Any = None, since: Any = None, until: Any = None, profile: Any = None, limit: Any = None, **_: Any
) -> dict[str, Any]:
    """List non-archived bookmarks in a period, newest first (secondary lookup)."""
    try:
        ctx = prepare_context({"days": days, "since": since, "until": until, "profile": profile})
        if not isinstance(ctx, ToolContext):
            return ctx
        try:
            count = DEFAULT_LIMIT if limit is None else int(limit)
        except (TypeError, ValueError):
            return {"error": f"limit must be an integer, got {limit!r}"}
        count = max(1, min(MAX_LIMIT, count))
        sync_before_read(ctx.settings, ctx.profile)
        bookmarks = bookmarks_in_period(ctx.settings, ctx.profile, ctx.period)[:count]
        return {
            "today": today_iso(),
            "profile": ctx.profile,
            "period": ctx.period.to_dict(),
            "count": len(bookmarks),
            "bookmarks": [bookmark.to_tool_summary() for bookmark in bookmarks],
            "next_action": LIST_NEXT_ACTION,
        }
    except Exception as exc:
        logger.exception("list_bookmarks failed")
        return error_result(exc)


# --- finish_insight ------------------------------------------------------------------


def finish_insight(
    conclusion: Any = None, reason: Any = None, transcript: str | None = None, **_: Any
) -> dict[str, Any]:
    """Close the open insight conversation and append its conclusion to the note (§3.3).

    ``transcript`` is an optional rendered ``대화 원문`` the host collected for
    the session (M8); it is attached collapsed under the conclusion. Hosts
    without a transcript (the MCP server) pass ``None``: conclusion only.
    """
    try:
        reason_text = str(reason or "explicit").strip()
        conclusion_text = str(conclusion or "").strip()
        if reason_text == "auto":
            return {"error": "reason 'auto' is reserved for the app"}
        if reason_text not in ALLOWED_REASONS:
            return {"error": f"reason must be one of {', '.join(ALLOWED_REASONS)}, got {reason_text!r}"}
        if not conclusion_text:
            return {"error": "conclusion is required"}
        session = SESSION_MANAGER.close()
        reopened_last = False
        if session is None:
            # Safety net (§3.3): the session was already closed — by the host (idle / sleep /
            # shutdown) or by a premature explicit finish — but the model finishes again with the
            # real conclusion. It belongs to the last note, so record it there once (consumed).
            last = SESSION_MANAGER.take_last_closed()
            if last is None or not Path(last.note_path).is_file():
                return {"error": "no open insight session", "next_action": NO_SESSION_NEXT_ACTION}
            session, reopened_last = last, True
        append_conversation_section(session.note_path, now_local_label(), conclusion_text, transcript=transcript)
        logger.info(
            "finish_insight: recorded conclusion (%s%s) in %s",
            reason_text,
            ", last closed session" if reopened_last else "",
            session.note_path,
        )
        result: dict[str, Any] = {
            "ok": True,
            "note_path": str(session.note_path),
            "reason": reason_text,
            "next_action": FINISH_NEXT_ACTION,
        }
        if reopened_last:
            result["reopened_last"] = True
        return result
    except Exception as exc:
        logger.exception("finish_insight failed")
        return error_result(exc)
