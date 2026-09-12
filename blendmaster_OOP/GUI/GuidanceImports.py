"""Asynchronous import transaction shared by planner UI and site preparation."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import os
import hashlib
from PyQt5.QtWidgets import QFileDialog, QMessageBox
from classes.GuidanceImport import inspect_import, snapshot_import, retain_selection, retain_rules, file_revision
from classes.HaulCycleDataHandler import HaulCycleDataHandler
from GUI.GuidancePreparation import prepare as prepare_guidance, FIELDS as GUIDANCE_FIELDS


class GuidanceImports:
    LABELS = {'two_wp': '2WP Mining.csv', 'day_plan': '24HR Mining.csv',
              'haul_cycles': '2WP Haul Infinity Cycles.csv', 'closing_balance': '2WP Closing ROM Stocks.xlsx'}

    def __init__(self, host):
        self.host = host
        self.generation = 0

    def browse(self, kind):
        filters = 'Excel workbooks (*.xlsx)' if kind == 'closing_balance' else 'CSV files (*.csv)'
        path, _ = QFileDialog.getOpenFileName(self.host, 'Select ' + self.LABELS[kind], '', filters)
        if path:
            self.accept(kind, path)

    def selections(self, kind):
        h = self.host
        if kind == 'two_wp':
            return dict(products=h.selected_two_wp_product_crusher_names(),
                        crushers=h.selected_aps_crusher_names(), ratios=h.selected_aps_ratio_crusher_names(),
                        rules=deepcopy(h.direct_tip_movement_rules))
        if kind == 'day_plan':
            return dict(agents=h.selected_24hr_expit_agent_names())
        if kind == 'closing_balance':
            return {}
        return dict(crushers=h.selected_haul_cycle_crusher_names(), mapping=h.current_haul_cycle_crusher_node())

    def accept(self, kind, path, on_complete=None, on_error=None):
        h = self.host
        self.generation += 1
        generation, site = self.generation, getattr(h, 'active_scenario_id', None)
        selected = self.selections(kind)
        guidance_values = {key: getattr(h, key, None) for key in GUIDANCE_FIELDS}
        implementation = type(h)
        site_folder = hashlib.sha256(str(site or 'unconfigured').encode()).hexdigest()[:20]
        directory = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'BlendMaster' / 'accepted_inputs' / site_folder
        def work():
            result = inspect_import(kind, path)
            if kind == 'two_wp' and selected['rules'] and not result['catalogue']['has_source_identity']:
                raise ValueError('Source.NamePart2 is required to validate the existing movement rules.')
            if kind == 'haul_cycles':
                kept, _ = retain_selection(selected['crushers'], result['catalogue']['crushers'])
                result['routes'] = HaulCycleDataHandler.build_nearest_crusher_routes(path, kept)
                result['destination_routes'] = HaulCycleDataHandler.build_destination_routes(path)
            result = snapshot_import(result, directory)
            if kind == 'two_wp':
                guidance_values['file_path_choice'] = result['accepted_path']
                guidance_values['selected_two_wp_product_crushers'] = retain_selection(
                    selected['products'], result['catalogue']['product_crushers'])[0]
                result['prepared_guidance'] = prepare_guidance(implementation, guidance_values)
            return result
        def failed(error):
            message = str(error.get('message', error) if isinstance(error, dict) else error)
            if on_error:
                on_error(message)
            else:
                QMessageBox.warning(h, 'Guidance import', message + '\nThe previous input and selections are retained.')
        def done(result):
            if (generation != self.generation or site != getattr(h, 'active_scenario_id', None)
                    or selected != self.selections(kind)):
                failed('The site or selections changed during import. Import the file again in the current context.')
                return
            try:
                unchanged = file_revision(path) == tuple(result['revision'])
            except OSError:
                unchanged = False
            if not unchanged:
                failed('The file changed after validation; import it again.')
                return
            catalogue, removed = result['catalogue'], []
            accepted_path = result.get('accepted_path', path)
            def retain(key, available):
                kept, missing = retain_selection(selected[key], available)
                removed.extend(f'{key}: {value}' for value in missing)
                return kept
            if kind == 'two_wp':
                h.file_path.setText(accepted_path)
                h.file_path.setToolTip('Delivery: ' + path)
                h.set_two_wp_product_crusher_items(catalogue['product_crushers'], retain('products', catalogue['product_crushers']))
                h.set_aps_crusher_items(catalogue['crushers'], retain('crushers', catalogue['crushers']))
                h.set_aps_ratio_crusher_items(catalogue['crushers'], retain('ratios', catalogue['crushers']))
                h.direct_tip_movement_rules, invalid = retain_rules(selected['rules'], catalogue['sources'], catalogue['crushers'])
                removed.extend('movement rule: ' + str(rule) for rule in invalid)
                h.direct_tip_grade_block_sources = list(catalogue['sources'])
                h.direct_tip_crusher_destinations = list(catalogue['crushers'])
                h.set_direct_tip_movement_options(catalogue['sources'], catalogue['crushers'])
                h.refresh_direct_tip_rule_list()
                for key, value in result.get('prepared_guidance', {}).items():
                    setattr(h, key, value)
            elif kind == 'day_plan':
                h.set_24hr_mining_path(accepted_path, reset_agents=False, show_mapping_errors=False)
                agents = retain('agents', catalogue['agents'])
                h.available_24hr_expit_agents = list(catalogue['agents'])
                h.selected_24hr_expit_agents = agents
                h.set_24hr_expit_agent_items(catalogue['agents'], agents)
            elif kind == 'haul_cycles':
                h.haul_cycle_file_path.setText(accepted_path)
                h.set_haul_cycle_crusher_items(catalogue['crushers'], retain('crushers', catalogue['crushers']))
                h.set_haul_cycle_crusher_mapping_items(catalogue['crushers'], retain('mapping', catalogue['crushers']), use_default=False)
                h.haul_cycle_routes = result['routes']
                h.destination_haul_routes = result['destination_routes']
                h._haul_cycle_routes_revision = (file_revision(accepted_path), tuple(sorted(h.selected_haul_cycle_crusher_names())))
                h.apply_haul_cycle_routes_to_stockpile_data()
            else:
                h.two_wp_closing_stocks_path.setText(accepted_path)
            record = dict(kind=kind, path=accepted_path, delivery_path=path, revision=result['revision'],
                          source_modified_at=datetime.fromtimestamp(result['revision'][2] / 1e9).astimezone().isoformat(),
                          imported_at=datetime.now().astimezone().isoformat(), removed=removed,
                          message='Removed invalid selections: ' + '; '.join(removed) if removed else
                                  'All existing selections and rules remain valid.')
            h.guidance_import_audit = [*(getattr(h, 'guidance_import_audit', None) or []), record][-100:]
            h.scenario_report_refresh_pending = True
            h.validate_form()
            refresh = vars(h).get('_workflow_context_refresh')
            if refresh:
                refresh()
            if on_complete:
                on_complete(record)
            else:
                detail = '\n'.join(removed) if removed else 'All existing selections and rules remain valid.'
                QMessageBox.information(h, 'Guidance imported', detail)
        h.run_background_task('Validating ' + self.LABELS[kind] + '…', work, done, failed)
