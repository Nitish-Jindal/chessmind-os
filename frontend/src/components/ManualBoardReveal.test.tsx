import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ManualBoardReveal } from "./ManualBoardReveal";

const render = (props: Partial<Parameters<typeof ManualBoardReveal>[0]> = {}) =>
  renderToStaticMarkup(
    <ManualBoardReveal
      blindfold
      revealed={false}
      finished={false}
      onReveal={() => {}}
      onHide={() => {}}
      {...props}
    >
      <div data-testid="chess-board">visible board</div>
    </ManualBoardReveal>,
  );

describe("ManualBoardReveal", () => {
  it("keeps a blindfold board hidden until the parent records a manual reveal", () => {
    const hidden = render();
    expect(hidden).toContain('aria-label="Board hidden for blindfold play"');
    expect(hidden).toContain("Reveal board");
    expect(hidden).not.toContain("data-testid=\"chess-board\"");

    const revealed = render({ revealed: true });
    expect(revealed).toContain("data-testid=\"chess-board\"");
    expect(revealed).toContain("Hide board");
  });

  it("keeps the manual reveal control available regardless of reveal count", () => {
    // Reveal counts are deliberately not part of this component's policy.
    const onReveal = vi.fn();
    const markup = render({ onReveal });
    expect(markup).toContain("Reveal board");
    expect(markup).not.toMatch(/reveals?\s*\d+\s*\/\s*\d+/i);
  });

  it("shows the board without a reveal control outside blindfold mode", () => {
    const markup = render({ blindfold: false });
    expect(markup).toContain("data-testid=\"chess-board\"");
    expect(markup).not.toContain("Reveal board");
  });
});
