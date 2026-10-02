// Show / hide a Blockly workspace's toolbox, as a presentation toggle only: no
// block, category, serialization or generator is touched. Kept apart from
// BlocklyWorkspace.jsx (which owns the workspace instance) so the sequence is
// testable without a DOM — `svgResize` is passed in rather than imported.
//
// Hiding the toolbox is not just `display: none`, because Blockly caches the
// toolbox's width and uses it for the workspace metrics:
//   1. the hidden class goes on first. `Toolbox.position()` (which `svgResize`
//      ends up calling) reads the toolbox's `offsetWidth`, so the toolbox must
//      already be out of the layout for the canvas to be handed its width. The
//      class is also what guarantees the result (see `.toolbox-hidden` in
//      build.css): `Toolbox.setVisible` returns early whenever its own private
//      `isVisible_` flag already equals the request, so on its own it cannot be
//      relied on to change what is actually drawn;
//   2. an open flyout belongs to the toolbox's selected category, so the
//      selection is cleared (which closes the flyout);
//   3. `setVisible` is Blockly's own switch — it also stops the hidden toolbox
//      from being a delete zone for dragged blocks;
//   4. `svgResize` makes Blockly re-measure, which is what hands the canvas the
//      space the toolbox occupied. Blockly measures the workspace's origin from
//      the toolbox edge, so the blocks move left with the edge and move back
//      when it returns — no scroll adjustment of our own is needed.
//
// Stateless on purpose: every call re-asserts the whole sequence from the
// requested value. An earlier version remembered the last value per workspace
// and skipped a repeat, which left the page believing the toolbox was hidden
// whenever Blockly had shown it again behind that memory's back.

// Put on the workspace's injection div (Blockly's own element, so React never
// rewrites it). The matching rule is in build.css.
export const TOOLBOX_HIDDEN_CLASS = 'toolbox-hidden'

/**
 * @param {object} workspace a Blockly WorkspaceSvg (or a stand-in with the same calls)
 * @param {boolean} shown whether the toolbox should be visible
 * @param {(workspace: object) => void} svgResize `Blockly.svgResize`
 */
export function applyToolboxVisibility(workspace, shown, svgResize) {
  const toolbox = workspace.getToolbox?.()
  if (!toolbox) return

  workspace.getInjectionDiv?.()?.classList.toggle(TOOLBOX_HIDDEN_CLASS, !shown)
  if (!shown) toolbox.clearSelection()
  toolbox.setVisible(shown)
  svgResize(workspace)
}
