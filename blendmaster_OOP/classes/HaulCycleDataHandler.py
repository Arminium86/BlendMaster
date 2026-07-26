import pandas as pd


class HaulCycleDataHandler:
    SOURCE_COLUMN = "Source Node"
    DESTINATION_COLUMN = "Dest Node"
    CYCLE_TIME_COLUMN = "Total Cycle Time (min)"
    REQUIRED_COLUMNS = {
        SOURCE_COLUMN,
        DESTINATION_COLUMN,
        CYCLE_TIME_COLUMN,
    }

    @staticmethod
    def normalized_node_name(value, expected_prefix=None):
        value = str(value or "").strip().replace("\\", "/")
        if not value:
            return ""
        prefix, separator, name = value.partition("/")
        if expected_prefix and (
            not separator or prefix.strip().lower() != expected_prefix.lower()
        ):
            return ""
        return (name if separator else value).strip()

    @classmethod
    def normalized_stockpile_source(cls, value):
        """Return the inventory name and its optional In/Out route variant."""
        stockpile = cls.normalized_node_name(value, "Stockpiles")
        if not stockpile:
            return "", ""
        base_name, separator, suffix = stockpile.rpartition(":")
        variant = suffix.strip().lower() if separator else ""
        if variant not in {"in", "out"}:
            return stockpile, ""
        return base_name.strip(), variant

    @classmethod
    def _read_cycles(cls, input_data):
        data = pd.read_csv(
            input_data,
            usecols=lambda column: column in cls.REQUIRED_COLUMNS,
        )
        missing = cls.REQUIRED_COLUMNS.difference(data.columns)
        if missing:
            raise ValueError(
                "Haul Infinity Cycles.csv is missing required column(s): "
                + ", ".join(sorted(missing))
            )
        data = data.copy()
        data[cls.CYCLE_TIME_COLUMN] = pd.to_numeric(
            data[cls.CYCLE_TIME_COLUMN],
            errors="coerce",
        )
        return data

    @classmethod
    def get_distinct_crusher_names(cls, input_data):
        data = cls._read_cycles(input_data)
        crusher_names = {
            cls.normalized_node_name(value, "Crushers")
            for value in data[cls.DESTINATION_COLUMN]
        }
        return sorted(name for name in crusher_names if name)

    @classmethod
    def build_nearest_crusher_routes(cls, input_data, selected_crushers):
        selected = {
            cls.normalized_node_name(value).upper()
            for value in (selected_crushers or [])
            if cls.normalized_node_name(value)
        }
        if not selected:
            return {}

        data = cls._read_cycles(input_data)
        candidate_rows = []
        stockpiles_with_out_routes = set()
        for row in data.to_dict(orient="records"):
            stockpile, variant = cls.normalized_stockpile_source(
                row.get(cls.SOURCE_COLUMN)
            )
            if not stockpile or variant == "in":
                continue
            key = stockpile.upper()
            if variant == "out":
                stockpiles_with_out_routes.add(key)
            candidate_rows.append((row, stockpile, variant))

        routes = {}
        for row, stockpile, variant in candidate_rows:
            key = stockpile.upper()
            if not variant and key in stockpiles_with_out_routes:
                continue
            crusher = cls.normalized_node_name(
                row.get(cls.DESTINATION_COLUMN),
                "Crushers",
            )
            cycle_time = row.get(cls.CYCLE_TIME_COLUMN)
            if (
                not stockpile
                or not crusher
                or crusher.upper() not in selected
                or pd.isna(cycle_time)
                or float(cycle_time) <= 0
            ):
                continue
            route = {
                "nearest_crusher": crusher,
                "cycle_time_minutes": float(cycle_time),
                "source_node": str(row.get(cls.SOURCE_COLUMN) or "").strip(),
                "destination_node": str(
                    row.get(cls.DESTINATION_COLUMN) or ""
                ).strip(),
            }
            if (
                key not in routes
                or route["cycle_time_minutes"]
                < routes[key]["cycle_time_minutes"]
            ):
                routes[key] = route
        return routes

    @staticmethod
    def cost_per_tonne(cycle_time_minutes, haulage_cost_per_hour):
        """Convert a cycle and $/hour into $/t using a nominal 100 t payload."""
        try:
            cycle_time_minutes = float(cycle_time_minutes)
            haulage_cost_per_hour = float(haulage_cost_per_hour)
        except (TypeError, ValueError):
            return 0.0
        if cycle_time_minutes <= 0 or haulage_cost_per_hour <= 0:
            return 0.0
        return haulage_cost_per_hour * cycle_time_minutes / (60.0 * 100.0)
