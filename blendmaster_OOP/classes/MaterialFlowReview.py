"""Read saved plans once and project their state onto the topology."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import pandas as pd
from classes.TransportReports import read_transport_reports
from database.DatabaseContext import get_database_path

REPORTS = dict(feed='optimisation_plan_blend_report',product='optimisation_plan_product_build_report')
FALLBACKS = dict(feed='optimised_blend_report',product='product_build_report')


def saved_flow_plans(database_name=None):
    path = Path(database_name or get_database_path())
    if not path.exists():
        return []
    with closing(sqlite3.connect('file:'+str(path).replace('\\','/')+'?mode=ro',uri=True)) as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        plans = set()
        for table in ('material_flow_topology','optimisation_plan_blend_report'):
            if table in names:
                plans.update(r[0] for r in connection.execute(f'SELECT DISTINCT plan_id FROM {table}'))
        if not plans and 'optimised_blend_report' in names:
            plans.add('Primary')
        return sorted(plans,key=lambda name:(name!='Primary',name))


def saved_flow_data(plan_id='Primary',database_name=None):
    path = database_name or get_database_path()
    frames = read_transport_reports(path,plan_id)
    graph,warnings = None,[]
    with closing(sqlite3.connect('file:'+str(path).replace('\\','/')+'?mode=ro',uri=True)) as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'material_flow_topology' in names:
            row = connection.execute('SELECT topology_json,warnings_json FROM material_flow_topology WHERE plan_id=?',(plan_id,)).fetchone()
            if row:
                graph,warnings = json.loads(row[0]),json.loads(row[1])
                from classes.PhaseSchemas import is_readable, TOPOLOGY_SCHEMA_VERSION
                if not is_readable(graph,TOPOLOGY_SCHEMA_VERSION):
                    raise ValueError('Unsupported saved material-flow topology version. Recalculate this plan with a compatible application.')
        for key,table in REPORTS.items():
            if table in names:
                frames[key] = pd.read_sql_query(f'SELECT * FROM "{table}" WHERE plan_id=?',connection,params=(plan_id,))
            elif plan_id=='Primary' and FALLBACKS[key] in names:
                frames[key] = pd.read_sql_query(f'SELECT * FROM "{FALLBACKS[key]}"',connection)
            else:
                frames[key] = pd.DataFrame()
    return dict(graph=graph,frames=frames,warnings=warnings,plan_id=plan_id)


class FlowTimeline:
    def __init__(self,graph,frames,warnings=()):
        self.graph = graph
        self.frames = {key:value.copy() for key,value in frames.items()}
        self.warnings = list(warnings)
        times = set()
        for key,frame in self.frames.items():
            if key == 'product':
                frame.rename(columns={name:'end_datetime' for name in ('steady_state_end_datetime',)
                                      if name in frame and 'end_datetime' not in frame},inplace=True)
            for column in ('start_datetime','end_datetime','datetime'):
                if column in frame:
                    frame[column] = pd.to_datetime(frame[column],errors='coerce')
                    if key in ('feed','transport_contents'):
                        times.update(frame[column].dropna().tolist())
        self.times = sorted(times)

    @staticmethod
    def active(frame,at):
        if frame.empty or not {'start_datetime','end_datetime'}.issubset(frame):
            return frame.iloc[:0]
        return frame[(frame.start_datetime <= at)&(frame.end_datetime>at)]

    @staticmethod
    def number(frame,column):
        return pd.to_numeric(frame.get(column,pd.Series(0.0,index=frame.index)),errors='coerce').fillna(0)

    @classmethod
    def summary(cls,frame,quantity='source_actual_tonnes'):
        tonnes = cls.number(frame,quantity).sum()
        hours = (frame.end_datetime-frame.start_datetime).dt.total_seconds()/3600 if 'end_datetime' in frame and 'start_datetime' in frame else pd.Series(0.0,index=frame.index)
        rate = (cls.number(frame,quantity)/hours.where(hours>0)).sum()
        weight = cls.number(frame,'selected_grade_weight_fe_tonnes') if 'selected_grade_weight_fe_tonnes' in frame else cls.number(frame,quantity)
        grade = (cls.number(frame,'source_grade_fe')*weight).sum()/weight.sum() if weight.sum()>0 else None
        text = f'{tonnes:,.1f} ROM WMT · {rate:,.1f} t/h'
        if grade is not None:
            text += f'\nFe {grade:.3f}%'
        return dict(text=text,active=tonnes>1e-6)

    @staticmethod
    def where(frame,column,value):
        return frame[frame[column].astype(str)==str(value)] if column in frame else frame.iloc[:0]

    def frame(self,index):
        if not self.times:
            return dict(annotations={},active_edges=[],tables={},caption='No saved steady states.')
        at = self.times[max(0,min(index,len(self.times)-1))]
        feed = self.active(self.frames.get('feed',pd.DataFrame()),at)
        arrival_data = self.frames.get('transport_product_arrivals',pd.DataFrame())
        transport = bool(self.graph.get('properties',{}).get('latency_enabled'))
        if not transport:
            arrival_data = self.frames.get('feed',pd.DataFrame())
        arrivals = self.active(arrival_data,at)
        movements = self.active(self.frames.get('transport_movements',pd.DataFrame()),at)
        all_contents = self.frames.get('transport_contents',pd.DataFrame())
        contents = all_contents.iloc[:0]
        if not all_contents.empty and 'datetime' in all_contents:
            previous = all_contents[all_contents.datetime<=at]
            if not previous.empty:
                contents = previous[previous.datetime==previous.datetime.max()]
        product = self.frames.get('product',pd.DataFrame())
        # Product report rows retain explicit cumulative build/OPF contributions.
        product = product[product.end_datetime<=at] if 'end_datetime' in product else product.iloc[:0]
        if not product.empty and 'product_build_id' in product:
            group = ['product_build_id']+(['contributing_opf'] if 'contributing_opf' in product else [])
            latest = product.groupby(group,dropna=False).end_datetime.transform('max')
            product = product[product.end_datetime==latest]
        annotations = {}
        source_nodes,tip_nodes = {},{}
        for node in self.graph['nodes']:
            key,kind,props = node['node_id'],node['node_type'],node['properties']
            if kind=='source':
                source = props['source']
                source_nodes[key] = source
                selected = self.where(feed,'source',source)
                annotations[key] = self.summary(selected)
                history = self.where(self.frames.get('feed',pd.DataFrame()),'source',source)
                history = history[history.end_datetime<=at] if 'end_datetime' in history else history.iloc[:0]
                if not history.empty and 'source_closing_balance' in history:
                    last = history[history.end_datetime==history.end_datetime.max()]
                    # A shared stockpile's sequential report balances share one owner.
                    balance = self.number(last,'source_closing_balance').min()
                    annotations[key]['text'] += f'\nClosing {balance:,.1f} WMT'
            elif kind=='tipping_point':
                point = props.get('crusher') or node['label']
                tip_nodes[key] = point
                selected = self.where(feed,'tipping_point',point) if 'tipping_point' in feed else feed
                annotations[key] = self.summary(selected)
            elif kind in ('cos','conveyor'):
                selected = self.where(self.where(contents,'tipping_point',props.get('tipping_point')),'stage',kind)
                if not props.get('placeholder'):
                    tonnes = self.number(selected,'physical_rom_wmt').sum()
                    text = f'{tonnes:,.1f} ROM WMT in storage'
                    if kind=='cos':
                        text += f"\n{(self.number(selected,'physical_rom_wmt')>0).sum()} occupied chunks"
                    annotations[key] = dict(text=text,active=tonnes>1e-6)
                else:
                    selected = self.where(feed,'tipping_point',props.get('tipping_point')) if 'tipping_point' in feed else feed
                    summary = self.summary(selected)
                    summary['text'] = 'Pass-through\n'+summary['text']
                    annotations[key] = summary
            elif kind=='opf':
                selected = self.where(arrivals,'opf',props.get('opf')) if 'opf' in arrivals else arrivals
                annotations[key] = self.summary(selected,'source_arrival_wmt' if transport else 'source_actual_tonnes')
            elif kind=='product_build_lane':
                lane = props.get('lane','product')
                selected = arrivals
                opfs = [n['properties'].get('opf') for n in self.graph['nodes'] if n['node_id'] in props.get('opf_node_ids',[])]
                if opfs and 'opf' in selected:
                    selected = selected[selected.opf.isin(opfs)]
                column = f'product_build_{lane}_source_tonnes'
                if lane=='product':
                    column = 'product_build_source_tonnes'
                tonnes = self.number(selected,column).sum()
                annotations[key] = dict(text=f'{tonnes:,.1f} product tonnes in state',active=tonnes>1e-6)
        active_edges = []
        for edge in self.graph['edges']:
            source,target = edge['source_node_id'],edge['target_node_id']
            if source in source_nodes and target in tip_nodes:
                rows = self.where(feed,'source',source_nodes[source])
                rows = self.where(rows,'tipping_point',tip_nodes[target]) if 'tipping_point' in rows else rows
                active = self.number(rows,'source_actual_tonnes').sum()>1e-6
            else:
                active = annotations.get(source,{}).get('active') and annotations.get(target,{}).get('active')
            if active:
                active_edges.append(edge['edge_id'])
        states = ', '.join(str(v) for v in feed.get('steady_state_number',pd.Series(dtype=str)).drop_duplicates())
        caption = f"{at:%Y-%m-%d %H:%M:%S} · "+(f'Steady state {states}' if states else 'Closing / no tipping')
        return dict(annotations=annotations,active_edges=active_edges,caption=caption,at=at,
            tables={'Tipping transactions':feed,'OPF arrivals':arrivals,'Conveyor / COS contents':contents,
                    'Transport movements':movements,'Product contributions':product})
