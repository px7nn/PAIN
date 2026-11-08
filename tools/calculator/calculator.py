import ast
import math
import operator


class CalculatorError(Exception):
    ...


class Calculator:

    OPERATORS = {
        ast.Add:      operator.add,
        ast.Sub:      operator.sub,
        ast.Mult:     operator.mul,
        ast.Div:      operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod:      operator.mod,
        ast.Pow:      operator.pow,
        ast.USub:     operator.neg,
        ast.UAdd:     operator.pos,
    }

    FUNCTIONS = {
        "sqrt":  math.sqrt,
        "sin":   math.sin,
        "cos":   math.cos,
        "tan":   math.tan,
        "log":   math.log,
        "log10": math.log10,
        "exp":   math.exp,
        "abs":   abs,
        "ceil":  math.ceil,
        "floor": math.floor,
    }

    CONSTANTS = {
        "pi":  math.pi,
        "e":   math.e,
        "tau": math.tau,
    }

    MAX_EXPRESSION_LENGTH = 256
    MAX_POWER = 1000
    MAX_NODES = 100

    def calculate(self, expression: str):

        if not isinstance(expression, str):
            raise CalculatorError("Expression must be a string.")

        if len(expression) > self.MAX_EXPRESSION_LENGTH:
            raise CalculatorError("Expression is too long.")

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise CalculatorError("Invalid mathematical expression.") from exc

        if sum(1 for _ in ast.walk(tree)) > self.MAX_NODES:
            raise CalculatorError("Expression is too complex.")

        result = self._evaluate(tree.body)

        if isinstance(result, (int, float)) and not isinstance(result, bool):
            if not math.isfinite(result):
                raise CalculatorError("Result is not finite.")

        return result

    def _evaluate(self, node):

        # Numeric constants
        if isinstance(node, ast.Constant):

            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise CalculatorError("Only numeric constants are allowed.")

            if isinstance(node.value, int) and node.value.bit_length() > 1024:
                raise CalculatorError("Integer is too large.")

            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise CalculatorError("Invalid numeric constant.")

            return node.value

        # Mathematical constants
        if isinstance(node, ast.Name):

            if node.id not in self.CONSTANTS:
                raise CalculatorError(f"Unknown constant: {node.id}")

            return self.CONSTANTS[node.id]

        # Binary operators
        if isinstance(node, ast.BinOp):

            operator_type = type(node.op)

            if operator_type not in self.OPERATORS:
                raise CalculatorError("Unsupported operator.")

            left = self._evaluate(node.left)
            right = self._evaluate(node.right)

            if operator_type is ast.Pow:

                if abs(right) > self.MAX_POWER:
                    raise CalculatorError("Exponent is too large.")

                if abs(left) > 1e100:
                    raise CalculatorError("Base is too large.")

            try:
                result = self.OPERATORS[operator_type](left, right)
            except (ArithmeticError, OverflowError, ValueError) as exc:
                raise CalculatorError(str(exc)) from exc

            if isinstance(result, int) and result.bit_length() > 4096:
                raise CalculatorError("Result is too large.")

            if isinstance(result, float) and not math.isfinite(result):
                raise CalculatorError("Result is not finite.")

            return result

        # Unary operators
        if isinstance(node, ast.UnaryOp):

            operator_type = type(node.op)

            if operator_type not in self.OPERATORS:
                raise CalculatorError("Unsupported unary operator.")

            return self.OPERATORS[operator_type](
                self._evaluate(node.operand)
            )

        # Function calls
        if isinstance(node, ast.Call):

            if not isinstance(node.func, ast.Name):
                raise CalculatorError("Invalid function call.")

            function_name = node.func.id

            if function_name not in self.FUNCTIONS:
                raise CalculatorError(f"Unknown function: {function_name}")

            if node.keywords:
                raise CalculatorError("Keyword arguments are not allowed.")

            arguments = [
                self._evaluate(argument)
                for argument in node.args
            ]

            try:
                result = self.FUNCTIONS[function_name](*arguments)
            except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
                raise CalculatorError(str(exc)) from exc

            if isinstance(result, float) and not math.isfinite(result):
                raise CalculatorError("Result is not finite.")

            return result

        raise CalculatorError(
            f"Unsupported expression element: {type(node).__name__}"
        )


# --------------------------------------------------
# TOOL REGISTRATION
# --------------------------------------------------

_calculator = Calculator()


def register(tool_manager):

    tool_manager.register(
        name="calculate",
        description=(
            "Evaluate mathematical expressions. "
            "Supports arithmetic, powers, modulo, parentheses, "
            "mathematical constants and common functions such as "
            "sqrt, sin, cos, tan, log and exp."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to evaluate."
                }
            },
            "required": ["expression"],
            "additionalProperties": False
        },
        handler=_calculator.calculate
    )