import csv
import io
import re
import zipfile
from xml.etree import ElementTree


def read_rows(filename: str, content: bytes) -> list[dict[str, str]]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "csv":
        return _read_csv(content)
    if suffix == "xlsx":
        return _read_xlsx(content)
    raise ValueError("Only CSV and XLSX files are supported")


def _read_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _read_xlsx(content: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared = _shared_strings(archive)
        sheet_name = _first_sheet_path(archive)
        root = ElementTree.fromstring(archive.read(sheet_name))

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    matrix: list[list[str]] = []
    for row in root.findall(".//m:sheetData/m:row", ns):
        values: dict[int, str] = {}
        for cell in row.findall("m:c", ns):
            reference = cell.attrib.get("r", "A1")
            column = _column_index(reference)
            cell_type = cell.attrib.get("t")
            value_node = cell.find("m:v", ns)
            inline_node = cell.find("m:is/m:t", ns)
            raw = inline_node.text if inline_node is not None else (value_node.text if value_node is not None else "")
            if cell_type == "s" and raw:
                raw = shared[int(raw)]
            values[column] = raw or ""
        width = max(values, default=-1) + 1
        matrix.append([values.get(index, "") for index in range(width)])

    if not matrix:
        return []
    headers = [str(value).strip() for value in matrix[0]]
    rows: list[dict[str, str]] = []
    for values in matrix[1:]:
        row = {header: str(values[index]).strip() if index < len(values) else "" for index, header in enumerate(headers) if header}
        if any(row.values()):
            rows.append(row)
    return rows


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read(path))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return ["".join(node.text or "" for node in item.findall(".//m:t", ns)) for item in root.findall("m:si", ns)]


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    candidates = sorted(name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
    if not candidates:
        raise ValueError("The XLSX workbook has no worksheets")
    return candidates[0]


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    value = 0
    for char in letters.group(0) if letters else "A":
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1
