import type {Metadata} from "next";
import "./workspace.css";
import "./workspace-fixes.css";
import "./workspace-performance.css";

export const metadata: Metadata = {
  title: "AI Workspace — рабочее пространство",
  description: "Чаты, проекты, файлы, изображения и подключённые AI-модели в одном рабочем пространстве.",
  robots: {index: false, follow: false},
};

export default function AppLayout({children}: Readonly<{children: React.ReactNode}>) {
  return children;
}
