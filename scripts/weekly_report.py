from datetime import date, datetime, timedelta, timezone
from pathlib import Path


PACIFIC_STANDARD_TIME = timezone(timedelta(hours=-8))
REPORT_DIRECTORY = Path(__file__).resolve().parent.parent / "report-history"


def week_start(today=None):
    today = today or datetime.now(PACIFIC_STANDARD_TIME).date()
    return today - timedelta(days=today.weekday())


def weekly_report_path(today=None):
    return REPORT_DIRECTORY / f"{week_start(today).isoformat()}.pdf"


def _pdf_text(value):
    value = str(value).encode("latin-1", "replace").decode("latin-1")
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(value, limit):
    words = str(value).split()
    lines = []
    line = ""
    for word in words:
        if line and len(line) + len(word) + 1 > limit:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines or [""]


def _page_stream(rows, start_rank, monday):
    commands = [
        "BT /F1 16 Tf 45 565 Td (Weekly Project Popularity Ranking) Tj ET",
        f"BT /F1 10 Tf 45 545 Td (Week of {_pdf_text(monday.isoformat())}) Tj ET",
        "BT /F1 9 Tf 45 520 Td (Rank) Tj ET",
        "BT /F1 9 Tf 85 520 Td (Repository) Tj ET",
        "BT /F1 9 Tf 445 520 Td (Score) Tj ET",
        "BT /F1 9 Tf 510 520 Td (Labels) Tj ET",
    ]
    y = 500
    rank = start_rank
    for title, score, labels in rows:
        repo_lines = _wrap(title, 53)
        label_lines = _wrap(", ".join(labels) if labels else "None", 39)
        height = max(len(repo_lines), len(label_lines))
        for index in range(height):
            line_y = y - index * 12
            if index == 0:
                commands.append(
                    f"BT /F1 9 Tf 45 {line_y} Td ({rank}) Tj ET"
                )
                commands.append(
                    f"BT /F1 9 Tf 445 {line_y} Td ({_pdf_text(f'{score:.2f}')}) Tj ET"
                )
            if index < len(repo_lines):
                commands.append(
                    f"BT /F1 9 Tf 85 {line_y} Td ({_pdf_text(repo_lines[index])}) Tj ET"
                )
            if index < len(label_lines):
                commands.append(
                    f"BT /F1 9 Tf 510 {line_y} Td ({_pdf_text(label_lines[index])}) Tj ET"
                )
        y -= height * 12 + 8
        rank += 1
    return ("\n".join(commands) + "\n").encode("latin-1")


def _make_pdf(pages):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_ids = []
    for stream in pages:
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 792 612] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            .encode("ascii")
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream"
        )
    kids = " ".join(f"{object_id} 0 R" for object_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def generate_weekly_report(rows, today=None):
    monday = week_start(today)
    normalized_rows = sorted(rows, key=lambda row: (-row[1], row[0]))
    pages = []
    page_rows = []
    used_height = 0
    rank = 1
    for row in normalized_rows:
        height = max(
            len(_wrap(row[0], 53)),
            len(_wrap(", ".join(row[2]) if row[2] else "None", 39)),
        ) * 12 + 8
        if page_rows and used_height + height > 435:
            pages.append(_page_stream(page_rows, rank - len(page_rows), monday))
            page_rows = []
            used_height = 0
        page_rows.append(row)
        used_height += height
        rank += 1
    pages.append(_page_stream(page_rows, rank - len(page_rows), monday))

    output_path = weekly_report_path(today)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_make_pdf(pages))
    print(f"Weekly ranking PDF saved to {output_path}")
    return output_path
