"""Agent entry point for the existing desktop pipeline and planner handoff.

Run with python -m execute.SiteWorkflow --help from blendmaster_OOP.
Qt/WebEngine and the site's existing Snowflake credentials are required.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--contract', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New directory for run.json and prepared.prj')
    parser.add_argument('--endpoint', choices=('prepare', 'plan'), default='prepare')
    parser.add_argument('--start', default='saved', help='saved, now, or a site-local ISO datetime')
    parser.add_argument('--show', action='store_true', help='Display the desktop window during execution')
    args = parser.parse_args(argv)
    from classes.SiteWorkflow import validate_contract
    contract = validate_contract(json.loads(args.contract.read_text(encoding='utf-8')))
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('--output must be empty, so an earlier handoff cannot be overwritten.')
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['BLENDMASTER_ROLE'] = 'agent'
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from GUI.InitialiseGUI import UserInputs
    from GUI.ProjectLoading import begin
    from classes.WorkflowCheckpoint import write_checkpoint
    app = QApplication([])
    host = UserInputs()
    host._unattended_workflow = True
    controller = host.site_workflow_controller
    controller.scheduler.stop()
    phase = {'value': 'loading'}
    begun = time.monotonic()

    def finish(status, error='', checkpoint=None):
        if phase['value'] == 'finished':
            return
        receipt = asdict(controller.run) if controller.run else dict(site_id=contract['site_id'])
        receipt.update(status=status, error=error or receipt.get('error', ''), checkpoint=checkpoint,
                       elapsed_seconds=time.monotonic() - begun)
        (args.output / 'run.json').write_text(json.dumps(receipt, default=str, indent=2), encoding='utf-8')
        print(json.dumps(dict(status=status, receipt=str(args.output / 'run.json'), error=receipt['error'])), flush=True)
        phase['value'] = 'finished'
        phase['exit_code'] = 0 if status in ('inputs_ready', 'plan_prepared', 'plan_requires_review') else 1

    def error(value):
        finish('failed', str(value.get('message', value) if isinstance(value, dict) else value))

    host.show_error_popup = error
    def unhandled(kind, value, trace):
        import traceback
        traceback.print_exception(kind, value, trace)
        if controller.active:
            controller.fail(str(value))
        error(str(value))
    sys.excepthook = unhandled
    original_fail = controller.fail
    def fail(value):
        if controller.active:
            original_fail(value)
        else:
            error(value)
    controller.fail = fail
    host.resolve_missing_aps_mining_csv_paths = lambda state: True  # No interactive path prompts; imports validate contract locations.

    def loaded(state):
        try:
            scenarios = state.get('site_scenarios')
            if not scenarios or contract['site_id'] not in scenarios:
                raise ValueError('The project must contain the configured contract site ID.')
            state['active_scenario_id'] = contract['site_id']
            # Resolve moved deliveries before load-time guidance is inspected.
            from GUI.SiteAutomation import SOURCE_WIDGETS
            row = scenarios[contract['site_id']]
            for kind, widget in SOURCE_WIDGETS.items():
                current = row.get(widget + '_choice')
                replacement = contract['sources'][kind]['path']
                if current and not Path(current).is_file() and replacement and Path(replacement).is_file():
                    row[widget + '_choice'] = replacement
            if args.start == 'now':
                row = scenarios[contract['site_id']]
                row['time_mode_choice'] = 1
            elif args.start != 'saved':
                row = scenarios[contract['site_id']]
                row['time_mode_choice'], row['start_time_choice'] = 2, datetime.fromisoformat(args.start)
            else:
                host.apply_loaded_project_now_time_choice(state, use_current_time=False)
            # Loading retains the saved evidence; the workflow refreshes it at its one captured start.
            begin(host, state, str(args.project), show_success=False, resolve_time=False,
                  ready=lambda: phase.update(value='ready'))
        except Exception as exc:
            error(str(exc))

    def poll():
        if phase['value'] == 'finished':
            if not host.background_tasks:
                app.exit(phase['exit_code'])
            return
        if time.monotonic() - begun > 6 * 3600:
            controller.cancel()
            if not host.background_tasks:
                error('Workflow exceeded the six-hour process deadline.')
            return
        if host.background_tasks or host.project_load_restore_in_progress or host.project_load_continuation_pending:
            return
        if phase['value'] == 'ready':
            host.site_workflow_contract = {**contract, 'enabled': False}
            phase['value'] = 'running'
            if not controller.start(args.endpoint):
                error(host.preparation_status_label.text())
        elif phase['value'] == 'running' and not controller.active:
            if controller.run.status in ('inputs_ready', 'plan_prepared', 'plan_requires_review'):
                phase['value'] = 'saving'
                host.save_active_scenario_state()
                host.run_background_task('Writing planner handoff…',
                    lambda: write_checkpoint(host.site_scenarios, host.active_scenario_id,
                                             args.output / 'prepared.prj', host.snapshot_database),
                    lambda path: finish(controller.run.status, checkpoint=path), error)
            else:
                error(controller.run.error or controller.run.status)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(250)
    if args.show:
        host.showMaximized()
    QTimer.singleShot(0, lambda: host.run_background_task('Reading project…',
        lambda: host.load_project_state_from_path(str(args.project)), loaded, error))
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
