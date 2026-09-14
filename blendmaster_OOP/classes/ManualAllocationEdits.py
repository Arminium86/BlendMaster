"""Recalculate edits to saved multi-point allocations using their material evidence.

Rows retain source/chunk and route identity. Quantities and interval times may
change, but shared inventory, route exclusivity and Calendar limits still apply.
"""
from copy import deepcopy
import math
import pandas as pd
from classes.CustomConstraints import source_property_kind
from classes.EquipmentLimits import require_equipment_limits
from classes.DirectTipLimits import direct_tip_violations
from classes.ProductBuildProgress import ProductBuildProgress
from classes.MultiFeedSettings import multi_feed_settings, scoped_builds


def require_target_capacity(frame,targets):
    from classes.MultiFeedSettings import build_opfs
    for scope in {build_opfs(t) for t in targets}:
        builds=[t for t in targets if build_opfs(t)==scope]
        rows=frame.loc[frame.opf.isin(scope)] if scope and 'opf' in frame else frame
        for lane in {str(t.get('byproduct') or 'product').lower() for t in builds}:
            selected=[t for t in builds if str(t.get('byproduct') or 'product').lower()==lane]
            limit=sum(max(0,float(t.get('target_tonnes') or 0)-float(t.get('opening_tonnes') or 0)) for t in selected)
            column='product_build_source_tonnes' if lane=='product' else f'product_build_{lane}_source_tonnes'
            amount=pd.to_numeric(rows.get(column,pd.Series(dtype=float)),errors='coerce').fillna(0).sum()
            if amount>limit+max(.1,limit*1e-7):
                raise ValueError(f'{", ".join(scope) or "Product"}: manual production exceeds the hard product-build tonnage target by {amount-limit:,.1f} t.')


