from __future__ import annotations

import operator
from typing import Callable


OPERATIONS: dict[str, Callable[[float, float], float]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "%": operator.mod,
    "**": operator.pow,
}


def calculate(left: float, op: str, right: float) -> float:
    if op not in OPERATIONS:
        raise ValueError(f"Unknown operation: {op}")
    if op in {"/", "//", "%"} and right == 0:
        raise ZeroDivisionError("Division by zero")
    return OPERATIONS[op](left, right)


def parse_number(raw: str) -> float:
    return float(raw.strip().replace(",", "."))


def print_help() -> None:
    print("Operations: +  -  *  /  //  %  **")
    print("Commands: help, quit")


def main() -> None:
    print_help()
    while True:
        expression = input("\n> ").strip()
        if not expression:
            continue
        if expression.lower() in {"quit", "exit", "q"}:
            print("Bye")
            return
        if expression.lower() in {"help", "h"}:
            print_help()
            continue

        parts = expression.split()
        if len(parts) != 3:
            print("Format: <number> <op> <number>  example: 12.5 * 3")
            continue

        left_raw, op, right_raw = parts
        try:
            result = calculate(parse_number(left_raw), op, parse_number(right_raw))
        except ValueError as exc:
            print(f"Error: {exc}")
            continue
        except ZeroDivisionError as exc:
            print(f"Error: {exc}")
            continue

        if result.is_integer():
            print(int(result))
        else:
            print(result)


if __name__ == "__main__":
    main()
