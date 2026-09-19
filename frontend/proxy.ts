import {NextRequest, NextResponse} from "next/server";

type CurrentUser={role?:string;status?:string};

const internalApiBase=process.env.INTERNAL_API_URL||"http://backend:8000/api/v1";

export async function proxy(request:NextRequest){
  const loginUrl=new URL("/login",request.url);
  try{
    const host=request.headers.get("host")||"localhost";
    const response=await fetch(`${internalApiBase}/auth/me/`,{
      method:"GET",
      headers:{
        cookie:request.headers.get("cookie")||"",
        host,
        "x-forwarded-proto":request.nextUrl.protocol.replace(":","")||"http",
      },
      cache:"no-store",
    });
    if(!response.ok)return NextResponse.redirect(loginUrl);
    const user=await response.json() as CurrentUser;
    if(user.status!=="active")return NextResponse.redirect(loginUrl);
    if(user.role!=="platform_admin")return NextResponse.redirect(new URL("/app",request.url));
    return NextResponse.next();
  }catch{
    return NextResponse.redirect(loginUrl);
  }
}

export const config={matcher:["/admin-console/:path*"]};
