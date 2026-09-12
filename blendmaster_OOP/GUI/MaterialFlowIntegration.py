"""Connect Material Flow setup to scenario state and background reads."""
from copy import deepcopy
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from GUI.TransportSetup import TransportSetup
from GUI.MaterialFlowGraph import MaterialFlowGraph
from classes.TransportSettings import transport_settings, reference_rate
from setup.TransportOpeningHistory import TransportOpeningHistory


def points_for_gui(gui):
    feed = gui.current_multi_feed_configuration()
    if feed['mode'] != 'single':
        return [dict(name=p['name'],opf=p['opf'],opening_rate=reference_rate(p.get('targets_by_period',{})))
                for p in feed['tipping_points']]
    rates = (getattr(gui,'calendar_inputs',{}) or {}).get('crusher_rate',{})
    rate = next((float(r) for r in rates.values() if float(r or 0)>0),0) if isinstance(rates,dict) else rates or 0
    name = getattr(gui,'crusher_input_choice','')
    return [dict(name=name,opf=getattr(gui,'opf_input_choice',''),opening_rate=rate)] if name else []


def install_setup(gui):
    gui.transport_settings = getattr(gui,'transport_settings',{})
    gui.transport_opening_history = getattr(gui,'transport_opening_history',{})
    gui.flow_node_positions = getattr(gui,'flow_node_positions',{})
    gui.transport_setup = TransportSetup()
    gui.setup_flow_graph = MaterialFlowGraph()
    gui.transport_setup.tabs.insertTab(0,gui.setup_flow_graph,'Topology')
    gui.setup_flow_graph.positions_changed.connect(lambda positions:save_positions(gui,positions))
    gui.transport_tab_index = gui.register_page('material_flow',gui.setup_tabs,gui.transport_setup,'Material Flow')
    gui.transport_setup.submit_requested.connect(lambda: submit_setup(gui))
    gui.transport_setup.refresh_requested.connect(lambda: refresh_history(gui))


def sync_setup(gui):
    points = points_for_gui(gui)
    gui.transport_setup.set_context(points,getattr(gui,'transport_settings',{}),getattr(gui,'transport_opening_history',{}))
    gui.setup_flow_graph.set_graph(topology_for_gui(gui),gui.flow_node_positions)


def submit_setup(gui):
    try:
        settings = gui.transport_setup.settings()
        gui.transport_settings = settings
        gui.setup_flow_graph.set_graph(topology_for_gui(gui),gui.flow_node_positions)
        gui.transport_setup.status.setText('Transport settings submitted. Refresh opening actual movements after changing capacities, opening rates or scenario start.')
        return True
    except (ValueError,TypeError) as exc:
        gui.transport_setup.status.setText(str(exc))
        return False


def refresh_history(gui):
    if not submit_setup(gui):
        return
    points = deepcopy(points_for_gui(gui))
    settings = deepcopy(gui.transport_settings)
    start = getattr(gui,'start_time_choice',None)
    mine = getattr(gui,'mine_input_choice',None)
    service = TransportOpeningHistory()
    try:
        request = service.request(mine,start,points,settings)
    except (ValueError,TypeError) as exc:
        gui.transport_setup.status.setText(str(exc))
        return
    gui.transport_setup.status.setText('Loading actual crusher movements and grade-block quantities...')
    gui.transport_setup.refresh.setEnabled(False)
    def finish(result):
        gui.transport_setup.refresh.setEnabled(True)
        try:
            current = service.request(getattr(gui,'mine_input_choice',None),getattr(gui,'start_time_choice',None),
                                      points_for_gui(gui),gui.transport_settings)
        except (ValueError,TypeError):
            current = None
        if current != request:
            gui.transport_setup.status.setText('Opening-history request changed while loading. Refresh the current setup.')
            return
        gui.transport_opening_history = result
        gui.transport_setup.show_history(result)
    def failed(message):
        gui.transport_setup.refresh.setEnabled(True)
        gui.transport_setup.status.setText(str(message))
    gui.run_background_task('Loading opening conveyor/COS contents',
        lambda: service.fetch(mine,start,points,settings),finish,failed,show_progress=False)


def save_positions(gui,positions):
    gui.flow_node_positions = {**getattr(gui,'flow_node_positions',{}),**deepcopy(positions)}


