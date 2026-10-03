"""The limit applies to each callable, including closures and local methods."""

from helpers import RepositoryCase

FOUR = """def decide(value):
    if value == 1:
        return 1
    if value == 2:
        return 2
    if value == 3:
        return 3
    return 0
"""
FIVE = """def decide(value):
    if value == 1:
        return 1
    if value == 2:
        return 2
    if value == 3:
        return 3
    if value == 4:
        return 4
    return 0
"""


class PythonGateTests(RepositoryCase):
    def test_complexity_four_passes(self) -> None:
        self.write("logic.py", FOUR)
        self.assertEqual(self.scan().violations, [])

    def test_complexity_five_fails(self) -> None:
        self.write("logic.py", FIVE)
        violation = self.scan().violations[0]
        self.assertEqual(violation.rule, "python-complexity")
        self.assertIn("complexity 5", violation.message)

    def test_nested_closure_five_fails(self) -> None:
        nested = "\n".join("    " + line for line in FIVE.splitlines())
        self.write("closure.py", "def outer(value):\n" + nested + "\n    return decide(value)\n")
        violations = self.scan().violations
        self.assertEqual(len(violations), 1)
        self.assertIn("decide has complexity 5", violations[0].message)
        self.assertEqual(violations[0].line, 2)

    def test_method_of_nested_class_is_checked(self) -> None:
        method = FIVE.replace("def decide(value)", "def decide(self, value)")
        nested = "\n".join("        " + line for line in method.splitlines())
        self.write("local_class.py", "def outer():\n    class Local:\n" + nested + "\n")
        self.assertEqual(self.rules(self.scan()), ["python-complexity"])

    def test_async_function_is_checked(self) -> None:
        self.write("async_logic.py", FIVE.replace("def decide", "async def decide"))
        self.assertEqual(self.rules(self.scan()), ["python-complexity"])

    def test_lambda_is_checked(self) -> None:
        self.write(
            "lambda_logic.py",
            "choose = lambda x: 1 if x else 2 if x else 3 if x else 4 if x else 0\n",
        )
        violation = self.scan().violations[0]
        self.assertIn("<lambda> has complexity 5", violation.message)

    def test_comprehensions_filters_and_boolean_branches_count(self) -> None:
        self.write(
            "comprehension.py",
            "def select(xs):\n    return [x for x in xs if x and x > 0 and x < 2]\n",
        )
        self.assertEqual(self.rules(self.scan()), ["python-complexity"])

    def test_asserts_count_as_radon_branches(self) -> None:
        self.write("assertions.py", "def validate(x):\n" + "    assert x\n" * 4)
        self.assertEqual(self.rules(self.scan()), ["python-complexity"])

    def test_class_aggregate_complexity_is_not_a_function_limit(self) -> None:
        methods = "\n".join("    " + line for line in FOUR.splitlines())
        text = "class Decisions:\n" + methods.replace("decide(value)", "first(self, value)")
        text += "\n" + methods.replace("decide(value)", "second(self, value)")
        self.write("class_logic.py", text)
        self.assertEqual(self.scan().violations, [])

    def test_python_syntax_error_fails(self) -> None:
        self.write("broken.py", "def broken(\n")
        self.assertEqual(self.rules(self.scan()), ["python-syntax"])

    def test_invalid_top_level_return_fails(self) -> None:
        self.write("broken.py", "return 1\n")
        self.assertEqual(self.rules(self.scan()), ["python-syntax"])

    def test_nul_in_python_is_not_silently_skipped_as_binary(self) -> None:
        self.write("broken.py", "value = 1\x00\n")
        self.assertEqual(self.rules(self.scan()), ["python-syntax"])
