"""Typed reports to people: report kinds, vocabulary, units, delivery records and the match API used by the
`report` / `report_set` predicate forms (docs/design/TIER1_SWEEP.md sections 5 and 16.1).

Stub from the sweep's Phase 0 (tier-1 A0): the frozen signatures only, so the stage C packages can code against each
other. Every function raises NotImplementedError; the module registers nothing and nothing imports it yet.
PEOPLE-IO implements it (library work may start in stage A, import-inert) and removes the TIER1-STUB markers before
stage C closes. TIME-EVAL's predicates call match / match_set.
"""


def normalize(args, state):
    """The canonical report record for a `report` command's arguments (sweep 5.1). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("reports.normalize lands with PEOPLE-IO (TIER1_SWEEP 5.1)")


def record(ctx, record):
    """Store a normalized report (report_log, delivery) and return the stored record (sweep 5.3).
    TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("reports.record lands with PEOPLE-IO (TIER1_SWEEP 5.3)")


def match(state, expectation):
    """Whether some stored report meets `expectation` (the `report` predicate form, sweep 5.4).
    TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("reports.match lands with PEOPLE-IO (TIER1_SWEEP 5.4)")


def match_set(state, spec):
    """(precision, recall, detail) of the stored reports against an expected set (`report_set`, sweep 5.4).
    TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("reports.match_set lands with PEOPLE-IO (TIER1_SWEEP 5.4)")


def convert(value, unit, quantity):
    """`value` in `unit` converted to the SI unit of `quantity` (sweep 5.2). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("reports.convert lands with PEOPLE-IO (TIER1_SWEEP 5.2)")


def format_quantity(state, value, quantity):
    """An SI value as the household reads it (locale units and formats, sweep 5.2). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("reports.format_quantity lands with PEOPLE-IO (TIER1_SWEEP 5.2)")


def issue_words(category):
    """The vocabulary words of an issue category (sweep 5.2). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("reports.issue_words lands with PEOPLE-IO (TIER1_SWEEP 5.2)")
