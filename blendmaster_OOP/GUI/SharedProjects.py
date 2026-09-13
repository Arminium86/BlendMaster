"""Review an updated upstream project and import inputs without losing local plans."""
from copy import deepcopy
from pathlib import Path
import tempfile

from PyQt5.QtCore import QObject, QTimer, Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
                            QDialogButtonBox, QPushButton, QMessageBox)
from classes.SharedProjects import (GROUPS, changes, group_hashes, merge_inputs, merge_database,
                                    revision, settings)
from database.DatabaseContext import set_database_path


class SharedProjectWatcher(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.pending = host, False
        self.button = QPushButton('Review updated inputs')
        self.button.setVisible(False)
        self.button.clicked.connect(self.review)
        host.scenario_toolbar.layout().addWidget(self.button)
        self.timer = QTimer(self)
        self.timer.setInterval(15_000)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def loaded(self, source, prepared, scenarios):
        h = self.host
        h.shared_project_settings = settings(prepared.get('shared_project_settings'))
        subscription = deepcopy(prepared.get('shared_project_subscription') or {})
        source_path = Path(source).resolve() if source and Path(source).is_file() else None
        shared_source = source_path is not None and source_path.parent == Path(h.shared_project_settings['folder']).resolve()
        # A published model may have been prepared from another project. When
        # the user explicitly opens the shared file, watch that file rather
        # than inheriting its author's template subscription. Planner working
        # copies saved elsewhere retain their upstream subscription.
        if source_path is not None and (shared_source or not subscription.get('path')):
            subscription = dict(path=str(Path(source).resolve()),
                revision=prepared.get('_shared_read_revision') or revision(source),
                baselines=prepared.get('_shared_input_baselines') or {})
        h.shared_project_subscription = subscription
        self.button.setVisible(False)
        self.check()

    def check(self):
        h = self.host
        if h.access_role != 'planner' or self.pending:
            return
        subscription = vars(h).get('shared_project_subscription') or {}
        try:
            changed = revision(subscription['path']) != tuple(subscription.get('revision') or ())
        except (KeyError, OSError):
            return
        self.button.setVisible(changed)
        if changed:
            self.button.setToolTip('Support updated the source project. Choose inputs to import; your planning edits are retained.')

    def review(self):
        h = self.host
        if self.pending or h.background_tasks or vars(h).get('project_load_restore_in_progress'):
            return
        h.save_active_scenario_state()
        local = dict(h.site_scenarios)
        subscription = deepcopy(h.shared_project_subscription)
        source = subscription['path']
        from GUI.ProjectLoading import RestoreContext, prepare
        context = RestoreContext(h)
        context.scenario_session_directory = tempfile.mkdtemp(prefix='blendmaster-incoming-')
        self.pending = True
        def work():
            raw = h.load_project_state_from_path(source)
            token = raw.get('_shared_read_revision') or revision(source)
            _, incoming, _ = prepare(context, raw)
            reviews = {}
            for site in local.keys() & incoming.keys():
                reviews[site] = changes((subscription.get('baselines') or {}).get(site, {}), local[site], incoming[site])
            return incoming, reviews, token
        def done(result):
            incoming, reviews, token = result
            self.pending = False
            if not any(reviews.values()):
                h.shared_project_subscription = dict(subscription, revision=token)
                self.button.setVisible(False)
                QMessageBox.information(h, 'Project update', 'No prepared input changes are available for your site models.')
                return
            dialog = QDialog(h)
            dialog.setWindowTitle('Import updated inputs')
            dialog.resize(760, 480)
            layout = QVBoxLayout(dialog)
            note = QLabel('Choose the prepared inputs to import. Checked items replace their current values. '
                          'Items you also edited are left unchecked. Calendar edits, targets and manual plans are retained.')
            note.setWordWrap(True)
            layout.addWidget(note)
            tree = QTreeWidget()
            tree.setHeaderLabels(['Site / input', 'Change'])
            layout.addWidget(tree)
            choices = []
            for site, rows in reviews.items():
                if not rows:
                    continue
                parent = QTreeWidgetItem(tree, [h.scenario_display_name(local[site], fallback=site), ''])
                for row in rows:
                    item = QTreeWidgetItem(parent, [row['group'], 'You also changed this input' if row['conflict'] else 'Updated by Support'])
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Checked if site == h.active_scenario_id and not row['conflict'] else Qt.Unchecked)
                    choices.append((site, row, item))
            tree.expandAll()
            tree.resizeColumnToContents(0)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText('Import selected inputs')
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec_() != QDialog.Accepted:
                return
            selected = {}
            for site, row, item in choices:
                if item.checkState(0) == Qt.Checked:
                    selected.setdefault(site, []).append(row['group'])
            if selected:
                self.apply(local, incoming, selected, token, subscription)
        h.run_background_task('Reading updated project inputs…', work, done, self.failed)

    def apply(self, local, incoming, selected, token, subscription):
        h = self.host
        old_directory, active = h.scenario_session_directory, h.active_scenario_id
        target_directory = tempfile.mkdtemp(prefix='blendmaster-merged-')
        self.pending = True
        def work():
            merged, baselines = {}, deepcopy(subscription.get('baselines') or {})
            for site, current in local.items():
                groups = selected.get(site, [])
                row = merge_inputs(current, incoming[site], groups) if groups else dict(current)
                destination = str(Path(target_directory) / (site + '.db'))
                merge_database(current.get('database_path'), (incoming.get(site) or {}).get('database_path'), destination, groups)
                row['database_path'] = destination
                row.pop('_prepared_amt_database_path', None)
                merged[site] = row
                if groups:
                    hashes = group_hashes(incoming[site])
                    baselines.setdefault(site, {}).update({group: hashes[group] for group in groups})
            return merged, dict(subscription, revision=token, baselines=baselines)
        def done(result):
            merged, updated_subscription = result
            try:
                h.scenario_session_directory = target_directory
                h.site_scenarios = merged
                h.restore_site_scenario(merged[active])
                h.refresh_scenario_selector()
                h.shared_project_subscription = updated_subscription
                h.update_chart_database_context()
            except Exception:
                h.scenario_session_directory = old_directory
                h.site_scenarios = local
                set_database_path(local[active]['database_path'])
                h.restore_site_scenario(local[active])
                self.pending = False
                raise
            def ready():
                self.pending = False
                self.button.setVisible(False)
                h.preparation_status_label.setText('Selected inputs imported. Recalculate to refresh your results.')
                self.check()
            def restore_after_profile_error(error):
                h.scenario_session_directory = old_directory
                h.site_scenarios = local
                set_database_path(local[active]['database_path'])
                h.restore_site_scenario(local[active])
                h.shared_project_subscription = subscription
                self.failed(error)
            # Replacing source evidence invalidates independent OPF chemistry.
            # Prepare it before releasing the UI or the assay timer can trigger
            # an expensive synchronous rebuild while capturing Calendar edits.
            from GUI.OPFProfileLoading import ensure
            if not ensure(h, ready, on_error=restore_after_profile_error):
                ready()
        h.run_background_task('Importing selected project inputs…', work, done, self.failed)

    def failed(self, error):
        self.pending = False
        QMessageBox.warning(self.host, 'Inputs were not imported', str(error.get('message', error) if isinstance(error, dict) else error))


def install(host):
    host.shared_project_watcher = SharedProjectWatcher(host)
