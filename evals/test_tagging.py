"""Structural eval: every CDK stack tags its resources Project/Environment/ManagedBy.

Imports the real CDK app (synth is guarded behind __main__, so importing it just
builds the constructs) and asserts the tags land on a representative resource per
environment.
"""

from __future__ import annotations

from aws_cdk.assertions import Template

import infrastructure.app as app_module


def _table_tags(stack: object) -> dict[str, str]:
    template = Template.from_stack(stack)
    tables = template.find_resources("AWS::DynamoDB::Table")
    assert tables, "expected a tagged DynamoDB table in the stack"
    props = next(iter(tables.values()))["Properties"]
    return {t["Key"]: t["Value"] for t in props.get("Tags", [])}


def test_dev_stack_is_tagged() -> None:
    tags = _table_tags(app_module.dev_agent)
    assert tags.get("Project") == "Lookout"
    assert tags.get("Environment") == "dev"
    assert tags.get("ManagedBy") == "cdk"


def test_prod_stack_is_tagged() -> None:
    tags = _table_tags(app_module.prod_agent)
    assert tags.get("Project") == "Lookout"
    assert tags.get("Environment") == "prod"
    assert tags.get("ManagedBy") == "cdk"
