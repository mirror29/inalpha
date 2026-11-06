"""Canonical chronological discovery/selection/holdout boundaries for E2."""


def event_partition_ends(bar_count: int) -> tuple[int, int]:
    """Keep the existing 60/20/20 policy, including its small-dataset bounds."""
    discovery_end = max(2, int(bar_count * 0.60))
    return discovery_end, min(max(discovery_end + 2, int(bar_count * 0.80)), bar_count - 1)
