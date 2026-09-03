"""Єдине оформлення всього DOCX без зміни тексту, полів і URL."""

from __future__ import annotations

from copy import deepcopy

from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.opc.oxml import serialize_part_xml


def plain_run_properties(font_name: str, font_size: int):
    """Явні значення також перекривають успадковане оформлення стилів."""
    properties = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for name in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{name}"), font_name)
    properties.append(fonts)
    for name in (
        "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
        "outline", "shadow", "emboss", "imprint", "vanish", "webHidden",
    ):
        value = OxmlElement(f"w:{name}")
        value.set(qn("w:val"), "0")
        properties.append(value)
    for name, value in (
        ("color", "000000"), ("spacing", "0"), ("w", "100"), ("kern", "0"),
        ("position", "0"), ("sz", str(font_size * 2)), ("szCs", str(font_size * 2)),
        ("u", "none"), ("vertAlign", "baseline"),
    ):
        element = OxmlElement(f"w:{name}")
        element.set(qn("w:val"), value)
        properties.append(element)
    return properties


def set_run_font(run, font_name: str, font_size: int, highlight: str | None = None) -> None:
    properties = run.find(qn("w:rPr"))
    if properties is not None:
        run.remove(properties)
    properties = plain_run_properties(font_name, font_size)
    if highlight is not None:
        element = OxmlElement("w:highlight")
        element.set(qn("w:val"), highlight)
        properties.insert(properties.index(properties.find(qn("w:u"))), element)
    run.insert(0, properties)


def normalize_document(document, font_name: str, font_size: int) -> None:
    """Охоплює інші таблиці, колонтитули, примітки, стилі та нумерацію."""
    template = plain_run_properties(font_name, font_size)
    for part in document.part.package.parts:
        if not str(part.partname).startswith("/word/") or not str(part.partname).endswith(".xml"):
            continue
        root = getattr(part, "_element", None)
        generic_part = root is None
        if generic_part:
            root = parse_xml(part.blob)
        # Скидаємо також стилі таблиць/гіперпосилань і властивості знака абзацу.
        for properties in tuple(root.iter(qn("w:rPr"))):
            properties.getparent().replace(properties, deepcopy(template))
        for run in root.iter(qn("w:r")):
            if run.find(qn("w:rPr")) is None:
                run.insert(0, deepcopy(template))
        for paragraph in root.iter(qn("w:p")):
            properties = paragraph.find(qn("w:pPr"))
            if properties is None:
                properties = OxmlElement("w:pPr")
                paragraph.insert(0, properties)
            if properties.find(qn("w:rPr")) is None:
                properties.insert_element_before(deepcopy(template), "w:sectPr", "w:pPrChange")
        for shading in tuple(root.iter(qn("w:shd"))):
            shading.getparent().remove(shading)
        if generic_part:
            part._blob = serialize_part_xml(root)
