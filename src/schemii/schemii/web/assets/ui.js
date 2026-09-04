// Compatibility entry point. The implementation is owned by common so every
// product uses the same vetted icons and interaction primitives.
const sharedUi = await import(
  typeof document === "undefined"
    ? "../../../common/web/assets/ui.js"
    : "/assets/common/ui.js"
);

export const {
  closeDetailsMenus,
  createIconButton,
  createIconElement,
  createStatePanel,
  decorateIconControl,
  DockPane,
  downloadContent,
  hydrateIconControls,
  ICONS,
  initializeUi,
  installDetailsMenu,
  installOverflowDisclosure,
  installVisualViewportSizing,
  isOverflowingText,
  renderStatePanel,
  setControlLoading,
  withLoadingControl,
} = sharedUi;
