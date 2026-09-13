"""Physical crusher identities for warehouse movements, separate from APS paths."""
import re
from classes.ExpitDataHandler import ExpitDataHandler


FEED_PREDICATE = """((MOVEMENT_CLASSIFICATION = 'Rehandle Ore'
    AND MOVEMENT_SUBCLASSIFICATION = 'Rehandle Ore Primary')
 OR (MOVEMENT_TYPE = 'ExPit' AND MOVEMENT_CLASSIFICATION = 'Expit Ore'
    AND MOVEMENT_SUBCLASSIFICATION IN ('Expit Ore', 'Expit Ore Direct Feed')))"""


def destination_matches(row, site, point):
    # FMS distinguishes the two CC OPF01 hoppers. The inventory DESTINATION
    # groups both under OP1_HOP01 and therefore cannot identify the tipping point.
    destination = row.get('DESTINATION_FMS') or row.get('DESTINATION') or ''
    compact = re.sub(r'[^A-Z0-9]', '', str(destination).upper())
    name = str(point['name']).upper()
    aliases = {re.sub(r'[^A-Z0-9]', '', name)}
    if str(site).upper() == 'CC':
        aliases.update({
            'OPF01_PC': {'OP1HOP01', 'OPF01', 'OPF1', 'OPF1CRUSHER', 'OPF01CRUSHER'},
            'HAL_PC': {'OP1HOP02', 'HAL', 'HALCRUSHER'},
            'OPF02_PC': {'OP2HOP01', 'OPF02', 'OPF2', 'RCH', 'OPF2CRUSHER', 'OPF02CRUSHER'},
        }.get(name, set()))
    elif name.startswith('OPF'):
        aliases.add(re.sub(r'[^A-Z0-9]', '', name.removesuffix('_PC')))
    return compact in aliases or any(
        ExpitDataHandler.crusher_destination_names_match(destination, alias)
        for alias in aliases
    )
