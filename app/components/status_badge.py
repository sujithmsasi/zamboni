"""
Zamboni — Status Badges
Coloured emoji badges for status fields in tables and lists.
"""

STATUS_BADGES = {
    "SUCCESS":  "✅ Success",
    "FAILURE":  "❌ Failure",
    "SKIPPED":  "⚠️ Skipped",
    "DRY_RUN":  "🧪 Dry Run",
    "PENDING":  "⏳ Pending",
    "RUNNING":  "🔄 Running",
}

LIFECYCLE_BADGES = {
    "ACTIVE":          "🟢 Active",
    "STALE_CANDIDATE": "🟡 Stale Candidate",
    "GREENZONE":       "🟢 Greenzone",
    "PENDING_DROP":    "🔴 Pending Drop",
    "DROPPED":         "⚫ Dropped",
}

TIER_BADGES = {
    "critical": "🔴 Critical",
    "standard": "🟡 Standard",
    "low":      "🟢 Low",
}

LAYER_BADGES = {
    "staging":  "📥 Staging",
    "datalake": "🗄️ Datalake",
    "base":     "📚 Base",
    "master":   "🎯 Master",
}


def status(value: str) -> str:
    return STATUS_BADGES.get(value, value or "—")


def lifecycle(value: str) -> str:
    return LIFECYCLE_BADGES.get(value, value or "—")


def tier(value: str) -> str:
    return TIER_BADGES.get(value, value or "—")


def layer(value: str) -> str:
    return LAYER_BADGES.get(value, value or "—")


def yes_no(value: bool) -> str:
    return "✅ Yes" if value else "❌ No"
