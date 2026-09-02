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
  container.classList.toggle("is-empty", !view);
  if (view?.designId) {
    container.dataset.changeObjectId = view.designId;
    container.dataset.changeRoot = "";
    container.dataset.changeField = "name kind definition populateOnCreate";
  } else {
    delete container.dataset.changeObjectId;
    delete container.dataset.changeRoot;
    delete container.dataset.changeField;
  }
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
