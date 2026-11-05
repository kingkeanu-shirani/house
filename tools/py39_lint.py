"""Python 3.9 compatibility lint: every .py file of the project must run on the oldest supported Python (setup.py).

usage: python tools/py39_lint.py [PATH ...] [--root DIR] [--json OUT] [--quiet]

The project is developed on Python 3.14 and supported from 3.9; a 3.10+ feature in a line that only runs on some path
(a tool, a rare branch, a test helper) fails nowhere until someone runs it on 3.9. This lint finds them statically
(stdlib only, no import of the checked code):

* grammar: `ast.parse(source, feature_version=(3, 9))` (match statements, `except*`, type parameters, ...);
* parenthesised context managers (`with (a as b, c as d):`, documented from 3.10);
* f-strings of PEP 701 (3.12): an enclosing quote reused inside a replacement field, a backslash or a comment in the
  expression part, a line break inside a single-quoted f-string's field (checked from the token stream on 3.12+; on
  3.9-3.11 the grammar check rejects them);
* PEP 604 unions evaluated at run time: `X | Y` in a function signature or a module- or class-level variable
  annotation of a module without `from __future__ import annotations`, `isinstance(x, A | B)` / `issubclass`, and a
  union of builtin types or `None` anywhere else (`Alias = int | None`);
* grammar `ast.parse(feature_version=)` lets through on a newer interpreter: an unparenthesised assignment expression in
  an index or a set (3.10), a star in a subscript or a `*args: *Ts` annotation (3.11);
* forward references Python 3.14 evaluates lazily (PEP 649) and every older Python at definition time: an annotation of
  a module- or class-level definition naming a class or function defined further down (or its own enclosing class, or
  a name imported only under `if TYPE_CHECKING:`), without `from __future__ import annotations`;
* standard-library features newer than 3.9: `zip(strict=)`, `itertools.pairwise` / `batched`, `int.bit_count`,
  `dataclass(slots= / kw_only= / match_args=)`, `field(kw_only=)`, `dataclasses.KW_ONLY`, the `bisect` functions' `key=`,
  `aiter` / `anext`, `tomllib`, `datetime.UTC`, and the names in NEWER below (math.cbrt, enum.StrEnum, contextlib.chdir,
  typing.TypeAlias / ParamSpec / Self / ..., types.NoneType / UnionType, ...), through import aliases as well
  (`import itertools as it`, `from dataclasses import dataclass as dc`); unittest's assertNoLogs / enterContext;
  `Counter.total`, `Path.walk` & co. on a receiver known to be one; `open(encoding="locale")`; `(5).to_bytes()` and
  `int.from_bytes(b)` without their byte order; `traceback.print_exception(exc)`; `glob(root_dir=)`;
  `subprocess.run(process_group=)`; regular expressions with atomic groups or possessive quantifiers;
  `fromisoformat("...Z")`. `str.removeprefix` and friends are 3.9.

Default paths: house_sim/, tools/ and tests/ under --root (default: this project) and the top-level *.py files. Prints
`path:line: message` per violation and a total; exit status 1 when there is any (CI and the tier-1 gate's G1 run it),
0 otherwise. --json writes {"files", "violations": [{"path", "line", "message"}]}.
"""
import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIRS = ("house_sim", "tools", "tests")
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules"}
WITH_PAREN = re.compile(r"\bwith\s*\(")
FSTRING = re.compile(r"(?i)(?<![\w])[rb]?f[rb]?['\"]")

