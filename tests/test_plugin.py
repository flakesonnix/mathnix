#!/usr/bin/env python3
"""Exercise the compiled plugin through real Nix evaluators, not C++ mocks."""

import argparse
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest


parser = argparse.ArgumentParser()
parser.add_argument("--nix", default="nix-instantiate")
parser.add_argument("--plugin", required=True)
parser.add_argument("--launcher")
OPTIONS, UNITTEST_ARGS = parser.parse_known_args()

BIT_UNARY = {"bitNot", "popCount", "countLeadingZeros", "countTrailingZeros", "byteSwap"}
BIT_BINARY = {"bitAnd", "bitOr", "bitXor"}
BIT_INDEXED = {
    "shiftLeft", "shiftRight", "shiftRightArithmetic", "rotateLeft", "rotateRight",
    "testBit", "setBit", "clearBit", "toggleBit",
}

UNARY = {
    "abs", "sign", "sqrt", "cbrt", "exp", "exp2", "expm1", "log", "log2",
    "log10", "log1p", "sin", "cos", "tan", "asin", "acos", "atan", "sinh",
    "cosh", "tanh", "asinh", "acosh", "atanh", "radians", "degrees", "ceil",
    "floor", "trunc", "round", "frexp", "modf", "erf", "erfc", "gamma",
    "lgamma", "factorial", "isqrt", "sum", "product", "mean", "norm",
} | BIT_UNARY
BINARY = {
    "add", "sub", "mul", "div", "min", "max", "pow", "hypot", "logBase",
    "atan2", "fmod", "remainder", "copysign", "nextAfter", "fdim", "ldexp",
    "isClose", "gcd", "lcm", "comb", "perm", "powInt", "mod", "dot",
} | BIT_BINARY | BIT_INDEXED
TERNARY = {"clamp", "lerp", "fma", "isCloseWith"}
CONSTANTS = {
    "pi": math.pi,
    "tau": math.tau,
    "e": math.e,
    "phi": (1 + math.sqrt(5)) / 2,
    "sqrt2": math.sqrt(2),
    "ln2": math.log(2),
    "ln10": math.log(10),
    "epsilon": 2.0 ** -52,
    "minFloat": float.fromhex("0x1p-1022"),
    "maxFloat": float.fromhex("0x1.fffffffffffffp+1023"),
    "minInt": -(2 ** 63),
    "maxInt": 2 ** 63 - 1,
    "version": "0.1.0",
}


