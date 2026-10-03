import type {MetadataRoute} from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "AIlegend",
    short_name: "AIlegend",
    description: "AI-чат, проекты, агенты, разработка и модели в одном сервисе.",
    start_url: "/",
    display: "standalone",
    background_color: "#f4f3f8",
    theme_color: "#171620",
    lang: "ru",
    icons: [{src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any"}],
  };
}
