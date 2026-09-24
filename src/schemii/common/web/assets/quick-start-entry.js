import { installQuickStart } from "./quick-start.js";

const trigger = document.querySelector("[data-quick-start-product]");
if (trigger) installQuickStart(trigger.dataset.quickStartProduct, trigger);
