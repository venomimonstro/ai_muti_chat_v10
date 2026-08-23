import type { Metadata } from "next";
import "./styles.css";
export const metadata: Metadata = {title: "AI Workspace", description: "Один AI. Все лучшие модели."};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="ru"><body>{children}</body></html>;
}

