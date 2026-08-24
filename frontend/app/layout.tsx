import type {Metadata, Viewport} from "next";
import "./styles.css";
export const metadata: Metadata = {
  title: "AI Workspace",
  description: "Чаты, проекты и лучшие AI-модели в одном рабочем пространстве.",
  applicationName: "AI Workspace",
  manifest: "/manifest.webmanifest",
};
export const viewport: Viewport = {themeColor: "#171620", width: "device-width", initialScale: 1};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="ru"><body>{children}</body></html>;
}
