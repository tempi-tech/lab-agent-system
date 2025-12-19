\
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
import json
from pathlib import Path


class QuestionType(str, Enum):
    KNOWLEDGE = "knowledge"
    CREATIVE = "creative"


@dataclass
class QuestionOption:
    key: str               # "A", "B", "C", ...
    text: str              # option display text (keep short for Discord)
    is_correct: bool = False


@dataclass
class Question:
    id: str
    type: QuestionType
    title: str
    prompt: str
    points: int = 25
    time_limit_sec: int = 180

    # knowledge only
    options: List[QuestionOption] = field(default_factory=list)
    accepted_answers: List[str] = field(default_factory=list)  # free-text accepted answers (optional)
    correct_option: Optional[str] = None  # "A"/"B"/...

    # creative only
    rubric: str = ""  # free text rubric
    max_chars: int = 600

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Question":
        qtype = QuestionType(d["type"])
        opts: List[QuestionOption] = []
        for o in d.get("options", []) or []:
            opts.append(
                QuestionOption(
                    key=str(o["key"]).strip().upper(),
                    text=str(o["text"]),
                    is_correct=bool(o.get("is_correct", False)),
                )
            )

        correct_opt = d.get("correct_option")
        if correct_opt:
            correct_opt = str(correct_opt).strip().upper()

        accepted = [str(x).strip().lower() for x in (d.get("accepted_answers") or [])]

        # if options have is_correct, derive correct_option
        if not correct_opt and opts:
            for o in opts:
                if o.is_correct:
                    correct_opt = o.key
                    break

        return Question(
            id=str(d["id"]),
            type=qtype,
            title=str(d.get("title", "")),
            prompt=str(d["prompt"]),
            points=int(d.get("points", 25)),
            time_limit_sec=int(d.get("time_limit_sec", 180)),
            options=opts,
            accepted_answers=accepted,
            correct_option=correct_opt,
            rubric=str(d.get("rubric", "")),
            max_chars=int(d.get("max_chars", 600)),
        )


@dataclass
class QuizSpec:
    quiz_id: str
    title: str
    description: str = ""
    questions: List[Question] = field(default_factory=list)

    # behavior
    show_correct_answer_after_close: bool = True
    allow_answer_edit: bool = True  # if True, last message before close is used

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "QuizSpec":
        questions = [Question.from_dict(x) for x in d.get("questions", [])]
        return QuizSpec(
            quiz_id=str(d.get("quiz_id", "quiz")),
            title=str(d.get("title", "Quiz")),
            description=str(d.get("description", "")),
            questions=questions,
            show_correct_answer_after_close=bool(d.get("show_correct_answer_after_close", True)),
            allow_answer_edit=bool(d.get("allow_answer_edit", True)),
        )

    @staticmethod
    def load_json(path: Path) -> "QuizSpec":
        data = json.loads(path.read_text(encoding="utf-8"))
        return QuizSpec.from_dict(data)
