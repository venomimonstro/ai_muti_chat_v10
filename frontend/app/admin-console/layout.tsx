import type {Metadata} from "next";
import {cookies} from "next/headers";
import {redirect} from "next/navigation";
import AdminNav from "./AdminNav";
import styles from "./admin.module.css";

export const metadata:Metadata={title:"AI Workspace — Панель администратора",robots:{index:false,follow:false}};

type CurrentUser={role?:string;status?:string;is_staff?:boolean;is_superuser?:boolean};

function isPlatformAdmin(user:CurrentUser){
  return user.role==="platform_admin"||user.is_staff===true||user.is_superuser===true;
}

async function requirePlatformAdmin(){
  const cookieStore=await cookies();
  const cookieHeader=cookieStore.toString();
  if(!cookieHeader) redirect("/login?next=%2Fadmin-console");

  const base=process.env.INTERNAL_API_URL||"http://backend:8000/api/v1";
  let response:Response;
  try{
    response=await fetch(`${base}/auth/me/`,{
      headers:{Cookie:cookieHeader},
      cache:"no-store",
      signal:AbortSignal.timeout(5000),
    });
  }catch{
    redirect("/login?next=%2Fadmin-console&error=session_check");
  }

  if(response.status===401||response.status===403) redirect("/login?next=%2Fadmin-console");
  if(!response.ok) redirect(`/login?next=%2Fadmin-console&error=admin_check_${response.status}`);

  const user=await response.json() as CurrentUser;
  if(user.status!=="active") redirect("/login?next=%2Fadmin-console&error=inactive");
  if(!isPlatformAdmin(user)) redirect("/app");
}

export default async function AdminLayout({children}:{children:React.ReactNode}){
  await requirePlatformAdmin();
  return <div className={styles.shell}><AdminNav/><main className={styles.main}>{children}</main></div>;
}
