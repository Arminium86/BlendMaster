"""Atomic project handoff from a completed desktop/agent workflow."""
from pathlib import Path
import os
import pickle
import tempfile
from classes.PlanningPersistence import migrate_project_state


def write_checkpoint(scenarios, active_site, destination, snapshot_database, *, legacy_state=None):
    saved = {}
    for site, state in scenarios.items():
        row = dict(state)
        database = row.pop('database_path', None)
        if database:
            row['database_snapshot'] = snapshot_database(database)
        saved[site] = row
    payload = dict(legacy_state or {})
    payload.update(saved[active_site], active_scenario_id=active_site, site_scenarios=saved)
    payload = migrate_project_state(payload)
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='handoff-', suffix='.prj', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)
