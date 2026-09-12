"""Role, site-contract and run-state boundaries shared by desktop and services.

These describe how existing planning operations run, not new blending rules.
The trusted application session supplies the role; a project/agent payload
cannot grant itself additional capabilities.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
import threading
import uuid

CONTRACT_VERSION = 1
ROLES = ('planner', 'support', 'owner', 'agent')
PLANNER_ACTIONS = frozenset({'select_site', 'planning_context', 'import_guidance',
    'stockpile_inventories', 'grade_reconciliation', 'amt_stockpiles', 'product_targets',
    'expit_sequence', 'destination_progress', 'decision_levers', 'calendar',
    'setup_blends', 'blend_sequence', 'prepare', 'optimise', 'reports', 'export', 'view', 'load_project'})
PREPARATION_STEPS = ('imports', 'inventory', 'guidance', 'reconciliation', 'amt', 'expit', 'destination', 'database', 'validate')
PLAN_STEPS = (*PREPARATION_STEPS, 'optimise', 'reports')


def normalise_role(value):
    role = str(value or 'planner').strip().lower()
    if role not in ROLES:
        raise ValueError('Unknown application role: ' + role)
    return role


def require_action(role, action):
    role = normalise_role(role)
    if role == 'planner' and action not in PLANNER_ACTIONS:
        raise PermissionError(f'{action.replace("_", " ")} is available to Support, Owner or Agent only.')


def default_contract(site_id, paths=None, timezone='Australia/Perth'):
    paths = paths or {}
    return dict(schema_version=CONTRACT_VERSION, site_id=str(site_id), timezone=timezone,
                enabled=False, handoff='prepare', refresh_minutes=30, retries=1,
                sources={name: dict(path=str(paths.get(name) or ''), cadence=cadence,
                                    weekday=2 if cadence == 'weekly' else None,
                                    window_start='14:00' if name == 'day_plan' else '00:00',
                                    window_end='15:00' if name == 'day_plan' else '23:59',
                                    required=name in ('two_wp', 'day_plan'), retain_previous=True)
                         for name, cadence in (('haul_cycles', 'weekly'), ('two_wp', 'weekly'),
                                               ('closing_balance', 'weekly'), ('day_plan', 'daily'))})


def validate_contract(value):
    if not isinstance(value, dict):
        raise ValueError('The site contract must be a JSON object.')
    value = deepcopy(value)
    if value.get('schema_version') != CONTRACT_VERSION or not value.get('site_id'):
        raise ValueError('A supported contract version and site ID are required.')
    try:
        ZoneInfo(value['timezone'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Select a valid IANA timezone, for example Australia/Perth.') from exc
    if value.get('handoff') not in ('prepare', 'plan'):
        raise ValueError('Handoff must be prepare or plan.')
    if not isinstance(value.get('enabled'), bool):
        raise ValueError('Enabled must be true or false.')
    if type(value.get('refresh_minutes')) is not int or not 1 <= value['refresh_minutes'] <= 1440:
        raise ValueError('Refresh interval must be between 1 and 1440 minutes.')
    if type(value.get('retries')) is not int or not 0 <= value['retries'] <= 3:
        raise ValueError('Retries must be between zero and three.')
    if not isinstance(value.get('sources'), dict) or set(value['sources']) != {'haul_cycles', 'two_wp', 'closing_balance', 'day_plan'}:
        raise ValueError('The contract must define all four source types.')
    for name, source in value['sources'].items():
        if not isinstance(source, dict):
            raise ValueError(f'{name}: source settings must be a JSON object.')
        if not isinstance(source.get('path'), str) or type(source.get('required')) is not bool or type(source.get('retain_previous')) is not bool:
            raise ValueError(f'{name}: path and input retention flags are invalid.')
        if source.get('cadence') not in ('daily', 'weekly'):
            raise ValueError(f'{name}: choose daily or weekly cadence.')
        if source['cadence'] == 'weekly' and (type(source.get('weekday')) is not int or source.get('weekday') not in range(7)):
            raise ValueError(f'{name}: weekday must be 0 (Monday) to 6 (Sunday).')
        try:
            start, end = [datetime.strptime(source[k], '%H:%M').time() for k in ('window_start', 'window_end')]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'{name}: arrival times must use HH:MM.') from exc
        if end < start:
            raise ValueError(f'{name}: arrival window must end after it starts.')
    return value


def source_arrival_status(source, now, *, timezone, imported_at=None):
    """A missed arrival is visible; it does not silently expire a previous plan."""
    if not source.get('path') and not source.get('required') and imported_at is None:
        return dict(status='optional source not configured', expected_by=None, previous_usable=False)
    now = now.astimezone(ZoneInfo(timezone))
    date = now.date()
    if source['cadence'] == 'weekly':
        date -= timedelta(days=(date.weekday() - source['weekday']) % 7)
    deadline = datetime.combine(date, datetime.strptime(source['window_end'], '%H:%M').time(), now.tzinfo)
    window = datetime.combine(date, datetime.strptime(source['window_start'], '%H:%M').time(), now.tzinfo)
    imported = datetime.fromisoformat(imported_at) if isinstance(imported_at, str) else imported_at
    if imported is not None:
        imported = imported.replace(tzinfo=now.tzinfo) if imported.tzinfo is None else imported.astimezone(now.tzinfo)
    if imported is not None and imported >= window:
        status = 'current delivery available'
    elif now < window:
        status = 'next delivery pending'
    elif now <= deadline:
        status = 'replacement being prepared'
    else:
        status = 'expected replacement not received'
    return dict(status=status, expected_by=deadline.isoformat(),
                previous_usable=bool(imported and source.get('retain_previous', True)))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


@dataclass
class WorkflowRun:
    site_id: str
    start_time: datetime
    input_revision: str
    endpoint: str = 'prepare'
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = 'pending'
    stages: list = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    error: str = ''

    @property
    def steps(self):
        if self.endpoint not in ('prepare', 'plan'):
            raise ValueError('Unknown workflow endpoint.')
        return PLAN_STEPS if self.endpoint == 'plan' else PREPARATION_STEPS

    def record(self, stage, started, result=None):
        finished = datetime.now(started.tzinfo)
        self.stages.append(dict(stage=stage, started_at=started.isoformat(),
                                duration_seconds=(finished-started).total_seconds()))
        self.outputs[stage] = result


class WorkflowEngine:
    """Execute existing operations with one immutable run context and site lock."""
    def __init__(self):
        self._lock = threading.Lock()

    def run(self, contract, actions, inputs, *, endpoint=None, now=None, progress=None, cancelled=None):
        contract = validate_contract(contract)
        endpoint = endpoint or contract['handoff']
        snapshot = deepcopy(inputs)
        start = now or datetime.now(ZoneInfo(contract['timezone']))
        run = WorkflowRun(contract['site_id'], start, fingerprint(snapshot), endpoint)
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('A workflow is already running for this site.')
        try:
            run.status = 'running'
            for stage in run.steps:
                if cancelled and cancelled():
                    run.status = 'cancelled'
                    return run
                if stage not in actions:
                    raise ValueError(f'No operation is configured for workflow stage {stage}.')
                if progress:
                    progress(stage, run)
                begun = datetime.now(start.tzinfo)
                result = actions[stage](snapshot, run)
                run.record(stage, begun, result)
            run.status = 'inputs_ready' if endpoint == 'prepare' else 'plan_prepared'
        except Exception as exc:
            run.status, run.error = 'failed', str(exc)
        finally:
            self._lock.release()
        return run