# module attributes (and `from module import name`) newer than Python 3.9: {module: {name: version}}
NEWER = {
    "itertools": {"pairwise": "3.10", "batched": "3.12"},
    "math": {"cbrt": "3.11", "exp2": "3.11", "sumprod": "3.12", "fma": "3.13"},
    "enum": {"StrEnum": "3.11", "verify": "3.11", "member": "3.11", "nonmember": "3.11", "global_enum": "3.11",
             "ReprEnum": "3.11", "EnumCheck": "3.11", "FlagBoundary": "3.11"},
    "contextlib": {"aclosing": "3.10", "chdir": "3.11"},
    "datetime": {"UTC": "3.11"},
    "dataclasses": {"KW_ONLY": "3.10"},
    "typing": {"TypeAlias": "3.10", "ParamSpec": "3.10", "ParamSpecArgs": "3.10", "ParamSpecKwargs": "3.10",
               "Concatenate": "3.10", "TypeGuard": "3.10", "is_typeddict": "3.10", "Self": "3.11",
               "LiteralString": "3.11", "Never": "3.11", "assert_never": "3.11", "assert_type": "3.11",
               "reveal_type": "3.11", "Required": "3.11", "NotRequired": "3.11", "Unpack": "3.11",
               "TypeVarTuple": "3.11", "dataclass_transform": "3.11", "get_overloads": "3.11",
               "clear_overloads": "3.11", "override": "3.12", "TypeAliasType": "3.12", "ReadOnly": "3.13",
               "TypeIs": "3.13", "NoDefault": "3.13"},
    "types": {"NoneType": "3.10", "EllipsisType": "3.10", "NotImplementedType": "3.10", "UnionType": "3.10"},
    "inspect": {"get_annotations": "3.10", "markcoroutinefunction": "3.12", "getmembers_static": "3.11",
                "BufferFlags": "3.12", "ismethodwrapper": "3.11"},
    "sys": {"orig_argv": "3.10", "stdlib_module_names": "3.10", "exception": "3.11", "monitoring": "3.12",
            "activate_stack_trampoline": "3.12", "is_stack_trampoline_active": "3.12"},
    "statistics": {"correlation": "3.10", "covariance": "3.10", "linear_regression": "3.10"},
    "operator": {"call": "3.11"},
    "hashlib": {"file_digest": "3.11"},
    "asyncio": {"TaskGroup": "3.11", "timeout": "3.11", "timeout_at": "3.11", "Runner": "3.11", "Barrier": "3.11"},
    "logging": {"getLevelNamesMapping": "3.11", "getHandlerByName": "3.12", "getHandlerNames": "3.12"},
    "re": {"NOFLAG": "3.11"},
    "random": {"binomialvariate": "3.12"},
    "os.path": {"splitroot": "3.12", "isjunction": "3.12", "isreserved": "3.13", "isdevdrive": "3.12"},
    "os": {"process_cpu_count": "3.13", "listdrives": "3.12", "listmounts": "3.12", "listvolumes": "3.12"},
    "copy": {"replace": "3.13"},
    "warnings": {"deprecated": "3.13"},
    "glob": {"translate": "3.13"},
    "queue": {"ShutDown": "3.13"},
    "pathlib": {"UnsupportedOperation": "3.13"},
    "base64": {"z85encode": "3.13", "z85decode": "3.13"},
    "collections.abc": {"Buffer": "3.12"},
}
NEWER_MODULES = {"tomllib": "3.11", "compression": "3.14"}
NEWER_BUILTINS = {"aiter": "3.10", "anext": "3.10", "ExceptionGroup": "3.11", "BaseExceptionGroup": "3.11",
                  "EncodingWarning": "3.10"}
# attribute names that are 3.10+ on any object of the type they belong to (the receiver is not known statically)
NEWER_METHODS = {"bit_count": "int.bit_count is 3.10+", "hardlink_to": "Path.hardlink_to is 3.10+",
                 "is_junction": "Path.is_junction is 3.12+", "assertNoLogs": "TestCase.assertNoLogs is 3.10+",
                 "enterContext": "TestCase.enterContext is 3.11+",
                 "enterClassContext": "TestCase.enterClassContext is 3.11+",
                 "enterModuleContext": "unittest.enterModuleContext is 3.11+",
                 "enterAsyncContext": "IsolatedAsyncioTestCase.enterAsyncContext is 3.11+"}
# methods new on one type only, whose name other types also have: reported when the receiver is known to be that type
PATH_METHODS = {"walk": "Path.walk is 3.12+", "with_segments": "Path.with_segments is 3.12+",
                "full_match": "Path.full_match is 3.13+", "from_uri": "Path.from_uri is 3.13+"}