def recalculate(original, edited, calendar, periods, targets, topology):
    saved_build=original.attrs.get('manual_build_report')
    base, result = original.copy().reset_index(drop=True), edited.copy().reset_index(drop=True)
    base.attrs = {}; result.attrs = {}
    identity = ['tipping_point','opf','source','source_type','steady_state_number','blend_ID']
    if len(base) != len(result) or not base[identity].equals(result[identity]):
        raise ValueError('Allocation source and tipping-point identities must be preserved.')
    unchanged_times=base[['start_datetime','end_datetime']].eq(result[['start_datetime','end_datetime']]).all(axis=1)
    from classes.ReportTiming import restore_report_timing
    base=restore_report_timing(base)
    for key in ('start_datetime','end_datetime'):
        result[key]=pd.to_datetime(result[key],errors='coerce',format='mixed')
        result.loc[unchanged_times,key]=base.loc[unchanged_times,key]
    config = calendar.get('solver_config') or {}
    feed = multi_feed_settings((calendar.get('site_context') or {}).get('multi_feed_settings') or config.get('multi_feed_settings'))
    context = calendar.get('site_context') or {}
    for prefix, default in [('crusher','modelled_rom_wmt'),('reclaimer','modelled_rom_wmt'),('product_build','modelled_product_wmt')]:
        column=prefix+'_source_tonnes'
        field=context.get(prefix+'_tonnes_stream') or config.get(prefix+'_tonnes_stream') or default
        if column not in base:
            values=base.get('source_property_'+field)
            if values is None and config.get('strict_mapped_fields'):
                raise ValueError(f'The saved plan lacks the selected {prefix} quantity {field}. Recalculate it before editing.')
            base[column]=values if values is not None else base.source_actual_tonnes
            result[column]=base[column]
    for frame in (base,result):
        for key in ('start_datetime','end_datetime'):
            frame[key] = pd.to_datetime(frame[key],errors='coerce')
        frame['source_actual_tonnes'] = pd.to_numeric(frame.source_actual_tonnes,errors='coerce')
        if (frame[['start_datetime','end_datetime','source_actual_tonnes']].isna().any().any()
                or not frame.source_actual_tonnes.map(math.isfinite).all()
                or (frame.source_actual_tonnes < 0).any() or (frame.end_datetime <= frame.start_datetime).any()):
            raise ValueError('Enter valid times and finite, non-negative source tonnes.')
    if ((base.source_actual_tonnes <= 0) & (result.source_actual_tonnes > 0)).any():
        raise ValueError('A new source needs prepared material evidence. Copy a plan containing that source before allocating it.')
    ratio = result.source_actual_tonnes.div(base.source_actual_tonnes).fillna(0)
    quantities = {'crusher_source_tonnes','reclaimer_source_tonnes','product_build_source_tonnes'}
    for key in result:
        if (key in quantities or key.startswith('selected_grade_weight_') and key.endswith('_tonnes')
                or key.startswith('product_build_') and (key.endswith('_source_tonnes') or '_grade_weight_' in key and key.endswith('_tonnes'))
                or key.startswith('source_property_') and not key.endswith(('_opening_balance','_closing_balance'))
                    and source_property_kind(key[len('source_property_'):],config.get('source_property_kinds')) == 'additive'):
            result[key] = pd.to_numeric(base[key],errors='coerce')*ratio
    # The saved ledger proves when material was available. Never bring a
    # future receipt forward or allocate the same payload/chunk twice.
    for (kind,source), rows in base.groupby(['source_type','source'],dropna=False,sort=False):
        indices = rows.index
        original_used, available = 0.0, []
        for _, row in rows.sort_values('start_datetime',kind='stable').iterrows():
            supply = float(row['source_opening_balance'])+original_used
            available.append((row.start_datetime,supply))
            original_used += float(row.source_actual_tonnes)
        used = 0.0
        for index,row in result.loc[indices].sort_values('start_datetime',kind='stable').iterrows():
            proven = max((amount for time,amount in available if time <= row.start_datetime),default=0)
            opening = max(0,proven-used)
            if row.source_actual_tonnes > opening+max(1e-4,opening*1e-7):
                raise ValueError(f'{source}: {row.source_actual_tonnes:,.1f} t requested but only {opening:,.1f} t is available at {row.start_datetime}.')
            result.loc[index,'source_opening_balance'] = opening
            result.loc[index,'source_closing_balance'] = max(0,opening-row.source_actual_tonnes)
            used += row.source_actual_tonnes
    for key in list(result):
        if (key.startswith('source_property_') and not key.endswith(('_opening_balance','_closing_balance'))
                and source_property_kind(key[len('source_property_'):],config.get('source_property_kinds')) == 'additive'):
            unit=pd.to_numeric(base[key],errors='coerce').div(base.source_actual_tonnes).fillna(0)
            for bound in ('opening','closing'):
                column=key+'_'+bound+'_balance'
                if column in result: result[column]=unit*result['source_'+bound+'_balance']
    active = result.loc[result.source_actual_tonnes > 1e-7].copy()
    # A footprint may move between points, but cannot reclaim to two at once.
    parent = active.get('parent_stockpile',pd.Series('',index=active.index)).fillna('').astype(str)
    active['_physical_source'] = parent.where(parent.ne(''),active.source)
    for _,rows in active.loc[active.source_type.eq('stockpile')].groupby('_physical_source'):
        records=rows.to_dict('records')
        for index,row in enumerate(records):
            if any(row['tipping_point'] != other['tipping_point'] and row['start_datetime'] < other['end_datetime']
                   and other['start_datetime'] < row['end_datetime'] for other in records[:index]):
                raise ValueError(f"{row['_physical_source']}: overlapping reclaim to different tipping points is not allowed.")
    for point,rows in active.groupby('tipping_point'):
        intervals=rows[['steady_state_number','start_datetime','end_datetime']].drop_duplicates().sort_values('start_datetime')
        previous=None
        for row in intervals.itertuples(index=False):
            if previous is not None and row.start_datetime < previous:
                raise ValueError(f'{point}: blend intervals overlap.')
            previous=row.end_datetime
    for (_,_,_), rows in result.groupby(['tipping_point','steady_state_number','blend_ID'],sort=False,dropna=False):
        if len(rows[['start_datetime','end_datetime']].drop_duplicates()) != 1:
            raise ValueError('Every source in one tipping-point interval needs the same start and end.')
        hours = (rows.end_datetime.iloc[0]-rows.start_datetime.iloc[0]).total_seconds()/3600
        total = rows.source_actual_tonnes.sum()
        result.loc[rows.index,'steady_state_duration'] = hours
        result.loc[rows.index,'source_blend_ratio'] = rows.source_actual_tonnes/total if total else 0
        result.loc[rows.index,'actual_direct_tip_ratio'] = rows.loc[rows.source_type.eq('grade_block'),'source_actual_tonnes'].sum()/total if total else 0
        crusher = pd.to_numeric(rows.get('crusher_source_tonnes',rows.source_actual_tonnes),errors='coerce').fillna(0)
        result.loc[rows.index,'crusher_actual_tonnes'] = crusher.sum()
        result.loc[rows.index,'crusher_rate_output'] = crusher.sum()/hours
        result.loc[rows.index,'equipment_rate_output'] = rows.source_actual_tonnes/hours
        point=next((p for p in feed['tipping_points'] if p['name']==rows.tipping_point.iloc[0]),{})
        parents=rows.get('parent_stockpile',pd.Series('',index=rows.index)).fillna('').astype(str)
        parents=parents.where(parents.ne(''),rows.source)
        count=parents[rows.source_type.eq('stockpile') & rows.source_actual_tonnes.gt(1e-7)].nunique()
        if total>1e-7:
            for name in ('min_stockpiles','max_stockpiles'):
                limit=point.get(name) if point.get(name) is not None else calendar.get(name)
                if limit is not None and (count<float(limit) if name.startswith('min') else count>float(limit)):
                    raise ValueError(f"{rows.tipping_point.iloc[0]}: {count} selected stockpiles violates {name.replace('_',' ')} {limit}.")
        for analyte in ('fe','si','al','p','mn'):
            values = pd.to_numeric(rows.get('source_grade_'+analyte),errors='coerce')
            weights = pd.to_numeric(rows.get('selected_grade_weight_'+analyte+'_tonnes',crusher),errors='coerce').fillna(0)
            if values is not None:
                result.loc[rows.index,'crusher_actual_grade_'+analyte] = (values*weights).sum()/weights.sum() if weights.sum() else None
    require_equipment_limits(result,calendar,periods,mode=feed['mode'],require_limits=True)
    errors = direct_tip_violations(result,calendar,periods,feed['mode'])
    if errors:
        raise ValueError('\n'.join(errors[:12]))
    # Calendar grade limits are operating constraints even for soft builds.
    from classes.EquipmentLimits import period_limit
    for row in result.drop_duplicates(['tipping_point','steady_state_number']).to_dict('records'):
        for key,start in periods.items():
            if not key.endswith('_start') or not (pd.Timestamp(start)<row['end_datetime'] and pd.Timestamp(periods[key[:-6]+'_end'])>row['start_datetime']):
                continue
            for a in ('fe','si','al','p','mn'):
                value=row.get('crusher_actual_grade_'+a)
                if value is None or pd.isna(value): continue
                for bound in ('min','max'):
                    field=f'target_{a}_{bound}' if feed['mode']!='single' else f'crusher_target_{a}_{bound}'
                    limit=period_limit(calendar,field,key[:-6],row['tipping_point'],feed['mode'])
                    if limit is not None and (value<limit-1e-7 if bound=='min' else value>limit+1e-7):
                        raise ValueError(f"{row['tipping_point']}: {a.upper()} violates the Calendar {bound} {limit}.")
    if config.get('custom_constraints'):
        from classes.CustomConstraints import compile_mapping_custom_constraint, custom_constraint_aggregate_totals, constraint_report_fields
        for _,rows in result.groupby(['tipping_point','steady_state_number'],sort=False):
            records=[]
            for row in rows.to_dict('records'):
                records.append({**row, 'balance':row['source_actual_tonnes'], 'constraint_source_balance':row['source_actual_tonnes'], 'source_properties':{
                    k[len('source_property_'):]:v for k,v in row.items() if k.startswith('source_property_')}})
            for constraint in config['custom_constraints']:
                compiled=compile_mapping_custom_constraint(records,constraint,config.get('source_property_kinds'),config.get('source_property_weights'))
                numerator,denominator=custom_constraint_aggregate_totals(compiled,rows.source_actual_tonnes.tolist())
                value=numerator/denominator if numerator is not None and denominator else None
                if value is None or any(constraint.get(b) is not None and (value<float(constraint[b]) if b=='minimum' else value>float(constraint[b])) for b in ('minimum','maximum')):
                    raise ValueError(f"Manual allocation violates {constraint.get('name','a custom constraint')}.")
                for key,value in constraint_report_fields(constraint,numerator,denominator,constraint.get('minimum'),constraint.get('maximum')).items():
                    result.loc[rows.index,key]=value
    stale = [c for c in result if c in ProductBuildProgress.COLUMNS or c.startswith('product_build_') and '@' in c]
    result=result.drop(columns=stale)
    targets=scoped_builds(targets,feed)
    from classes.TransportSettings import transport_enabled
    with_transport=transport_enabled(context.get('transport_settings') or config.get('transport_settings'))
    if not with_transport: require_target_capacity(result,targets)
    result=ProductBuildProgress.annotate(result,targets,solver_config=config,byproducts_enabled=config.get('byproducts_enabled'))
    result['blend_option']='Manual'; result['manual_origin']='Edited saved allocations'
    if isinstance(saved_build,pd.DataFrame) and not saved_build.empty:
        profile=saved_build.copy()
        time_key=next((k for k in ('time','start_datetime','snapshot_datetime') if k in profile),None)
        if time_key and {'stockpile','closing_balance'}.issubset(profile):
            def depletion(frame,source,at):
                parent=frame.get('parent_stockpile',pd.Series('',index=frame.index)).fillna('').astype(str)
                names=parent.where(parent.ne(''),frame.source)
                rows=frame.loc[frame.source_type.eq('stockpile') & names.eq(source)]
                hours=(rows.end_datetime-rows.start_datetime).dt.total_seconds()
                fraction=(at-rows.start_datetime).dt.total_seconds().div(hours).clip(0,1)
                return (rows.source_actual_tonnes*fraction).sum()
            for index,row in profile.iterrows():
                at=pd.to_datetime(row[time_key],errors='coerce')
                if pd.isna(at): continue
                delta=depletion(base,row.stockpile,at)-depletion(result,row.stockpile,at)
                profile.loc[index,'closing_balance']=float(row.closing_balance)+delta
        result.attrs['manual_build_report']=profile
    result.attrs['material_flow_topology']=deepcopy(topology)
    from classes.ManualTransport import replay
    result=replay(result,calendar,periods,targets,topology)
    if with_transport:
        arrivals=result.attrs.get('transport_frames',{}).get('transport_product_arrivals',pd.DataFrame())
        require_target_capacity(arrivals,targets)
    return result
