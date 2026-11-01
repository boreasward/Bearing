from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from investment_assistant.data.sec.extraction import LABELS, analyze_filing


def build_filing_report(meta: dict, raw_text: str, *, analysis: dict | None = None) -> str:
    analysis = analysis or meta.get("analysis") or analyze_filing(meta, raw_text)
    title = f"{meta['ticker']} {meta['form']} {meta['filing_date']} 的正文事实与来源"
    lines = ["---", f"title: {json.dumps(title, ensure_ascii=False)}", "tags: [投资, SEC, 财报核验]",
             f"created: {datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()}", "---", "", f"# {title}", "",
             "本报告提取申报主文和随附文件中的数字、期间及原文表述。计算值单独列出公式；缺少可核验数据时保留为待核验。",
             "", "## 申报", "", f"- accession：{meta['accession_number']}",
             f"- 申报日期：{meta['filing_date']}", f"- 类型：{meta['form']}",
             f"- 内容：{'财务结果' if analysis['kind'] == 'financial_results' else '业务事件公告；含 EX-99.1 不代表披露财务结果'}",
             "", "## 财务事实", "", "| 项目 | 数值 | 期间 | 口径及依据 |", "| --- | --- | --- | --- |"]
    sources: list[dict] = []

    def cite(source: dict) -> str:
        if source not in sources:
            sources.append(source)
        return f"[S{sources.index(source) + 1}](#s{sources.index(source) + 1})"

    for key in ("revenue", "gross_profit", "operating_income", "gross_margin", "operating_margin", "eps_diluted", "eps_non_gaap"):
        fact = analysis["facts"].get(key)
        if fact:
            method = "计算值" if fact["method"] == "calculated" else "原文数值"
            refs = "、".join(cite(s) for s in fact["sources"])
            lines.append(f"| {LABELS[key]} | {fact['display']} | {fact['period']} | {fact['basis']}；{method}；{refs} |")
        else:
            lines.append(f"| {LABELS[key]} | 未自动提取，待核验 | — | 未找到受支持的明确条目 |")
    lines += ["", "“未自动提取”不等同于公司“未披露”；扫描范围和可解析格式会影响结果。季度与年初至今、GAAP 与 non-GAAP 分别处理。", ""]
    for key in ("gross_margin", "operating_margin"):
        fact = analysis["facts"].get(key, {})
        if fact.get("formula"):
            lines.append(f"- {LABELS[key]}计算：{fact['formula']} = {fact['display']}。")
    for key, label in (("guidance", "指引原文"), ("developments", "业务进展原文"), ("risks", "风险原文")):
        lines += ["", f"## {label}", ""]
        items = analysis[key]
        if not items:
            lines.append("未自动检出可核验条目，待人工核查全文。")
        for source in items:
            lines += [f"{cite(source)}：", "", *["> " + line for line in source["quote"].splitlines()], ""]
    lines += ["", "## 推断与限制", "", "未生成投资判断。原文中的指引和预计进度是公司的表述，尚未发生的结果不作为已实现事实。"]
    lines.extend("- " + warning for warning in dict.fromkeys(analysis["warnings"]))
    lines += ["", "## 来源", ""]
    for document in analysis["sources"]:
        lines += [f"- 原文：{document['path']}", f"  - SEC：{document['url'] or '未提供 URL；以本地原文为准'}",
                  f"  - SHA-256：{document['sha256']}"]
    for i, source in enumerate(sources, 1):
        lines += ["", f"### S{i}", "", f"- 文件：{source['path']}", f"- 定位：`{source['locator']}`"]
        if source.get("url"):
            url = source["url"] + ("#" + source["locator"] if not source["locator"].startswith("/") else "")
            lines.append(f"- [查看 SEC 原文]({url})")
        if len(source["quote"]) < 200:
            lines.append(f"- 原文值：{source['quote']}")
    return "\n".join(lines) + "\n"


def report_path(report_dir: Path, meta: dict) -> Path:
    name = f"{meta['form']}_{meta['filing_date']}_{meta['accession_number']}-整理.md"
    return Path(report_dir) / meta["ticker"] / name


def write_filing_report(report_dir: Path, meta: dict, raw_text: str) -> Path | None:
    dest = report_path(report_dir, meta)
    if dest.exists():
        return None
    text = build_filing_report(meta, raw_text)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a complete, flushed file; link refuses to overwrite an existing report.
    fd, temp_name = tempfile.mkstemp(prefix=".report-", dir=dest.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_name, dest)
        directory_fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        return None
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return dest


def filing_notification(meta: dict, report_paths: list[str]) -> str:
    analysis = meta.get("analysis", {})
    facts = analysis.get("facts", {})
    financial = analysis.get("kind") == "financial_results"
    lines = [f"【SEC{'财报' if financial else '公告'}】{meta['ticker']} {meta['form']} {meta['filing_date']}"]
    if facts:
        lines.append("期间：" + next(iter(facts.values()))["period"])
        for key in ("revenue", "gross_margin", "operating_margin", "eps_diluted", "eps_non_gaap"):
            if key in facts:
                f = facts[key]
                lines.append(f"{LABELS[key]}：{f['display']}" + ("（计算值）" if f['method'] == 'calculated' else ""))
    else:
        lines.append("本财报的财务指标未自动提取，需核查原文。" if financial
                     else "本公告未自动提取财务指标；不按财报结果解读。")
    for key, label in (("guidance", "公司指引原文"), ("developments", "业务进展原文")):
        items = analysis.get(key, [])
        if items:
            excerpt = items[0]["quote"]
            lines.append(f"{label}：{excerpt[:350]}" + ("…" if len(excerpt) > 350 else ""))
    lines.append("原文：" + meta["edgar_url"])
    if meta.get("document_urls") and meta.get("attachments"):
        url = meta["document_urls"].get(meta["attachments"][0])
        if url:
            lines.append("附件：" + url)
    if report_paths:
        lines.append("完整报告：" + report_paths[0])
    lines.append("数字及摘录带来源；待核验项见完整报告。")
    return "\n".join(lines)[:1900]
