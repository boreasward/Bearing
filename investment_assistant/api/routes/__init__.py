from . import status, watchlist, runs, jobs, settings, filings, us_research, workbench, data, knowledge, accounts

ALL_ROUTERS = [
    status.router, watchlist.router, runs.router, jobs.router,
    settings.router, filings.router, us_research.router, workbench.router, data.router,
    knowledge.router, accounts.router,
]
