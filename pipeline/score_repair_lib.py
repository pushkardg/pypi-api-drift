"""Shared statistics for the code-repair scorers."""
import math


def wilson(k, n, z=1.96):
    p = k / n; c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


def mcnemar_exact(b, c):
    """two-sided exact binomial p for discordant pairs"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return round(min(1.0, 2 * p), 4)
