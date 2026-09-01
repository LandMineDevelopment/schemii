"""Schemii-specific enrichment for source-derived view query analysis."""

from __future__ import annotations

from schemii.common.postgres.view_analysis import (
    ViewDefinitionError,
    analyze_view_definition,
    referenced_relations,
)

from .models import (
    DesignViewAnalysis,
    DesignViewAnalysisRequest,
    SchemiiDesignContent,
)


def analyze_design_view(
    content: SchemiiDesignContent,
    request: DesignViewAnalysisRequest,
) -> DesignViewAnalysis:
    """Analyze a draft against the current design without persisting derived data."""

    relations = [
        {
            "namespace": "desired",
            "name": table.name,
            "kind": "table",
            "columns": [
                {"name": column.name, "data_type": column.data_type}
                for column in table.columns
            ],
        }
        for table in content.tables
    ]
    relations.extend(
        {
            "namespace": "desired",
            "name": view.name,
            "kind": view.kind,
            "columns": [],
        }
        for view in content.views
        if view.id != request.view_id
    )
    analysis = analyze_view_definition(
        request.definition,
        relations,
        current_namespace="desired",
    )
    consumers = []
    for view in content.views:
        if view.id == request.view_id:
            continue
        try:
            references = referenced_relations(
                view.definition,
                current_namespace="desired",
            )
        except ViewDefinitionError:
            continue
        if ("desired", request.name) in references:
            consumers.append({"id": view.id, "name": view.name, "kind": view.kind})
    if any(source["name"] == request.name for source in analysis["sources"]):
        analysis["warnings"] = sorted({*analysis["warnings"], "recursive_reference"})
        analysis["status"] = "partial"
    analysis["consumers"] = consumers
    return DesignViewAnalysis.model_validate(analysis)