def literal(value):
    """Parenthesize negatives so Nix parses applications rather than subtraction."""
    # Nix parses unary minus separately and rejects the positive 2^63 literal.
    if isinstance(value, int) and value == -(2 ** 63):
        return "(-9223372036854775807 - 1)"
    return f"({json.dumps(value)})"


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nix = shutil.which(OPTIONS.nix)
        if not cls.nix:
            raise RuntimeError(f"Nix evaluator not found: {OPTIONS.nix}")
        cls.plugin = str(Path(OPTIONS.plugin).resolve(strict=True))
        preferred_tmp = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else None
        cls.temp = tempfile.TemporaryDirectory(
            prefix="mathnix-tests-", dir=os.environ.get("TMPDIR", preferred_tmp)
        )
        cls.addClassCleanup(cls.temp.cleanup)
        cls.env = dict(os.environ)
        cls.env.update({
            "HOME": cls.temp.name,
            "NIX_CONF_DIR": cls.temp.name,
            "NIX_USER_CONF_FILES": "/dev/null",
            "NIX_CONFIG": "experimental-features = nix-command flakes\n",
            "NIX_STATE_DIR": cls.temp.name + "/state",
            "NIX_STORE_DIR": cls.temp.name + "/store",
            "NIX_REMOTE": "local",
            "NIX_PATH": "",
            "NO_COLOR": "1",
        })

    def run_expression(self, expression, modern=False, launcher=False):
        expression = "let m = builtins.math; in " + expression
        if launcher:
            command = [OPTIONS.launcher, "--offline", "eval", "--json"]
        elif modern:
            command = [
                str(Path(self.nix).parent / "nix"), "--offline",
                "--option", "plugin-files", self.plugin, "eval", "--json",
            ]
        else:
            command = [
                self.nix, "--option", "plugin-files", self.plugin,
                "--eval", "--strict", "--json",
            ]
        return subprocess.run(
            command + ["--expr", expression], env=self.env,
            text=True, capture_output=True, timeout=30, check=False,
        )

    def evaluate(self, expression, **kwargs):
        result = self.run_expression(expression, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_values(self, cases):
        expressions = "[ " + " ".join(f"({expr})" for expr, _ in cases) + " ]"
        actual = self.evaluate(expressions)
        self.assertEqual(len(actual), len(cases))
        for (expression, expected), value in zip(cases, actual):
            with self.subTest(expression=expression):
                if isinstance(expected, float):
                    self.assertTrue(
                        math.isclose(value, expected, rel_tol=3e-13, abs_tol=0),
                        f"{value!r} != {expected!r}",
                    )
                else:
                    self.assertEqual(value, expected)

    def test_inventory_and_constants(self):
        expected = UNARY | BINARY | TERNARY | CONSTANTS.keys()
        self.assertEqual(self.evaluate("builtins.attrNames m"), sorted(expected))
        self.assertEqual(
            self.evaluate("builtins.attrNames (builtins.removeAttrs m "
                          "(builtins.filter (n: builtins.isFunction m.${n}) (builtins.attrNames m)))"),
            sorted(CONSTANTS),
        )
        self.assert_values([(f"m.{name}", value) for name, value in CONSTANTS.items()])

    def test_every_function(self):
        cases = {
            "add": ("2 3", 5.0), "sub": ("2 3", -1.0),
            "mul": ("2 3", 6.0), "div": ("5 2", 2.5),
            "abs": ("(-7)", 7), "sign": ("(-7)", -1),
            "min": ("2 3", 2.0), "max": ("2 3", 3.0),
            "clamp": ("0 1 2", 1.0), "lerp": ("10 20 0.25", 12.5),
            "fma": ("2 3 4", 10.0), "sqrt": ("9", 3.0),
            "cbrt": ("(-27)", -3.0), "pow": ("2 (-3)", 0.125),
            "hypot": ("3 4", 5.0), "exp": ("1", math.e),
            "exp2": ("3", 8.0), "expm1": ("1.0e-12", math.expm1(1e-12)),
            "log": ("m.e", 1.0), "log2": ("8", 3.0),
            "log10": ("1000", 3.0), "log1p": ("1.0e-12", math.log1p(1e-12)),
            "logBase": ("2 1024", 10.0), "sin": ("(m.pi / 2)", 1.0),
            "cos": ("0", 1.0), "tan": ("0.5", math.tan(0.5)),
            "asin": ("1", math.pi / 2), "acos": ("(-1)", math.pi),
            "atan": ("1", math.pi / 4), "atan2": ("1 (-1)", 3 * math.pi / 4),
            "sinh": ("1", math.sinh(1)), "cosh": ("1", math.cosh(1)),
            "tanh": ("1", math.tanh(1)), "asinh": ("1", math.asinh(1)),
            "acosh": ("2", math.acosh(2)), "atanh": ("0.5", math.atanh(0.5)),
            "radians": ("180", math.pi), "degrees": ("m.pi", 180.0),
            "ceil": ("(-1.2)", -1), "floor": ("(-1.2)", -2),
            "trunc": ("(-1.9)", -1), "round": ("(-1.5)", -2),
            "fmod": ("(-5.5) 2", -1.5), "remainder": ("5.5 2", -0.5),
            "copysign": ("3 (-2)", -3.0),
            "nextAfter": ("1 2", math.nextafter(1, 2)), "fdim": ("3 2", 1.0),
            "frexp": ("12", {"fraction": 0.75, "exponent": 4}),
            "ldexp": ("0.75 4", 12.0),
            "modf": ("(-3.25)", {"integral": -3.0, "fraction": -0.25}),
            "erf": ("1", math.erf(1)), "erfc": ("3", math.erfc(3)),
            "gamma": ("5", 24.0), "lgamma": ("5", math.lgamma(5)),
            "isClose": ("1 (1 + 1.0e-10)", True),
            "isCloseWith": ("{ absTol = 0.01; } 0 0.001", True),
            "gcd": ("(-54) 24", 6), "lcm": ("(-6) 8", 24),
            "factorial": ("20", math.factorial(20)), "comb": ("52 5", math.comb(52, 5)),
            "perm": ("10 3", 720), "isqrt": ("m.maxInt", math.isqrt(2 ** 63 - 1)),
            "powInt": ("2 60", 2 ** 60), "mod": ("(-5) 3", 1),
            "sum": ("[ 1.0e16 1 (-1.0e16) ]", 1.0),
            "product": ("[ 2 3 4 ]", 24.0), "mean": ("[ 2 3 4 ]", 3.0),
            "norm": ("[ 3 4 12 ]", 13.0), "dot": ("[ 1 2 3 ] [ 4 5 6 ]", 32.0),
            "bitAnd": ("12 10", 8), "bitOr": ("12 10", 14),
            "bitXor": ("12 10", 6), "bitNot": ("0", -1),
            "shiftLeft": ("3 4", 48), "shiftRight": ("(-1) 1", 2 ** 63 - 1),
            "shiftRightArithmetic": ("(-8) 2", -2),
            "rotateLeft": ("m.minInt 1", 1), "rotateRight": ("1 1", -(2 ** 63)),
            "popCount": ("(-1)", 64), "countLeadingZeros": ("1", 63),
            "countTrailingZeros": ("8", 3), "byteSwap": ("1", 2 ** 56),
            "testBit": ("8 3", True), "setBit": ("0 63", -(2 ** 63)),
            "clearBit": ("(-1) 63", 2 ** 63 - 1), "toggleBit": ("8 3", 0),
        }
        self.assertEqual(cases.keys(), UNARY | BINARY | TERNARY)
        self.assert_values([(f"m.{name} {args}", value) for name, (args, value) in cases.items()])

    def test_numerical_edges(self):
        self.assert_values([
            ("m.ceil m.maxInt", 2 ** 63 - 1),
            ("m.floor m.minInt", -(2 ** 63)),
            ("m.trunc 9007199254740993", 9007199254740993),
            ("m.round m.maxInt", 2 ** 63 - 1),
            ("m.round 2.5", 3), ("m.round (-2.5)", -3),
            ("m.floor (-0.1)", -1), ("m.ceil (-0.1)", 0),
            ("m.floor (-9.223372036854776e18)", -(2 ** 63)),
            ("m.abs (-9007199254740993)", 9007199254740993),
            ("m.abs (-0.5)", 0.5), ("m.sign 0", 0), ("m.sign 0.5", 1),
            ("m.pow 0 0", 1.0), ("m.powInt 0 0", 1),
            ("m.powInt (-2) 63", -(2 ** 63)),
            ("m.powInt m.minInt 1", -(2 ** 63)),
            ("m.powInt (-1) m.maxInt", -1),
            ("m.powInt 0 m.maxInt", 0),
            ("m.gcd 0 0", 0), ("m.gcd m.minInt 2", 2),
            ("m.lcm m.minInt 0", 0), ("m.factorial 0", 1),
            ("m.comb 4 5", 0), ("m.perm 4 5", 0),
            ("m.comb m.maxInt 1", 2 ** 63 - 1),
            ("m.comb m.maxInt m.maxInt", 1),
            ("m.comb 66 33", math.comb(66, 33)),
            ("m.perm m.maxInt 1", 2 ** 63 - 1),
            ("m.perm m.maxInt 0", 1),
            ("m.mod m.minInt m.maxInt", 2 ** 63 - 2),
            ("m.isqrt 0", 0), ("m.isqrt 1", 1), ("m.isqrt 15", 3),
            ("m.isqrt 9223372030926249001", 3037000499),
            ("m.isqrt 9223372030926249000", 3037000498),
            ("m.sum []", 0.0), ("m.product []", 1.0),
            ("m.norm []", 0.0), ("m.dot [] []", 0.0),
            ("m.sum [ 1.0e308 1.0e308 (-1.0e308) ]", 1e308),
            ("m.mean [ 1.0e308 1.0e308 ]", 1e308),
            ("m.dot [ 1.0e308 1.0e308 ] [ 2 (-2) ]", 0.0),
            ("m.hypot 1.0e308 1.0e308", math.hypot(1e308, 1e308)),
            ("m.norm [ 1.0e-300 1.0e-300 ]", math.hypot(1e-300, 1e-300)),
            ("m.ldexp 1 (-1074)", 5e-324),
            ("m.ldexp 1 (-1075)", 0.0),
            ("m.nextAfter 0 1", 5e-324),
            ("m.exp (-1000)", 0.0),
            ("m.copysign 1 (m.copysign 0 (-1))", -1.0),
            ("m.lerp (-1.0e308) 1.0e308 0.5", 0.0),
            ("m.fma (1 + m.epsilon) (1 - m.epsilon) (-1)", -(2.0 ** -104)),
            ("m.remainder 5 2", 1.0), ("m.remainder 7 2", -1.0),
            ("m.gamma (-0.5)", math.gamma(-0.5)),
            ("m.lgamma (-0.5)", math.lgamma(-0.5)),
            ("m.frexp (m.ldexp (-1) (-1074))", {"fraction": -0.5, "exponent": -1073}),
            ("m.fdim 2 3", 0.0), ("m.logBase 0.5 4", -2.0),
            ("m.isClose 0 1.0e-12", False),
            ("m.isCloseWith {} 1 1", True),
            ("m.isCloseWith { relTol = 0; absTol = 0; } 1 1", True),
            ("m.isCloseWith { relTol = 0; } 1 1.0000000001", False),
            ("m.isCloseWith { relTol = 1; } 1.0e308 (-1.0e308)", False),
            ("m.isCloseWith { relTol = 2; } 1.0e308 (-1.0e308)", True),
            ("(m.frexp 0).exponent", 0),
            ("(m.modf m.maxFloat).integral", CONSTANTS["maxFloat"]),
        ])

    def test_types_currying_and_laziness(self):
        self.assertEqual(self.evaluate('''{
          integer = builtins.typeOf (m.abs 3);
          float = builtins.typeOf (m.abs 3.0);
          division = builtins.typeOf (m.div 4 2);
          rounded = builtins.typeOf (m.round 3.0);
          sum = builtins.typeOf (m.sum [ 1 2 ]);
          boolean = builtins.typeOf (m.isClose 1 1);
          partial = (m.pow 2) 4;
          mapped = builtins.map m.sqrt [ 0 1 4 9 ];
          lazy = let unused = m.sqrt (-1); in m.sqrt 4;
        }'''), {
            "integer": "int", "float": "float", "division": "float",
            "rounded": "int", "sum": "float", "boolean": "bool",
            "partial": 16.0, "mapped": [0.0, 1.0, 2.0, 3.0], "lazy": 2.0,
        })

    def test_wrong_types_for_every_function(self):
        for names, arity in [(UNARY, 1), (BINARY, 2), (TERNARY, 3)]:
            for name in sorted(names):
                with self.subTest(function=name):
                    expression = f'm.{name} "not a number"' + " 1" * (arity - 1)
                    result = self.run_expression(expression)
                    self.assertGreater(result.returncode, 0, result.stderr)
                    self.assertIn("error:", result.stderr)

    def test_domain_and_overflow_errors(self):
        cases = {
            "m.sqrt (-1)": "non-finite result",
            "m.log 0": "non-finite result", "m.log (-1)": "non-finite result",
            "m.log1p (-1)": "non-finite result", "m.log2 0": "non-finite result",
            "m.log10 0": "non-finite result",
            "m.asin 2": "non-finite result", "m.acos 2": "non-finite result",
            "m.acosh 0": "non-finite result", "m.atanh 1": "non-finite result",
            "m.pow (-1) 0.5": "non-finite result",
            "m.pow 0 (-1)": "non-finite result",
            "m.div 1 0": "non-finite result", "m.div 0 0": "non-finite result",
            "m.fmod 1 0": "non-finite result", "m.remainder 1 0": "non-finite result",
            "m.gamma 0": "non-finite result", "m.gamma (-2)": "non-finite result",
            "m.lgamma 0": "non-finite result", "m.gamma 172": "non-finite result",
            "m.exp 1000": "non-finite result", "m.exp2 1024": "non-finite result",
            "m.sinh 1000": "non-finite result", "m.cosh 1000": "non-finite result",
            "m.add m.maxFloat m.maxFloat": "non-finite result",
            "m.mul m.maxFloat 2": "non-finite result",
            "m.nextAfter m.maxFloat m.maxFloat": None,
            "m.sum [ m.maxFloat m.maxFloat ]": "non-finite result",
            "m.product [ m.maxFloat 2 ]": "overflow",
            "m.dot [ m.maxFloat ] [ 2 ]": "non-finite result",
            "m.floor 9.223372036854776e18": "64-bit integer range",
            "m.ceil (-9.223372036854778e18)": "64-bit integer range",
            "m.round 1.0e100": "64-bit integer range",
            "m.trunc 1.0e100": "64-bit integer range",
            "m.abs m.minInt": "overflow",
            "m.gcd m.minInt 0": "64-bit range",
            "m.lcm m.minInt 1": "64-bit range",
            "m.lcm m.maxInt 2": "64-bit range",
            "m.powInt 2 63": "integer overflow",
            "m.powInt m.minInt 2": "integer overflow",
            "m.factorial 21": "overflows",
            "m.factorial m.maxInt": "overflows",
            "m.comb 67 33": "integer overflow",
            "m.comb m.maxInt 2": "integer overflow",
            "m.perm 21 21": "integer overflow",
            "m.perm m.maxInt m.maxInt": "integer overflow",
            "m.factorial (-1)": "nonnegative", "m.isqrt (-1)": "nonnegative",
            "m.comb 4 (-1)": "nonnegative", "m.comb (-1) 0": "nonnegative",
            "m.perm 4 (-1)": "nonnegative", "m.powInt 2 (-1)": "nonnegative",
            "m.mod 1 0": "positive modulus", "m.mod 1 (-1)": "positive modulus",
            "m.logBase 1 2": "logBase requires", "m.logBase (-2) 2": "logBase requires",
            "m.logBase 2 0": "logBase requires", "m.clamp 2 1 0": "lower <= upper",
            "m.mean []": "nonempty", "m.dot [ 1 ] []": "equal length",
            "m.sum [ 1 true ]": "error:", "m.product [ 0 true ]": "error:",
            "m.gcd 4.0 2": "error:", "m.ldexp 1 2.0": "error:",
            "m.ldexp 1 m.maxInt": "native int range",
            "m.ldexp 1 1024": "non-finite result",
            "m.isCloseWith { relTol = -1; } 1 1": "nonnegative",
            "m.isCloseWith { absTol = -1; } 1 1": "nonnegative",
            "m.isCloseWith { typo = 1; } 1 1": "only accepts",
            'm.sqrt (throw "argument sentinel")': "argument sentinel",
            "m.sqrt (m.maxFloat * 2)": "finite numbers",
        }
        for expression, message in cases.items():
            with self.subTest(expression=expression):
                if message is None:
                    self.assertEqual(self.evaluate(expression), CONSTANTS["maxFloat"])
                    continue
                result = self.run_expression(expression)
                self.assertGreater(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stderr)

    def test_randomized_integer_reference(self):
        rng = random.Random(20261102)
        cases = []
        for _ in range(80):
            a, b = rng.randrange(-10 ** 8, 10 ** 8), rng.randrange(1, 10 ** 8)
            n, k = rng.randrange(61), rng.randrange(61)
            perm_n, perm_k = rng.randrange(21), rng.randrange(25)
            root = rng.randrange(2 ** 63)
            base, exponent = rng.randrange(-10, 11), rng.randrange(16)
            cases.extend([
                (f"m.gcd {literal(a)} {b}", math.gcd(a, b)),
                (f"m.lcm {literal(a)} {b}", math.lcm(a, b)),
                (f"m.mod {literal(a)} {b}", a % b),
                (f"m.comb {n} {k}", math.comb(n, k)),
                (f"m.perm {perm_n} {perm_k}", math.perm(perm_n, perm_k)),
                (f"m.isqrt {root}", math.isqrt(root)),
                (f"m.powInt {literal(base)} {exponent}", base ** exponent),
            ])
        self.assert_values(cases)

    def test_bit_boundaries(self):
        self.assert_values([
            ("m.bitNot m.minInt", 2 ** 63 - 1),
            ("m.bitNot m.maxInt", -(2 ** 63)),
            ("m.bitNot (-1)", 0),
            ("m.bitAnd m.minInt m.maxInt", 0),
            ("m.bitOr m.minInt m.maxInt", -1),
            ("m.bitXor m.minInt (-1)", 2 ** 63 - 1),
            ("m.shiftLeft 1 63", -(2 ** 63)),
            ("m.shiftLeft m.minInt 1", 0),
            ("m.shiftLeft (-1) 63", -(2 ** 63)),
            ("m.shiftRight m.minInt 63", 1),
            ("m.shiftRightArithmetic m.minInt 63", -1),
            ("m.shiftRightArithmetic (-3) 1", -2),
            ("m.rotateLeft m.minInt 1", 1),
            ("m.rotateRight 1 1", -(2 ** 63)),
            ("m.popCount 0", 0), ("m.popCount m.maxInt", 63),
            ("m.popCount m.minInt", 1),
            ("m.countLeadingZeros 0", 64), ("m.countTrailingZeros 0", 64),
            ("m.countLeadingZeros (-1)", 0), ("m.countTrailingZeros (-1)", 0),
            ("m.countLeadingZeros m.minInt", 0), ("m.countTrailingZeros m.minInt", 63),
            ("m.byteSwap m.minInt", 128), ("m.byteSwap 128", -(2 ** 63)),
            ("m.byteSwap (-1)", -1),
            ("m.byteSwap 72623859790382856", 578437695752307201),
            ("m.testBit m.minInt 63", True), ("m.testBit m.minInt 0", False),
            ("m.setBit 0 0", 1), ("m.setBit m.minInt 63", -(2 ** 63)),
            ("m.clearBit m.minInt 63", 0), ("m.clearBit 0 0", 0),
            ("m.toggleBit m.minInt 63", 0), ("m.toggleBit 0 63", -(2 ** 63)),
            ("(m.bitAnd 255) 511", 255),
            ("builtins.typeOf (m.testBit 0 0)", "bool"),
            ("builtins.typeOf (m.shiftLeft 1 63)", "int"),
        ])

    def test_bit_argument_validation(self):
        for names, arity in [(BIT_UNARY, 1), (BIT_BINARY | BIT_INDEXED, 2)]:
            for name in sorted(names):
                for position in range(arity):
                    for bad in ["1.0", "true", "null"]:
                        args = ["1"] * arity
                        args[position] = bad
                        expression = f"m.{name} " + " ".join(args)
                        with self.subTest(expression=expression):
                            result = self.run_expression(expression)
                            self.assertGreater(result.returncode, 0, result.stderr)
                            self.assertIn("error:", result.stderr)
        for name in sorted(BIT_INDEXED):
            for bad in ["(-1)", "64", "m.minInt", "m.maxInt"]:
                expression = f"m.{name} 1 {bad}"
                with self.subTest(expression=expression):
                    result = self.run_expression(expression)
                    self.assertGreater(result.returncode, 0, result.stderr)
                    self.assertIn("[0, 63]", result.stderr)

    def test_randomized_bit_reference(self):
        rng = random.Random(64)
        mask = 2 ** 64 - 1

        def signed(word):
            word &= mask
            return word - 2 ** 64 if word >= 2 ** 63 else word

        cases = []
        for count in range(64):
            for x in [0, -1, -(2 ** 63), 2 ** 63 - 1, rng.randrange(-(2 ** 63), 2 ** 63)]:
                y = rng.randrange(-(2 ** 63), 2 ** 63)
                word = x & mask
                a, b = literal(x), literal(y)
                expected = {
                    "bitAnd": (f"{a} {b}", signed(x & y)),
                    "bitOr": (f"{a} {b}", signed(x | y)),
                    "bitXor": (f"{a} {b}", signed(x ^ y)),
                    "bitNot": (a, signed(~x)),
                    "shiftLeft": (f"{a} {count}", signed(word << count)),
                    "shiftRight": (f"{a} {count}", signed(word >> count)),
                    "shiftRightArithmetic": (f"{a} {count}", x >> count),
                    "rotateLeft": (f"{a} {count}", signed((word << count) | (word >> ((64 - count) % 64)))),
                    "rotateRight": (f"{a} {count}", signed((word >> count) | (word << ((64 - count) % 64)))),
                    "popCount": (a, word.bit_count()),
                    "countLeadingZeros": (a, 64 - word.bit_length()),
                    "countTrailingZeros": (a, (word & -word).bit_length() - 1 if word else 64),
                    "byteSwap": (a, signed(int.from_bytes(word.to_bytes(8, "little"), "big"))),
                    "testBit": (f"{a} {count}", bool(word & (1 << count))),
                    "setBit": (f"{a} {count}", signed(word | (1 << count))),
                    "clearBit": (f"{a} {count}", signed(word & ~(1 << count))),
                    "toggleBit": (f"{a} {count}", signed(word ^ (1 << count))),
                }
                cases.extend((f"m.{name} {args}", value) for name, (args, value) in expected.items())
        # Stay below the operating system's per-argument size limit.
        for offset in range(0, len(cases), 256):
            self.assert_values(cases[offset:offset + 256])

    def test_randomized_float_reference(self):
        rng = random.Random(42)
        cases = []
        for _ in range(30):
            x = rng.uniform(-0.9, 0.9)
            for name in ["sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh",
                         "tanh", "asinh", "atanh", "exp", "expm1", "log1p", "erf", "erfc"]:
                cases.append((f"m.{name} {literal(x)}", getattr(math, name)(x)))
            x = rng.uniform(1, 20)
            for name in ["sqrt", "log", "log2", "log10", "acosh", "gamma", "lgamma"]:
                cases.append((f"m.{name} {literal(x)}", getattr(math, name)(x)))
        self.assert_values(cases)

    def test_pure_evaluation(self):
        self.assertEqual(self.evaluate("m.sqrt 81", modern=True), 9.0)
        self.assertEqual(self.evaluate("m.shiftRight (-1) 1", modern=True), 2 ** 63 - 1)

    def test_allocation_stress(self):
        self.assertEqual(self.evaluate(
            "m.sum (builtins.genList (i: m.sqrt (m.pow i 2)) 20000)"
        ), 199990000.0)

    def test_example(self):
        example = Path(__file__).resolve().parent.parent / "examples" / "demo.nix"
        result = self.evaluate(f"import {example}")
        self.assertEqual(result["combinations"], 2598960)
        self.assertEqual(result["accurateSum"], 1.0)
        self.assertEqual(result["vectorLength"], 13.0)
        self.assertFalse(result["approximatelyEqual"])
        self.assertTrue(result["nearZero"])
        self.assertEqual(result["flags"], -(2 ** 63) + 48)
        self.assertEqual(result["setBits"], 64)

    @unittest.skipUnless(OPTIONS.launcher, "installed launcher not supplied")
    def test_installed_launcher(self):
        self.assertEqual(self.evaluate("m.powInt 2 10", launcher=True), 1024)
        self.assertEqual(self.evaluate("m.rotateLeft 1 63", launcher=True), -(2 ** 63))
        result = self.run_expression("m.sqrt (-1)", launcher=True)
        self.assertGreater(result.returncode, 0)
        self.assertIn("mathnix:", result.stderr)


if __name__ == "__main__":
    unittest.main(argv=[__file__, *UNITTEST_ARGS], verbosity=2)
