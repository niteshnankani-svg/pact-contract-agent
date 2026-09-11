from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

TOPICS = Literal["notice_period", "renewal_date", "auto_renewal", "liability", "assignment", "confidentiality"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MatterCreate(Strict):
    title: str = Field(min_length=3, max_length=160)
    owner: str = Field(min_length=1, max_length=100)


class DocumentCreate(Strict):
    title: str = Field(min_length=1, max_length=180)
    text: str = Field(min_length=30, max_length=120_000)
    kind: Literal["agreement", "amendment", "order_form"] = "agreement"
    executed: bool = False
    supersedes: str | None = None
    strategy: Literal["clause", "fixed", "recursive", "semantic"] = "clause"


class Playbook(Strict):
    max_notice_days: int = Field(default=60, ge=1, le=365)
    require_assignment_consent: bool = True
    max_liability_months: int = Field(default=12, ge=1, le=120)
    preferred_notice_clause: str = Field(default="Either party may prevent renewal by giving at least {max_notice_days} days' written notice before the renewal date.", max_length=1500)


class Plan(Strict):
    topics: list[TOPICS] = Field(min_length=1, max_length=6)
    rationale: str = Field(max_length=500)


class Extraction(Strict):
    items: list["Extracted"] = Field(max_length=12)


class Extracted(Strict):
    kind: TOPICS
    clause_id: str = Field(max_length=80)
    quote: str = Field(min_length=3, max_length=2200)
    value: str = Field(max_length=300)


def extraction_schema(topics):
    """Constrain generation to the topics actually requested by this graph node."""
    item = create_model('TopicEvidence', __base__=Extracted, kind=(Literal[tuple(topics)], ...))
    return create_model('TopicExtraction', __base__=Strict, items=(list[item], Field(max_length=12)))


class Followup(Strict):
    queries: list[str] = Field(max_length=2)
    rationale: str = Field(max_length=500)


class Decision(Strict):
    decision: Literal["approve", "reject", "revise"]
    plan_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=3, max_length=1200)
    confirmed_finding_ids: list[str] = Field(default_factory=list, max_length=30)


class TaskSync(Strict):
    confirm_external_write: bool = False


class TaskComplete(Strict):
    plan_hash: str = Field(min_length=64, max_length=64)
