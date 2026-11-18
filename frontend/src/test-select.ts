import { fireEvent } from "@testing-library/react";

/** Picks `value` in a SelectMenu (the app's dropdown) the way a user does: open it,
 * click the option. */
export function selectValue(trigger: HTMLElement, value: string) {
  fireEvent.click(trigger);
  const option = [
    ...document.querySelectorAll<HTMLElement>("[role=option]"),
  ].find((o) => o.dataset.value === value);
  if (!option) throw new Error(`no option with value ${JSON.stringify(value)}`);
  fireEvent.click(option);
}