COUNTER_METHODS = {"total": "Counter.total is 3.10+"}
# keyword arguments newer than 3.9: {callable name: {keyword: what}}
NEWER_KEYWORDS = {
    "zip": {"strict": "zip(strict=) is 3.10+"},
    "dataclass": {"slots": "dataclass(slots=) is 3.10+", "kw_only": "dataclass(kw_only=) is 3.10+",
                  "match_args": "dataclass(match_args=) is 3.10+", "weakref_slot": "dataclass(weakref_slot=) is 3.11+"},
    "field": {"kw_only": "dataclasses.field(kw_only=) is 3.10+"},
    "bisect": {"key": "bisect(key=) is 3.10+"}, "bisect_left": {"key": "bisect_left(key=) is 3.10+"},
    "bisect_right": {"key": "bisect_right(key=) is 3.10+"}, "insort": {"key": "insort(key=) is 3.10+"},
    "insort_left": {"key": "insort_left(key=) is 3.10+"}, "insort_right": {"key": "insort_right(key=) is 3.10+"},
    "TemporaryDirectory": {"ignore_cleanup_errors": "TemporaryDirectory(ignore_cleanup_errors=) is 3.10+",
                           "delete": "TemporaryDirectory(delete=) is 3.12+"},
    "NamedTemporaryFile": {"delete_on_close": "NamedTemporaryFile(delete_on_close=) is 3.12+"},
    "rmtree": {"onexc": "shutil.rmtree(onexc=) is 3.12+"},
    # pathlib (first CI run on a real 3.9: house_sim/eval/viewer.py and tools/vocab.py)
    "write_text": {"newline": "Path.write_text(newline=) is 3.10+"},
    "read_text": {"newline": "Path.read_text(newline=) is 3.13+"},
    "relative_to": {"walk_up": "Path.relative_to(walk_up=) is 3.12+"},
    "exists": {"follow_symlinks": "Path.exists(follow_symlinks=) is 3.12+"},
    "glob": {"root_dir": "glob(root_dir=) is 3.10+", "dir_fd": "glob(dir_fd=) is 3.10+",
             "include_hidden": "glob(include_hidden=) is 3.11+", "case_sensitive": "Path.glob(case_sensitive=) is 3.12+",
             "recurse_symlinks": "Path.glob(recurse_symlinks=) is 3.13+"},
    "iglob": {"root_dir": "iglob(root_dir=) is 3.10+", "dir_fd": "iglob(dir_fd=) is 3.10+",
              "include_hidden": "iglob(include_hidden=) is 3.11+"},
    "rglob": {"case_sensitive": "Path.rglob(case_sensitive=) is 3.12+",
              "recurse_symlinks": "Path.rglob(recurse_symlinks=) is 3.13+"},
    "run": {"process_group": "subprocess.run(process_group=) is 3.11+",
            "pipesize": "subprocess.run(pipesize=) is 3.10+"},
    "Popen": {"process_group": "subprocess.Popen(process_group=) is 3.11+",
              "pipesize": "subprocess.Popen(pipesize=) is 3.10+"},
    "call": {"process_group": "subprocess.call(process_group=) is 3.11+"},
    "check_call": {"process_group": "subprocess.check_call(process_group=) is 3.11+"},
    "check_output": {"process_group": "subprocess.check_output(process_group=) is 3.11+"},
    "fmean": {"weights": "statistics.fmean(weights=) is 3.11+"},
    "map": {"strict": "map(strict=) is 3.14+"},
}
# the re functions a literal pattern is read from (their first argument)
RE_CALLS = ("compile", "match", "search", "fullmatch", "sub", "subn", "split", "findall", "finditer")
# names that are certainly types: a `|` with one of them (or None) outside an annotation is a run-time PEP 604 union
TYPE_NAMES = {"int", "float", "complex", "str", "bytes", "bytearray", "bool", "list", "dict", "set", "frozenset",
              "tuple", "type", "object", "memoryview", "range", "slice"}
TYPING_GENERICS = {"Optional", "Union", "List", "Dict", "Set", "Tuple", "Callable", "Sequence", "Mapping", "Iterable"}


