import type { SVGProps } from "react";

// A small stroke icon set (24px grid, drawn at 16px by default).
const paths = {
  dashboard: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  cloud: "M7 18h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.1 9.4 4.3 4.3 0 0 0 7 18z",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
  plus: "M12 5v14M5 12h14",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
  trash: "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12M18 6L6 18",
  checkCircle: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0zM7.5 12.5l3 3 6-6.5",
  alertTriangle: "M12 3.5l9.5 16.5h-19zM12 10v4.5M12 17.5h.01",
  alertOctagon: "M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9zM12 7.5v5.5M12 16.5h.01",
  helpCircle: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0zM9.5 9a2.6 2.6 0 0 1 5 1c0 1.8-2.5 2.2-2.5 3.8M12 17.2h.01",
  info: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0zM12 11v6M12 7.5h.01",
  clock: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0zM12 6.5V12l3.5 2",
  loader: "M12 3a9 9 0 1 0 9 9",
  slash: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0zM5 5l14 14",
  chevronRight: "M9 6l6 6-6 6",
  chevronDown: "M6 9l6 6 6-6",
  menu: "M4 6h16M4 12h16M4 18h16",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  scale: "M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5",
  heart: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z",
  zap: "M13 2L3 14h8l-1 8 10-12h-8z",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5",
  network: "M12 3v6M6 15v-3h12v3M4 15h4v6H4zM16 15h4v6h-4zM10 3h4v6h-4z",
  key: "M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  server: "M3 4h18v6H3zM3 14h18v6H3zM7 7h.01M7 17h.01",
  flask: "M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9V3",
} as const;

export type IconName = keyof typeof paths;

export function Icon({
  name,
  size = 16,
  title,
  ...rest
}: { name: IconName; size?: number; title?: string } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      {...rest}
    >
      {title ? <title>{title}</title> : null}
      <path d={paths[name]} />
    </svg>
  );
}
