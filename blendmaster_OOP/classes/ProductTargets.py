"""Product Targets naming at project, navigation and agent boundaries.

Solver model APIs still accept ``product_build_settings``. New project and
workflow payloads use ``product_targets``; explicit current values, including
an empty list, always take precedence over legacy aliases.
"""

PRODUCT_TARGET_KEYS = (
    "product_targets",
    "product_targets_tab",
    "product_build_settings",
    "product_builds",
    "product_build_settings_tab",
)


def product_targets_value(mapping, default=None):
    if isinstance(mapping, dict):
        for key in PRODUCT_TARGET_KEYS:
            if key in mapping:
                return mapping[key]
    return default


def product_targets_identifier(identifier):
    """Canonicalize only the page/workflow prefix, leaving row fields intact."""
    text = str(identifier or "").strip()
    prefix, separator, suffix = text.partition(".")
    if prefix.lower() in PRODUCT_TARGET_KEYS:
        return "product_targets" + separator + suffix
    return identifier


def migrate_product_target_state(state):
    """Copy known state containers and migrate names without touching row data.

    Avoid walking arbitrary dictionaries (or copying large inventory tables).
    Inactive scenarios and their calendar inputs must migrate as well as the
    active scenario so a subsequent save cannot resurrect the old key.
    """
    if not isinstance(state, dict):
        return state
    migrated = dict(state)
    if any(key in state for key in PRODUCT_TARGET_KEYS):
        migrated["product_targets"] = product_targets_value(state)
        for key in PRODUCT_TARGET_KEYS[1:]:
            migrated.pop(key, None)
    calendar = state.get("calendar_inputs")
    if isinstance(calendar, dict):
        migrated["calendar_inputs"] = migrate_product_target_state(calendar)
    scenarios = state.get("site_scenarios")
    if isinstance(scenarios, dict):
        migrated["site_scenarios"] = {
            key: migrate_product_target_state(value)
            for key, value in scenarios.items()
        }
    pages = state.get("tab_states")
    if isinstance(pages, dict):
        migrated["tab_states"] = {
            product_targets_identifier(key): value
            for key, value in pages.items()
            if product_targets_identifier(key) == key
            or product_targets_identifier(key) not in pages
        }
    return migrated