class Problem:
    __slots__ = ("path", "line", "message")

    def __init__(self, path, line, message):
        self.path, self.line, self.message = path, line, message

    def as_dict(self):
        return {"path": self.path, "line": self.line, "message": self.message}

    def __str__(self):
        return "{}:{}: {}".format(self.path, self.line, self.message)


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _dotted(node):
    """"os.path" for os.path, "math" for math, None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _has_union(node):
    """True when an expression contains a `|` outside string constants (a string annotation is never evaluated)."""
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(node))


def _type_like(node, classes=()):
    """True when an expression is certainly a type: a builtin type, None, a class this module defines, or a subscript
    of one (or of a typing generic)."""
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.Name) and (node.id in TYPE_NAMES or node.id in classes):
        return True
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and (
            node.value.id in TYPE_NAMES or node.value.id in classes or node.value.id in TYPING_GENERICS):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _type_like(node.left, classes) or _type_like(node.right, classes)
    return False


def _annotations(tree):
    """[(node, where)] of the annotations Python evaluates at run time: every function signature, and variable
    annotations at module or class level (a function's local annotations are never evaluated)."""
    out = []
    stack = [(tree, False)]
    while stack:
        node, in_function = stack.pop()
        # only statements hold definitions and variable annotations (an expression never does: lambdas take none)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = child.args
                for a in list(getattr(args, "posonlyargs", [])) + list(args.args) + list(args.kwonlyargs) + \
                        [x for x in (args.vararg, args.kwarg) if x is not None]:
                    if a.annotation is not None:
                        out.append((a.annotation, "an annotation"))
                if child.returns is not None:
                    out.append((child.returns, "a return annotation"))
                stack.append((child, True))
            elif isinstance(child, ast.ClassDef):
                stack.append((child, False))
            elif isinstance(child, ast.AnnAssign):
                if not in_function:
                    out.append((child.annotation, "a variable annotation"))
            elif isinstance(child, (ast.stmt, ast.excepthandler)):
                stack.append((child, in_function))
    return out


def _future_annotations(tree):
    return any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and
               any(a.name == "annotations" for a in n.names) for n in tree.body)


def _tokens(source):
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []


def _parenthesised_with(nodes, tokens):
    """Lines of `with (a as b, c):` -- the parentheses enclose the whole item list and it has `as` or a comma at their
    top level (Python 3.10 grammar; `with (a):` and `with (a) as b:` are parenthesised expressions, fine on 3.9)."""
    by_line = {}
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.NAME and tok.string == "with":
            by_line.setdefault(tok.start[0], []).append((tok.start[1], i))
    out = []
    for node in nodes:
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        index = next((i for col, i in by_line.get(node.lineno, ()) if col >= node.col_offset), None)
        if index is None or index + 1 >= len(tokens):
            continue
        j = index + 1
        while j < len(tokens) and tokens[j].type in (tokenize.NL, tokenize.COMMENT):
            j += 1
        if j >= len(tokens) or tokens[j].string != "(":
            continue
        depth, top_level, k = 0, False, j
        while k < len(tokens):
            s = tokens[k].string
            if tokens[k].type == tokenize.OP and s in "([{":
                depth += 1
            elif tokens[k].type == tokenize.OP and s in ")]}":
                depth -= 1
                if depth == 0:
                    break
            elif depth == 1 and (s == "," and tokens[k].type == tokenize.OP or
                                 s == "as" and tokens[k].type == tokenize.NAME):
                top_level = True
            k += 1
        after = k + 1
        while after < len(tokens) and tokens[after].type in (tokenize.NL, tokenize.COMMENT):
            after += 1
        if after < len(tokens) and tokens[after].string == ":" and top_level:
            out.append(node.lineno)
    return out


