"""people domain: residents, guests, visitors and infants as agents (roles, authority, abilities,
schedules, sleep, moods, privacy), the user simulator's hidden intents and world-aware question
scoring, communication (alerts, relays, calls, knocking, waking), physical assistance (bracing,
steadying, guiding, dressing), care (temperatures, medication fetching/logging without dosing,
heating pads, hearing aids, stories, bedtime, supervised play), the front door (screening,
recognition, letting in, declining, signing), photos and video-call framing, and the household
norms and invariants that make wrong behaviour fail for real reasons.

Importing this package registers everything (see docs/domains/people.md).
"""
from . import common  # noqa: F401
from . import senses  # noqa: F401
from . import model  # noqa: F401
from . import routine  # noqa: F401
from . import comms  # noqa: F401
from . import reach  # noqa: F401
from . import assist  # noqa: F401
from . import care  # noqa: F401
from . import children  # noqa: F401
from . import door  # noqa: F401
from . import social  # noqa: F401
from . import harms  # noqa: F401
from . import knowledge  # noqa: F401
from . import perception  # noqa: F401
from . import metrics  # noqa: F401
from . import memory  # noqa: F401
from . import furnish  # noqa: F401
from . import helpers  # noqa: F401
from . import skills  # noqa: F401
