"""Source-grounded financial extraction; never infer a missing financial value."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lxml import html

VERSION = 1
LABELS = {
    "revenue": "营收", "gross_profit": "毛利", "operating_income": "营业利润",
    "gross_margin": "毛利率", "operating_margin": "营业利润率",
    "eps_diluted": "摊薄 EPS（GAAP）", "eps_non_gaap": "摊薄 EPS（non-GAAP）",
}
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"], "operating_income": ["OperatingIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}


def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def _text(node) -> str:
    return " ".join(node.text_content().split())


def _date(text: str) -> str | None:
    text = " ".join(text.split())
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _number(text: str, *, transform: str = "", scale: str = "0", sign: str = "") -> Decimal:
    transform = transform.rsplit(":", 1)[-1].lower()
    if transform not in {"", "num-dot-decimal", "numdotdecimal", "num-comma-decimal", "numcommadecimal",
                         "zerodash", "fixed-zero", "num-dash"}:
        raise ValueError("unsupported numeric transform")
    text = text.strip().replace("\xa0", "").replace(" ", "").replace("$", "").replace("%", "")
    if text in {"—", "–", "-"}:
        text = "0"
    negative = sign == "-" or text.startswith("(")
    text = text.strip("()")
    if "comma" in transform:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text) or not -15 <= int(scale) <= 15:
        raise ValueError("unrecognized numeric value")
    value = Decimal(text) * (Decimal(10) ** int(scale))
    return -abs(value) if negative else value


def _source(doc: dict, node, *, quote: str | None = None) -> dict:
    locator = node.get("id") or node.getroottree().getpath(node)
    return {"path": doc["path"], "url": doc.get("url", ""), "sha256": doc["sha256"],
            "locator": locator, "quote": quote if quote is not None else _text(node)}


def _display(value: Decimal, unit: str) -> str:
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "USD/shares":
        return f"{value:,.2f} 美元/股"
    if unit == "USD":
        return f"{value / Decimal(1000000):,.2f} 百万美元"
    return f"{value:,} {unit}"


def _fact(value: Decimal, unit: str, period: str, source: dict, *, basis="GAAP", method="disclosed") -> dict:
    return {"value": str(value), "unit": unit, "period": period, "basis": basis,
            "display": _display(value, unit), "method": method, "sources": [source]}


def _inline_facts(root, doc: dict, meta: dict, warnings: list[str]) -> dict:
    nodes = [n for n in root.iter() if isinstance(n.tag, str)]
    contexts, units = {}, {}
    for n in nodes:
        if _local(n.tag) == "context":
            parts = {_local(x.tag): _text(x) for x in n.iter()}
            if any(_local(x.tag) in {"segment", "scenario"} for x in n.iter()):
                continue  # Do not confuse business segments with consolidated results.
            start, end = _date(parts.get("startdate", "")), _date(parts.get("enddate", ""))
            if start and end:
                contexts[n.get("id")] = (start, end)
        elif _local(n.tag) == "unit":
            measures = [_text(x).rsplit(":", 1)[-1] for x in n.iter() if _local(x.tag) == "measure"]
            units[n.get("id")] = "/".join(measures)
    ends = []
    for n in nodes:
        if n.get("name", "").lower() == "dei:documentperiodenddate":
            end = _date(_text(n))
            if end:
                ends.append(end)
    if len(set(ends)) != 1:
        if any(_local(n.tag) == "nonfraction" for n in nodes):
            warnings.append("无法唯一确定财报封面期间，未自动选择历史/当期数字。")
        return {}
    target_end = ends[0]
    target_contexts = {}
    for key, (start, end) in contexts.items():
        duration = (date.fromisoformat(end) - date.fromisoformat(start)).days
        valid = 300 <= duration <= 400 if meta["form"] == "10-K" else 60 <= duration <= 120
        if end == target_end and valid:
            target_contexts[key] = (start, end)
    results = {}
    for metric, concepts in CONCEPTS.items():
        for concept in concepts:
            matches = []
            for n in nodes:
                if (_local(n.tag) != "nonfraction" or n.get("name", "").lower() != "us-gaap:" + concept.lower()
                        or n.get("contextref") not in target_contexts
                        or any(_local(k) == "nil" and v in {"true", "1"} for k, v in n.attrib.items())):
                    continue
                unit = units.get(n.get("unitref"), "")
                if unit != ("USD/shares" if metric == "eps_diluted" else "USD"):
                    continue
                clone = deepcopy(n)
                for child in list(clone.iter()):
                    if child is not clone and _local(child.tag) == "exclude":
                        child.drop_tree()
                try:
                    value = _number(_text(clone), transform=n.get("format", ""), scale=n.get("scale", "0"),
                                    sign=n.get("sign", ""))
                except (ValueError, InvalidOperation):
                    warnings.append(f"未识别 {concept} 的数字格式，需人工核验。")
                    continue
                start, end = target_contexts[n.get("contextref")]
                fact = _fact(value, unit, start + " 至 " + end, _source(doc, n))
                fact.update(concept=n.get("name"), context=n.get("contextref"), scale=n.get("scale", "0"))
                matches.append(fact)
            if matches:
                if len({(f["value"], f["unit"], f["period"]) for f in matches}) == 1:
                    results[metric] = matches[0]
                else:
                    warnings.append(f"{LABELS[metric]}存在相同期间的冲突值，未自动选取。")
                break
    for metric, numerator in [("gross_margin", "gross_profit"), ("operating_margin", "operating_income")]:
        a, b = results.get(numerator), results.get("revenue")
        if a and b and a["period"] == b["period"] and a["unit"] == b["unit"] and Decimal(b["value"]) > 0:
            value = Decimal(a["value"]) / Decimal(b["value"]) * 100
            fact = _fact(value, "%", a["period"], a["sources"][0], method="calculated")
            fact["sources"] += b["sources"]
            fact["formula"] = f"{LABELS[numerator]} {a['display']} ÷ 营收 {b['display']} × 100"
            results[metric] = fact
    return results


def _table_grid(table) -> list[list[str]]:
    """Expand colspan/rowspan before associating a number with its column heading."""
    rows, pending = [], {}
    for row_index, row in enumerate(table.xpath('./tr|./thead/tr|./tbody/tr|./tfoot/tr')):
        cells = {col: text for (r, col), text in pending.items() if r == row_index}
        col = 0
        for cell in row.xpath('./td|./th'):
            while col in cells:
                col += 1
            width, height = int(cell.get("colspan", "1")), int(cell.get("rowspan", "1"))
            if not 1 <= width <= 100 or not 1 <= height <= 100:
                raise ValueError("表格跨度过大")
            for delta in range(width):
                cells[col + delta] = _text(cell)
                for future in range(1, height):
                    pending[(row_index + future, col + delta)] = _text(cell)
            col += width
        rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
    return rows


def _joined(values) -> str:
    # Colspan expands one cell into several identical slots; collapse those copies.
    parts = []
    for value in values:
        if value and (not parts or value != parts[-1]):
            parts.append(value)
    return " ".join(parts)


def _table_facts(root, doc: dict) -> dict:
    for table in root.xpath('//table'):
        text = _text(table)
        if not re.search(r"Quarterly Financial Results", text, re.I) or "in millions" not in text.lower():
            continue
        grid = _table_grid(table)
        for row_index, header in enumerate(grid):
            groups = []
            col = 0
            while col < len(header):
                period = header[col]
                end = col + 1
                while end < len(header) and header[end] == period:
                    end += 1
                match = re.fullmatch(r"F?Q([1-4])[- ](\d{2}|\d{4})", period, re.I)
                if match:
                    year = int(match[2]); year += 2000 if year < 100 else 0
                    basis = None
                    for previous in reversed(grid[:row_index]):
                        cell = previous[col] if col < len(previous) else ""
                        if re.fullmatch(r"Non-GAAP(?:\(\d+\))?", cell, re.I):
                            basis = "non-GAAP"; break
                        if re.fullmatch(r"GAAP(?:\(\d+\))?", cell, re.I):
                            basis = "GAAP"; break
                    if basis:
                        groups.append((year, int(match[1]), col, end, period, basis))
                col = end
            if not groups:
                continue
            latest = max((g[0], g[1]) for g in groups)
            facts = {}
            for _, _, start, end, period, basis in [g for g in groups if g[:2] == latest]:
                previous_metric = ""
                for row in grid[row_index + 1:]:
                    label = row[0].lower() if row else ""
                    metric = {"revenue": "revenue", "gross margin": "gross_profit",
                              "operating income": "operating_income", "diluted earnings per share": "eps_diluted"}.get(label)
                    if label == "percent of revenue":
                        metric = {"gross_profit": "gross_margin", "operating_income": "operating_margin"}.get(previous_metric)
                    elif metric:
                        previous_metric = metric
                    else:
                        previous_metric = ""
                    if not metric or (basis != "GAAP" and metric != "eps_diluted"):
                        continue
                    value_text = _joined(row[start:end])
                    unit = "%" if metric.endswith("margin") else "USD/shares" if metric == "eps_diluted" else "USD"
                    try:
                        value = _number(value_text, scale="6" if unit == "USD" else "0")
                    except (ValueError, InvalidOperation):
                        continue
                    key = "eps_non_gaap" if basis == "non-GAAP" else metric
                    quote = f"{period} | {basis} | {row[0]} | {value_text} (in millions, except per share amounts)"
                    facts[key] = _fact(value, unit, period, _source(doc, table, quote=quote), basis=basis)
            if facts:
                return facts
    return {}


def _excerpts(root, doc: dict) -> dict:
    result = {"guidance": [], "developments": [], "risks": []}
    for table in root.xpath('//table'):
        text = _text(table)
        if len(text) < 6000 and re.search(r"outlook|guidance", text, re.I) and re.search(r"revenue|earnings per share", text, re.I):
            rows = [" | ".join(_text(c) for c in r.xpath('./td|./th') if _text(c)) for r in table.xpath('.//tr')]
            quote = "\n".join(x for x in rows if x)
            if quote:
                result["guidance"].append(_source(doc, table, quote=quote))
                break
    for node in root.xpath('//p|//div|//li'):
        if node.xpath('.//p|.//div|.//li|.//table') or node.xpath('ancestor::table'):
            continue
        if any("display:none" in n.get("style", "").replace(" ", "").lower() for n in [node, *node.iterancestors()]):
            continue
        text = _text(node)
        if not 60 <= len(text) <= 1800 or re.search(r"forward-looking|safe harbor|actual results.{0,50}differ", text, re.I):
            continue
        if (len(result["guidance"]) < 3 and re.search(r"\b(?:we|the company)\s+(?:currently\s+)?(?:expect|anticipate|forecast|project)\b", text, re.I)
                and re.search(r"revenue|gross margin|earnings per share|capital expenditures", text, re.I)
                and re.search(r"\$\s*\d|\d[\d,.]*\s*(?:billion|million|%|percent)", text, re.I)
                and not re.search(r"may be unable|adversely|risk factor|laws|regulations", text, re.I)):
            result["guidance"].append(_source(doc, node))
        if (len(result["developments"]) < 6
                and re.search(r"\bHBM\b|\bDRAM\b|\bNAND\b|SerDes|Neutron|launch|NASA|vehicles|deliveries", text, re.I)
                and re.search(r"achieved|began|launched|expect|ramp|increased|announced|anticipat|shift|produc|deliver", text, re.I)):
            result["developments"].append(_source(doc, node))
        if len(result["risks"]) < 3 and re.search(r"(?:could|may|might).{0,120}(?:adversely|material|delay|loss)", text, re.I):
            result["risks"].append(_source(doc, node))
    return result


def analyze_filing(meta: dict, raw_text: str, *, attachments: list[dict] | None = None) -> dict:
    documents = [{"path": meta["path"], "url": meta.get("edgar_url", ""), "text": raw_text}, *(attachments or [])]
    analysis = {"version": VERSION, "facts": {}, "guidance": [], "developments": [], "risks": [],
                "warnings": [], "sources": []}
    for i, doc in enumerate(documents):
        content = doc["text"]
        if not content.strip():
            raise ValueError(f"申报正文为空，不能生成分析报告: {doc['path']}")
        doc = {**doc, "sha256": doc.get("sha256") or hashlib.sha256(content.encode()).hexdigest()}
        root = html.fromstring(content.encode("utf-8"), parser=html.HTMLParser(encoding="utf-8"))
        for node in root.xpath('//script|//style'):
            node.drop_tree()
        analysis["sources"].append({k: doc[k] for k in ("path", "url", "sha256")})
        if i == 0:
            analysis["facts"].update(_inline_facts(root, doc, meta, analysis["warnings"]))
        if meta["form"] == "8-K":
            for key, fact in _table_facts(root, doc).items():
                analysis["facts"].setdefault(key, fact)
        excerpts = _excerpts(root, doc)
        for key in ("guidance", "developments", "risks"):
            for item in excerpts[key]:
                if not any(existing["quote"] == item["quote"] for existing in analysis[key]):
                    analysis[key].append(item)
    analysis["developments"].sort(key=lambda s: (
        bool(re.search(r"\d", s["quote"])),
        bool(re.search(r"expect|anticipat|shift|increased|achieved", s["quote"], re.I)),
        s["path"] != meta["path"],
    ), reverse=True)
    for key in ("guidance", "developments", "risks"):
        analysis[key] = analysis[key][:3]
    analysis["kind"] = "financial_results" if analysis["facts"] or meta["form"] in {"10-K", "10-Q"} else "business_update"
    analysis["coverage"] = "partial" if not all(k in analysis["facts"] for k in ("revenue", "gross_margin", "eps_diluted")) else "financial_metrics_extracted"
    return analysis


def analyze_files(meta: dict, raw_text: str | None = None) -> dict:
    primary = Path(meta["path"])
    content = primary.read_bytes()
    attachments = []
    for path in meta.get("attachments", []):
        data = Path(path).read_bytes()  # Missing exhibits are errors, never an empty report.
        url = meta.get("document_urls", {}).get(path, "")
        attachments.append({"path": str(path), "url": url, "text": data.decode("utf-8", errors="replace"),
                            "sha256": hashlib.sha256(data).hexdigest()})
    analysis = analyze_filing(meta, raw_text if raw_text is not None else content.decode("utf-8", errors="replace"),
                              attachments=attachments)
    # Hash the original bytes, not a possibly re-encoded text representation.
    primary_hash = hashlib.sha256(content).hexdigest()
    analysis["sources"][0]["sha256"] = primary_hash
    for fact in analysis["facts"].values():
        for source in fact["sources"]:
            if source["path"] == str(primary):
                source["sha256"] = primary_hash
    for key in ("guidance", "developments", "risks"):
        for source in analysis[key]:
            if source["path"] == str(primary):
                source["sha256"] = primary_hash
    return analysis