def _fstring_problems(tokens):
    """PEP 701 f-strings (3.12+) from the token stream (FSTRING_START / FSTRING_MIDDLE / FSTRING_END exist from 3.12):
    [(line, message)]."""
    start = getattr(tokenize, "FSTRING_START", None)
    if start is None:
        return []
    middle, end = tokenize.FSTRING_MIDDLE, tokenize.FSTRING_END
    stack, out = [], []

    def quote_of(text):
        body = text.lstrip("rRbBfFuU")
        if body[:3] in ('"""', "'''"):
            return body[:3]
        return body[:1]

    def in_expression():
        return any(level["depth"] > 0 for level in stack)

    for tok in tokens:
        if stack and in_expression():
            if tok.type in (tokenize.STRING, start):
                q = quote_of(tok.string)
                for level in stack:
                    if level["depth"] > 0 and (q == level["quote"] or (len(level["quote"]) == 1 and
                                                                          q[:1] == level["quote"])):
                        out.append((tok.start[0], "an f-string reuses its quote {} inside a replacement field "
                                                  "(3.12+)".format(level["quote"])))
                        break
                if "\\" in tok.string:
                    out.append((tok.start[0], "a backslash in an f-string's expression part (3.12+)"))
            elif tok.type == middle and "\\" in tok.string and len(stack) > 1 and \
                    any(level["depth"] > 0 for level in stack[:-1]):
                out.append((tok.start[0], "a backslash in an f-string's expression part (3.12+)"))
            elif tok.type == tokenize.COMMENT:
                out.append((tok.start[0], "a comment inside an f-string (3.12+)"))
            elif tok.type in (tokenize.NL, tokenize.NEWLINE) and any(len(level["quote"]) == 1 and level["depth"] > 0
                                                                     for level in stack):
                out.append((tok.start[0], "a line break inside a single-quoted f-string's replacement field "
                                          "(3.12+)"))
        if tok.type == start:
            stack.append({"quote": quote_of(tok.string), "depth": 0})
        elif tok.type == end and stack:
            stack.pop()
        elif stack and tok.type == tokenize.OP and tok.string in ("{", "}"):
            stack[-1]["depth"] += 1 if tok.string == "{" else -1
    seen, unique = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _parenthesised(node, lines):
    """True when the source just before an expression is "(" (ast keeps no parentheses): `a[(x := 1)]` is 3.9."""
    try:
        before = lines[node.lineno - 1].encode("utf-8")[:node.col_offset].decode("utf-8", "replace").rstrip()
    except (IndexError, AttributeError):
        return True
    line = node.lineno - 1
    while not before and line > 0:
        line -= 1
        before = lines[line].rstrip()
    return before.endswith("(")


def _tuple_parenthesised(node, lines):
    """A Tuple's col_offset is its "(" when it has parentheses."""
    try:
        return lines[node.lineno - 1].encode("utf-8")[node.col_offset:node.col_offset + 1] == b"("
    except IndexError:
        return True


def _grammar_problems(nodes, lines):
    """What ast.parse(feature_version=(3, 9)) lets through on a newer interpreter: [(line, message)]."""
    out = []
    for node in nodes:
        if isinstance(node, ast.Subscript):
            index = node.slice
            items = [index]
            if isinstance(index, ast.Tuple) and not _tuple_parenthesised(index, lines):
                items = list(index.elts)
            for item in items:
                if isinstance(item, ast.NamedExpr) and not _parenthesised(item, lines):
                    out.append((item.lineno, "an unparenthesised assignment expression in an index is 3.10+"))
                elif isinstance(item, ast.Starred):
                    out.append((item.lineno, "a star in a subscript is 3.11+ (PEP 646)"))
        elif isinstance(node, ast.Set):
            for item in node.elts:
                if isinstance(item, ast.NamedExpr) and not _parenthesised(item, lines):
                    out.append((item.lineno, "an unparenthesised assignment expression in a set is 3.10+"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.args.vararg is not None and \
                isinstance(node.args.vararg.annotation, ast.Starred):
            out.append((node.lineno, "a star in a *args annotation is 3.11+ (PEP 646)"))
    return out


def _bound_by(stmt):
    """The names a module- or class-level statement binds when it runs."""
    names = set()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(stmt.name)
    elif isinstance(stmt, ast.Import):
        names.update((a.asname or a.name).split(".")[0] for a in stmt.names)
    elif isinstance(stmt, ast.ImportFrom):
        names.update(a.asname or a.name for a in stmt.names)
    elif isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.With, ast.If, ast.Try, ast.While)):
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Import):
                names.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(a.asname or a.name for a in node.names)
    return names


