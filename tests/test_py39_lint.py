"""tools/py39_lint.py: the Python 3.9 compatibility lint (phase-1 close-out, K-GATE3).

The project runs on Python 3.9 (setup.py) and is developed on 3.14. Each case below is a construct that parses or runs
on 3.14 but fails on 3.9 -- only when its line runs, so nothing else finds it -- and must be reported with its line; the
controls are their 3.9-safe forms, which must not be. The last test runs the lint over the project (CI and the tier-1
gate's G1 run it too).
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("py39_lint_under_test", str(ROOT / "tools" / "py39_lint.py"))
lint = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lint)


def problems(source):
    return [(p.line, p.message) for p in lint.lint_source(textwrap.dedent(source), "case.py")]


class GrammarTests(unittest.TestCase):
    def test_match_statements_and_newer_grammar_fail_the_parse(self):
        found = problems("""
            def f(x):
                match x:
                    case 1:
                        return 1
            """)
        self.assertEqual(len(found), 1)
        self.assertIn("not Python 3.9 syntax", found[0][1])
        self.assertIn("not Python 3.9 syntax", problems("try:\n    pass\nexcept* ValueError:\n    pass\n")[0][1])
        self.assertIn("not Python 3.9 syntax", problems("def f[T](x: T) -> T:\n    return x\n")[0][1])

    def test_parenthesised_context_managers(self):
        self.assertEqual(problems("""
            with (open("a") as a, open("b") as b):
                pass
            with (open("a"), open("b")):
                pass
            """), [(2, "parenthesised context managers are 3.10+"), (4, "parenthesised context managers are 3.10+")])
        self.assertEqual(problems("""
            with (open("a")) as a:
                pass
            with (lock):
                pass
            with open("a") as a, open("b") as b:
                pass
            with (yield_cm()) as c, d:
                pass
            """), [])

    @unittest.skipUnless(sys.version_info >= (3, 12), "PEP 701 f-strings only tokenize on 3.12+ (on 3.9-3.11 the "
                                                      "grammar check rejects them)")
    def test_pep_701_f_strings(self):
        quote = 'x = f"{"a"}"\n'
        backslash = "y = f\"{'\\n'.join(z)}\"\n"
        comment = 'w = f"{1 # c\n}"\n'
        for source, needle in ((quote, "reuses its quote"), (backslash, "backslash"), (comment, "comment")):
            found = problems(source)
            self.assertTrue(any(needle in m for _, m in found), (source, found))
        for fine in ('x = f"{\'a\'}"\n', 'x = f"""{"a"}"""\n', "x = f'{d[\"k\"]!r:>{w}}'\n",
                     'x = f"{f\'{y}\'}"\n', 'x = f"a\\n{b}"\n', 'x = {f"{k}": v for k, v in d.items()}\n'):
            self.assertEqual(problems(fine), [], fine)


class UnionTests(unittest.TestCase):
    def test_unions_evaluated_at_run_time(self):
        found = problems("""
            from typing import Optional

            def f(x: int | None = None) -> str | None:
                local: int | None = None
                return None

            class C:
                y: dict | None = None

            Alias = int | None
            ok = isinstance(3, int | str)
            flags = 1 | 2
            """)
        self.assertEqual([line for line, _ in found], [4, 4, 9, 11, 12], found)    # not the local annotation (5)
        self.assertIn("evaluated at run time", found[0][1])
        self.assertIn("isinstance(x, A | B) is 3.10+", [m for _, m in found])

    def test_future_annotations_make_annotations_lazy_but_not_assignments(self):
        found = problems("""
            from __future__ import annotations

            def f(x: int | None = None) -> str | None:
                return None

            class C:
                y: dict | None = None

            Alias = int | None
            """)
        self.assertEqual(found, [(10, "a run-time union of types (X | Y) is 3.10+")])
        self.assertEqual(problems('def f(x: "int | None" = None):\n    return x\n'), [])     # a string annotation
        self.assertEqual(problems("import re\nflags = re.I | re.M\nbits = a | b\n"), [])


class StdlibTests(unittest.TestCase):
    def test_newer_calls_and_names(self):
        found = problems("""
            import itertools
            import math
            import tomllib
            from dataclasses import dataclass, field, KW_ONLY
            from itertools import pairwise
            from typing import Self

            pairs = list(zip([1], [2], strict=True))
            windows = list(itertools.pairwise([1, 2, 3]))
            ones = (5).bit_count()
            root = math.cbrt(8)

            @dataclass(slots=True, kw_only=True)
            class P:
                x: int = field(default=0, kw_only=True)

            async def g(it):
                return await anext(it)

            def h(path):
                path.write_text("x", encoding="utf-8", newline="\\n")
                return path.read_text(encoding="utf-8", newline="")
            """)
        messages = [m for _, m in found]
        for needle in ("tomllib is 3.11+", "dataclasses.KW_ONLY is 3.10+", "itertools.pairwise is 3.10+",
                       "typing.Self is 3.11+", "zip(strict=) is 3.10+", "int.bit_count is 3.10+",
                       "math.cbrt is 3.11+", "dataclass(slots=) is 3.10+", "dataclass(kw_only=) is 3.10+",
                       "dataclasses.field(kw_only=) is 3.10+", "anext is 3.10+",
                       "Path.write_text(newline=) is 3.10+", "Path.read_text(newline=) is 3.13+"):
            self.assertIn(needle, messages)
        self.assertEqual(found[0][0], 4)

    def test_python_3_9_features_are_fine(self):
        self.assertEqual(problems("""
            from functools import cache
            import zoneinfo, graphlib

            def f(names: list[str], d: dict[str, int]) -> tuple[int, ...]:
                merged = d | {"a": 1}
                return tuple(len(n.removeprefix("x").removesuffix("y")) for n in names)

            pairs = list(zip([1], [2]))
            """), [])


class ProjectTests(unittest.TestCase):
    def test_the_command_line_reports_and_exits_non_zero(self):
        tmp = Path(tempfile.mkdtemp(prefix="py39_lint_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(tmp), True))
        (tmp / "tools").mkdir()
        (tmp / "tools" / "bad.py").write_text("x = isinstance(1, int | str)\n", encoding="utf-8")
        (tmp / "tools" / "good.py").write_text("x = isinstance(1, (int, str))\n", encoding="utf-8")
        out = tmp / "out.json"
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "py39_lint.py"), "--root", str(tmp), "--json",
                               str(out)], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("tools/bad.py:1: isinstance(x, A | B) is 3.10+", proc.stdout)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((data["files"], len(data["violations"])), (2, 1))
        (tmp / "tools" / "bad.py").unlink()
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "py39_lint.py"), "--root", str(tmp)],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_every_python_file_of_the_project_is_python_3_9(self):
        count, found = lint.lint(ROOT)
        self.assertGreater(count, 300)
        self.assertEqual([str(p) for p in found], [], "\n".join(str(p) for p in found[:40]))


if __name__ == "__main__":
    unittest.main()
