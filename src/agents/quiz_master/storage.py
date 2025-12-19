\
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path
import json
import time


@dataclass
class Submission:
    user_id: int
    user_display: str
    content: str
    created_at: float  # unix seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Submission":
        return Submission(
            user_id=int(d["user_id"]),
            user_display=str(d.get("user_display", "")),
            content=str(d.get("content", "")),
            created_at=float(d.get("created_at", time.time())),
        )


@dataclass
class QuestionGrading:
    scores: Dict[str, int] = field(default_factory=dict)      # user_id -> points
    reasons: Dict[str, str] = field(default_factory=dict)     # user_id -> short reason (creative)
    meta: Dict[str, Any] = field(default_factory=dict)        # extra debug info


@dataclass
class QuizSessionState:
    # immutable-ish
    quiz_id: str = ""
    quiz_title: str = ""

    # where it runs
    guild_id: int = 0
    channel_id: int = 0

    # session seed for transparent draws
    draw_seed: str = ""         # reveal at the end
    draw_seed_hash: str = ""    # publish at start

    # progress
    started_at: float = 0.0
    current_index: int = -1
    is_question_open: bool = False
    current_question_id: str = ""

    question_message_id: int = 0
    thread_id: int = 0

    # submissions per question_id per user_id
    submissions: Dict[str, Dict[str, Submission]] = field(default_factory=dict)

    # grading per question_id
    grading: Dict[str, QuestionGrading] = field(default_factory=dict)

    # totals per user_id
    totals: Dict[str, int] = field(default_factory=dict)
    users: Dict[str, str] = field(default_factory=dict)  # user_id -> display name

    def to_dict(self) -> Dict[str, Any]:
        # manual because dataclasses + nested objects
        subs_ser: Dict[str, Dict[str, Any]] = {}
        for qid, by_user in self.submissions.items():
            subs_ser[qid] = {uid: sub.to_dict() for uid, sub in by_user.items()}

        grading_ser: Dict[str, Any] = {}
        for qid, g in self.grading.items():
            grading_ser[qid] = {
                "scores": g.scores,
                "reasons": g.reasons,
                "meta": g.meta,
            }

        d = asdict(self)
        d["submissions"] = subs_ser
        d["grading"] = grading_ser
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "QuizSessionState":
        st = QuizSessionState(
            quiz_id=str(d.get("quiz_id", "")),
            quiz_title=str(d.get("quiz_title", "")),
            guild_id=int(d.get("guild_id", 0)),
            channel_id=int(d.get("channel_id", 0)),
            draw_seed=str(d.get("draw_seed", "")),
            draw_seed_hash=str(d.get("draw_seed_hash", "")),
            started_at=float(d.get("started_at", 0.0)),
            current_index=int(d.get("current_index", -1)),
            is_question_open=bool(d.get("is_question_open", False)),
            current_question_id=str(d.get("current_question_id", "")),
            question_message_id=int(d.get("question_message_id", 0)),
            thread_id=int(d.get("thread_id", 0)),
            submissions={},
            grading={},
            totals={str(k): int(v) for k, v in (d.get("totals", {}) or {}).items()},
            users={str(k): str(v) for k, v in (d.get("users", {}) or {}).items()},
        )

        subs_in = d.get("submissions") or {}
        for qid, by_user in subs_in.items():
            st.submissions[str(qid)] = {str(uid): Submission.from_dict(sd) for uid, sd in (by_user or {}).items()}

        grading_in = d.get("grading") or {}
        for qid, gd in grading_in.items():
            g = QuestionGrading(
                scores={str(uid): int(sc) for uid, sc in (gd.get("scores") or {}).items()},
                reasons={str(uid): str(rs) for uid, rs in (gd.get("reasons") or {}).items()},
                meta=gd.get("meta") or {},
            )
            st.grading[str(qid)] = g

        return st


class JsonStore:
    """Tiny JSON persistence for one-night events (no DB needed)."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> Optional[QuizSessionState]:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return QuizSessionState.from_dict(data)

    def save(self, state: QuizSessionState) -> None:
        self.path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
