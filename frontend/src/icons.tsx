import type { ReactNode, SVGProps } from "react";

// Stroke icons copied from docs/design (24×24 grid, currentColor).
function Stroke({
  size = 16,
  width = 1.8,
  children,
  ...rest
}: {
  size?: number;
  width?: number;
  children: ReactNode;
} & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

type P = { size?: number };

export const DogIcon = ({ size = 26 }: P) => (
  <Stroke size={size} width={1.7}>
    <path d="M7 9.2C7 6.4 9.2 4.6 12 4.6s5 1.8 5 4.6v4.3c0 3-2.2 5.6-5 5.6s-5-2.6-5-5.6z" />
    <path d="M7.3 7.4C5.6 6.1 3.3 6.7 2.9 8.9c-.4 2.2.5 4.7 2.5 5.3 1 .3 1.7-.3 1.7-1.3" />
    <path d="M16.7 7.4c1.7-1.3 4-.7 4.4 1.5.4 2.2-.5 4.7-2.5 5.3-1 .3-1.7-.3-1.7-1.3" />
    <circle cx="10" cy="11" r="0.9" fill="currentColor" stroke="none" />
    <circle cx="14" cy="11" r="0.9" fill="currentColor" stroke="none" />
    <path d="M10.9 14.3h2.2L12 15.6z" fill="currentColor" />
  </Stroke>
);

export const PlusIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <path d="M12 5v14M5 12h14" />
  </Stroke>
);

export const SearchIcon = ({ size = 15 }: P) => (
  <Stroke size={size}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Stroke>
);

export const SunIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5" />
  </Stroke>
);

export const MoonIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
  </Stroke>
);

export const InboxIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <path d="M3 13h5l2 3h4l2-3h5" />
    <path d="M5.5 5h13l2.5 8v6H3v-6z" />
  </Stroke>
);

export const CheckIcon = ({ size = 16 }: P) => (
  <Stroke size={size} width={2}>
    <path d="m5 12 5 5 9-10" />
  </Stroke>
);

export const CalendarIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </Stroke>
);

export const CloseIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Stroke>
);

export const SettingsIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </Stroke>
);

export const PawIcon = ({ size = 16 }: P) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
  >
    <ellipse cx="12" cy="15.6" rx="4.3" ry="3.7" />
    <circle cx="6.3" cy="10.4" r="1.9" />
    <circle cx="9.9" cy="6.8" r="1.9" />
    <circle cx="14.1" cy="6.8" r="1.9" />
    <circle cx="17.7" cy="10.4" r="1.9" />
  </svg>
);

export const KanbanIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <rect x="3" y="4" width="5" height="16" rx="1" />
    <rect x="10" y="4" width="5" height="10" rx="1" />
    <rect x="17" y="4" width="4" height="13" rx="1" />
  </Stroke>
);

export const PinIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <path d="M12 17v5M8 3h8l-1 6 3 4H6l3-4z" />
  </Stroke>
);

export const SendIcon = ({ size = 16 }: P) => (
  <Stroke size={size} width={2}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Stroke>
);
