"""Build the Excel annotation workbook from the gold sample.

The workbook shows sample_id, star rating, property name and text, then the label columns.
Split and weight stay in the metadata CSV. Rows keep the gold order:
G0001-G0050 are the checkpoint batch (stop and review after them).

Usage:
    python -m src.labeling.make_sheets                  # from data/processed/gold/gold_v1.csv
    python -m src.labeling.make_sheets --gold gold_v1 --force

Output: data/processed/gold/<gold>_annotation.xlsx with a "reviews" sheet (dropdowns for
every label) and a "labels" reference sheet.
"""
import argparse

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from src import config
from src.labeling.schema import REVIEW_TYPES, SENTIMENTS, TOPIC_HINTS, TOPICS

GOLD_DIR = config.DB_PATH.parent / "gold"
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
TOPIC_FILL = PatternFill("solid", fgColor="F2F2F2")
CHECKPOINT_ROWS = 50


def add_reviews_sheet(wb: Workbook, rows: pd.DataFrame) -> None:
    ws = wb.create_sheet("reviews")
    info = ["sample_id", "rating", "property_name", "text"]
    header = [*info, "review_type", *TOPICS, "notes"]
    ws.append(header)
    for r in rows.itertuples():
        ws.append([getattr(r, c) for c in info] + [None] * (len(header) - len(info)))

    for col, name in enumerate(header, start=1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.fill = TOPIC_FILL if name in TOPICS else HEADER_FILL
        cell.alignment = Alignment(text_rotation=90 if name in TOPICS else 0, vertical="bottom", wrap_text=True)
        if name in TOPIC_HINTS:
            cell.comment = Comment(TOPIC_HINTS[name], "guideline")
    widths = {"sample_id": 9, "rating": 6, "property_name": 20, "text": 80, "review_type": 18, "notes": 30}
    for col, name in enumerate(header, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = widths.get(name, 6)
    ws.row_dimensions[1].height = 140
    for row in ws.iter_rows(min_row=2, max_col=len(info)):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    # Thick line under the checkpoint batch.
    for cell in ws[CHECKPOINT_ROWS + 1]:
        cell.border = Border(bottom=Side(style="thick", color="C00000"))
    ws.freeze_panes = ws.cell(row=2, column=len(info) + 1).coordinate

    last = len(rows) + 1
    col_of = {name: ws.cell(row=1, column=i).column_letter for i, name in enumerate(header, start=1)}
    type_dv = DataValidation(type="list", formula1=f'"{",".join(REVIEW_TYPES)}"', allow_blank=True)
    type_dv.add(f"{col_of['review_type']}2:{col_of['review_type']}{last}")
    topic_dv = DataValidation(type="list", formula1=f'"{",".join(SENTIMENTS)}"', allow_blank=True)
    first_topic, last_topic = col_of[TOPICS[0]], col_of[TOPICS[-1]]
    topic_dv.add(f"{first_topic}2:{last_topic}{last}")
    ws.add_data_validation(type_dv)
    ws.add_data_validation(topic_dv)


def add_reference_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("labels")
    ws.append(["field", "values / meaning"])
    ws.append(["review_type", " | ".join(REVIEW_TYPES)])
    ws.append(["topics", "leave empty = not mentioned; pos = praised; neg = complained (both -> neg)"])
    for t in TOPICS:
        ws.append([t, TOPIC_HINTS[t]])
    ws.append(["rules", "generic -> all topics empty; staff_shoutout -> only staff_management; "
                        "elevator / door / gate problems -> elevators_access only"])
    ws.append(["checkpoint", f"stop after the first {CHECKPOINT_ROWS} rows (red line) for review"])
    ws.append(["full guideline", "docs/annotation_guideline.md"])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 120


def main(gold: str, force: bool) -> None:
    df = pd.read_csv(GOLD_DIR / f"{gold}.csv", encoding="utf-8-sig", dtype={"review_id": str})
    path = GOLD_DIR / f"{gold}_annotation.xlsx"
    if path.exists() and not force:
        raise SystemExit(f"{path} exists (may contain labels); use --force to overwrite")
    wb = Workbook()
    wb.remove(wb.active)
    add_reviews_sheet(wb, df)
    add_reference_sheet(wb)
    wb.save(path)
    print(f"{path.name}: {len(df)} reviews, first {CHECKPOINT_ROWS} = checkpoint batch")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold_v1")
    ap.add_argument("--force", action="store_true", help="overwrite an existing workbook")
    args = ap.parse_args()
    main(args.gold, args.force)
