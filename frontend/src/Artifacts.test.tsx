import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArtifactView } from "./Artifacts";

describe("artifact rendering", () => {
  it("falls back to a result table without numeric values", () => {
    render(
      <ArtifactView
        artifact={{
          type: "table",
          columns: ["name", "state"],
          rows: [["Acme", null]],
          row_count: 1,
          truncated: true,
        }}
      />,
    );
    expect(screen.queryByRole("tab", { name: /Chart/ })).not.toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "—" })).toBeInTheDocument();
    expect(screen.getByText("1 rows · limited")).toBeInTheDocument();
  });

  it("renders invalid candidate errors and suggestions", () => {
    render(
      <ArtifactView
        artifact={{
          type: "candidate_rule",
          rule: null,
          valid: false,
          repair_count: 2,
          errors: [{ code: "unsafe", message: "Unsafe field.", suggestion: "Use amount instead." }],
        }}
      />,
    );
    expect(screen.getByText("Fraud hypothesis")).toBeInTheDocument();
    expect(screen.getByText("Unsafe field. Use amount instead.")).toBeInTheDocument();
  });

  it("switches back from the table to the chart", async () => {
    const user = userEvent.setup();
    render(
      <ArtifactView
        artifact={{
          type: "table",
          columns: ["name", "count"],
          rows: [["A", 3]],
          row_count: 1,
          truncated: false,
        }}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "▦ Result" }));
    await user.click(screen.getByRole("tab", { name: "▥ Chart" }));
    expect(screen.getByLabelText("Chart of count")).toBeInTheDocument();
  });

  it("does not chart signed values as positive bars", () => {
    render(
      <ArtifactView
        artifact={{
          type: "table",
          columns: ["name", "delta"],
          rows: [
            ["A", 3],
            ["B", -2],
          ],
          row_count: 2,
          truncated: false,
        }}
      />,
    );
    expect(screen.queryByRole("tab", { name: /Chart/ })).not.toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "-2" })).toBeInTheDocument();
  });
});
