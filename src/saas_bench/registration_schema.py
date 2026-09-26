"""Shared declaration input shapes. Predicates are stored, never evaluated here."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


Text = Annotated[str, Field(min_length=1)]
Number = Annotated[float, Field(allow_inf_nan=False)]


class BusinessObject(Input):
    kind: Text
    id: Text


class ContentTime(Input):
    day: Annotated[int, Field(ge=0)] | None = None
    start_day: Annotated[int, Field(ge=0)] | None = None
    end_day: Annotated[int, Field(ge=0)] | None = None
    unknown: Text | None = None

    @model_validator(mode='after')
    def shape(self):
        if self.unknown is not None:
            valid = self.day is self.start_day is self.end_day is None
        elif self.day is not None:
            valid = self.start_day is self.end_day is None
        else:
            valid = self.start_day is not None and self.end_day is not None and self.start_day <= self.end_day
        if not valid:
            raise ValueError('Use day, start_day/end_day, or unknown with a reason')
        return self


class Selector(Input):
    row: dict[str, str | int | float | bool | None] | None = None
    col: Text | None = None
    path: str | None = None

    @model_validator(mode='after')
    def shape(self):
        if self.path is not None:
            if self.row is not None or self.col is not None or (self.path and not self.path.startswith('/')):
                raise ValueError('JSON path must be a JSON Pointer; do not mix path and row/col')
        elif self.col is None:
            raise ValueError('Table selection requires col; row uses equality keys, never a row number')
        elif self.row == {}:
            raise ValueError('row must contain at least one equality key')
        return self


class Tolerance(Input):
    type: Literal['tolerance']
    amount: Annotated[Number, Field(ge=0)]


class Threshold(Input):
    type: Literal['threshold']
    op: Literal['>', '>=', '<', '<=']
    value: Number


class Compare(Input):
    type: Literal['compare']
    left: Selector
    op: Literal['>', '>=', '<', '<=']
    right: Selector


class Evidence(Input):
    path: Text | None = None
    commit: Text | None = None
    sql: Text | None = None
    record: Annotated[str, Field(pattern=r'^r[1-9][0-9]*(\.[1-9][0-9]*)?$')] | None = None
    version: Annotated[str, Field(pattern=r'^v[1-9][0-9]*$')] | None = None
    unknown: Text | None = None

    @model_validator(mode='after')
    def shape(self):
        if sum(x is not None for x in (self.path, self.sql, self.record, self.version, self.unknown)) != 1:
            raise ValueError('Specify exactly one of path, sql, record, version, unknown')
        if self.commit is not None and self.path is None:
            raise ValueError('commit requires a path')
        return self


class Reference(Input):
    evidence: Evidence
    purpose: Literal['current', 'historical_only']
    select: Selector | None = None
    predicate: Annotated[Tolerance | Threshold | Compare, Field(discriminator='type')] | None = None
    note: str | None = None

    @model_validator(mode='after')
    def shape(self):
        if isinstance(self.predicate, Compare):
            if self.select is not None:
                raise ValueError('compare uses left/right selectors, not select')
            if self.predicate.left.path is not None or self.predicate.right.path is not None:
                raise ValueError('compare requires two cells of one query result')
            if self.evidence.path or self.evidence.record:
                raise ValueError('compare requires a query view')
        elif self.predicate is not None and self.select is None:
            raise ValueError('A numeric predicate requires a selector')
        if self.evidence.record and (self.select or self.predicate):
            raise ValueError('Registered text supports whole-text equality only')
        return self


class Create(Input):
    text: Text
    objects: Annotated[list[BusinessObject], Field(min_length=1)]
    references: list[Reference]
    applies_at: ContentTime
    reason: Text


class Revise(Input):
    record: Annotated[str, Field(pattern=r'^r[1-9][0-9]*$')]
    reason: Text
    text: Text | None = None
    objects: Annotated[list[BusinessObject], Field(min_length=1)] | None = None
    references: list[Reference] | None = None
    applies_at: ContentTime | None = None


class Retire(Input):
    record: Annotated[str, Field(pattern=r'^r[1-9][0-9]*$')]
    reason: Text


class ListTexts(Input):
    after: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 20


MODELS = dict(create=Create, revise=Revise, retire=Retire, list=ListTexts)


def tool_definitions():
    descriptions = {
        'create': 'Register a hypothesis, forecast, plan, conclusion or counterevidence in registrations.json. Supply explicit business objects and applicability time; use unknown with a reason when evidence is unavailable. Evidence uses path (optionally path@commit or commit), record, or in PF only sql/version. purpose is current or historical_only. Optional select uses row equality keys and col, or a JSON Pointer path. Optional predicates: tolerance amount around the cited value, threshold op/value, or compare left/op/right within one query result. Predicates are only stored. Notes over 200 characters are truncated. Returns rN and rN.M.',
        'revise': 'Append a revision with a reason. Omitted fields, including existing evidence bindings, stay unchanged. Supplied references replace the entire reference list and are validated anew. Old revisions remain in registrations.json.',
        'retire': 'Stop using a registered text. Append a retired revision with a reason and preserve all history.',
        'list': 'Page through current, active registered texts in creation order. Includes text and reference notes; does not expand references, find reverse links, or check staleness. Pass next_after as after for the next page.',
    }
    return [dict(name='text_' + name, description=descriptions[name], parameters=model.model_json_schema())
            for name, model in MODELS.items()]


REGISTRATION_PROMPT = '''

You may use text_create, text_revise, text_retire and text_list to preserve useful
hypotheses, forecasts, plans, conclusions and counterevidence across weeks.
Choose what to register; missing registration never blocks business actions.
Registrations are saved in registrations.json. MEMORY.md remains your free-form
weekly memory; registrations are not automatically injected into your context.
Distinguish acquisition time, the day/interval described by evidence, and when
you read it. Use an explicit unknown reason when applicability is unclear.
Dashboard normally reflects the previous weekly advance. After changing settings
within a week, use the corresponding public query to obtain current settings.
Git file references require a path present in a commit. A path alone binds HEAD;
path@commit accepts a unique commit prefix. Commit new files yourself or wait for
the weekly commit. Registration never commits or copies cited file contents.
Registered texts can cite each other directly using rN.M, including before a Git
commit. A bare rN binds its current revision; later revisions do not change that
reference. Revise with omitted references to preserve the original bindings.
In PF, a path or SQL defaults to the last version actually sent to your model,
not the latest captured version. Only already delivered evidence and selected
ranges can be cited. Optional vN handles identify versions; use a path if a handle
is unavailable. An unknown reference with a reason is always allowed.
'''
