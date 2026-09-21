import type {Metadata} from "next";
import CryptoCompat from "../components/CryptoCompat";
import WorkspaceUxEnhancer from "../components/WorkspaceUxEnhancer";
import "./workspace.css";
import "./workspace-fixes.css";
import "./workspace-performance.css";
import "./workspace-premium.css";

export const metadata: Metadata = {
  title: "AI Workspace — рабочее пространство",
  description: "Чаты, проекты, файлы, изображения и подключённые AI-модели в одном рабочем пространстве.",
  robots: {index: false, follow: false},
};

export default function AppLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <><CryptoCompat/><WorkspaceUxEnhancer/>{children}</>;
}
