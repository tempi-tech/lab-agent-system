from src.services.invite_role_service.course_pass import (
    CoursePassOutcome,
    evaluate_course_pass_claim,
)


def test_course_pass_claim_grants_role_when_ready() -> None:
    decision = evaluate_course_pass_claim(
        interaction_guild_id=1,
        expected_guild_id=1,
        member_is_resolved=True,
        role_exists=True,
        bot_member_resolved=True,
        bot_has_manage_roles=True,
        bot_top_role_position=10,
        role_position=3,
        member_has_role=False,
    )

    assert decision.outcome is CoursePassOutcome.GRANTED
    assert decision.should_add_role is True


def test_course_pass_claim_skips_when_member_already_has_role() -> None:
    decision = evaluate_course_pass_claim(
        interaction_guild_id=1,
        expected_guild_id=1,
        member_is_resolved=True,
        role_exists=True,
        bot_member_resolved=True,
        bot_has_manage_roles=True,
        bot_top_role_position=10,
        role_position=3,
        member_has_role=True,
    )

    assert decision.outcome is CoursePassOutcome.ALREADY_HAS_ROLE
    assert decision.should_add_role is False


def test_course_pass_claim_blocks_without_manage_roles() -> None:
    decision = evaluate_course_pass_claim(
        interaction_guild_id=1,
        expected_guild_id=1,
        member_is_resolved=True,
        role_exists=True,
        bot_member_resolved=True,
        bot_has_manage_roles=False,
        bot_top_role_position=10,
        role_position=3,
        member_has_role=False,
    )

    assert decision.outcome is CoursePassOutcome.MISSING_MANAGE_ROLES
    assert decision.should_add_role is False


def test_course_pass_claim_blocks_when_hierarchy_is_too_low() -> None:
    decision = evaluate_course_pass_claim(
        interaction_guild_id=1,
        expected_guild_id=1,
        member_is_resolved=True,
        role_exists=True,
        bot_member_resolved=True,
        bot_has_manage_roles=True,
        bot_top_role_position=3,
        role_position=3,
        member_has_role=False,
    )

    assert decision.outcome is CoursePassOutcome.HIERARCHY_BLOCKED
    assert decision.should_add_role is False
