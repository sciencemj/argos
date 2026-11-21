import { afterEach, describe, expect, it } from "vitest";
import { readMascotPref, setMascotPref } from "./pref";

afterEach(() => {
  localStorage.clear();
  setMascotPref("follow");
});

describe("mascot preference", () => {
  it("follows the cursor by default", () => {
    localStorage.clear();
    expect(readMascotPref()).toBe("follow");
  });

  it("remembers still and off on this device", () => {
    setMascotPref("off");
    expect(readMascotPref()).toBe("off");
    setMascotPref("still");
    expect(readMascotPref()).toBe("still");
  });

  it("ignores values it does not know", () => {
    localStorage.setItem("argos.mascot", "dance");
    expect(readMascotPref()).toBe("follow");
  });
});
