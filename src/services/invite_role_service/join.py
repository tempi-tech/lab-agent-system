from __future__ import annotations


def plan_join_role_ids(*, used_code: str | None, review_role_id: int | None) -> list[int]:
    """Role ids to add when a member joins.

    The general member role (agi-lab) is never granted here. Who holds it is
    decided by agi-backend (membership or a time-limited grant). On join we only
    add the review role when the invite code could not be detected.
    """
    if used_code is None and review_role_id is not None:
        return [review_role_id]
    return []
