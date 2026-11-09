"""Phase 2 analysis layer. ``run_all(model)`` answers the six questions.

Each module can also be used on its own (see tests/test_analysis.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..pipeline import Model
from .churn_analysis import ChurnResult, analyze_churn
from .client_analysis import ClientResult, analyze_clients
from .communication_analysis import CommResult, analyze_requests
from .context import Context, make_context
from .financial_analysis import FinanceResult, analyze_finances
from .marketing_analysis import MarketingResult, analyze_marketing
from .owner_priorities import PriorityResult, owner_priorities


@dataclass
class Analysis:
    ctx: Context
    clients: ClientResult
    finance: FinanceResult
    comms: CommResult
    churn: ChurnResult
    marketing: MarketingResult
    priorities: PriorityResult

    @property
    def ledger(self):
        return self.ctx.ledger.frame()


def run_all(model: Model) -> Analysis:
    ctx = make_context(model)
    cl = analyze_clients(ctx)
    names = dict(zip(cl.clients.client_id, cl.clients.canonical_name))
    names.update(dict(zip(cl.payers.client_id, cl.payers["name"])))
    fin = analyze_finances(ctx, names, cl.pays_for)
    com = analyze_requests(ctx, names)
    ch = analyze_churn(ctx, cl.clients, com, fin)
    mk = analyze_marketing(ctx, cl.clients, cl.payers, cl.record_links, fin)
    pr = owner_priorities(ctx, cl.clients, fin, com, ch, mk)
    return Analysis(ctx=ctx, clients=cl, finance=fin, comms=com, churn=ch, marketing=mk, priorities=pr)
