import type {Metadata, Viewport} from "next";
import ProductAnalytics from "./components/ProductAnalytics";
import "./styles.css";

const siteUrl=process.env.NEXT_PUBLIC_SITE_URL??"http://localhost:3000";
export const metadata: Metadata = {
  metadataBase:new URL(siteUrl),
  title:{default:"AI Workspace",template:"%s | AI Workspace"},
  description:"Чаты, проекты и AI-модели в одном рабочем пространстве с единым рублёвым балансом.",
  applicationName:"AI Workspace",
  manifest:"/manifest.webmanifest",
  alternates:{canonical:"/"},
  openGraph:{siteName:"AI Workspace",type:"website",locale:"ru_RU",url:"/"},
  twitter:{card:"summary_large_image"},
};
export const viewport: Viewport = {themeColor:"#171620",width:"device-width",initialScale:1};
export default function RootLayout({children}:Readonly<{children:React.ReactNode}>){return <html lang="ru"><body><ProductAnalytics/>{children}</body></html>}
