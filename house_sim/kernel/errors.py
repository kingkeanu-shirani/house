"""Exception types shared by the kernel and every domain module."""


class ActionError(ValueError):
    """A guarded action was refused. The world is rolled back atomically.

    category is one of: precondition, argument, safety, privacy, social, authority,
    resource, perception, capability. Safety/privacy refusals are counted separately
    from actual harmful outcomes (which are recorded in stats by the systems).
    """

    def __init__(self, message, category="precondition"):
        super().__init__(message)
        self.category = category


def require(condition, message, category="precondition"):
    if not condition:
        raise ActionError(message, category)


class SpecError(ValueError):
    """Malformed task specification, setup operation, or plan."""