def _type_checking_test(test):
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or \
        (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _forward_references(tree):
    """[(line, name)] of annotations evaluated at definition time that name something not bound yet: a class or function
    defined further down in the same scope, the enclosing class itself, or a name imported only under TYPE_CHECKING.
    Fine on 3.14 (PEP 649 evaluates them lazily), NameError on every older Python."""
    out = []
    only_typing = set()

    def scan(stmts, bound, enclosing, outer=()):
        bound = set(bound)
        later = set(outer)
        for stmt in stmts:
            if not (isinstance(stmt, ast.If) and _type_checking_test(stmt.test)):
                later |= _bound_by(stmt)
        for stmt in stmts:
            anns = []
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = stmt.args
                anns = [a.annotation for a in list(getattr(args, "posonlyargs", [])) + list(args.args) +
                        list(args.kwonlyargs) + [x for x in (args.vararg, args.kwarg) if x is not None]
                        if a.annotation is not None]
                if stmt.returns is not None:
                    anns.append(stmt.returns)
            elif isinstance(stmt, ast.AnnAssign):
                anns = [stmt.annotation]
            for ann in anns:
                for node in ast.walk(ann):
                    if isinstance(node, ast.Name) and node.id not in bound and (
                            node.id in enclosing or node.id in only_typing or node.id in later):
                        out.append((node.lineno, node.id))
            if isinstance(stmt, ast.ClassDef):
                scan(stmt.body, bound, set(enclosing) | {stmt.name}, later)
            elif isinstance(stmt, ast.If) and _type_checking_test(stmt.test):
                for inner in stmt.body:
                    only_typing.update(_bound_by(inner))
                scan(stmt.orelse, bound, enclosing, later)
            elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                inner = [s for field in ("body", "orelse", "finalbody") for s in getattr(stmt, field, []) or []]
                for handler in getattr(stmt, "handlers", []) or []:
                    inner += handler.body
                scan(inner, bound, enclosing, later)
            if not (isinstance(stmt, ast.If) and _type_checking_test(stmt.test)):
                bound |= _bound_by(stmt)
    scan(tree.body, (), ())
    return out


def _possessive(pattern):
    """The 3.11+ regular expression syntax in a literal pattern: "atomic" for (?>...), "possessive" for *+ ++ ?+ {m,n}+;
    else None. Escapes and character classes are skipped."""
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            if pattern[i + 1:i + 2] == "^":
                i += 1
            if pattern[i + 1:i + 2] == "]":
                i += 1
        elif pattern.startswith("(?>", i):
            return "atomic"
        elif c in "*+?}" and pattern[i + 1:i + 2] == "+" and (c != "?" or i == 0 or pattern[i - 1] != "("):
            return "possessive"
        i += 1
    return None


def _kinds_of_names(tree):
    """({name assigned a pathlib path}, {name assigned a collections.Counter}, is_path): one pass over simple
    assignments (twice, for names assigned from names assigned earlier)."""
    paths, counters = set(), set()

    def is_path(value):
        if isinstance(value, ast.Call):
            name = _call_name(value.func)
            if name in ("Path", "PurePath", "PosixPath", "WindowsPath"):
                return True
            if isinstance(value.func, ast.Attribute) and name in ("resolve", "absolute", "with_name", "with_suffix",
                                                                  "expanduser", "joinpath"):
                return is_path(value.func.value)
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div):
            return is_path(value.left) or is_path(value.right)
        if isinstance(value, ast.Name):
            return value.id in paths
        if isinstance(value, ast.Attribute) and value.attr in ("parent", "parents"):
            return is_path(value.value)
        if isinstance(value, ast.Subscript):
            return is_path(value.value)
        return False

    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if is_path(node.value):
                    paths.add(name)
                elif isinstance(node.value, ast.Call) and _call_name(node.value.func) == "Counter":
                    counters.add(name)
    return paths, counters, is_path


