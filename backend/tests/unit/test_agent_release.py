"""Release carries the passages a grounded answer was drafted from, for evaluation."""

from uuid import uuid4

from app.services.agent.state import AgentPolicy, AgentState, Draft, DraftClaim
from app.services.agent.steps import NO_EVIDENCE_GAP, released
from app.services.models import Identity
from support import evidence

PLANS = evidence("tidepool-plans.md", "Harbor costs $14 per user per month.")
SUPPORT = evidence("tidepool-support.md", "P1 target: 30 minutes.")


def state(**changes):
    return AgentState(Identity("release-test", uuid4()), "Question?", (), (), 0.0, **changes)


def test_a_grounded_answer_keeps_its_whole_drafted_context():
    claims = (DraftClaim("Harbor costs $14.", (PLANS,)), DraftClaim("P1 in 30 min.", (SUPPORT,)))
    draft = Draft("answered", claims, (), (PLANS, SUPPORT))
    outcome = released(state(draft=draft, unsupported=(1,)), AgentPolicy())
    assert (outcome.answer.status, len(outcome.answer.claims)) == ("partial", 1)
    assert outcome.context == (PLANS, SUPPORT)


def test_a_draft_without_claims_still_reports_what_it_read():
    draft = Draft("insufficient_evidence", (), ("No price found.",), (PLANS,))
    outcome = released(state(draft=draft), AgentPolicy())
    assert (outcome.answer.status, outcome.answer.gaps, outcome.context) == (
        "insufficient_evidence",
        ("No price found.",),
        (PLANS,),
    )


def test_no_draft_and_direct_responses_have_no_context():
    undrafted = released(state(), AgentPolicy())
    direct = released(state(route="direct", direct_response="Hello!"), AgentPolicy())
    assert (undrafted.answer.status, undrafted.answer.gaps, undrafted.context) == (
        "insufficient_evidence",
        (NO_EVIDENCE_GAP,),
        (),
    )
    assert (direct.answer.status, direct.context) == ("direct", ())
