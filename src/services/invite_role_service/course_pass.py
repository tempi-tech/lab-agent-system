from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CoursePassOutcome(str, Enum):
    GRANTED = "granted"
    ALREADY_HAS_ROLE = "already_has_role"
    WRONG_GUILD = "wrong_guild"
    ROLE_NOT_FOUND = "role_not_found"
    MEMBER_UNRESOLVED = "member_unresolved"
    MISSING_MANAGE_ROLES = "missing_manage_roles"
    HIERARCHY_BLOCKED = "hierarchy_blocked"


@dataclass(frozen=True)
class CoursePassDecision:
    outcome: CoursePassOutcome
    should_add_role: bool
    user_message: str
    log_detail: str


def evaluate_course_pass_claim(
    *,
    interaction_guild_id: int | None,
    expected_guild_id: int,
    member_is_resolved: bool,
    role_exists: bool,
    bot_member_resolved: bool,
    bot_has_manage_roles: bool,
    bot_top_role_position: int,
    role_position: int,
    member_has_role: bool,
) -> CoursePassDecision:
    """Pure decision function for a Course Pass claim. Keeps handler logic testable."""
    if interaction_guild_id != expected_guild_id:
        return CoursePassDecision(
            outcome=CoursePassOutcome.WRONG_GUILD,
            should_add_role=False,
            user_message="このサーバーでは使えません。",
            log_detail=f"wrong_guild interaction_guild={interaction_guild_id}",
        )
    if not member_is_resolved:
        return CoursePassDecision(
            outcome=CoursePassOutcome.MEMBER_UNRESOLVED,
            should_add_role=False,
            user_message="メンバー情報を確認できませんでした。",
            log_detail="member_unresolved",
        )
    if not role_exists:
        return CoursePassDecision(
            outcome=CoursePassOutcome.ROLE_NOT_FOUND,
            should_add_role=False,
            user_message="講座ロールが見つかりません。運営に連絡してください。",
            log_detail="role_not_found",
        )
    if not bot_member_resolved:
        return CoursePassDecision(
            outcome=CoursePassOutcome.MEMBER_UNRESOLVED,
            should_add_role=False,
            user_message="Bot の権限状態を確認できませんでした。",
            log_detail="bot_member_unresolved",
        )
    if not bot_has_manage_roles:
        return CoursePassDecision(
            outcome=CoursePassOutcome.MISSING_MANAGE_ROLES,
            should_add_role=False,
            user_message="Bot にロール付与権限がありません。運営に連絡してください。",
            log_detail="missing_manage_roles",
        )
    if bot_top_role_position <= role_position:
        return CoursePassDecision(
            outcome=CoursePassOutcome.HIERARCHY_BLOCKED,
            should_add_role=False,
            user_message="Bot のロール階層が足りません。運営に連絡してください。",
            log_detail=f"hierarchy_blocked bot_top={bot_top_role_position} role={role_position}",
        )
    if member_has_role:
        return CoursePassDecision(
            outcome=CoursePassOutcome.ALREADY_HAS_ROLE,
            should_add_role=False,
            user_message="すでに講座用ロールを持っています。",
            log_detail="already_has_role",
        )
    return CoursePassDecision(
        outcome=CoursePassOutcome.GRANTED,
        should_add_role=True,
        user_message="講座用ロールを付与しました。講座案内チャンネルから受講ガイドを確認してください。",
        log_detail="granted",
    )
