"""Decode AMT map evidence outside the Qt thread, then publish one snapshot."""
from copy import copy, deepcopy
from database.DatabaseContext import get_database_path


def refresh(host):
    chart = vars(host).get('draw_AMT_map')
    if chart is None:
        return
    generation = vars(host).get('_amt_map_generation', 0) + 1
    host._amt_map_generation, host._amt_map_pending = generation, True
    database = get_database_path()
    excluded = host.excluded_amt_footprints()
    shadow = copy(chart)
    shadow.db_path, shadow.excluded_footprints = database, excluded

    def done(data):
        if generation != host._amt_map_generation or chart is not vars(host).get('draw_AMT_map'):
            return
        chart.excluded_footprints, chart.data = excluded, data
        host._amt_map_pending = False
        host.reconcile_saved_AMT_chunk_grade_streams(allow_pending=True)
        chart.update_chunk_settings(deepcopy(host.AMT_chunk_settings))
        chart.selected_points = deepcopy(host.hex_sequence_table or [])
        chart.unique_footprints = chart.get_unique_footprints()
        chart.clean_up_hex_sequence_table()
        chart.update_sequence_counter()
        chart.refresh_call = True
        chart.init_layout()

    def failed(error):
        if generation == host._amt_map_generation:
            host._amt_map_pending = False
            host.handle_AMT_stockpile_fetch_error(error)

    host.run_background_task('Preparing the AMT map…', shadow.fetch_data, done, failed)
