import type {Metadata} from "next";
import {cookies} from "next/headers";
import {redirect} from "next/navigation";
import AdminNav from "./AdminNav";
import styles from "./admin.module.css";

export const metadata:Metadata={title:"AI Workspace — Панель администратора",robots:{index:false,follow:false}};

type CurrentUser={role?:string;status?:string};

async function requirePlatformAdmin(){
  const cookieStore=await cookies();
  const cookieHeader=cookieStore.toString();
  if(!cookieHeader) redirect("/login?next=%2Fadmin-console");

  const base=process.env.INTERNAL_API_URL||"http://backend:8000/api/v1";
  const appDomain=process.env.APP_DOMAIN||"localhost";
  const publicSite=process.env.NEXT_PUBLIC_SITE_URL||"http://localhost";
  const forwardedProto=publicSite.startsWith("https://")?"https":"http";
  let response:Response;
  try{
    response=await fetch(`${base}/auth/me/`,{
      headers:{Cookie:cookieHeader,Host:appDomain,"X-Forwarded-Proto":forwardedProto},
      cache:"no-store",
    });
  }catch{
    redirect("/login?next=%2Fadmin-console");
  }

  if(response.status===401||response.status===403) redirect("/login?next=%2Fadmin-console");
  if(!response.ok) redirect("/login?next=%2Fadmin-console");

  const user=await response.json() as CurrentUser;
  if(user.status!=="active") redirect("/login?next=%2Fadmin-console");
  if(user.role!=="platform_admin") redirect("/app");
}

export default async function AdminLayout({children}:{children:React.ReactNode}){
  await requirePlatformAdmin();
  return <div className={styles.shell}><AdminNav/><main className={styles.main}>{children}</main></div>;
}
