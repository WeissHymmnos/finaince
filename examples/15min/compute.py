"""Fixture compute(panel) for the 15-minute desk walk. Isolated impl only."""

NAME = "rank_delta"
EXPRESSION = "Rank(Delta(close, 1))"


def compute(panel):
    close = list(panel["close"])
    out = [0.0]
    for i in range(1, len(close)):
        prev = close[i - 1] or 1.0
        out.append((close[i] - close[i - 1]) / prev)
    return out
