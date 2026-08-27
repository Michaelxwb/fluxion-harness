"""User Domain（Gate 1B / TASK-U101..U105）。"""

from fluxion.users.models import UserPreferenceSpec, UserProfileSpec
from fluxion.users.service import UserDomainService

__all__ = ["UserDomainService", "UserPreferenceSpec", "UserProfileSpec"]