def lint_source(source, path="<source>"):
    """[Problem] of one module's source."""
    problems = []
    try:
        tree = ast.parse(source, filename=path, feature_version=(3, 9))
    except SyntaxError as exc:
        return [Problem(path, exc.lineno or 0, "not Python 3.9 syntax: {}".format(exc.msg))]
    nodes = list(ast.walk(tree))
    for line, message in _grammar_problems(nodes, source.splitlines()):
        problems.append(Problem(path, line, message))
    # the token checks only where they can find something (tokenizing every file doubles the lint's time)
    with_paren = WITH_PAREN.search(source) is not None
    fstrings = getattr(tokenize, "FSTRING_START", None) is not None and FSTRING.search(source) is not None
    tokens = _tokens(source) if with_paren or fstrings else []
    if with_paren:
        for line in _parenthesised_with(nodes, tokens):
            problems.append(Problem(path, line, "parenthesised context managers are 3.10+"))
    if fstrings:
        for line, message in _fstring_problems(tokens):
            problems.append(Problem(path, line, message))
    future = _future_annotations(tree)
    # expressions the general union rule leaves to a more specific one: every annotation (a local variable's is never
    # evaluated) and the class argument of isinstance / issubclass
    claimed = set()
    for node in nodes:
        parts = []
        if isinstance(node, ast.AnnAssign):
            parts = [node.annotation]
        elif isinstance(node, ast.Call) and _call_name(node.func) in ("isinstance", "issubclass") and \
                len(node.args) == 2:
            parts = [node.args[1]]
        for part in parts:
            claimed.update(id(n) for n in ast.walk(part))
    for ann, what in _annotations(tree):
        claimed.update(id(n) for n in ast.walk(ann))
        if not future and _has_union(ann):
            problems.append(Problem(path, getattr(ann, "lineno", 0), "X | Y in {} is evaluated at run time: 3.10+ "
                                                                     "without from __future__ import annotations"
                                    .format(what)))
    if not future:
        for line, name in _forward_references(tree):
            problems.append(Problem(path, line, "the annotation names {!r} before it is bound: evaluated at definition "
                                                "time below 3.14 (NameError) without from __future__ import "
                                                "annotations".format(name)))
    classes = {n.name for n in nodes if isinstance(n, ast.ClassDef)}
    imported = {}           # local name -> (module, name) of `from module import name`
    modules = {}            # local name -> the module of `import module as name`
    paths, counters, is_path = _kinds_of_names(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported[alias.asname or alias.name] = (node.module, alias.name)
                full = "{}.{}".format(node.module, alias.name)
                if full in NEWER:       # from os import path; from collections import abc
                    modules[alias.asname or alias.name] = full

    def base_module(value):
        """The module an attribute's receiver names, through import aliases."""
        base = _dotted(value)
        if not base:
            return None
        head, _, rest = base.partition(".")
        head = modules.get(head, head)
        return head + ("." + rest if rest else "")

    def call_key(func):
        """The callable's own name, through `from x import name as alias`."""
        if isinstance(func, ast.Name) and func.id in imported:
            return imported[func.id][1]
        return _call_name(func)

    for node in nodes:
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                version = NEWER.get(node.module, {}).get(alias.name)
                if version:
                    problems.append(Problem(path, line, "{}.{} is {}+".format(node.module, alias.name, version)))
            if node.module.split(".")[0] in NEWER_MODULES:
                problems.append(Problem(path, line, "{} is {}+".format(node.module,
                                                                       NEWER_MODULES[node.module.split(".")[0]])))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in NEWER_MODULES:
                    problems.append(Problem(path, line, "{} is {}+".format(alias.name, NEWER_MODULES[top])))
        elif isinstance(node, ast.Attribute):
            base = base_module(node.value)
            version = NEWER.get(base, {}).get(node.attr) if base else None
            if version:
                problems.append(Problem(path, line, "{}.{} is {}+".format(base, node.attr, version)))
            elif node.attr in NEWER_METHODS:
                problems.append(Problem(path, line, NEWER_METHODS[node.attr]))
            elif node.attr in PATH_METHODS and is_path(node.value):
                problems.append(Problem(path, line, PATH_METHODS[node.attr]))
            elif node.attr in COUNTER_METHODS and (
                    (isinstance(node.value, ast.Name) and node.value.id in counters) or
                    (isinstance(node.value, ast.Call) and call_key(node.value.func) == "Counter")):
                problems.append(Problem(path, line, COUNTER_METHODS[node.attr]))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in NEWER_BUILTINS and \
                node.id not in imported:
            problems.append(Problem(path, line, "{} is {}+".format(node.id, NEWER_BUILTINS[node.id])))
        elif isinstance(node, ast.Call):
            name = call_key(node.func)
            for kw in node.keywords:
                what = NEWER_KEYWORDS.get(name, {}).get(kw.arg)
                if what:
                    problems.append(Problem(path, line, what))
                if kw.arg == "encoding" and isinstance(kw.value, ast.Constant) and kw.value.value == "locale":
                    problems.append(Problem(path, line, "encoding=\"locale\" is 3.10+"))
            if name in ("isinstance", "issubclass") and len(node.args) == 2 and _has_union(node.args[1]):
                problems.append(Problem(path, line, "{}(x, A | B) is 3.10+".format(name)))
            positional, keywords = len(node.args), {kw.arg for kw in node.keywords}
            first = node.args[0] if node.args else None
            literal = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
            if isinstance(node.func, ast.Attribute):
                attr, owner = node.func.attr, base_module(node.func.value)
                if attr == "to_bytes" and not positional and "length" not in keywords and None not in keywords:
                    problems.append(Problem(path, line, "int.to_bytes() without length and byteorder is 3.11+"))
                elif attr == "from_bytes" and owner == "int" and positional < 2 and \
                        "byteorder" not in keywords and None not in keywords:
                    problems.append(Problem(path, line, "int.from_bytes() without byteorder is 3.11+"))
                elif attr in ("print_exception", "format_exception", "format_exception_only") and \
                        owner == "traceback" and positional == 1 and not keywords:
                    problems.append(Problem(path, line, "traceback.{}(exc) with the exception alone is 3.10+".format(
                        attr)))
                elif attr == "fromisoformat" and positional == 1 and literal and literal.endswith(("Z", "z")):
                    problems.append(Problem(path, line, "fromisoformat() reading a trailing Z is 3.11+"))
                elif attr in RE_CALLS and owner == "re" and literal is not None:
                    kind = _possessive(literal)
                    if kind:
                        problems.append(Problem(path, line, "{} regular expression syntax is 3.11+".format(kind)))
            elif isinstance(node.func, ast.Name) and name in ("print_exception", "format_exception",
                                                              "format_exception_only") and \
                    imported.get(node.func.id, (None,))[0] == "traceback" and positional == 1 and not keywords:
                problems.append(Problem(path, line, "traceback.{}(exc) with the exception alone is 3.10+".format(name)))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and id(node) not in claimed and \
                (_type_like(node.left, classes) or _type_like(node.right, classes)):
            problems.append(Problem(path, line, "a run-time union of types (X | Y) is 3.10+"))
    # one report per line and message (a chain a | b | None is one union)
    seen, out = set(), []
    for p in sorted(problems, key=lambda p: (p.line, p.message)):
        key = (p.line, p.message)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def python_files(root, paths=None):
    """The .py files to check: `paths` (files or folders, relative to root) or the default folders and the top-level
    *.py files."""
    root = Path(root)
    targets = [root / p for p in paths] if paths else [root / d for d in DEFAULT_DIRS] + sorted(root.glob("*.py"))
    files = []
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            files.append(target)
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if not SKIP_DIRS.intersection(path.relative_to(target).parts):
                    files.append(path)
    return sorted(set(files))


def lint(root=ROOT, paths=None):
    """(files checked, [Problem]) over the project (paths relative to root, posix)."""
    root = Path(root).resolve()
    problems, files = [], python_files(root, paths)
    for path in files:
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem(rel, 0, "unreadable: {}".format(exc)))
            continue
        problems += lint_source(source, rel)
    return len(files), problems


def main(argv=None):
    p = argparse.ArgumentParser(description="Python 3.9 compatibility lint (stdlib only)")
    p.add_argument("paths", nargs="*", help="files or folders relative to --root (default: house_sim tools tests and "
                                            "the top-level *.py files)")
    p.add_argument("--root", default=str(ROOT), help="project root (default: this project)")
    p.add_argument("--json", help="write the result as JSON here")
    p.add_argument("--quiet", action="store_true", help="print the total only")
    args = p.parse_args(argv)
    count, problems = lint(args.root, args.paths or None)
    if not args.quiet:
        for problem in problems:
            print(problem)
    print("py39_lint: {} file(s), {} violation(s){}".format(count, len(problems),
                                                           "" if problems else " (every file is Python 3.9)"))
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"files": count, "violations": [x.as_dict() for x in problems]}, f, indent=1)
            f.write("\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
