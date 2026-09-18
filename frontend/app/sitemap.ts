import type {MetadataRoute} from "next";

const siteUrl=(process.env.NEXT_PUBLIC_SITE_URL??"http://localhost:3000").replace(/\/$/,"");
export default function sitemap():MetadataRoute.Sitemap{
  const paths=["/","/pricing","/faq","/getting-started","/api","/use-cases/marketing","/use-cases/coding","/use-cases/documents","/legal/offer","/legal/privacy","/legal/refunds","/legal/acceptable-use","/status"];
  return paths.map((path,index)=>({url:`${siteUrl}${path}`,lastModified:new Date(),changeFrequency:index===0?"daily":"weekly",priority:index===0?1:path.startsWith("/legal/")?.4:.7}));
}
