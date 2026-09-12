"""Copy editable settings without recopying the active site's accepted evidence.

AMT is owned by the active site. File-derived guidance is replaced as a whole.
Project loading and site activation copy evidence before using the saved site.
"""
from copy import deepcopy


def copy_active_state(host, value):
    shared = [vars(host).get(name) for name in ('AMT_stockpile_data', 'aps_destination_guidance',
        'destination_haul_routes', 'destination_progress_snapshot')]
    context = (vars(host).get('calendar_inputs') or {}).get('site_context') or {}
    # The derived context is rebuilt as a whole when Calendar is captured.
    shared.append(context)
    shared.append(context.get('aps_destination_guidance'))
    shared.append((context.get('destination_rules') or {}).get('guidance'))
    memo = {id(item): item for item in shared if isinstance(item, (dict, list))}
    return deepcopy(value, memo)
