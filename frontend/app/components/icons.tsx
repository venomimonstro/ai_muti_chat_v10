type IconName =
  | "chat"
  | "search"
  | "folder"
  | "file"
  | "wallet"
  | "bell"
  | "settings"
  | "support"
  | "menu"
  | "close"
  | "send"
  | "plus"
  | "spark"
  | "copy"
  | "stop"
  | "memory";

const paths: Record<IconName, React.ReactNode> = {
  chat: <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v7a2.5 2.5 0 0 1-2.5 2.5H10l-5 4v-4.5A2.5 2.5 0 0 1 4 12.5z" />,
  search: <><circle cx="11" cy="11" r="7"/><path d="m16 16 4 4"/></>,
  folder: <path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v8A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z"/>,
  file: <><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5"/></>,
  wallet: <><path d="M3 6a2 2 0 0 1 2-2h14v16H5a2 2 0 0 1-2-2z"/><path d="M15 10h6v5h-6a2.5 2.5 0 0 1 0-5"/></>,
  bell: <><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 22h4"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1"/></>,
  support: <><circle cx="12" cy="12" r="9"/><path d="M9.8 9a2.3 2.3 0 1 1 3.1 2.2c-.9.4-.9 1-.9 1.8M12 17h.01"/></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16"/>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  send: <path d="m5 12 14-8-4 16-3-6zM5 12l7 2"/>,
  plus: <path d="M12 5v14M5 12h14"/>,
  spark: <path d="m12 2 1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6z"/>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></>,
  stop: <rect x="6" y="6" width="12" height="12" rx="2"/>,
  memory: <><path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 3c0 1 .5 2 1.3 2.5A3 3 0 0 0 8 18h1M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 3c0 1-.5 2-1.3 2.5A3 3 0 0 1 16 18h-1M9 4v16M15 4v16M9 9h3M12 15h3"/></>,
};

export function Icon({name, size = 20}: {name: IconName; size?: number}) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