def topology_for_gui(gui):
    from classes.MaterialFlowTopology import planning_topology
    feed = gui.current_multi_feed_configuration()
    calendar = getattr(gui,'calendar_inputs',{}) or {}
    context = {key:getattr(gui,attribute,'') for key,attribute in [
        ('hub','hub_input_choice'),('mine','mine_input_choice'),('opf','opf_input_choice'),('crusher','crusher_input_choice')]}
    inventory = gui.included_stockpile_data() if callable(getattr(gui,'included_stockpile_data',None)) else getattr(gui,'stockpile_data',{})
    sources = [dict(source=name,source_id=name,source_type='stockpile',is_amt=bool(row.get('amt')))
               for name,row in (inventory or {}).items()]
    payloads = getattr(gui,'expit_payload_transactions',None)
    if payloads is not None and hasattr(payloads,'to_dict'):
        sources += [dict(source=r.get('source') or str(r.get('direct_tip_id')),
                         source_id=str(r.get('direct_tip_id')),source_type='grade_block')
                    for r in payloads.to_dict('records') if r.get('direct_tip_eligible',True)]
    targets = {}
    for label,rate in (calendar.get('crusher_rate') or {}).items():
        period = str(label).lower().replace(' ','_')
        targets[period] = dict(crusher_rate=rate)
        for field,values in calendar.items():
            if field.startswith('crusher_target_') and isinstance(values,dict):
                targets[period][field[len('crusher_'):]] = values.get(label)
    return planning_topology(multi_feed=feed,site_context=context,sources=sources,crusher_targets=targets,
        product_build_settings=getattr(gui,'product_targets',[]) or [],
        byproducts_enabled=bool(getattr(gui,'byproducts_enabled',False)),transport_settings=gui.transport_settings)


def install_results(gui):
    from GUI.MaterialFlowResults import MaterialFlowResults
    gui.material_flow_results = MaterialFlowResults(gui,
        run_async=lambda work,success,failure:gui.run_background_task('Loading material flow',work,success,failure,show_progress=False),
        positions=lambda:getattr(gui,'flow_node_positions',{}))
    gui.material_flow_results.positions_changed.connect(lambda positions:save_positions(gui,positions))
    gui.reports_child_tabs.addTab(gui.material_flow_results,'Material Flow')
    gui.reports_child_tabs.currentChanged.connect(
        lambda:gui.material_flow_results.refresh() if gui.reports_child_tabs.currentWidget() is gui.material_flow_results else None)
    install_operational_reports(gui)


def install_operational_reports(gui):
    from GUI.OperationalBlendPlanView import OperationalBlendPlanView
    from classes.BlendPlanBackups import backup_choices
    from classes.DestinationBuildOrder import inventory_areas
    from classes.ExpitDataHandler import ExpitDataHandler
    gui.operational_plan_backups = getattr(gui,'operational_plan_backups',{})
    def context(data,sheets):
        graph = data.get('graph') or topology_for_gui(gui)
        points = [n['properties'].get('crusher') or n['label'] for n in graph['nodes'] if n['node_type']=='tipping_point']
        feed = gui.current_multi_feed_configuration()
        areas = inventory_areas(getattr(gui,'stockpile_data',{}) or {})
        evidence = next((frame for name,frame in sheets if name=='Material Destination Plan'),None)
        rows = evidence.to_dict('records') if evidence is not None else []
        def matches(area,point):
            configured = next((p for p in feed['tipping_points'] if p['name']==point),None)
            if configured:
                return area.casefold()==configured['rom_area'].casefold()
            return area.upper()==point.upper() or ExpitDataHandler.crusher_destination_matches(
                area,getattr(gui,'mine_input_choice',''),point,getattr(gui,'opf_input_choice',''))
        choices = backup_choices(points,rows,areas,matches)
        selections = gui.operational_plan_backups.get(data.get('plan_id','Primary'),getattr(gui,'blend_plan_backup_destinations',{}) or {})
        return choices,selections
    def save(plan,selections):
        gui.operational_plan_backups[plan] = deepcopy(selections)
    gui.operational_blend_plans = OperationalBlendPlanView(gui,
        run_async=lambda work,success,failure:gui.run_background_task('Preparing tipping-point Blend Plans',work,success,failure,show_progress=False),
        backup_context=context,save_backups=save)
    gui.reports_child_tabs.addTab(gui.operational_blend_plans,'Tipping-point Blend Plans')
    gui.reports_child_tabs.currentChanged.connect(
        lambda:gui.operational_blend_plans.refresh() if gui.reports_child_tabs.currentWidget() is gui.operational_blend_plans else None)
