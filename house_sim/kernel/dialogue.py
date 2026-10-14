"""Canonical questions to people: the `ask` schema, subject resolution, facts, routes and necessity
(docs/design/TIER1_SWEEP.md sections 4 and 16.1).

Stub from the sweep's Phase 0 (tier-1 A0): the frozen signatures only, so the stage C packages can code against each
other. Every function raises NotImplementedError; the module registers nothing and nothing imports it yet.
PEOPLE-IO implements it (library work may start in stage A, import-inert) and removes the TIER1-STUB markers before
stage C closes.
"""
from collections import namedtuple

# The resolved subject of a question (sweep 4.4): kind is "entity" | "room" | "person" | "pet" | "house" | "word";
# id is the true id (None for words and the house); words are the normalized words of a word subject.
Subject = namedtuple("Subject", ["kind", "id", "words"], defaults=(None, ()))


def resolve_subject(env, pid, about):
    """The Subject of `about` as person `pid` understands it (handle, id, "it", "request", "house" or words).
    TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("dialogue.resolve_subject lands with PEOPLE-IO (TIER1_SWEEP 4.4)")


def match_fact(state, subject, aspect, detail):
    """The most specific fact answering (subject, aspect, detail), or None (sweep 4.3 step 4). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("dialogue.match_fact lands with PEOPLE-IO (TIER1_SWEEP 4.4)")


def necessary(env, pid, fact, subject):
    """Whether asking `pid` this was necessary (sweep 4.5). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("dialogue.necessary lands with PEOPLE-IO (TIER1_SWEEP 4.5)")


def route(name, aspects, fn):
    """Register a route from canonical questions to a legacy answerer topic (sweep 4.9). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("dialogue.route lands with PEOPLE-IO (TIER1_SWEEP 4.9)")


def answer(ctx, args):
    """Run the answer pipeline of one `ask` and return the answer dict (sweep 4.3). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("dialogue.answer lands with PEOPLE-IO (TIER1_SWEEP 4.3)")


def canonical_key(aspect, subject, detail):
    """The fact key "q:<aspect>:<subject>[:<detail>]" of a question (sweep 4.3 step 8). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("dialogue.canonical_key lands with PEOPLE-IO (TIER1_SWEEP 4.3)")


def vocabulary():
    """The loaded question vocabulary (aspects, acts, synonyms; data/vocab/*.json). TIER1-STUB(PEOPLE-IO)"""
    raise NotImplementedError("dialogue.vocabulary lands with PEOPLE-IO (TIER1_SWEEP 4.1)")
