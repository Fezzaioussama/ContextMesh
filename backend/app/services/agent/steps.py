"""The agent's steps. Each takes the run's AgentState and returns the next one with a
trace stage; `graph.py` decides which step runs next.

Every model call goes through `_call`, which enforces the deadline and token budget.
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import replace

from app.services.agent.prompts import (
    ANSWER,
    ANSWER_SCHEMA,
    ASSESS,
    ASSESS_SCHEMA,
    DIRECT,
    DIRECT_SCHEMA,
    PLAN,
    PLAN_SCHEMA,
    RERANK,
    RERANK_SCHEMA,
    ROUTE,
    ROUTE_CRITERIA,
    SUPPORT,
    SUPPORT_SCHEMA,
)
from app.services.agent.state import (
    TOP_GRADE,
    AgentPolicy,
    AgentState,
    Draft,
    DraftClaim,
    catalog_payload,
    conversation_payload,
    counted,
    flag,
    known_sources,
    labeled,
    merged_evidence,
    normalized_query,
    objects,
    passages_payload,
    regraded,
    request_payload,
    selected_context,
    string_list,
    text_field,
)
from app.services.models import (
    AgentOutcome,
    Answer,
    CatalogSource,
    ChoiceTask,
    Citation,
    Claim,
    DecisionModel,
    Evidence,
    Identity,
    Message,
    ReasoningModel,
    SearchQueries,
    StructuredReply,
    StructuredTask,
)
from app.services.retrieval import HybridRetriever
from app.utils.errors import ContextMeshError
from app.utils.logging import flow_event

ANSWER_STATUSES = frozenset({"answered", "partial", "insufficient_evidence"})
BUDGET_FAILURES = frozenset({"agent_deadline_exceeded", "agent_budget_exceeded"})
NO_EVIDENCE_GAP = "No indexed passage in the selected sources answers this question."
UNSPECIFIED_GAP = "The model judged the answer incomplete but did not name the missing detail."


class Agent:
    def __init__(
        self,
        model: ReasoningModel,
        retriever: HybridRetriever,
        decisions: DecisionModel | None = None,
        policy: AgentPolicy = AgentPolicy(),
        clock: Callable[[], float] = time.monotonic,
    ):
        self._model = model
        self._retriever = retriever
        self._decisions = decisions
        self._policy = policy
        self._clock = clock

    def start(
        self,
        identity: Identity,
        question: str,
        history: tuple[Message, ...],
        catalog: tuple[CatalogSource, ...],
    ) -> AgentState:
        deadline = self._clock() + self._policy.deadline_seconds
        return AgentState(identity, question, history, catalog, deadline)

    # Conditions the graph branches on

    def has_sources(self, state: AgentState) -> bool:
        return bool(state.searchable)

    def should_answer_direct(self, state: AgentState) -> bool:
        return state.route == "direct"

    def should_search(self, state: AgentState) -> bool:
        return not state.ready

    def has_claims(self, state: AgentState) -> bool:
        return state.draft is not None and bool(state.draft.claims)

    def should_repair(self, state: AgentState) -> bool:
        repairable = bool(state.unsupported) and state.repairs < self._policy.max_repairs
        return repairable and self._remaining(state) > self._policy.answer_reserve_seconds

    # Routing

    def route(self, state: AgentState) -> AgentState:
        """Jev chooses direct or retrieve; anything uncertain or unavailable retrieves."""
        decisions, policy = self._decisions, self._policy
        if decisions is None or not decisions.configured:
            summary = "The route classifier is unavailable. Defaulted to retrieval."
            return state.traced("route", summary, route="retrieve")
        task = ChoiceTask(
            "retrieval_route",
            {
                "request": state.request,
                "earlier_requests": request_payload(state.history, policy.route_history_requests),
                "sources": [
                    {"name": source.name, "description": source.description}
                    for source in state.searchable
                ],
            },
            ROUTE,
            ROUTE_CRITERIA,
            min(policy.call_timeout_seconds, self._require_time(state)),
        )
        try:
            reply = decisions.choose(task)
        except ContextMeshError:
            summary = "The route classifier failed safely. Defaulted to retrieval."
            return state.traced("route", summary, route="retrieve")
        choice = reply.choice.strip().casefold()
        p_direct = reply.probabilities.get("direct", 0.0)
        threshold = policy.direct_route_confidence
        confident = reply.confidence >= threshold and p_direct >= threshold
        routed = "direct" if choice == "direct" and confident else "retrieve"
        flow_event(
            "ask_question",
            "route_decided",
            route=routed,
            p_direct=f"{p_direct:.2f}",
            confidence=f"{reply.confidence:.2f}",
        )
        if routed == "direct":
            summary = "The classifier confidently selected a direct response."
        elif choice not in ROUTE_CRITERIA:
            summary = "The classifier returned an unknown route; defaulted to retrieval."
        else:
            summary = (
                "The classifier was not confident enough for a direct response; using retrieval."
            )
        summary = f"{summary} (p(direct)={p_direct:.2f})"
        return state.charged(reply.usage).traced("route", summary, route=routed)

    def answer_direct(self, state: AgentState) -> AgentState:
        content = {
            "question": state.request,
            "conversation": conversation_payload(state.history, self._policy.history_messages),
        }
        reply = self._call(state, "direct_answer", DIRECT, content, DIRECT_SCHEMA)
        answer = text_field(reply.data, "answer", 8_000)
        if not answer:
            raise ContextMeshError("model_output_invalid")
        summary = "Generated a citation-free direct response."
        return state.charged(reply.usage).traced("direct", summary, direct_response=answer)

    # Research

    def plan(self, state: AgentState) -> AgentState:
        """Rewrite the question into typed queries and pick sources; unknown sources are
        dropped. The standalone question is always the first query."""
        policy = self._policy
        content = {
            "question": state.request,
            "conversation": conversation_payload(state.history, policy.history_messages),
            "sources": catalog_payload(state.searchable),
        }
        reply = self._call(state, "search_plan", PLAN, content, PLAN_SCHEMA)
        question = text_field(reply.data, "standalone_question", 1000) or state.request
        chosen = known_sources(string_list(reply.data, "source_ids", 50, 64), state.searchable)
        sources = chosen or tuple(source.id for source in state.searchable)
        rewrites = string_list(reply.data, "questions", policy.max_questions, 300)
        queries = SearchQueries(
            _distinct((question, *rewrites))[: policy.max_questions],
            string_list(reply.data, "keywords", policy.max_keyword_queries, 300),
            text_field(reply.data, "hypothetical_answer", 1000),
        )
        summary = (
            f"Selected {len(sources)} of {counted(len(state.searchable), 'searchable source')}"
            f"; rewrote the question as {_described(queries)}."
        )
        return state.charged(reply.usage).traced(
            "plan", summary, question=question, selected=sources, queries=queries
        )

    def retrieve(self, state: AgentState) -> AgentState:
        """One retrieval round over the selected, already authorized sources. Its passages
        wait in `candidates` for grading."""
        self._require_time(state)
        round_number = state.rounds + 1
        result = self._retriever.search(state.identity, state.selected, state.queries, round_number)
        names = [state.source_name(source_id) for source_id in state.selected[:3]]
        if len(state.selected) > 3:
            names.append(f"{len(state.selected) - 3} more")
        summary = (
            f"Round {round_number}: searched {', '.join(names)} with "
            f"{_described(state.queries)}; "
            f"{counted(len(result.evidence), 'passage')} passed access checks."
        )
        if not result.vector_available:
            summary += " Vector search was unavailable, so only keyword search ran."
        return state.traced(
            "retrieve",
            summary,
            rounds=round_number,
            searched=state.searched | frozenset(state.selected),
            asked=state.asked | {normalized_query(query) for query in state.queries.texts},
            candidates=result.evidence,
            consulted=state.consulted | {item.passage.document_id for item in result.evidence},
        )

    def rerank(self, state: AgentState) -> AgentState:
        """Grade this round's passages, drop those judged irrelevant, and pool the strongest.
        A disabled, skipped, or failed grading keeps the retrieval order."""
        policy = self._policy
        candidates, graded = state.candidates, state.candidates
        reserve = policy.answer_reserve_seconds + policy.round_reserve_seconds
        if not candidates:
            summary = "No passages to grade."
        elif not policy.rerank:
            summary = "Passage grading is off; kept the retrieval order."
        elif self._remaining(state) <= reserve:
            summary = "Too little time left to grade passages; kept the retrieval order."
        else:
            content = {
                "question": state.question,
                "passages": passages_payload(candidates, policy.rerank_text_chars),
            }
            try:
                reply = self._call(state, "passage_rerank", RERANK, content, RERANK_SCHEMA)
                grades = _grades(objects(reply.data, "grades", len(candidates)))
            except ContextMeshError as error:
                if error.code in BUDGET_FAILURES:
                    raise
                summary = "Passage grading failed safely; kept the retrieval order."
            else:
                state = state.charged(reply.usage)
                graded = regraded(candidates, grades, policy.rerank_weight)
                dropped = len(candidates) - len(graded)
                summary = (
                    f"Graded {counted(len(candidates), 'passage')}: kept {len(graded)}, "
                    f"dropped {dropped} as irrelevant."
                )
        kept = graded[: policy.max_reranked]
        return state.traced(
            "rerank",
            summary,
            candidates=(),
            evidence=merged_evidence(state.evidence, kept, policy.max_evidence),
        )

    def assess(self, state: AgentState) -> AgentState:
        """Stop searching (`ready`), or set up one more round with new queries or sources."""
        policy = self._policy
        reserve = policy.answer_reserve_seconds + policy.round_reserve_seconds
        if state.rounds >= policy.max_rounds or self._remaining(state) <= reserve:
            summary = "Search budget reached; answering with the evidence found."
            return state.traced("assess", summary, ready=True)
        if not state.evidence:
            if not state.unsearched:
                summary = "No passages matched in any eligible source."
                return state.traced("assess", summary, ready=True)
            more = counted(len(state.unsearched), "more source")
            summary = f"No passages matched; expanding the search to {more}."
            return state.traced("expand", summary, selected=state.unsearched, ready=False)
        unsearched = tuple(source for source in state.searchable if source.id in state.unsearched)
        content = {
            "question": state.question,
            "passages": passages_payload(selected_context(state.evidence, policy)),
            "unsearched_sources": catalog_payload(unsearched),
        }
        reply = self._call(state, "evidence_assessment", ASSESS, content, ASSESS_SCHEMA)
        sufficient = flag(reply.data, "sufficient")
        gaps = string_list(reply.data, "gaps", policy.max_gaps, 300)
        questions = _unasked(
            string_list(reply.data, "questions", policy.max_questions, 300), state.asked
        )
        keywords = _unasked(
            string_list(reply.data, "keywords", policy.max_keyword_queries, 300), state.asked
        )
        chosen = known_sources(
            string_list(reply.data, "expand_source_ids", 50, 64), state.searchable
        )
        new_sources = tuple(source_id for source_id in chosen if source_id not in state.searched)
        state = state.charged(reply.usage)
        if sufficient:
            return state.traced("assess", "The passages cover the question.", ready=True, gaps=())
        if not questions and not keywords and not new_sources:
            summary = "Evidence is incomplete and no new search strategy remains."
            return state.traced("assess", summary, ready=True, gaps=gaps)
        if questions or keywords:  # New queries revisit searched sources too.
            wanted = state.searched | frozenset(new_sources)
            sources = tuple(source.id for source in state.searchable if source.id in wanted)
            queries = SearchQueries(questions, keywords)
        else:  # Repeated queries only run on new sources.
            sources = new_sources
            queries = state.queries
        missing = f"“{gaps[0][:120]}”" if gaps else "details"
        summary = (
            f"Missing {missing}; searching again with "
            f"{_described(queries)} across {counted(len(sources), 'source')}."
        )
        return state.traced(
            "expand", summary, selected=sources, queries=queries, gaps=gaps, ready=False
        )

    # Answering

    def generate(self, state: AgentState) -> AgentState:
        context = selected_context(state.evidence, self._policy)
        if not context:
            summary = "No passage passed access checks, so no answer was drafted."
            return state.traced("answer", summary, draft=None)
        return self._write(state, context, (), "answer")

    def verify(self, state: AgentState) -> AgentState:
        """Model-checked claim support; missing or malformed verdicts count as unsupported."""
        draft = _draft(state)
        labels = {item: label for label, item in labeled(draft.context).items()}
        claims = [
            {
                "index": index,
                "text": claim.text,
                "passages": [
                    {"id": labels[item], "text": item.passage.text} for item in claim.evidence
                ],
            }
            for index, claim in enumerate(draft.claims)
        ]
        reply = self._call(state, "claim_support", SUPPORT, {"claims": claims}, SUPPORT_SCHEMA)
        verdicts = objects(reply.data, "verdicts", 100)
        supported = {item.get("index") for item in verdicts if item.get("supported") is True}
        unsupported = tuple(index for index in range(len(draft.claims)) if index not in supported)
        summary = (
            f"Checked claim support: {len(draft.claims) - len(unsupported)} supported, "
            f"{len(unsupported)} unsupported."
        )
        return state.charged(reply.usage).traced("verify", summary, unsupported=unsupported)

    def repair(self, state: AgentState) -> AgentState:
        """Redraft once, telling the model which claims lacked support."""
        draft = _draft(state)
        rejected = tuple(draft.claims[index].text for index in state.unsupported)
        revised = self._write(state, draft.context, rejected, "repair")
        return replace(revised, repairs=state.repairs + 1, unsupported=())

    def release(self, state: AgentState) -> AgentOutcome:
        return released(state, self._policy)

    def _write(
        self,
        state: AgentState,
        context: tuple[Evidence, ...],
        rejected: tuple[str, ...],
        stage: str,
    ) -> AgentState:
        """Draft cited claims; a claim citing no supplied passage is dropped."""
        content: dict[str, object] = {
            "question": state.question,
            "passages": passages_payload(context),
        }
        if rejected:
            content["rejected_claims"] = list(rejected)
        reply = self._call(state, "grounded_answer", ANSWER, content, ANSWER_SCHEMA)
        labels = labeled(context)
        claims = []
        for item in objects(reply.data, "claims", self._policy.max_claims):
            text = text_field(item, "text", 1000)
            keys = [label.upper() for label in string_list(item, "evidence_ids", 8, 16)]
            cited = tuple(dict.fromkeys(labels[key] for key in keys if key in labels))
            if text and cited:
                claims.append(DraftClaim(text, cited))
        gaps = string_list(reply.data, "gaps", self._policy.max_gaps, 300)
        status = text_field(reply.data, "status", 40)
        status = status if status in ANSWER_STATUSES else "partial"
        draft = Draft(status, tuple(claims), gaps, context)
        summary = (
            f"Drafted {counted(len(draft.claims), 'cited claim')} from "
            f"{counted(len(context), 'passage')}."
        )
        return state.charged(reply.usage).traced(stage, summary, draft=draft)

    # Budget

    def _remaining(self, state: AgentState) -> float:
        return state.deadline - self._clock()

    def _require_time(self, state: AgentState) -> float:
        """Every model call and retrieval round starts only with usable time left."""
        remaining = self._remaining(state)
        if remaining < self._policy.minimum_call_seconds:
            raise ContextMeshError("agent_deadline_exceeded")
        return remaining

    def _call(
        self,
        state: AgentState,
        name: str,
        instructions: str,
        content: Mapping[str, object],
        schema: Mapping[str, object],
    ) -> StructuredReply:
        remaining = self._require_time(state)
        if state.usage.total >= self._policy.max_total_tokens:
            raise ContextMeshError("agent_budget_exceeded")
        timeout = min(self._policy.call_timeout_seconds, remaining)
        payload = json.dumps(content, ensure_ascii=False)
        return self._model.complete(StructuredTask(name, instructions, payload, schema, timeout))


def _draft(state: AgentState) -> Draft:
    if state.draft is None:
        raise RuntimeError("Verification requires a drafted answer.")
    return state.draft


def _distinct(queries: tuple[str, ...]) -> tuple[str, ...]:
    """First of each query that differs only by case or spacing."""
    first: dict[str, str] = {}
    for query in queries:
        first.setdefault(normalized_query(query), query)
    return tuple(first.values())


def _unasked(queries: tuple[str, ...], asked: frozenset[str]) -> tuple[str, ...]:
    return tuple(query for query in queries if normalized_query(query) not in asked)


def _described(queries: SearchQueries) -> str:
    """`2 questions, 1 keyword query and a hypothetical answer` for trace summaries."""
    parts = []
    if queries.questions:
        parts.append(counted(len(queries.questions), "question"))
    if queries.keywords:
        parts.append(counted(len(queries.keywords), "keyword query", "keyword queries"))
    if queries.hypothetical:
        parts.append("a hypothetical answer")
    if len(parts) < 2:
        return "".join(parts) or "no queries"
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _grades(items: tuple[Mapping[str, object], ...]) -> dict[str, int]:
    """Passage label to grade; items with an unknown grade are ignored."""
    grades: dict[str, int] = {}
    for item in items:
        label, grade = item.get("id"), item.get("relevance")
        valid = isinstance(grade, int) and not isinstance(grade, bool)
        if isinstance(label, str) and valid and 0 <= grade <= TOP_GRADE:
            grades.setdefault(label.strip().upper(), grade)
    return grades


# Release: the final answer from supported claims and canonical evidence records


def released(state: AgentState, policy: AgentPolicy) -> AgentOutcome:
    if state.route == "direct":
        answer = Answer("direct", state.direct_response, (), (), ())
        trace = state.traced("release", "Released a citation-free direct response.").trace
        return AgentOutcome(answer, trace, state.usage)
    draft = state.draft
    drafted = () if draft is None else draft.claims
    unsupported = set(state.unsupported)
    kept = tuple(claim for index, claim in enumerate(drafted) if index not in unsupported)
    removed = len(drafted) - len(kept)
    drafted_gaps = () if draft is None else draft.gaps
    gaps = tuple(dict.fromkeys((*drafted_gaps, *state.gaps)))[: policy.max_gaps]
    if not gaps and draft is not None and draft.status == "partial":
        gaps = (UNSPECIFIED_GAP,)  # A partial verdict is never shown unexplained.
    if kept:
        complete = draft.status == "answered" and not gaps and removed == 0
        answer = _cited_answer(kept, gaps, "answered" if complete else "partial")
        summary = (
            f"Released {counted(len(answer.claims), 'supported claim')} with "
            f"{counted(len(answer.citations), 'citation')}."
        )
        if removed:
            summary += f" Removed {counted(removed, 'unsupported claim')}."
    else:
        text = "I could not find enough evidence in the selected sources to answer this question."
        answer = Answer("insufficient_evidence", text, (), (), gaps or (NO_EVIDENCE_GAP,))
        summary = "No supported answer was found; reported the evidence gap."
    trace = state.traced("release", summary).trace
    # Every document retrieved in any round counts, including passages later pushed out.
    consulted = tuple(sorted(state.consulted, key=str))
    return AgentOutcome(answer, trace, state.usage, consulted)


def _cited_answer(kept: tuple[DraftClaim, ...], gaps: tuple[str, ...], status: str) -> Answer:
    """Number each cited passage once, in order of first use."""
    ordered = dict.fromkeys(item for claim in kept for item in claim.evidence)
    citations = tuple(
        Citation(
            f"citation-{number}",
            number,
            item.passage.source_id,
            item.passage.document_id,
            item.passage.document_version_id,
            item.passage.index_generation_id,
            item.passage.chunk_id,
            item.passage.title,
            item.passage.locator,
            _snippet(item.passage.text),
            item.passage.uri,
        )
        for number, item in enumerate(ordered, start=1)
    )
    by_chunk = {citation.chunk_id: citation for citation in citations}
    claims = []
    texts = []
    for index, claim in enumerate(kept, start=1):
        cited = tuple(dict.fromkeys(by_chunk[item.passage.chunk_id] for item in claim.evidence))
        claims.append(Claim(f"claim-{index}", claim.text, tuple(c.id for c in cited)))
        texts.append(f"{claim.text} {''.join(f'[{c.number}]' for c in cited)}")
    return Answer(status, " ".join(texts), tuple(claims), citations, gaps)


def _snippet(text: str, limit: int = 280) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rsplit(' ', 1)[0]}…"
