"""Research prompts are open questions, never machine-invented operating facts."""
from investment_assistant.data.fundamentals import stock_id

QUESTIONS = {
    'MU': ['DRAM/NAND价格、bit出货和产品组合分别如何影响增长', 'HBM收入或产能披露能否与实际交付对应', '库存天数、资本开支和指引是否支持未来现金回报'],
    'SNDK': ['NAND价格与bit出货如何影响毛利', '数据中心收入及产品导入是否兑现', '拆分前后报告主体、库存及资本支出口径是否可比'],
    'CRDO': ['AEC及其他互连产品是否有客户采用和量产证据', '客户集中度及收入指引如何变化', '股权激励和股本稀释是否影响每股经营收益'],
    'NVDA': ['数据中心计算和网络收入分别如何兑现', '平台爬坡和产品组合如何影响毛利率', '库存、采购承诺及出口限制披露对供需有何影响'],
    'MSFT': ['Azure增长及云收入能否转为营业利润', '剩余履约义务中未来12个月可确认收入的部分有多少', '资本开支、租赁和折旧如何影响现金回报'],
    'GOOG': ['Google Cloud收入、营业利润与订单积压是否相互印证', '资本开支及折旧对整体利润和现金流有何影响', '广告主业增长及利润是否支持云投入'],
    'TSLA': ['汽车交付与库存变化是否转为收入和毛利', '剔除监管积分影响后汽车毛利如何变化', '储能部署是否转为利润且资本开支是否可持续'],
    'RKLB': ['发射及空间系统的订单积压和新增订单何时履约', '研发里程碑能否转为客户交付和收入', '现金消耗、融资及股本变化是否支持现有计划'],
}
GENERIC = ['收入增长是否持续且有业务证据支持', '利润、经营现金流和资本开支是否相互印证', '客户集中、库存、债务及股本变化有哪些待核实风险']


def research_template(ticker: str) -> list[dict]:
    ticker = stock_id(ticker).split(':')[-1]
    return [{'id': f'{ticker}:{i+1}', 'question': text+'？', 'value': None,
             'status': 'needs_evidence', 'sources': [], 'missing_reason': 'Requires dated company disclosure and manual verification'}
            for i, text in enumerate(QUESTIONS.get(ticker, GENERIC))]
