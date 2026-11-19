import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AreaIconPicker, isIconKey } from "./areaIcons";
import { AREA_ICON_KEYS, AreaIcon } from "./icons";

beforeEach(() => localStorage.setItem("argos-language", "ko"));
afterEach(cleanup);

test("known icon keys draw an svg, letters fall back", () => {
  const { container, rerender } = render(
    <AreaIcon icon="book" fallback="학" />,
  );
  expect(container.querySelector("svg")).not.toBeNull();
  rerender(<AreaIcon icon="학" fallback="학" />);
  expect(container.querySelector("svg")).toBeNull();
  expect(container.textContent).toBe("학");
  expect(isIconKey("book")).toBe(true);
  expect(isIconKey("AB")).toBe(false);
  expect(isIconKey(null)).toBe(false);
});

test("curated set has 12 to 16 icons", () => {
  expect(AREA_ICON_KEYS.length).toBeGreaterThanOrEqual(12);
  expect(AREA_ICON_KEYS.length).toBeLessThanOrEqual(16);
});

test("letters or an icon, one at a time", () => {
  const onChange = vi.fn();
  const { rerender } = render(
    <AreaIconPicker value="flask" name="연구" onChange={onChange} />,
  );
  expect(
    screen.getByRole("button", { name: "flask" }).getAttribute("aria-pressed"),
  ).toBe("true");
  const letters = screen.getByLabelText(/레일 글자/) as HTMLInputElement;
  expect(letters.value).toBe(""); // an icon is chosen, so no letters
  fireEvent.change(letters, { target: { value: "AI" } });
  expect(onChange).toHaveBeenLastCalledWith("AI");
  fireEvent.click(screen.getByRole("button", { name: "글자로 표시" }));
  expect(onChange).toHaveBeenLastCalledWith("");

  rerender(<AreaIconPicker value="AI" name="연구" onChange={onChange} />);
  expect((screen.getByLabelText(/레일 글자/) as HTMLInputElement).value).toBe(
    "AI",
  );
  expect(
    screen
      .getByRole("button", { name: "글자로 표시" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "code" }));
  expect(onChange).toHaveBeenLastCalledWith("code");
});
