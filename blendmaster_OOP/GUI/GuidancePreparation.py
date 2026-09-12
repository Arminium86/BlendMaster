"""Prepare file-derived schedule guidance outside the UI thread."""
from GUI.InventoryStreamApplication import InventoryContext
from classes.ExpitDataHandler import ExpitDataHandler
from classes.GuidanceImport import file_revision

FIELDS = ('file_path_choice', 'start_time_choice', 'mine_input_choice',
          'opf_input_choice', 'crusher_input_choice', 'product_brand_labels_choice',
          'selected_two_wp_product_crushers')
OUTPUTS = ('aps_stockpile_brand_map', 'aps_stockpile_timing_guidance',
           'aps_active_blend_guidance', 'aps_destination_guidance',
           'aps_guidance_request_signature')


def prepare(implementation, values):
    context = InventoryContext(implementation, dict(values, aps_brand_guidance_cache={}))
    context.refresh_aps_stockpile_brand_map()
    path = values.get('file_path_choice')
    result = {key: vars(context).get(key) for key in OUTPUTS}
    if path:
        result['_calendar_destination_stockpiles'] = (
            file_revision(path), ExpitDataHandler.get_distinct_stockpile_destinations(path))
    return result


def calendar_destinations(host):
    path = getattr(host, 'file_path_choice', None)
    if not path:
        return []
    revision = file_revision(path)
    cached = vars(host).get('_calendar_destination_stockpiles')
    if not cached or tuple(cached[0]) != revision:
        cached = (revision, ExpitDataHandler.get_distinct_stockpile_destinations(path))
        host._calendar_destination_stockpiles = cached
    return list(cached[1])
