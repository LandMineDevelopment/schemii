import { createQueryStoryRenderer } from "/assets/common/query-story.js";

import { element, emptyPanel, errorPanel, replace } from "./dom.js";
import { installOverflowDisclosure } from "./ui.js";

const { renderQueryStory } = createQueryStoryRenderer({
  element,
  emptyPanel,
  errorPanel,
  replace,
  installOverflowDisclosure,
});

function viewEyebrow(view) {
  if (view.catalogKind !== "materialized_view") return "LIVE QUERY · recalculated when read";
  return view.populateOnCreate === false
    ? "STORED RESULT · created empty"
    : "STORED RESULT · populated when created";
}

export function renderDesignViewStory(container, options = {}) {
  const { view, analysis = null, ...rest } = options;
  return renderQueryStory(container, {
    ...rest,
    subject: view ? {
      name: view.name,
      definition: view.queryDefinition,
      eyebrow: viewEyebrow(view),
    } : null,
    analysis,
    impactItems: analysis?.consumers || [],
    emptyLabel: "VIEW",
    emptyTitle: "No view selected",
    emptyMessage: "Select a designed view to see its source-derived relational meaning.",
    warningCopy: {
      recursive_reference: "The query refers to the view it is defining.",
    },
  });
}
