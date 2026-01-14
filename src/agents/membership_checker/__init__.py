"""membership_checker エージェント"""
from .logic import MembershipCheckerAgent


def get_agent() -> MembershipCheckerAgent:
    """エージェントインスタンスを取得"""
    return MembershipCheckerAgent()


__all__ = ["MembershipCheckerAgent", "get_agent"]
