import type {MetadataRoute} from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "AI Workspace",
    short_name: "AI Workspace",
    description: "Чаты, проекты и лучшие AI-модели в одном месте.",
    start_url: "/",
    display: "standalone",
    background_color: "#f4f3f8",
    theme_color: "#171620",
    lang: "ru",
    icons: [{src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any"}],
  };
}
