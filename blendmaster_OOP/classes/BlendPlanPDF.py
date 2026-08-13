from __future__ import annotations

import html
import math
import os
from datetime import datetime

import pandas as pd


class BlendPlanPDFError(RuntimeError):
    """Raised when a Blend Plan PDF cannot be generated."""


class BlendPlanPDF:
    """Export the complete manual Blend Plan without UI viewport limits."""

    PALETTE = (
        "#d9bf82", "#91c4b7", "#9fb9de", "#d6a6bd", "#b4cf8b",
        "#d8a47f", "#aeb0dc", "#84c7d0", "#c7b1de", "#e2cb78",
    )

    @staticmethod
    def _text(value):
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        return str(value).strip()

    @staticmethod
    def _number(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _timestamp(row, *keys):
        for key in keys:
            value = row.get(key)
            timestamp = pd.to_datetime(value, errors="coerce")
            if pd.notna(timestamp):
                return pd.Timestamp(timestamp)
        return pd.NaT

    @classmethod
    def _format_value(cls, column, value):
        text = cls._text(value)
        number = cls._number(value)
        if number is None:
            return text
        key = str(column or "").lower()
        if any(token in key for token in ("tonnes", "_wmt", "_dmt")):
            return f"{number:,.0f}"
        if any(token in key for token in ("grade", "ratio", "rate")):
            return f"{number:,.2f}"
        if number.is_integer():
            return f"{int(number):,}"
        return f"{number:,.2f}"

    @classmethod
    def _column_bands(cls, columns, widths, available_width):
        """Split a wide report into readable horizontal page bands."""
        columns = list(columns or [])
        if not columns:
            return []
        requested = {
            column: max(52.0, min(180.0, float(widths.get(column, 150)) * 0.52))
            for column in columns
        }
        anchors = [
            column for column in (
                "steady_state_number", "blend_ID", "source", "source_id"
            ) if column in columns
        ][:2]
        data_columns = [column for column in columns if column not in anchors]
        anchor_width = sum(requested[column] for column in anchors)
        if anchor_width >= available_width * 0.45:
            anchors = anchors[:1]
            anchor_width = sum(requested[column] for column in anchors)

        bands = []
        current = list(anchors)
        current_width = anchor_width
        for column in data_columns:
            width = requested[column]
            if len(current) > len(anchors) and current_width + width > available_width:
                bands.append(current)
                current = list(anchors)
                current_width = anchor_width
            current.append(column)
            current_width += width
        if current and (len(current) > len(anchors) or not bands):
            bands.append(current)
        return bands

    @classmethod
    def export(
        cls,
        output_path,
        sequence_rows,
        summaries,
        detailed_report,
        selected_columns,
        aliases=None,
        widths=None,
        title="Blend Plan",
        plan_id="Manual",
        logo_path=None,
        report_datetime=None,
    ):
        try:
            from reportlab.graphics.shapes import Drawing, Line, Rect, String
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER, TA_LEFT
            from reportlab.lib.pagesizes import A3, landscape
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.lib.utils import ImageReader
            from reportlab.platypus import (
                Image as ReportLabImage,
                LongTable,
                PageBreak,
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:
            raise BlendPlanPDFError(
                "PDF export requires ReportLab. Install the project "
                "requirements and try again."
            ) from exc

        output_path = os.path.abspath(str(output_path))
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        aliases = dict(aliases or {})
        widths = dict(widths or {})
        sequence = [dict(row or {}) for row in (sequence_rows or [])]
        summaries = [dict(row or {}) for row in (summaries or [])]
        report = (
            detailed_report.copy()
            if isinstance(detailed_report, pd.DataFrame)
            else pd.DataFrame(detailed_report or [])
        )
        selected_columns = [
            column for column in (selected_columns or [])
            if column in report.columns
        ]
        report_datetime = report_datetime or datetime.now()
        if not isinstance(report_datetime, datetime):
            parsed_report_datetime = pd.to_datetime(
                report_datetime, errors="coerce"
            )
            report_datetime = (
                parsed_report_datetime.to_pydatetime()
                if pd.notna(parsed_report_datetime) else datetime.now()
            )

        page_size = landscape(A3)
        margin = 12 * mm
        document = SimpleDocTemplate(
            output_path,
            pagesize=page_size,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=14 * mm,
            bottomMargin=13 * mm,
            title=title,
            author="BlendMaster",
            subject="Manual blend plan",
        )
        page_width = page_size[0] - document.leftMargin - document.rightMargin

        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name="BlendPlanTitle", parent=styles["Title"], fontName="Helvetica-Bold",
            fontSize=18, leading=21, textColor=colors.HexColor("#17365d"),
            spaceAfter=4,
        ))
        styles.add(ParagraphStyle(
            name="BlendPlanSection", parent=styles["Heading2"],
            fontName="Helvetica-Bold", fontSize=12, leading=14,
            textColor=colors.HexColor("#17365d"), spaceAfter=6,
        ))
        styles.add(ParagraphStyle(
            name="BlendPlanCell", parent=styles["BodyText"], fontSize=6.5,
            leading=8, alignment=TA_LEFT, wordWrap="CJK",
        ))
        styles.add(ParagraphStyle(
            name="BlendPlanHeader", parent=styles["BodyText"],
            fontName="Helvetica-Bold", fontSize=6.4, leading=7.5,
            alignment=TA_CENTER, textColor=colors.white, wordWrap="CJK",
        ))
        styles.add(ParagraphStyle(
            name="BlendPlanLegend", parent=styles["BodyText"], fontSize=7,
            leading=8.5, alignment=TA_LEFT,
        ))
        cell_style = styles["BlendPlanCell"]
        header_style = styles["BlendPlanHeader"]

        def paragraph(value, style=cell_style):
            return Paragraph(
                html.escape(cls._text(value)).replace("\n", "<br/>") or "&#160;",
                style,
            )

        def page_footer(canvas, doc):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#d7dde5"))
            canvas.line(
                document.leftMargin, 9 * mm,
                page_size[0] - document.rightMargin, 9 * mm,
            )
            canvas.setFillColor(colors.HexColor("#5f6b78"))
            canvas.setFont("Helvetica", 7)
            canvas.drawString(document.leftMargin, 5.5 * mm, "BlendMaster - Blend Plan")
            canvas.drawCentredString(
                page_size[0] / 2.0,
                5.5 * mm,
                "Report date and time: "
                + report_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            )
            canvas.drawRightString(
                page_size[0] - document.rightMargin,
                5.5 * mm,
                f"Page {doc.page}",
            )
            canvas.restoreState()

        def make_gantt():
            valid_rows = []
            for index, row in enumerate(sequence):
                start = cls._timestamp(row, "_exact_start", "Start Datetime")
                end = cls._timestamp(row, "_exact_end", "End Datetime")
                if pd.notna(start) and pd.notna(end) and end > start:
                    valid_rows.append((index, row, start, end))
            if not valid_rows:
                return paragraph("No manual Gantt bars are available for export.")

            start_all = min(item[2] for item in valid_rows)
            end_all = max(item[3] for item in valid_rows)
            duration = max((end_all - start_all).total_seconds(), 1.0)
            lane_count = len(valid_rows)
            height = min(350.0, max(135.0, 55.0 + lane_count * 18.0))
            lane_height = min(18.0, max(8.0, (height - 48.0) / lane_count))
            drawing = Drawing(page_width, height)
            label_width = 88.0
            plot_left = label_width
            plot_width = page_width - label_width - 4.0
            plot_top = height - 25.0
            plot_bottom = 22.0
            drawing.add(Rect(
                plot_left, plot_bottom, plot_width, plot_top - plot_bottom,
                fillColor=colors.HexColor("#edf3fb"), strokeColor=colors.HexColor("#b9c5d3"),
            ))
            for tick in range(6):
                fraction = tick / 5.0
                x = plot_left + plot_width * fraction
                drawing.add(Line(
                    x, plot_bottom, x, plot_top,
                    strokeColor=colors.HexColor("#c8d2df"), strokeWidth=0.5,
                ))
                tick_time = start_all + (end_all - start_all) * fraction
                drawing.add(String(
                    x, 7.0, tick_time.strftime("%d %b %H:%M"),
                    fontName="Helvetica", fontSize=6.5, textAnchor="middle",
                    fillColor=colors.HexColor("#4d5b6a"),
                ))

            colour_by_mix = {}
            for lane, (index, row, start, end) in enumerate(valid_rows):
                summary = summaries[index] if index < len(summaries) else {}
                mix_key = cls._text(
                    summary.get("_stockpile_mix_key")
                    or row.get("_stockpile_mix_key")
                    or summary.get("Sources")
                    or row.get("Sources")
                    or f"BAR_{index}"
                )
                if mix_key not in colour_by_mix:
                    colour_by_mix[mix_key] = cls.PALETTE[
                        len(colour_by_mix) % len(cls.PALETTE)
                    ]
                y = plot_top - (lane + 1) * lane_height + 1.0
                x = plot_left + (
                    (start - start_all).total_seconds() / duration
                ) * plot_width
                bar_width = max(
                    1.0,
                    ((end - start).total_seconds() / duration) * plot_width,
                )
                drawing.add(Rect(
                    x, y, bar_width, max(lane_height - 2.0, 4.0),
                    fillColor=colors.HexColor(colour_by_mix[mix_key]),
                    strokeColor=colors.HexColor("#52606d"), strokeWidth=0.55,
                ))
                bar_number = summary.get("Bar", index + 1)
                blend_id = summary.get("Blend ID", row.get("Blend ID", ""))
                label = f"Bar {bar_number} | Blend {blend_id}"
                drawing.add(String(
                    label_width - 4.0, y + max(lane_height - 2.0, 4.0) / 2.0 - 2.2,
                    label, fontName="Helvetica", fontSize=max(5.2, min(7.0, lane_height * 0.42)),
                    textAnchor="end", fillColor=colors.HexColor("#273444"),
                ))
            return drawing

        def legend_card(summary):
            grades = " | ".join(
                f"{grade.replace('Grade ', '')} {cls._format_value(grade, summary.get(grade))}"
                for grade in ("Grade Fe", "Grade Si", "Grade Al", "Grade P", "Grade Mn")
                if cls._text(summary.get(grade))
            )
            lines = [
                f"<b>Bar {html.escape(cls._text(summary.get('Bar')))} - Blend "
                f"{html.escape(cls._text(summary.get('Blend ID')))}</b>",
                f"{html.escape(cls._text(summary.get('Start Datetime')))} → "
                f"{html.escape(cls._text(summary.get('End Datetime')))}",
                f"<b>Stream:</b> {html.escape(cls._text(summary.get('Optimiser Grade Stream')))}",
            ]
            if grades:
                lines.append(html.escape(grades))
            lines.append(
                f"<b>Stockpiles:</b> {html.escape(cls._text(summary.get('Sources and Ratios')) or 'None')}"
            )
            lines.append(
                f"<b>Direct tip:</b> {html.escape(cls._text(summary.get('Direct Tip Grade Blocks')) or 'None')}"
            )
            card = Table(
                [[Paragraph("<br/>".join(lines), styles["BlendPlanLegend"])]],
                colWidths=[(page_width - 12.0) / 3.0],
            )
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9fc")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#9eacbc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            return card

        def make_legend_grid():
            if not summaries:
                return paragraph("No blend legend entries are available.")
            cards = [legend_card(summary) for summary in summaries]
            rows = []
            for index in range(0, len(cards), 3):
                row = cards[index:index + 3]
                row.extend([""] * (3 - len(row)))
                rows.append(row)
            grid = Table(
                rows,
                colWidths=[page_width / 3.0] * 3,
                hAlign="LEFT",
                splitByRow=1,
            )
            grid.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            return grid

        def styled_long_table(data, column_widths, font_size=6.5):
            table = LongTable(
                data,
                colWidths=column_widths,
                repeatRows=1,
                splitByRow=1,
                hAlign="LEFT",
            )
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#315b7d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#bdc8d3")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8fb")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("FONTSIZE", (0, 1), (-1, -1), font_size),
            ]))
            return table

        story = []
        if logo_path and os.path.isfile(str(logo_path)):
            image_width, image_height = ImageReader(str(logo_path)).getSize()
            max_width = 55 * mm
            max_height = 42 * mm
            scale = min(
                max_width / max(float(image_width), 1.0),
                max_height / max(float(image_height), 1.0),
            )
            logo = ReportLabImage(
                str(logo_path),
                width=float(image_width) * scale,
                height=float(image_height) * scale,
            )
            logo.hAlign = "CENTER"
            story.extend([logo, Spacer(1, 4)])
        story.extend([
            Paragraph(html.escape(title), styles["BlendPlanTitle"]),
            paragraph(
                f"Plan: {plan_id} | Report date and time: "
                f"{report_datetime.strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            Spacer(1, 6),
            Paragraph("Manual Blend Gantt", styles["BlendPlanSection"]),
            make_gantt(),
            Spacer(1, 7),
            Paragraph("Gantt Legend", styles["BlendPlanSection"]),
            make_legend_grid(),
            PageBreak(),
            Paragraph("Blend Summary", styles["BlendPlanSection"]),
        ])

        summary_columns = [
            "Bar", "Blend ID", "Start Datetime", "End Datetime",
            "Optimiser Grade Stream", "Grade Fe", "Grade Si", "Grade Al",
            "Grade P", "Grade Mn", "Sources and Ratios",
            "Direct Tip Grade Blocks",
        ]
        if summaries:
            summary_data = [[paragraph(column, header_style) for column in summary_columns]]
            for summary in summaries:
                summary_data.append([
                    paragraph(cls._format_value(column, summary.get(column)))
                    for column in summary_columns
                ])
            fixed = [28, 38, 72, 72, 76, 35, 35, 35, 35, 35]
            remainder = page_width - sum(fixed)
            summary_widths = fixed + [remainder * 0.43, remainder * 0.57]
            story.append(styled_long_table(summary_data, summary_widths, 6.2))
        else:
            story.append(paragraph("No blend summary rows are available."))

        story.extend([
            PageBreak(),
            Paragraph("Detailed Report", styles["BlendPlanSection"]),
        ])
        bands = cls._column_bands(selected_columns, widths, page_width)
        if report.empty or not bands:
            story.append(paragraph("No detailed report rows are available."))
        else:
            for band_index, band in enumerate(bands):
                if band_index:
                    story.extend([
                        PageBreak(),
                        Paragraph(
                            f"Detailed Report - columns {band_index + 1} of {len(bands)}",
                            styles["BlendPlanSection"],
                        ),
                    ])
                requested_widths = [
                    max(52.0, min(180.0, float(widths.get(column, 150)) * 0.52))
                    for column in band
                ]
                scale = min(1.0, page_width / sum(requested_widths))
                column_widths = [width * scale for width in requested_widths]
                data = [[
                    paragraph(aliases.get(column, column), header_style)
                    for column in band
                ]]
                for _, row in report.iterrows():
                    data.append([
                        paragraph(cls._format_value(column, row.get(column)))
                        for column in band
                    ])
                story.append(styled_long_table(data, column_widths, 6.2))

        try:
            document.build(
                story,
                onFirstPage=page_footer,
                onLaterPages=page_footer,
            )
        except Exception as exc:
            raise BlendPlanPDFError(
                f"Unable to create Blend Plan PDF: {exc}"
            ) from exc
        return output_path
